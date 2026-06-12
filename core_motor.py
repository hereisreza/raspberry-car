import logging
import threading
import time

from gpiozero import PWMOutputDevice

logger = logging.getLogger("MechatronicsCore")


# =================================================================
# Motor Wrapper
# =================================================================
class CustomMotor:
    """
    مدیریت پین‌های موتور با قابلیت تغییر زنده فرکانس و سازگار با سیستم‌عامل
    """
    def __init__(self, forward_pin, backward_pin, freq=500):
        # ساخت پین‌های PWM با فرکانس مشخص
        self.fwd = PWMOutputDevice(forward_pin, frequency=freq)
        self.bwd = PWMOutputDevice(backward_pin, frequency=freq)

    def set_speed(self, speed):
        """ speed بین -1.0 تا 1.0 """
        speed = max(-1.0, min(1.0, speed))
        
        if speed > 0.001:
            self.bwd.value = 0.0
            self.fwd.value = speed
        elif speed < -0.001:
            self.fwd.value = 0.0
            self.bwd.value = abs(speed)
        else:
            self.stop()

    def set_frequency(self, freq):
        """ تغییر زنده فرکانس موتور """
        self.fwd.frequency = freq
        self.bwd.frequency = freq

    def stop(self):
        self.fwd.value = 0.0
        self.bwd.value = 0.0

    def close(self):
        self.fwd.close()
        self.bwd.close()


class DifferentialDriveController:
    def __init__(self):
        try:
            self.pwm_frequency = 500
            
            self.left_motor = CustomMotor(22, 23, freq=self.pwm_frequency)
            self.right_motor = CustomMotor(17, 18, freq=self.pwm_frequency)

            # -----------------------------
            # Runtime Configurable Settings
            # -----------------------------
            self.deadzone = 0.10
            self.max_motor_power = 0.70
            self.steering_gain = 1.0
            self.acceleration_rate = 1.0
            self.braking_rate = 1.5
            self.throttle_expo = 2.0

            # -----------------------------
            # Target Inputs
            # -----------------------------
            self.target_throttle = 0.0
            self.target_steering = 0.0

            # -----------------------------
            # Throttle State (For Ramping)
            # -----------------------------
            # ما فقط گاز/ترمز را نرم میکنیم، فرمان آنی خواهد بود
            self.current_throttle = 0.0

            # -----------------------------
            # Actual Motor Speeds (Telemetry)
            # -----------------------------
            self.current_left_speed = 0.0
            self.current_right_speed = 0.0

            self.running = True

            self.ramp_thread = threading.Thread(
                target=self._motor_loop,
                daemon=True
            )
            self.ramp_thread.start()

            logger.info(f"Motor controller initialized at {self.pwm_frequency}Hz PWM.")

        except Exception as e:
            logger.error(f"Motor initialization failed: {e}")
            raise

    # -----------------------------------------------------
    # Runtime Settings Update
    # -----------------------------------------------------

    def update_settings(self, settings: dict):
        try:
            self.max_motor_power = max(0.0, min(1.0, float(settings.get("max_motor_power", self.max_motor_power))))
            self.deadzone = max(0.0, min(0.4, float(settings.get("deadzone", self.deadzone))))
            self.steering_gain = max(0.1, min(2.0, float(settings.get("steering_gain", self.steering_gain))))
            
            # تغییر محدودیت شتاب و ترمز به بازه 0.1 تا 2.5
            self.acceleration_rate = max(0.1, min(2.5, float(settings.get("acceleration_rate", self.acceleration_rate))))
            self.braking_rate = max(0.1, min(2.5, float(settings.get("braking_rate", self.braking_rate))))
            
            self.throttle_expo = max(1.0, min(5.0, float(settings.get("throttle_expo", self.throttle_expo))))

            # در سیستم Bookworm و lgpio فرکانس‌های PWM نرم‌افزاری محدود به پله‌های زیر هستند
            # فرکانس بالای 4000 طبق درخواست محدود شد
            requested_pwm = int(settings.get("pwm_frequency", self.pwm_frequency))
            valid_freqs = [50, 100, 200, 400, 500, 800, 1000, 2000, 4000]
            new_pwm = min(valid_freqs, key=lambda x: abs(x - requested_pwm))
            
            if new_pwm != self.pwm_frequency:
                self.pwm_frequency = new_pwm
                self.left_motor.set_frequency(self.pwm_frequency)
                self.right_motor.set_frequency(self.pwm_frequency)

            logger.info(
                f"Settings updated: "
                f"max_power={self.max_motor_power:.2f}, "
                f"deadzone={self.deadzone:.2f}, "
                f"steering_gain={self.steering_gain:.2f}, "
                f"accel={self.acceleration_rate:.2f}, "
                f"brake={self.braking_rate:.2f}, "
                f"expo={self.throttle_expo:.2f}, "
                f"pwm={self.pwm_frequency}Hz"
            )

        except Exception as e:
            logger.error(f"update_settings error: {e}")

    # -----------------------------------------------------
    # Main Drive Function
    # -----------------------------------------------------

    def drive(self, throttle: float, steering: float):
        if abs(throttle) < self.deadzone:
            throttle = 0.0

        if abs(steering) < self.deadzone:
            steering = 0.0

        self.target_throttle = max(-1.0, min(1.0, throttle))
        self.target_steering = max(-1.0, min(1.0, steering))

    # -----------------------------------------------------
    # Ramp Helper (Only for Throttle now)
    # -----------------------------------------------------

    def _get_rate(self, current, target):
        direction_changed = (current > 0 > target or current < 0 < target)
        if direction_changed:
            return self.braking_rate
        if abs(target) < abs(current):
            return self.braking_rate
        return self.acceleration_rate

    # -----------------------------------------------------
    # Background Motor Loop
    # -----------------------------------------------------

    def _motor_loop(self):
        last_time = time.time()

        while self.running:

            now = time.time()
            dt = now - last_time
            last_time = now

            # ---------------------
            # 1. Ramp Throttle Only
            # ---------------------
            # اینرسی فقط برای حرکت به جلو و عقب است تا از فشار جریان لحظه‌ای جلوگیری کند
            rate_t = self._get_rate(self.current_throttle, self.target_throttle)
            step_t = rate_t * dt

            if self.current_throttle < self.target_throttle:
                self.current_throttle = min(self.current_throttle + step_t, self.target_throttle)
            elif self.current_throttle > self.target_throttle:
                self.current_throttle = max(self.current_throttle - step_t, self.target_throttle)

            # ---------------------
            # 2. Apply Curves & Mixer
            # ---------------------
            # اعمال منحنی حساسیت (Expo) روی گازِ نرم شده
            throttle_shaped = (abs(self.current_throttle) ** self.throttle_expo) * (1 if self.current_throttle >= 0 else -1)
            
            # چرخش به صورت کاملاً آنی، زنده و تیز بدون فیلتر اینرسی میکس می‌شود
            steering_mix = self.target_steering * self.steering_gain

            left_speed = throttle_shaped + steering_mix
            right_speed = throttle_shaped - steering_mix

            # ---------------------
            # 3. Normalize & Apply Power
            # ---------------------
            max_mag = max(abs(left_speed), abs(right_speed), 1.0)

            left_speed /= max_mag
            right_speed /= max_mag

            left_speed *= self.max_motor_power
            right_speed *= self.max_motor_power

            # ثبت برای تله‌متری
            self.current_left_speed = left_speed
            self.current_right_speed = right_speed

            # اعمال روی سخت افزار
            self.left_motor.set_speed(self.current_left_speed)
            self.right_motor.set_speed(self.current_right_speed)

            time.sleep(0.02)

    # -----------------------------------------------------
    # Stop & Shutdown
    # -----------------------------------------------------

    def stop_all(self):
        self.target_throttle = 0.0
        self.target_steering = 0.0

    def emergency_stop(self):
        self.target_throttle = 0.0
        self.target_steering = 0.0
        self.current_throttle = 0.0
        
        self.current_left_speed = 0.0
        self.current_right_speed = 0.0

        self.left_motor.stop()
        self.right_motor.stop()

    def shutdown(self):
        self.running = False
        if hasattr(self, "ramp_thread") and self.ramp_thread.is_alive():
            self.ramp_thread.join(timeout=1.0)

        self.emergency_stop()
        self.left_motor.close()
        self.right_motor.close()

        logger.info("Motor controller shutdown complete.")
