/*
 * Offline dashboard controller for the Raspberry Pi robot.
 * No CDN, no external libraries — the joystick, WebSocket handling and UI
 * rendering are all implemented here in vanilla JS.
 */
(function () {
    "use strict";

    // ================================================================
    // Small helpers
    // ================================================================

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function safeFinite(value, fallback) {
        const n = Number(value);
        return Number.isFinite(n) ? n : (fallback !== undefined ? fallback : 0);
    }

    function escapeHtml(value) {
        return String(value).replace(/[&<>'"]/g, (char) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
        }[char]));
    }

    // ================================================================
    // DOM cache
    // ================================================================

    const DOM = {
        cpuVal: document.getElementById("tel-cpu-val"), cpuBar: document.getElementById("tel-cpu-bar"),
        ramVal: document.getElementById("tel-ram-val"), ramBar: document.getElementById("tel-ram-bar"),
        tempVal: document.getElementById("tel-temp-val"), tempBar: document.getElementById("tel-temp-bar"),
        wifiVal: document.getElementById("tel-wifi-val"), wifiBar: document.getElementById("tel-wifi-bar"),
        powerVal: document.getElementById("tel-power-val"), powerBar: document.getElementById("tel-power-bar"),
        gaugeFill: document.getElementById("gauge-fill"), gaugeText: document.getElementById("gauge-text"),
        gaugeContainer: document.getElementById("gauge-container"),
        gaugeDirectionLabel: document.getElementById("gauge-direction-label"),
        reverseLed: document.getElementById("reverse-led"),
        motorPowerDisplay: document.getElementById("motor-power-display"),
        driveMode: document.getElementById("drive-mode"),
        leftMotorBar: document.getElementById("left-motor-bar"), rightMotorBar: document.getElementById("right-motor-bar"),
        leftMotorVal: document.getElementById("left-motor-val"), rightMotorVal: document.getElementById("right-motor-val"),
        logContainer: document.getElementById("log-container"), statusBadge: document.getElementById("status-badge"),
        settingsModal: document.getElementById("settings-modal"),
        settingsPanel: document.getElementById("settings-panel"),
        settingsContent: document.getElementById("settings-content"),
        throttleZone: document.getElementById("throttle-zone"),
        steeringZone: document.getElementById("steering-zone"),
    };

    // ================================================================
    // Static configuration (mirrors core_motor.DriveSettings bounds)
    // ================================================================

    const DATA = {
        storageKey: "robot_settings",
        defaultSettings: {
            max_motor_power: 0.70,
            deadzone: 0.10,
            steering_gain: 1.00,
            acceleration_rate: 1.00,
            braking_rate: 1.80,
            throttle_expo: 2.00,
            steering_expo: 1.35,
            pwm_frequency: 500,
            quick_turn_throttle: 0.08,
            pivot_turn_threshold: 0.68,
            counter_rotation_max: 0.60,
            inner_wheel_min_ratio: 0.18,
            min_pwm_output: 0.00,
        },
        presets: {
            soft: { max_motor_power: 0.55, acceleration_rate: 0.70, braking_rate: 1.40, throttle_expo: 2.40, steering_expo: 1.60, steering_gain: 0.85, inner_wheel_min_ratio: 0.25, counter_rotation_max: 0.45 },
            balanced: { max_motor_power: 0.70, acceleration_rate: 1.00, braking_rate: 1.80, throttle_expo: 2.00, steering_expo: 1.35, steering_gain: 1.00, inner_wheel_min_ratio: 0.18, counter_rotation_max: 0.60 },
            sport: { max_motor_power: 0.85, acceleration_rate: 1.45, braking_rate: 2.30, throttle_expo: 1.65, steering_expo: 1.20, steering_gain: 1.15, inner_wheel_min_ratio: 0.12, counter_rotation_max: 0.70 },
        },
        settingGroups: [
            {
                title: "توان و PWM",
                items: [
                    { title: "حداکثر سرعت", key: "max_motor_power", min: 0.05, max: 1, step: 0.05, format: "percent", help: "سقف توان PWM هر دو موتور. برای تست اولیه یا کاهش فشار روی گیربکس مقدار ۵۰ تا ۷۰ درصد امن‌تر است." },
                    { title: "فرکانس PWM", key: "pwm_frequency", min: 50, max: 4000, step: 50, format: "hz", help: "فرکانس سیگنال PWM درایور BTN7960. با اپتوکوپلر TLP281 معمولاً ۵۰۰Hz پایدار و قابل کنترل است؛ بک‌اند مقدار را به نزدیک‌ترین فرکانس مجاز گرد می‌کند." },
                ],
            },
            {
                title: "نرمی حرکت",
                items: [
                    { title: "نرخ شتاب‌گیری", key: "acceleration_rate", min: 0.1, max: 4, step: 0.1, help: "سرعت افزایش تدریجی فرمان موتور. مقدار کمتر شروع حرکت را نرم‌تر می‌کند و شوک جریان و ضربه به دنده‌ها را کم می‌کند." },
                    { title: "نرخ ترمزگیری", key: "braking_rate", min: 0.2, max: 5, step: 0.1, help: "سرعت کم شدن فرمان موتور هنگام رها کردن گاز یا تغییر جهت. مقدار بالاتر توقف را تیزتر می‌کند، اما روی گیربکس سخت‌تر است." },
                    { title: "حداقل PWM شروع حرکت", key: "min_pwm_output", min: 0, max: 0.2, step: 0.01, format: "percent", help: "فقط وقتی موتور در توان خیلی کم صدا می‌دهد ولی نمی‌چرخد، این مقدار را کمی بالا ببرید. مقدار زیاد باعث پرش ناگهانی در شروع حرکت می‌شود." },
                ],
            },
            {
                title: "جوی‌استیک و حساسیت",
                items: [
                    { title: "منحنی حساسیت گاز", key: "throttle_expo", min: 1, max: 5, step: 0.1, help: "عدد بزرگ‌تر، گاز کم را نرم‌تر و دقیق‌تر می‌کند. برای کنترل آرام در فضای کم، ۲ تا ۲.۵ مناسب است." },
                    { title: "حساسیت فرمان", key: "steering_gain", min: 0.2, max: 2, step: 0.05, help: "ضریب تقویت فرمان. اگر ماشین با فرمان کم بیش از حد تند می‌پیچد، مقدار را کمتر کنید." },
                    { title: "منحنی حساسیت فرمان", key: "steering_expo", min: 1, max: 3, step: 0.1, help: "عدد بزرگ‌تر، فرمان‌های کوچک را نرم‌تر می‌کند و در انتهای جوی‌استیک همچنان امکان چرخش کامل می‌دهد." },
                    { title: "نقطه مرده جوی‌استیک", key: "deadzone", min: 0, max: 0.35, step: 0.01, help: "حرکت‌های خیلی کوچک اطراف مرکز جوی‌استیک نادیده گرفته می‌شود تا لرزش دست یا خطای لمس باعث حرکت ناخواسته نشود." },
                ],
            },
            {
                title: "هندسه گردش",
                items: [
                    { title: "مرز گردش درجا", key: "quick_turn_throttle", min: 0.02, max: 0.30, step: 0.01, help: "اگر مقدار گاز از این حد کمتر باشد، فرمان وارد منطق گردش درجا می‌شود. مقدار بیشتر، ورود به چرخش درجا را آسان‌تر می‌کند." },
                    { title: "آستانه شروع معکوس درجا", key: "pivot_turn_threshold", min: 0.35, max: 0.90, step: 0.01, help: "تا قبل از این آستانه، چرخ داخلی در گردش درجا ثابت می‌ماند. بعد از آن، چرخ داخلی آرام در جهت معکوس وارد مدار می‌شود." },
                    { title: "قدرت معکوس چرخ داخلی", key: "counter_rotation_max", min: 0, max: 1, step: 0.05, help: "حداکثر توان معکوس چرخ داخلی در چرخش درجا. برای کاهش فشار مکانیکی، معمولاً زیر ۷۰ درصد نگه دارید." },
                    { title: "حداقل نسبت چرخ داخلی در حرکت", key: "inner_wheel_min_ratio", min: 0, max: 0.5, step: 0.01, help: "در پیچ هنگام حرکت جلو یا عقب، چرخ داخلی کمتر از این نسبت کند نمی‌شود و معکوس نمی‌چرخد؛ این گزینه از گیر کردن و فشار به گیربکس کم می‌کند." },
                ],
            },
        ],
    };

    function findSettingItem(key) {
        for (const group of DATA.settingGroups) {
            const found = group.items.find((item) => item.key === key);
            if (found) return found;
        }
        return null;
    }

    // ================================================================
    // Logger
    // ================================================================

    const Logger = {
        append(level, message) {
            const entry = document.createElement("div");
            let cls = "log-entry";
            if (level === "ERROR") cls += " level-error";
            else if (level === "WARNING") cls += " level-warning";
            entry.className = cls;
            entry.textContent = `[${level}] ${message}`;
            DOM.logContainer.appendChild(entry);
            while (DOM.logContainer.children.length > 120) {
                DOM.logContainer.removeChild(DOM.logContainer.firstChild);
            }
            DOM.logContainer.scrollTop = DOM.logContainer.scrollHeight;
        },
        clear() {
            DOM.logContainer.innerHTML = "";
        },
    };

    // ================================================================
    // Settings manager
    // ================================================================

    const SettingsManager = (function () {
        let settings = loadFromStorage();

        function loadFromStorage() {
            try {
                const raw = localStorage.getItem(DATA.storageKey);
                const saved = raw ? JSON.parse(raw) : {};
                return { ...DATA.defaultSettings, ...sanitize(saved) };
            } catch (_) {
                return { ...DATA.defaultSettings };
            }
        }

        function sanitize(candidate) {
            const clean = {};
            for (const group of DATA.settingGroups) {
                for (const item of group.items) {
                    if (candidate[item.key] === undefined) continue;
                    const n = Number(candidate[item.key]);
                    if (Number.isFinite(n)) clean[item.key] = clamp(n, item.min, item.max);
                }
            }
            return clean;
        }

        function saveToStorage() {
            try {
                localStorage.setItem(DATA.storageKey, JSON.stringify(settings));
            } catch (_) {
                // Storage may be unavailable (private mode, quota) — safe to ignore.
            }
        }

        function get() {
            return settings;
        }

        function getValue(key) {
            return settings[key];
        }

        function setValue(key, rawValue) {
            const item = findSettingItem(key);
            const n = Number(rawValue);
            if (!item || !Number.isFinite(n)) return;
            settings[key] = clamp(n, item.min, item.max);
        }

        function applyFromServer(serverSettings) {
            settings = { ...DATA.defaultSettings, ...sanitize(serverSettings || {}) };
            saveToStorage();
        }

        function applyPreset(name) {
            const preset = DATA.presets[name];
            if (!preset) return;
            settings = { ...settings, ...sanitize(preset) };
            saveToStorage();
        }

        function resetToDefaults() {
            settings = { ...DATA.defaultSettings };
            saveToStorage();
        }

        return { get, getValue, setValue, applyFromServer, applyPreset, resetToDefaults, saveToStorage };
    })();

    function displayValue(item, value) {
        if (item.format === "percent") return `${Math.round(Number(value) * 100)}%`;
        if (item.format === "hz") return `${Math.round(Number(value))}Hz`;
        return Number(value).toFixed(2);
    }

    function renderSlider(item) {
        const current = SettingsManager.getValue(item.key) ?? item.min;
        const safeHelp = escapeHtml(item.help);
        const tooltipId = `tip_${item.key}`;
        return `
        <div class="setting-row">
            <div class="setting-row-top">
                <div class="setting-row-title">
                    <span class="name">${escapeHtml(item.title)}</span>
                    <span class="help-wrap">
                        <button type="button" class="help-button" aria-describedby="${tooltipId}" aria-expanded="false" title="${safeHelp}">؟</button>
                        <span id="${tooltipId}" class="help-tooltip" role="tooltip">${safeHelp}</span>
                    </span>
                </div>
                <span id="val_${item.key}" class="setting-value">${displayValue(item, current)}</span>
            </div>
            <input id="${item.key}" type="range" min="${item.min}" max="${item.max}" step="${item.step}" value="${current}" aria-label="${escapeHtml(item.title)}">
        </div>`;
    }

    function renderSettingsPanel() {
        DOM.settingsContent.innerHTML = DATA.settingGroups.map((group) => `
            <section class="settings-group">
                <h3 class="settings-group-title">${escapeHtml(group.title)}</h3>
                ${group.items.map(renderSlider).join("")}
            </section>
        `).join("");

        DATA.settingGroups.flatMap((group) => group.items).forEach((item) => {
            const el = document.getElementById(item.key);
            if (!el) return;
            el.addEventListener("input", () => {
                SettingsManager.setValue(item.key, el.value);
                updateSliderLabel(item);
                WsManager.throttledSendSettings();
            });
            el.addEventListener("change", () => {
                SettingsManager.saveToStorage();
                WsManager.sendSettingsNow();
            });
        });

        initializeTooltips();
    }

    function initializeTooltips() {
        DOM.settingsContent.querySelectorAll(".help-button").forEach((button) => {
            button.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                const wrapper = button.closest(".help-wrap");
                const shouldOpen = !wrapper.classList.contains("is-open");
                closeAllTooltips();
                wrapper.classList.toggle("is-open", shouldOpen);
                button.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
            });
        });
    }

    function closeAllTooltips() {
        DOM.settingsContent.querySelectorAll(".help-wrap.is-open").forEach((wrapper) => {
            wrapper.classList.remove("is-open");
            const button = wrapper.querySelector(".help-button");
            if (button) button.setAttribute("aria-expanded", "false");
        });
    }

    function updateSliderLabel(item) {
        const label = document.getElementById(`val_${item.key}`);
        const value = SettingsManager.getValue(item.key);
        if (label) label.textContent = displayValue(item, value);
        if (item.key === "max_motor_power") DOM.motorPowerDisplay.textContent = displayValue(item, value);
    }

    function updateAllSliderLabels() {
        DATA.settingGroups.flatMap((group) => group.items).forEach((item) => {
            const el = document.getElementById(item.key);
            const value = SettingsManager.getValue(item.key);
            if (el) el.value = value;
            updateSliderLabel(item);
        });
    }

    // ================================================================
    // Joystick manager (vanilla, Pointer Events — no external library)
    // ================================================================

    function createJoystick(zone, axis, onChange, onEnd) {
        const nub = document.createElement("div");
        nub.className = "joystick-nub";
        zone.appendChild(nub);

        let active = false;
        let pointerId = null;

        function update(clientX, clientY) {
            const rect = zone.getBoundingClientRect();
            const maxDistance = Math.max(10, Math.min(rect.width, rect.height) / 2 - 34);
            let dx = clientX - (rect.left + rect.width / 2);
            let dy = clientY - (rect.top + rect.height / 2);

            if (axis === "y") dx = 0;
            if (axis === "x") dy = 0;

            const dist = Math.hypot(dx, dy);
            if (dist > maxDistance && dist > 0) {
                const scale = maxDistance / dist;
                dx *= scale;
                dy *= scale;
            }

            nub.style.transform = `translate(${dx}px, ${dy}px)`;

            let value = 0;
            if (axis === "y") value = -dy / maxDistance;
            else value = dx / maxDistance;

            onChange(clamp(safeFinite(value, 0), -1, 1));
        }

        function onPointerDown(event) {
            event.preventDefault();
            active = true;
            pointerId = event.pointerId;
            zone.classList.add("is-active");
            try { zone.setPointerCapture(pointerId); } catch (_) { /* not fatal */ }
            update(event.clientX, event.clientY);
        }

        function onPointerMove(event) {
            if (!active || event.pointerId !== pointerId) return;
            update(event.clientX, event.clientY);
        }

        function onPointerUp(event) {
            if (event.pointerId !== pointerId) return;
            reset();
            onEnd();
        }

        function reset() {
            if (active && pointerId !== null) {
                try { zone.releasePointerCapture(pointerId); } catch (_) { /* not fatal */ }
            }
            active = false;
            pointerId = null;
            zone.classList.remove("is-active");
            nub.style.transform = "translate(0px, 0px)";
        }

        zone.addEventListener("pointerdown", onPointerDown);
        zone.addEventListener("pointermove", onPointerMove);
        zone.addEventListener("pointerup", onPointerUp);
        zone.addEventListener("pointercancel", onPointerUp);

        return { reset };
    }

    // ================================================================
    // Drive state + throttled sending
    // ================================================================

    const DriveState = (function () {
        let throttleValue = 0;
        let steeringValue = 0;
        let sendTimeout = null;
        let lastPayload = "";

        function setThrottle(v) { throttleValue = clamp(safeFinite(v, 0), -1, 1); }
        function setSteering(v) { steeringValue = clamp(safeFinite(v, 0), -1, 1); }

        function transmit(force) {
            if (!WsManager.isConnected()) return;
            const payload = JSON.stringify({
                type: "drive",
                throttle: Number(throttleValue.toFixed(3)),
                steering: Number(steeringValue.toFixed(3)),
            });
            if (!force && payload === lastPayload) return;
            WsManager.send(payload);
            lastPayload = payload;
        }

        function send(force) {
            if (force) {
                clearTimeout(sendTimeout);
                sendTimeout = null;
                transmit(true);
                return;
            }
            if (sendTimeout) return;
            transmit(false);
            sendTimeout = setTimeout(() => { sendTimeout = null; }, 33);
        }

        function stopAndSend() {
            throttleValue = 0;
            steeringValue = 0;
            send(true);
        }

        return { setThrottle, setSteering, send, stopAndSend };
    })();

    // ================================================================
    // WebSocket manager (with reconnect backoff + heartbeat)
    // ================================================================

    const WsManager = (function () {
        let ws = null;
        let connected = false;
        let pingInterval = null;
        let reconnectTimer = null;
        let settingsSendTimeout = null;
        let reconnectDelay = 1000;
        const RECONNECT_MIN = 1000;
        const RECONNECT_MAX = 8000;

        function isConnected() {
            return connected && ws && ws.readyState === WebSocket.OPEN;
        }

        function send(payload) {
            if (isConnected()) ws.send(payload);
        }

        function sendSettingsNow() {
            if (isConnected()) ws.send(JSON.stringify({ type: "settings", settings: SettingsManager.get() }));
        }

        function throttledSendSettings() {
            if (!isConnected() || settingsSendTimeout) return;
            settingsSendTimeout = setTimeout(() => {
                sendSettingsNow();
                settingsSendTimeout = null;
            }, 150);
        }

        function setConnectionState(state) {
            // state: "connected" | "connecting" | "disconnected"
            connected = state === "connected";
            DOM.statusBadge.classList.remove("is-connected", "is-connecting", "is-disconnected");
            if (state === "connected") {
                DOM.statusBadge.textContent = "متصل";
                DOM.statusBadge.classList.add("is-connected");
            } else if (state === "connecting") {
                DOM.statusBadge.textContent = "در حال اتصال مجدد";
                DOM.statusBadge.classList.add("is-connecting");
            } else {
                DOM.statusBadge.textContent = "قطع ارتباط";
                DOM.statusBadge.classList.add("is-disconnected");
            }
        }

        function connect() {
            setConnectionState(reconnectTimer ? "connecting" : "connecting");
            const wsProtocol = location.protocol === "https:" ? "wss" : "ws";
            ws = new WebSocket(`${wsProtocol}://${location.host}/ws`);

            ws.onopen = () => {
                reconnectDelay = RECONNECT_MIN;
                setConnectionState("connected");
                sendSettingsNow();
                clearInterval(pingInterval);
                pingInterval = setInterval(() => {
                    if (isConnected()) ws.send(JSON.stringify({ type: "ping" }));
                }, 220);
                Logger.append("INFO", "اتصال به سرور برقرار شد.");
            };

            ws.onclose = () => {
                const wasConnected = connected;
                setConnectionState("disconnected");
                clearInterval(pingInterval);
                SafetyManager.onConnectionLost();
                if (wasConnected) Logger.append("WARNING", "ارتباط قطع شد؛ ربات متوقف شد.");
                clearTimeout(reconnectTimer);
                reconnectTimer = setTimeout(() => {
                    reconnectTimer = null;
                    connect();
                }, reconnectDelay);
                reconnectDelay = Math.min(RECONNECT_MAX, reconnectDelay * 1.6);
            };

            ws.onerror = () => setConnectionState("disconnected");

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === "telemetry") Telemetry.update(data);
                    else if (data.type === "log") Logger.append(data.level, data.message);
                    else if (data.type === "settings" && data.settings) {
                        SettingsManager.applyFromServer(data.settings);
                        updateAllSliderLabels();
                    }
                } catch (error) {
                    Logger.append("WARNING", `پیام نامعتبر از سرور: ${error.message}`);
                }
            };
        }

        return { connect, send, sendSettingsNow, throttledSendSettings, isConnected };
    })();

    // ================================================================
    // Telemetry renderer
    // ================================================================

    const Telemetry = {
        update(data) {
            const cpu = clamp(safeFinite(data.cpu, 0), 0, 100);
            const ram = clamp(safeFinite(data.ram, 0), 0, 100);
            const temp = clamp(safeFinite(data.temp, 0), 0, 100);
            const wifi = clamp(safeFinite(data.wifi, 0), 0, 100);
            const powerOk = data.power_ok !== false;

            DOM.cpuVal.textContent = `${Math.round(cpu)}%`;
            DOM.cpuBar.style.width = `${cpu}%`;
            DOM.ramVal.textContent = `${Math.round(ram)}%`;
            DOM.ramBar.style.width = `${ram}%`;
            DOM.tempVal.textContent = `${safeFinite(data.temp, 0).toFixed(1)}°C`;
            DOM.tempBar.style.width = `${temp}%`;
            DOM.wifiVal.textContent = `${Math.round(wifi)}%`;
            DOM.wifiBar.style.width = `${wifi}%`;
            DOM.powerVal.textContent = powerOk ? "OK" : "LOW";
            DOM.powerBar.classList.toggle("is-low", !powerOk);
            DOM.powerBar.style.width = powerOk ? "100%" : "35%";
            DOM.driveMode.textContent = data.drive_mode || "idle";

            if (data.motor_power !== undefined) {
                DOM.motorPowerDisplay.textContent = `${Math.round(safeFinite(data.motor_power, 0))}%`;
            }
            if (data.left_motor !== undefined && data.right_motor !== undefined) {
                this.updateGauge(data.left_motor, data.right_motor);
            }
        },

        updateGauge(leftMotor, rightMotor) {
            const left = safeFinite(leftMotor, 0);
            const right = safeFinite(rightMotor, 0);
            const motorDemand = clamp(Math.max(Math.abs(left), Math.abs(right)), 0, 100);
            const translation = (left + right) / 2;
            const tilt = (left - right) / 2;
            const dashOffset = 100 - motorDemand;

            DOM.gaugeFill.style.strokeDashoffset = String(dashOffset);
            DOM.gaugeFill.style.opacity = motorDemand > 0.4 ? "1" : "0";
            DOM.gaugeText.textContent = `${Math.round(motorDemand)}%`;
            DOM.gaugeContainer.style.transform = `rotate(${tilt * 0.22}deg)`;

            if (Math.abs(translation) < 1 && Math.abs(left - right) > 2) DOM.gaugeDirectionLabel.textContent = "چرخش";
            else if (translation > 1) DOM.gaugeDirectionLabel.textContent = "جلو";
            else if (translation < -1) DOM.gaugeDirectionLabel.textContent = "عقب";
            else DOM.gaugeDirectionLabel.textContent = "آماده";

            if (translation < -1) {
                DOM.reverseLed.style.backgroundColor = "var(--danger)";
                DOM.reverseLed.style.boxShadow = "0 0 8px var(--danger)";
            } else {
                DOM.reverseLed.style.backgroundColor = "";
                DOM.reverseLed.style.boxShadow = "none";
            }

            this.updateMotorBar(DOM.leftMotorBar, DOM.leftMotorVal, left);
            this.updateMotorBar(DOM.rightMotorBar, DOM.rightMotorVal, right);
        },

        updateMotorBar(bar, label, value) {
            const safeValue = safeFinite(value, 0);
            const magnitude = clamp(Math.abs(safeValue), 0, 100);
            bar.style.width = `${magnitude}%`;
            label.textContent = `${Math.round(safeValue)}%`;
        },
    };

    // ================================================================
    // Safety manager
    // ================================================================

    const SafetyManager = {
        onConnectionLost() {
            DriveState.setThrottle(0);
            DriveState.setSteering(0);
            if (joysticks.throttle) joysticks.throttle.reset();
            if (joysticks.steering) joysticks.steering.reset();
            Telemetry.updateGauge(0, 0);
        },
        onVisibilityOrFocusLost() {
            DriveState.stopAndSend();
            if (joysticks.throttle) joysticks.throttle.reset();
            if (joysticks.steering) joysticks.steering.reset();
        },
        emergencyStop() {
            DriveState.stopAndSend();
            if (joysticks.throttle) joysticks.throttle.reset();
            if (joysticks.steering) joysticks.steering.reset();
            WsManager.send(JSON.stringify({ type: "emergency_stop" }));
            Logger.append("WARNING", "توقف اضطراری توسط کاربر فعال شد.");
            if (navigator.vibrate) navigator.vibrate(80);
        },
    };

    const joysticks = { throttle: null, steering: null };

    // ================================================================
    // Theme + modal + wiring
    // ================================================================

    function setTheme(dark) {
        document.documentElement.classList.toggle("dark", dark);
        try { localStorage.theme = dark ? "dark" : "light"; } catch (_) { /* ignore */ }
        const btn = document.getElementById("theme-toggle");
        if (btn) btn.textContent = dark ? "☀️" : "🌙";
    }

    function openSettingsModal() {
        DOM.settingsModal.classList.add("is-open");
        requestAnimationFrame(() => DOM.settingsModal.classList.add("is-visible"));
    }

    function closeSettingsModal() {
        DOM.settingsModal.classList.remove("is-visible");
        setTimeout(() => DOM.settingsModal.classList.remove("is-open"), 260);
    }

    function initializeUI() {
        let prefersDark = true;
        try {
            prefersDark = localStorage.theme === "dark"
                || (!("theme" in localStorage) && window.matchMedia("(prefers-color-scheme: dark)").matches);
        } catch (_) { /* default to dark */ }
        setTheme(prefersDark);

        renderSettingsPanel();
        updateAllSliderLabels();
        Telemetry.updateGauge(0, 0);

        document.getElementById("theme-toggle").addEventListener("click", () => {
            setTheme(!document.documentElement.classList.contains("dark"));
        });
        document.getElementById("settings-btn").addEventListener("click", openSettingsModal);
        document.getElementById("close-settings").addEventListener("click", closeSettingsModal);
        document.getElementById("clear-logs").addEventListener("click", () => Logger.clear());
        document.getElementById("reset-settings").addEventListener("click", () => {
            SettingsManager.resetToDefaults();
            updateAllSliderLabels();
            WsManager.sendSettingsNow();
        });
        document.querySelectorAll(".preset-btn").forEach((button) => {
            button.addEventListener("click", () => {
                SettingsManager.applyPreset(button.dataset.preset);
                updateAllSliderLabels();
                WsManager.sendSettingsNow();
            });
        });
        DOM.settingsModal.addEventListener("click", (event) => {
            if (event.target === DOM.settingsModal) closeSettingsModal();
            else if (!event.target.closest(".help-wrap")) closeAllTooltips();
        });
        DOM.settingsPanel.addEventListener("scroll", closeAllTooltips, { passive: true });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                closeAllTooltips();
                closeSettingsModal();
            }
        });

        document.getElementById("emergency-stop").addEventListener("click", () => SafetyManager.emergencyStop());
        window.addEventListener("blur", () => SafetyManager.onVisibilityOrFocusLost());
        document.addEventListener("visibilitychange", () => {
            if (document.hidden) SafetyManager.onVisibilityOrFocusLost();
        });

        joysticks.throttle = createJoystick(DOM.throttleZone, "y", (value) => {
            DriveState.setThrottle(value);
            DriveState.send(false);
        }, () => DriveState.stopAndSend());

        joysticks.steering = createJoystick(DOM.steeringZone, "x", (value) => {
            DriveState.setSteering(value);
            DriveState.send(false);
        }, () => DriveState.stopAndSend());

        WsManager.connect();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initializeUI);
    } else {
        initializeUI();
    }
})();
