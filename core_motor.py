from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import asdict, dataclass
from typing import Optional, Tuple

try:
    from gpiozero import PWMOutputDevice
except Exception:  # pragma: no cover - keeps the app importable on non-Raspberry Pi machines
    PWMOutputDevice = None


logger = logging.getLogger("MechatronicsCore")


# =====================================================================
# Low-level PWM abstraction
# =====================================================================

class _DummyPWMOutputDevice:
    """Development-only PWM stub used when gpiozero is not available."""

    def __init__(self, pin: int, frequency: int = 500):
        self.pin = pin
        self.frequency = frequency
        self.value = 0.0

    def close(self) -> None:
        self.value = 0.0


class CustomMotor:
    """Safe two-PWM driver wrapper for one BTN7960 channel."""

    def __init__(
        self,
        forward_pin: int,
        backward_pin: int,
        *,
        freq: int = 500,
        inverted: bool = False,
        name: str = "motor",
        direction_change_deadtime_s: float = 0.003,
    ) -> None:
        self.name = name
        self.inverted = inverted
        self.direction_change_deadtime_s = direction_change_deadtime_s
        self._last_direction = 0
        self._lock = threading.RLock()

        device_cls = PWMOutputDevice or _DummyPWMOutputDevice
        if PWMOutputDevice is None:
            logger.warning(
                "gpiozero is not available; %s is running in dry-run mode.",
                self.name,
            )

        self.fwd = device_cls(forward_pin, frequency=freq)
        self.bwd = device_cls(backward_pin, frequency=freq)

    @staticmethod
    def _direction(speed: float) -> int:
        if speed > 0.001:
            return 1
        if speed < -0.001:
            return -1
        return 0

    def set_speed(self, speed: float) -> None:
        """Apply a normalized motor command in the range [-1.0, 1.0]."""
        with self._lock:
            speed = max(-1.0, min(1.0, float(speed)))
            if self.inverted:
                speed = -speed

            direction = self._direction(speed)

            if direction == 0:
                self.stop()
                return

            if self._last_direction not in (0, direction):
                self.fwd.value = 0.0
                self.bwd.value = 0.0
                time.sleep(self.direction_change_deadtime_s)

            if direction > 0:
                self.bwd.value = 0.0
                self.fwd.value = abs(speed)
            else:
                self.fwd.value = 0.0
                self.bwd.value = abs(speed)

            self._last_direction = direction

    def set_frequency(self, freq: int) -> None:
        """Update PWM frequency for both half-bridge inputs."""
        with self._lock:
            self.stop()
            self.fwd.frequency = int(freq)
            self.bwd.frequency = int(freq)

    def stop(self) -> None:
        with self._lock:
            self.fwd.value = 0.0
            self.bwd.value = 0.0
            self._last_direction = 0

    def close(self) -> None:
        with self._lock:
            self.stop()
            self.fwd.close()
            self.bwd.close()


# =====================================================================
# High-level differential-drive controller
# =====================================================================

@dataclass
class DriveSettings:
    max_motor_power: float = 0.70
    deadzone: float = 0.10
    steering_gain: float = 1.00
    acceleration_rate: float = 1.00
    braking_rate: float = 1.80
    throttle_expo: float = 2.00
    steering_expo: float = 1.35
    pwm_frequency: int = 500

    # Steering model parameters
    quick_turn_throttle: float = 0.08
    pivot_turn_threshold: float = 0.68
    counter_rotation_max: float = 0.60
    inner_wheel_min_ratio: float = 0.18

    # Set this to 0.05-0.12 only if the motors stall/hum at very low PWM.
    min_pwm_output: float = 0.00


class DifferentialDriveController:
    """
    Smooth differential-drive controller optimized for heavy geared DC motors.

    Steering behavior:
    - With throttle near zero, steering is a two-stage pivot turn.
      Example for right steering: left wheel ramps forward first while the right
      wheel stays stopped; only after the steering stick passes the pivot
      threshold does the right wheel gradually reverse.
    - While driving forward/backward, steering uses curvature mixing. The inner
      wheel is slowed but not reversed, which reduces scrubbing, gearbox shock,
      and motor current spikes during turns.
    - Final motor commands are slew-rate limited per wheel before they reach the
      BTN7960 drivers.
    """

    VALID_PWM_FREQUENCIES = (50, 100, 200, 400, 500, 800, 1000, 2000, 4000)

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.settings = DriveSettings()

        # Change these flags if a motor spins backward for a positive command.
        self.left_motor_inverted = False
        self.right_motor_inverted = False

        self.left_motor = CustomMotor(
            22,
            23,
            freq=self.settings.pwm_frequency,
            inverted=self.left_motor_inverted,
            name="left motor",
        )
        self.right_motor = CustomMotor(
            17,
            18,
            freq=self.settings.pwm_frequency,
            inverted=self.right_motor_inverted,
            name="right motor",
        )

        self.target_throttle = 0.0
        self.target_steering = 0.0

        self._left_command = 0.0
        self._right_command = 0.0

        self.current_left_speed = 0.0
        self.current_right_speed = 0.0
        self.current_drive_mode = "idle"

        self.running = True
        self._motor_thread = threading.Thread(target=self._motor_loop, daemon=True)
        self._motor_thread.start()

        logger.info("Motor controller initialized at %dHz PWM.", self.settings.pwm_frequency)

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def update_settings(self, settings: dict) -> dict:
        """Validate and apply runtime settings from the web UI."""
        with self._lock:
            current = self.settings

            def f(name: str, low: float, high: float) -> float:
                fallback = getattr(current, name)
                try:
                    value = float(settings.get(name, fallback))
                except (TypeError, ValueError):
                    value = fallback
                if not math.isfinite(value):
                    value = fallback
                return max(low, min(high, value))

            try:
                requested_pwm = int(float(settings.get("pwm_frequency", current.pwm_frequency)))
            except (TypeError, ValueError):
                requested_pwm = current.pwm_frequency
            pwm_frequency = min(
                self.VALID_PWM_FREQUENCIES,
                key=lambda value: abs(value - requested_pwm),
            )

            self.settings = DriveSettings(
                max_motor_power=f("max_motor_power", 0.05, 1.00),
                deadzone=f("deadzone", 0.00, 0.35),
                steering_gain=f("steering_gain", 0.20, 2.00),
                acceleration_rate=f("acceleration_rate", 0.10, 4.00),
                braking_rate=f("braking_rate", 0.20, 5.00),
                throttle_expo=f("throttle_expo", 1.00, 5.00),
                steering_expo=f("steering_expo", 1.00, 3.00),
                pwm_frequency=pwm_frequency,
                quick_turn_throttle=f("quick_turn_throttle", 0.02, 0.30),
                pivot_turn_threshold=f("pivot_turn_threshold", 0.35, 0.90),
                counter_rotation_max=f("counter_rotation_max", 0.00, 1.00),
                inner_wheel_min_ratio=f("inner_wheel_min_ratio", 0.00, 0.50),
                min_pwm_output=f("min_pwm_output", 0.00, 0.20),
            )

            if self.settings.pwm_frequency != current.pwm_frequency:
                self.left_motor.set_frequency(self.settings.pwm_frequency)
                self.right_motor.set_frequency(self.settings.pwm_frequency)

            logger.info(
                "Settings updated: max=%.2f deadzone=%.2f steer_gain=%.2f "
                "accel=%.2f brake=%.2f throttle_expo=%.2f steering_expo=%.2f "
                "quick_turn=%.2f pivot=%.2f counter=%.2f inner_min=%.2f min_pwm=%.2f pwm=%dHz",
                self.settings.max_motor_power,
                self.settings.deadzone,
                self.settings.steering_gain,
                self.settings.acceleration_rate,
                self.settings.braking_rate,
                self.settings.throttle_expo,
                self.settings.steering_expo,
                self.settings.quick_turn_throttle,
                self.settings.pivot_turn_threshold,
                self.settings.counter_rotation_max,
                self.settings.inner_wheel_min_ratio,
                self.settings.min_pwm_output,
                self.settings.pwm_frequency,
            )

            return self.get_settings()

    def get_settings(self) -> dict:
        with self._lock:
            return asdict(self.settings)

    def get_state(self) -> dict:
        with self._lock:
            return {
                "left_motor": self.current_left_speed,
                "right_motor": self.current_right_speed,
                "drive_mode": self.current_drive_mode,
                "target_throttle": self.target_throttle,
                "target_steering": self.target_steering,
            }

    def drive(self, throttle: float, steering: float) -> None:
        """Receive raw normalized joystick values from the web UI."""
        with self._lock:
            self.target_throttle = self._safe_normalized_input(throttle)
            self.target_steering = self._safe_normalized_input(steering)

    def stop_all(self) -> None:
        """Request a controlled ramp-down to zero."""
        with self._lock:
            self.target_throttle = 0.0
            self.target_steering = 0.0
            self.current_drive_mode = "soft_stop"

    def emergency_stop(self) -> None:
        """Immediately cut PWM outputs and reset all command states."""
        with self._lock:
            self.target_throttle = 0.0
            self.target_steering = 0.0
            self._left_command = 0.0
            self._right_command = 0.0
            self.current_left_speed = 0.0
            self.current_right_speed = 0.0
            self.current_drive_mode = "emergency_stop"

        self.left_motor.stop()
        self.right_motor.stop()

    def shutdown(self) -> None:
        self.running = False
        if self._motor_thread.is_alive():
            self._motor_thread.join(timeout=1.0)

        self.emergency_stop()
        self.left_motor.close()
        self.right_motor.close()
        logger.info("Motor controller shutdown complete.")

    # -----------------------------------------------------------------
    # Control math
    # -----------------------------------------------------------------

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _sign(value: float) -> float:
        return 1.0 if value >= 0.0 else -1.0

    @classmethod
    def _safe_normalized_input(cls, value: float) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(numeric):
            return 0.0
        return cls._clamp(numeric, -1.0, 1.0)

    @classmethod
    def _smoothstep(cls, value: float) -> float:
        x = cls._clamp(value, 0.0, 1.0)
        return x * x * (3.0 - 2.0 * x)

    @classmethod
    def _apply_deadzone(cls, value: float, deadzone: float) -> float:
        magnitude = abs(value)
        if magnitude <= deadzone:
            return 0.0
        return cls._sign(value) * ((magnitude - deadzone) / max(1e-6, 1.0 - deadzone))

    @classmethod
    def _expo(cls, value: float, expo: float) -> float:
        if abs(value) < 1e-6:
            return 0.0
        return cls._sign(value) * (abs(value) ** expo)

    @staticmethod
    def _blend(a: float, b: float, weight_b: float) -> float:
        return (a * (1.0 - weight_b)) + (b * weight_b)

    def _mix_stationary_turn(self, steering: float, settings: DriveSettings) -> Tuple[float, float]:
        """Two-stage turn-in-place mixer with inside wheel held still first."""
        amount = abs(steering)
        if amount < 1e-6:
            return 0.0, 0.0

        pivot_threshold = self._clamp(settings.pivot_turn_threshold, 0.35, 0.90)

        if amount <= pivot_threshold:
            outer = self._smoothstep(amount / pivot_threshold)
            inner = 0.0
        else:
            outer = 1.0
            reverse_zone = (amount - pivot_threshold) / max(1e-6, 1.0 - pivot_threshold)
            inner = -settings.counter_rotation_max * self._smoothstep(reverse_zone)

        if steering > 0.0:
            return outer, inner
        return inner, outer

    def _mix_curvature_drive(self, throttle: float, steering: float, settings: DriveSettings) -> Tuple[float, float]:
        """Curvature mixer that never reverses the inner wheel while translating."""
        throttle_mag = abs(throttle)
        if throttle_mag < 1e-6:
            return 0.0, 0.0

        turn = self._clamp(abs(steering), 0.0, 1.0)
        inner_ratio = 1.0 - (turn * (1.0 - settings.inner_wheel_min_ratio))
        inner_mag = throttle_mag * self._clamp(inner_ratio, 0.0, 1.0)
        outer_mag = throttle_mag

        if throttle > 0.0:
            if steering > 0.0:
                return outer_mag, inner_mag
            if steering < 0.0:
                return inner_mag, outer_mag
            return throttle_mag, throttle_mag

        if steering > 0.0:
            return -inner_mag, -outer_mag
        if steering < 0.0:
            return -outer_mag, -inner_mag
        return -throttle_mag, -throttle_mag

    def _calculate_targets(self) -> Tuple[float, float, str]:
        with self._lock:
            settings = self.settings
            raw_throttle = self.target_throttle
            raw_steering = self.target_steering

        throttle = self._apply_deadzone(raw_throttle, settings.deadzone)
        steering = self._apply_deadzone(raw_steering, settings.deadzone)

        throttle_cmd = self._expo(throttle, settings.throttle_expo)
        steering_cmd = self._expo(steering, settings.steering_expo)
        steering_cmd = self._clamp(steering_cmd * settings.steering_gain, -1.0, 1.0)

        moving_left, moving_right = self._mix_curvature_drive(throttle_cmd, steering_cmd, settings)
        pivot_left, pivot_right = self._mix_stationary_turn(steering_cmd, settings)

        if abs(steering_cmd) > 1e-4:
            # Use the post-deadzone raw throttle for mode selection. This keeps
            # any intentional forward/backward command in curvature-drive mode,
            # so the inner wheel does not reverse while the robot is translating.
            moving_weight = self._smoothstep(abs(throttle) / settings.quick_turn_throttle)
        else:
            moving_weight = 1.0

        left = self._blend(pivot_left, moving_left, moving_weight)
        right = self._blend(pivot_right, moving_right, moving_weight)

        left *= settings.max_motor_power
        right *= settings.max_motor_power

        left = self._clamp(left, -settings.max_motor_power, settings.max_motor_power)
        right = self._clamp(right, -settings.max_motor_power, settings.max_motor_power)

        if abs(throttle) < 1e-4 and abs(steering_cmd) < 1e-4:
            mode = "idle"
        elif moving_weight < 0.15:
            mode = "pivot_turn"
        elif moving_weight > 0.85:
            mode = "curvature_drive"
        else:
            mode = "blended_turn"

        return left, right, mode

    def _rate_for_motor(self, current: float, target: float, settings: DriveSettings) -> float:
        if current * target < 0.0:
            return settings.braking_rate
        if abs(target) < abs(current):
            return settings.braking_rate
        return settings.acceleration_rate

    def _slew(self, current: float, target: float, dt: float, settings: DriveSettings) -> float:
        if abs(current) < 1e-5 and abs(target) < 1e-5:
            return 0.0

        step_target = 0.0 if current * target < 0.0 else target
        max_step = self._rate_for_motor(current, step_target, settings) * dt

        if current < step_target:
            return min(current + max_step, step_target)
        if current > step_target:
            return max(current - max_step, step_target)
        return step_target

    @classmethod
    def _apply_min_pwm(cls, value: float, min_pwm_output: float) -> float:
        if abs(value) < 1e-4:
            return 0.0
        min_pwm_output = cls._clamp(min_pwm_output, 0.0, 0.20)
        if min_pwm_output <= 0.0 or abs(value) >= min_pwm_output:
            return value
        return cls._sign(value) * min_pwm_output

    # -----------------------------------------------------------------
    # Background control loop
    # -----------------------------------------------------------------

    def _motor_loop(self) -> None:
        last_time = time.monotonic()
        sleep_time = 0.02  # 50Hz control loop

        while self.running:
            now = time.monotonic()
            dt = max(0.001, min(0.05, now - last_time))
            last_time = now

            target_left, target_right, mode = self._calculate_targets()

            with self._lock:
                settings = self.settings
                self._left_command = self._slew(self._left_command, target_left, dt, settings)
                self._right_command = self._slew(self._right_command, target_right, dt, settings)

                left_output = self._apply_min_pwm(self._left_command, settings.min_pwm_output)
                right_output = self._apply_min_pwm(self._right_command, settings.min_pwm_output)

                self.current_left_speed = left_output
                self.current_right_speed = right_output
                self.current_drive_mode = mode

            self.left_motor.set_speed(left_output)
            self.right_motor.set_speed(right_output)

            time.sleep(sleep_time)
