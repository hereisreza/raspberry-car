from __future__ import annotations

import asyncio
import json
import logging
import math
import mimetypes
import subprocess
import time
from pathlib import Path
from typing import Optional

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core_motor import DifferentialDriveController


# ============================================================
# Logging
# ============================================================

class WebSocketLogHandler(logging.Handler):
    """Broadcast log records to the active WebSocket client."""

    def __init__(self) -> None:
        super().__init__()
        self.log_queue: asyncio.Queue[dict] = asyncio.Queue()

    def emit(self, record: logging.LogRecord) -> None:
        log_entry = {
            "type": "log",
            "level": record.levelname,
            "message": self.format(record),
        }

        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self.log_queue.put_nowait, log_entry)
        except RuntimeError:
            pass


logger = logging.getLogger()
logger.setLevel(logging.INFO)

ws_handler = WebSocketLogHandler()
ws_handler.setFormatter(logging.Formatter("%(message)s"))

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

# Avoid duplicate handlers when uvicorn reloads the module.
if not any(isinstance(handler, WebSocketLogHandler) for handler in logger.handlers):
    logger.addHandler(ws_handler)
if not any(isinstance(handler, logging.StreamHandler) and handler is not ws_handler for handler in logger.handlers):
    logger.addHandler(console_handler)


# ============================================================
# App state
# ============================================================

app = FastAPI(title="Mechatronics Web Drive")
motors = DifferentialDriveController()

active_websocket: Optional[WebSocket] = None
last_heartbeat_time = time.monotonic()
watchdog_tripped = False
current_settings = motors.get_settings()
server_start_time = time.monotonic()
background_tasks_started = False

BASE_DIR = Path(__file__).resolve().parent
DASHBOARD_TEMPLATE = BASE_DIR / "templates" / "index.html"
STATIC_DIR = BASE_DIR / "static"

# The system mimetypes database is inconsistent across platforms (a fresh
# Windows dev machine and a Raspberry Pi's Linux install can disagree on
# font types), which breaks font preloading. Register the ones the
# dashboard actually serves explicitly so behavior is identical everywhere.
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/svg+xml", ".svg")

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============================================================
# Startup / shutdown
# ============================================================

@app.on_event("startup")
async def startup_event() -> None:
    global background_tasks_started

    logger.info("Server starting up.")

    if background_tasks_started:
        logger.warning("Background tasks already running; skipping duplicate startup.")
        return

    background_tasks_started = True
    asyncio.create_task(watchdog_task())
    asyncio.create_task(telemetry_task())
    asyncio.create_task(log_broadcaster_task())


@app.on_event("shutdown")
def shutdown_event() -> None:
    logger.warning("Server shutting down.")
    motors.shutdown()


# ============================================================
# Background tasks
# ============================================================

async def watchdog_task() -> None:
    """Immediately stop the robot if the browser stops sending heartbeats."""
    global watchdog_tripped

    while True:
        await asyncio.sleep(0.05)

        timed_out = (time.monotonic() - last_heartbeat_time) > 0.55
        state = motors.get_state()
        moving = abs(state["left_motor"]) > 0.005 or abs(state["right_motor"]) > 0.005

        if timed_out and moving:
            if not watchdog_tripped:
                logger.error("WATCHDOG: heartbeat lost; emergency stop applied.")
            watchdog_tripped = True
            motors.emergency_stop()


async def log_broadcaster_task() -> None:
    global active_websocket

    while True:
        log_entry = await ws_handler.log_queue.get()
        if not active_websocket:
            continue

        try:
            await active_websocket.send_json(log_entry)
        except Exception:
            pass


def read_cpu_temperature() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as file:
            return int(file.read()) / 1000.0
    except Exception:
        return 0.0


def read_wifi_signal() -> int:
    try:
        with open("/proc/net/wireless", "r", encoding="utf-8") as file:
            lines = file.readlines()
            if len(lines) > 2:
                parts = lines[2].split()
                quality = float(parts[2].replace(".", ""))
                return min(100, int(quality / 70.0 * 100))
    except Exception:
        pass
    return 0


def read_power_ok() -> bool:
    try:
        output = subprocess.check_output(
            ["vcgencmd", "get_throttled"],
            timeout=0.25,
        ).decode("utf-8").strip()
        value = int(output.split("=")[1], 16)
        return (value & 0x1) == 0
    except Exception:
        return True


def safe_normalized_number(value: object, default: float = 0.0) -> float:
    """Return a finite joystick value clamped to [-1, 1]."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(numeric):
        return default
    return max(-1.0, min(1.0, numeric))


async def telemetry_task() -> None:
    global active_websocket

    while True:
        await asyncio.sleep(0.20)

        if not active_websocket:
            continue

        try:
            state = motors.get_state()
            settings_snapshot = motors.get_settings()

            payload = {
                "type": "telemetry",
                "cpu": psutil.cpu_percent(),
                "ram": psutil.virtual_memory().percent,
                "temp": round(read_cpu_temperature(), 1),
                "wifi": read_wifi_signal(),
                "power_ok": read_power_ok(),
                "motor_power": round(settings_snapshot["max_motor_power"] * 100),
                "left_motor": round(state["left_motor"] * 100),
                "right_motor": round(state["right_motor"] * 100),
                "drive_mode": state["drive_mode"],
            }

            await active_websocket.send_json(payload)

        except Exception as exc:
            logger.error("Telemetry error: %s", exc)


# ============================================================
# Routes
# ============================================================

@app.get("/")
async def get_interface() -> HTMLResponse:
    """Serve the Raspberry Pi control dashboard.

    This must always be templates/index.html — the repository root index.html
    is a separate, unrelated GitHub Pages landing page and is never served
    here, even as a fallback.
    """
    if not DASHBOARD_TEMPLATE.exists():
        raise FileNotFoundError(f"Dashboard template not found at {DASHBOARD_TEMPLATE}")

    return HTMLResponse(content=DASHBOARD_TEMPLATE.read_text(encoding="utf-8"))


@app.get("/health")
async def get_health() -> JSONResponse:
    state = motors.get_state()
    return JSONResponse({
        "status": "ok",
        "uptime_s": round(time.monotonic() - server_start_time, 1),
        "client_connected": active_websocket is not None,
        "watchdog_tripped": watchdog_tripped,
        "drive_mode": state["drive_mode"],
    })


@app.get("/api/status")
async def get_status() -> JSONResponse:
    state = motors.get_state()
    return JSONResponse({
        "cpu": psutil.cpu_percent(),
        "ram": psutil.virtual_memory().percent,
        "temp": round(read_cpu_temperature(), 1),
        "wifi": read_wifi_signal(),
        "power_ok": read_power_ok(),
        "left_motor": round(state["left_motor"] * 100),
        "right_motor": round(state["right_motor"] * 100),
        "drive_mode": state["drive_mode"],
        "settings": motors.get_settings(),
        "client_connected": active_websocket is not None,
        "watchdog_tripped": watchdog_tripped,
    })


# ============================================================
# WebSocket endpoint
# ============================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    global active_websocket, last_heartbeat_time, watchdog_tripped, current_settings

    await websocket.accept()

    # Only one active driver is allowed at a time. A new connection takes
    # over and the previous one is closed safely; motors are stopped during
    # the handover so control never briefly belongs to no one and no one
    # simultaneously.
    previous_websocket = active_websocket
    active_websocket = websocket
    last_heartbeat_time = time.monotonic()
    watchdog_tripped = False

    if previous_websocket is not None and previous_websocket is not websocket:
        motors.emergency_stop()
        try:
            await previous_websocket.send_json({
                "type": "log",
                "level": "WARNING",
                "message": "این نشست توسط یک اتصال جدید جایگزین شد.",
            })
            await previous_websocket.close(code=4001, reason="replaced_by_new_connection")
        except Exception:
            pass
        logger.warning("Previous WebSocket client replaced by a new connection; motors stopped.")

    logger.info("Client connected.")

    try:
        await websocket.send_json({
            "type": "settings",
            "settings": current_settings,
        })

        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Ignored malformed WebSocket JSON message.")
                continue

            if not isinstance(data, dict):
                logger.warning("Ignored non-object WebSocket message.")
                continue

            last_heartbeat_time = time.monotonic()
            watchdog_tripped = False

            msg_type = data.get("type")

            if msg_type == "ping":
                continue

            if msg_type == "drive":
                throttle = safe_normalized_number(data.get("throttle", 0.0))
                steering = safe_normalized_number(data.get("steering", 0.0))
                motors.drive(throttle, steering)
                continue

            if msg_type == "settings":
                requested_settings = data.get("settings", {})
                if not isinstance(requested_settings, dict):
                    logger.warning("Ignored invalid settings payload.")
                    continue
                current_settings.update(requested_settings)
                current_settings = motors.update_settings(current_settings)

                await websocket.send_json({
                    "type": "settings",
                    "settings": current_settings,
                })
                continue

            if msg_type == "emergency_stop":
                motors.emergency_stop()
                logger.warning("Emergency stop activated from UI.")
                continue

            logger.warning("Unknown WebSocket message type: %s", msg_type)

    except WebSocketDisconnect:
        motors.emergency_stop()
        if active_websocket is websocket:
            active_websocket = None
            logger.warning("Client disconnected; motors stopped.")

    except Exception as exc:
        motors.emergency_stop()
        if active_websocket is websocket:
            active_websocket = None
            logger.error("WebSocket error: %s", exc)
