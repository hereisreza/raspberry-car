import asyncio
import json
import logging
import time
import psutil
import subprocess

from fastapi import FastAPI
from fastapi import WebSocket
from fastapi import WebSocketDisconnect

from fastapi.responses import HTMLResponse

from core_motor import DifferentialDriveController


# ============================================================
# WebSocket Log Handler
# ============================================================

class WebSocketLogHandler(logging.Handler):

    def __init__(self):
        super().__init__()
        self.log_queue = asyncio.Queue()

    def emit(self, record):

        log_entry = {
            "type": "log",
            "level": record.levelname,
            "message": self.format(record)
        }

        try:
            loop = asyncio.get_running_loop()

            loop.call_soon_threadsafe(
                self.log_queue.put_nowait,
                log_entry
            )

        except RuntimeError:
            pass


# ============================================================
# Logging
# ============================================================

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ws_handler = WebSocketLogHandler()
ws_handler.setFormatter(logging.Formatter("%(message)s"))

logger.addHandler(ws_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s"
    )
)

logger.addHandler(console_handler)


# ============================================================
# App
# ============================================================

app = FastAPI(
    title="Mechatronics Web Drive"
)

motors = DifferentialDriveController()

# ============================================================
# Runtime State
# ============================================================

active_websocket = None
last_heartbeat_time = time.time()

current_settings = {
    "max_motor_power": 0.70,
    "deadzone": 0.10,
    "steering_gain": 1.00,
    "acceleration_rate": 1.00,
    "braking_rate": 1.50,
    "throttle_expo": 2.00,
    "pwm_frequency": 500
}


# ============================================================
# Startup / Shutdown
# ============================================================

@app.on_event("startup")
async def startup_event():

    logger.info(
        "Server starting up..."
    )

    asyncio.create_task(
        watchdog_task()
    )

    asyncio.create_task(
        telemetry_task()
    )

    asyncio.create_task(
        log_broadcaster_task()
    )


@app.on_event("shutdown")
def shutdown_event():

    logger.warning(
        "Server shutting down."
    )

    motors.shutdown()


# ============================================================
# Watchdog
# ============================================================

async def watchdog_task():

    global last_heartbeat_time

    while True:

        await asyncio.sleep(0.1)

        if (
            time.time() - last_heartbeat_time
        ) > 0.5:

            if (
                motors.current_left_speed != 0
                or
                motors.current_right_speed != 0
            ):

                logger.error(
                    "WATCHDOG: connection lost."
                )

                motors.stop_all()


# ============================================================
# Log Broadcaster
# ============================================================

async def log_broadcaster_task():

    global active_websocket

    while True:

        log_entry = await ws_handler.log_queue.get()

        if not active_websocket:
            continue

        try:

            await active_websocket.send_json(
                log_entry
            )

        except Exception:
            pass


# ============================================================
# Telemetry
# ============================================================

async def telemetry_task():

    global active_websocket

    while True:

        # ارسال داده با سرعت بیشتر برای روان بودن انیمیشن گیج موتورها (0.5 ثانیه)
        await asyncio.sleep(0.5)

        if not active_websocket:
            continue

        try:

            cpu_load = psutil.cpu_percent()
            ram_usage = psutil.virtual_memory().percent

            # CPU Temp
            try:
                with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                    temp_c = int(f.read()) / 1000.0
            except Exception:
                temp_c = 0.0

            # WiFi
            wifi_signal = 0
            try:
                with open("/proc/net/wireless", "r") as f:
                    lines = f.readlines()
                    if len(lines) > 2:
                        parts = lines[2].split()
                        quality = float(parts[2].replace(".", ""))
                        wifi_signal = min(100, int(quality / 70.0 * 100))
            except Exception:
                wifi_signal = 0

            # Power
            try:
                output = subprocess.check_output(["vcgencmd", "get_throttled"]).decode("utf-8").strip()
                value = int(output.split("=")[1], 16)
                power_ok = (value & 0x1) == 0
            except Exception:
                power_ok = True

            payload = {
                "type": "telemetry",
                "cpu": cpu_load,
                "ram": ram_usage,
                "temp": round(temp_c, 1),
                "wifi": wifi_signal,
                "power_ok": power_ok,
                "motor_power": round(current_settings["max_motor_power"] * 100),
                
                # قدرت لحظه‌ای موتورها برای گیج فرانت‌اند
                "left_motor": round(motors.current_left_speed * 100),
                "right_motor": round(motors.current_right_speed * 100)
            }

            await active_websocket.send_json(payload)

        except Exception as e:
            logger.error(f"Telemetry error: {e}")


# ============================================================
# Routes
# ============================================================

@app.get("/")
async def get_interface():

    with open(
        "templates/index.html",
        "r",
        encoding="utf-8"
    ) as f:

        html = f.read()

    return HTMLResponse(content=html)


# ============================================================
# WebSocket
# ============================================================

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket
):

    global active_websocket
    global last_heartbeat_time

    await websocket.accept()

    active_websocket = websocket

    logger.info(
        "Client connected."
    )

    last_heartbeat_time = time.time()

    try:

        # ----------------------------------
        # Send current settings immediately
        # ----------------------------------

        await websocket.send_json({
            "type": "settings",
            "settings": current_settings
        })

        while True:

            raw = await websocket.receive_text()

            data = json.loads(raw)

            last_heartbeat_time = time.time()

            msg_type = data.get("type")

            # ----------------------------------
            # Ping
            # ----------------------------------

            if msg_type == "ping":
                continue

            # ----------------------------------
            # Drive
            # ----------------------------------

            elif msg_type == "drive":

                throttle = float(data.get("throttle", 0.0))
                steering = float(data.get("steering", 0.0))

                motors.drive(throttle, steering)

            # ----------------------------------
            # Settings Update
            # ----------------------------------

            elif msg_type == "settings":

                settings = data.get("settings", {})

                current_settings.update(settings)

                motors.update_settings(current_settings)

                logger.info("Settings updated from UI")

                await websocket.send_json({
                    "type": "settings",
                    "settings": current_settings
                })

            # ----------------------------------
            # Emergency Stop
            # ----------------------------------

            elif msg_type == "emergency_stop":

                motors.emergency_stop()

                logger.warning("Emergency stop activated.")

    except WebSocketDisconnect:

        logger.warning("Client disconnected.")

        motors.stop_all()

        active_websocket = None

    except Exception as e:

        logger.error(f"WebSocket error: {e}")

        motors.stop_all()

        active_websocket = None
