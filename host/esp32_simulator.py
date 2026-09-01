import time
import threading
import json
from collections import deque

class ESP32Simulator:
    def __init__(self, on_serial_output_callback=None):
        self.on_serial_output_callback = on_serial_output_callback
        
        # State variables mirroring MachineConfig struct in main.cpp
        self.bin_full_threshold_cm = 15
        self.pet_nir_w_min = 200
        self.pet_nir_w_max = 5000
        self.entrance_gate_timeout = 30
        
        # Hardware Timings (ms)
        self.settle_time_ms = 500
        self.success_drop_tout_ms = 3000
        self.reject_drop_time_ms = 2000

        # PCA9685 Servo Positions (0-180 degrees)
        self.ent_open_angle = 90
        self.ent_close_angle = 0
        self.suc_open_angle = 90
        self.suc_close_angle = 0
        self.rej_open_angle = 90
        self.rej_close_angle = 0

        # Live Real-time Servo Actuation Angles
        self.entrance_servo_angle = 0  # PCA Channel 0
        self.success_servo_angle = 0   # PCA Channel 1
        self.reject_servo_angle = 0    # PCA Channel 2

        # Feedback components
        self.buzzer_state = False
        self.led_green = False
        self.led_red = False
        
        # Sensors
        self.bin_distance_cm = 60      # > 15 cm means bin OK
        self.is_bin_full = False
        
        self.top_ir_triggered = False
        self.bottom_ir_triggered = False
        self.prox_metal_detected = False
        self.prox_capacitive_plastic = True
        self.nir_spectrometer_val = 1450 # normal PET NIR reading
        
        self.current_session_bottles = 0
        self.entrance_gate_requested = False
        self.force_gate_close = False
        
        # 20x4 I2C LCD Display (HD44780 + PCF8574 @ 0x27)
        self.lcd_lines = [
            "=== ECO-Fi VENDO ===",
            "Ready for Deposit   ",
            "Rate: 1 Bottle = 10m",
            "Session Bottles: 0  "
        ]
        
        # OLED Display text
        self.oled_text = "Eco-Fi Vendo Ready"
        
        # Serial message history
        self.serial_logs = deque(maxlen=100)
        self.lock = threading.RLock()
        
        self.running = True
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()

    def set_lcd(self, line0=None, line1=None, line2=None, line3=None):
        with self.lock:
            if line0 is not None: self.lcd_lines[0] = str(line0).ljust(20)[:20]
            if line1 is not None: self.lcd_lines[1] = str(line1).ljust(20)[:20]
            if line2 is not None: self.lcd_lines[2] = str(line2).ljust(20)[:20]
            if line3 is not None: self.lcd_lines[3] = str(line3).ljust(20)[:20]

    def log_serial(self, direction, msg):
        timestamp = time.strftime("%H:%M:%S")
        entry = {"time": timestamp, "dir": direction, "msg": msg}
        with self.lock:
            self.serial_logs.append(entry)
        if direction == "TX" and self.on_serial_output_callback:
            try:
                self.on_serial_output_callback(msg)
            except Exception as e:
                print(f"[ESP32 Simulator] Error in serial callback: {e}")

    def send_uart(self, payload):
        msg = json.dumps(payload)
        self.log_serial("TX", msg)

    def receive_uart(self, raw_str):
        self.log_serial("RX", raw_str.strip())
        try:
            data = json.loads(raw_str)
            cmd = data.get("cmd")
            if cmd == "OPEN_GATE":
                timeout = data.get("timeout", self.entrance_gate_timeout)
                self.open_entrance_gate(timeout)
            elif cmd == "CLOSE_GATE":
                self.close_entrance_gate()
            elif cmd == "TRIGGER_CONFIG":
                self.set_lcd(
                    line0="=== ECO-Fi CONFIG ==",
                    line1="WIFI: ECO-Fi-Config ",
                    line2="IP: 192.168.4.1     ",
                    line3="Port: 80 / AP Active"
                )
            elif cmd == "SET_CONFIG":
                with self.lock:
                    if "bin_full_threshold_cm" in data: self.bin_full_threshold_cm = data["bin_full_threshold_cm"]
                    if "entrance_gate_timeout" in data: self.entrance_gate_timeout = data["entrance_gate_timeout"]
                    if "settle_time_ms" in data: self.settle_time_ms = data["settle_time_ms"]
                    if "success_drop_tout_ms" in data: self.success_drop_tout_ms = data["success_drop_tout_ms"]
                    if "reject_drop_time_ms" in data: self.reject_drop_time_ms = data["reject_drop_time_ms"]
                    if "pet_nir_w_min" in data: self.pet_nir_w_min = data["pet_nir_w_min"]
                    if "pet_nir_w_max" in data: self.pet_nir_w_max = data["pet_nir_w_max"]
                    if "ent_open_angle" in data: self.ent_open_angle = data["ent_open_angle"]
                    if "ent_close_angle" in data: self.ent_close_angle = data["ent_close_angle"]
                    if "suc_open_angle" in data: self.suc_open_angle = data["suc_open_angle"]
                    if "suc_close_angle" in data: self.suc_close_angle = data["suc_close_angle"]
                    if "rej_open_angle" in data: self.rej_open_angle = data["rej_open_angle"]
                    if "rej_close_angle" in data: self.rej_close_angle = data["rej_close_angle"]
                    
                    # Snap servos to new closed positions
                    self.entrance_servo_angle = self.ent_close_angle
                    self.success_servo_angle = self.suc_close_angle
                    self.reject_servo_angle = self.rej_close_angle
                
                self.send_uart({"event": "CONFIG_SAVED"})
        except Exception:
            if '"OPEN_GATE"' in raw_str:
                self.open_entrance_gate(self.entrance_gate_timeout)
            elif '"CLOSE_GATE"' in raw_str:
                self.close_entrance_gate()

    def open_entrance_gate(self, timeout=30):
        with self.lock:
            self.entrance_gate_timeout = timeout
            self.entrance_gate_requested = True

    def close_entrance_gate(self):
        with self.lock:
            self.force_gate_close = True
            self.entrance_servo_angle = self.ent_close_angle
            self.led_green = False

    def buzz(self, duration_sec=0.12, pulses=1):
        def _buzz():
            for _ in range(pulses):
                self.buzzer_state = True
                time.sleep(duration_sec)
                self.buzzer_state = False
                if pulses > 1:
                    time.sleep(0.08)
        threading.Thread(target=_buzz, daemon=True).start()

    def _run_loop(self):
        last_ultrasonic_check = time.time()
        last_bin_state = False

        while self.running:
            now = time.time()
            # Ultrasonic check every 1 sec
            if now - last_ultrasonic_check >= 1.0:
                currently_full = (0 < self.bin_distance_cm < self.bin_full_threshold_cm)
                if currently_full != last_bin_state:
                    self.is_bin_full = currently_full
                    last_bin_state = currently_full
                    if currently_full:
                        self.set_lcd(
                            line0="=== ECO-Fi VENDO ===",
                            line1="STATUS: STORAGE FULL",
                            line2="Empty Bin Required  ",
                            line3=f"Session Bottles: {self.current_session_bottles:<3}"
                        )
                        self.led_red = True
                        self.send_uart({"event": "BIN_FULL"})
                    else:
                        self.set_lcd(
                            line0="=== ECO-Fi VENDO ===",
                            line1="Ready for Deposit   ",
                            line2="Rate: 1 Bottle = 10m",
                            line3=f"Session Bottles: {self.current_session_bottles:<3}"
                        )
                        self.led_red = False
                        self.send_uart({"event": "BIN_OK"})
                last_ultrasonic_check = now

            if self.is_bin_full:
                time.sleep(0.1)
                continue

            if self.entrance_gate_requested:
                self.entrance_gate_requested = False
                self._handle_entrance_cycle()

            time.sleep(0.05)

    def _handle_entrance_cycle(self):
        # 1. Open Entrance Gate Servo (Ch 0)
        with self.lock:
            self.entrance_servo_angle = self.ent_open_angle
            self.top_ir_triggered = False
            self.force_gate_close = False
            self.led_green = False
            self.led_red = False
        
        self.set_lcd(
            line0="=== ECO-Fi VENDO ===",
            line1="GATE OPEN: INSERT...",
            line2=f"Drop within {self.entrance_gate_timeout}s   ",
            line3=f"Session Bottles: {self.current_session_bottles:<3}"
        )
        
        start_time = time.time()
        dropped = False

        while (time.time() - start_time) < self.entrance_gate_timeout:
            if self.top_ir_triggered or self.force_gate_close:
                if self.top_ir_triggered:
                    dropped = True
                break
            time.sleep(0.02)

        # Close Entrance Gate Servo (Ch 0)
        with self.lock:
            self.entrance_servo_angle = self.ent_close_angle
            self.force_gate_close = False

        if not dropped:
            # Drop timeout
            self._handle_reject("Drop / Sensor Error ", "REJECTED")
            return

        # Settle in airlock
        self.set_lcd(
            line0="=== ECO-Fi VENDO ===",
            line1="STATUS: SCANNING... ",
            line2="Analyzing Material  ",
            line3=f"Session Bottles: {self.current_session_bottles:<3}"
        )
        time.sleep(self.settle_time_ms / 1000.0)

        # 2. Validation Pipeline (Metal -> Capacitive -> AS7263 NIR Spectrometer)
        is_valid = True
        reject_display = "No Plastic Detected "

        if self.prox_metal_detected:
            is_valid = False
            reject_display = "Tin/Can Detected    "
        elif not self.prox_capacitive_plastic:
            is_valid = False
            reject_display = "No Plastic Detected "
        elif not (self.pet_nir_w_min <= self.nir_spectrometer_val <= self.pet_nir_w_max):
            is_valid = False
            reject_display = "Invalid Material NIR"

        # 3. Actuation
        if is_valid:
            # Success Sequence: Open Success Gate (Ch 1)
            self.led_green = True
            self.set_lcd(
                line0="=== ECO-Fi VENDO ===",
                line1="STATUS: VERIFIED OK ",
                line2="Dropping to bin...  ",
                line3=f"Session Bottles: {self.current_session_bottles:<3}"
            )
            with self.lock:
                self.bottom_ir_triggered = False
                self.success_servo_angle = self.suc_open_angle

            gate_open_time = time.time()
            passed_drop = False

            # Wait for Bottom IR drop verification
            while (time.time() - gate_open_time) < (self.success_drop_tout_ms / 1000.0):
                if self.bottom_ir_triggered:
                    passed_drop = True
                    break
                time.sleep(0.02)

            # Close Success Gate (Ch 1)
            with self.lock:
                self.success_servo_angle = self.suc_close_angle

            if passed_drop:
                # Bottle physically verified in bin
                self.current_session_bottles += 1
                self.buzz(duration_sec=0.12, pulses=2)
                
                self.send_uart({
                    "event": "CREDIT_ADD",
                    "bottles": 1,
                    "sessionTotal": self.current_session_bottles
                })
                
                self.set_lcd(
                    line0="=== ECO-Fi VENDO ===",
                    line1="STATUS: BOTTLE SAVED",
                    line2="Ready for Deposit   ",
                    line3=f"Session Bottles: {self.current_session_bottles:<3}"
                )
                time.sleep(1.2)
                self.led_green = False
                self.set_lcd(
                    line0="=== ECO-Fi VENDO ===",
                    line1="Ready for Deposit   ",
                    line2="Rate: 1 Bottle = 10m",
                    line3=f"Session Bottles: {self.current_session_bottles:<3}"
                )
            else:
                # Bottle stuck in chute
                self._handle_reject("Drop / Sensor Error ", "REJECTED")
        else:
            # Reject Sequence: Open Reject Gate (Ch 2)
            self._handle_reject(reject_display, "REJECTED")

    def _handle_reject(self, display_msg, uart_event):
        self.led_red = True
        self.led_green = False
        self.set_lcd(
            line0="=== ECO-Fi VENDO ===",
            line1="STATUS: REJECTED!   ",
            line2=display_msg,
            line3=f"Session Bottles: {self.current_session_bottles:<3}"
        )
        
        self.send_uart({"event": uart_event})
        self.buzz(duration_sec=0.6, pulses=1)
        
        # Open Reject Servo (Ch 2)
        with self.lock:
            self.reject_servo_angle = self.rej_open_angle
        time.sleep(self.reject_drop_time_ms / 1000.0)
        with self.lock:
            self.reject_servo_angle = self.rej_close_angle
            
        time.sleep(1.5)
        self.led_red = False
        self.set_lcd(
            line0="=== ECO-Fi VENDO ===",
            line1="Ready for Deposit   ",
            line2="Rate: 1 Bottle = 10m",
            line3=f"Session Bottles: {self.current_session_bottles:<3}"
        )

    # High-level simulation triggers for frontend/tests
    def simulate_insert(self, item_type="valid_pet"):
        if not self.entrance_gate_requested and self.entrance_servo_angle == self.ent_close_angle:
            self.open_entrance_gate(timeout=15)
            time.sleep(0.1)

        def _do_drop():
            time.sleep(0.3)
            if item_type == "timeout_cheat":
                return

            with self.lock:
                self.top_ir_triggered = True

            if item_type == "valid_pet":
                self.prox_metal_detected = False
                self.prox_capacitive_plastic = True
                self.nir_spectrometer_val = 1450
                def _trigger_bottom():
                    time.sleep(0.9)
                    with self.lock:
                        self.bottom_ir_triggered = True
                threading.Thread(target=_trigger_bottom, daemon=True).start()

            elif item_type == "metal_can":
                self.prox_metal_detected = True
                self.prox_capacitive_plastic = True
                self.nir_spectrometer_val = 1450

            elif item_type == "non_plastic":
                self.prox_metal_detected = False
                self.prox_capacitive_plastic = False
                self.nir_spectrometer_val = 1450

            elif item_type == "invalid_polymer":
                self.prox_metal_detected = False
                self.prox_capacitive_plastic = True
                self.nir_spectrometer_val = 45

            elif item_type == "stuck_bottle":
                self.prox_metal_detected = False
                self.prox_capacitive_plastic = True
                self.nir_spectrometer_val = 1450
                # bottom_ir_triggered is never set, triggering timeout

        threading.Thread(target=_do_drop, daemon=True).start()

    def set_bin_distance(self, distance_cm):
        with self.lock:
            self.bin_distance_cm = distance_cm

    def reset_session(self):
        with self.lock:
            self.current_session_bottles = 0
            self.set_lcd(line3="Session Bottles: 0  ")

    def get_state(self):
        with self.lock:
            return {
                "lcd_lines": list(self.lcd_lines),
                "oled_text": self.oled_text,
                "entrance_servo": self.entrance_servo_angle,
                "success_servo": self.success_servo_angle,
                "reject_servo": self.reject_servo_angle,
                "buzzer": self.buzzer_state,
                "led_green": self.led_green,
                "led_red": self.led_red,
                "bin_distance_cm": self.bin_distance_cm,
                "is_bin_full": self.is_bin_full,
                "top_ir": self.top_ir_triggered,
                "bottom_ir": self.bottom_ir_triggered,
                "prox_metal": self.prox_metal_detected,
                "prox_capacitive": self.prox_capacitive_plastic,
                "nir_val": self.nir_spectrometer_val,
                "session_bottles": self.current_session_bottles,
                "gate_requested": self.entrance_gate_requested,
                "serial_logs": list(self.serial_logs)
            }

    def render_simulator_html(self):
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>ECO-Fi ESP32 Hardware Simulator & 2004A 20x4 I2C LCD Display</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/admin-lte/3.2.0/css/adminlte.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.0/jquery.min.js"></script>
    <style>
        body { 
            background: #090d16; 
            color: #f8fafc; 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            min-height: 100vh;
        }

        /* Outer FR4 Industrial Green PCB */
        .lcd-module-wrapper {
            max-width: 680px;
            margin: 0 auto 16px;
        }

        .lcd-pcb {
            background-color: #0d6e38;
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(255,255,255,0.06) 0%, transparent 40%),
                linear-gradient(135deg, #13773e 0%, #0d6834 50%, #085227 100%);
            border: 2px solid #06401e;
            border-radius: 6px;
            padding: 14px 18px;
            box-shadow: 0 16px 40px rgba(0, 0, 0, 0.8), inset 0 1px 1px rgba(255,255,255,0.25);
            position: relative;
            user-select: none;
        }

        /* 4 Plated Mounting Holes in PCB Corners */
        .pcb-hole {
            position: absolute;
            width: 14px;
            height: 14px;
            border-radius: 50%;
            background: #050b07;
            border: 2px solid #b4cbb7;
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.9), 0 0 1px rgba(255,255,255,0.4);
        }
        .hole-tl { top: 8px; left: 8px; }
        .hole-tr { top: 8px; right: 8px; }
        .hole-bl { bottom: 8px; left: 8px; }
        .hole-br { bottom: 8px; right: 8px; }

        /* Top Gold 16-Pin Header Strip */
        .pin-strip-top {
            display: flex;
            justify-content: flex-start;
            align-items: center;
            margin-left: 28px;
            margin-bottom: 6px;
            gap: 5px;
        }
        .pin-strip-bottom {
            display: flex;
            justify-content: flex-start;
            align-items: center;
            margin-left: 28px;
            margin-top: 6px;
            gap: 5px;
        }
        .gold-pad {
            width: 9px;
            height: 14px;
            background: linear-gradient(180deg, #fef08a 0%, #ca8a04 100%);
            border: 1px solid #78350f;
            border-radius: 1.5px;
            box-shadow: inset 0 1px 1px rgba(255,255,255,0.6);
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .gold-pad::after {
            content: '';
            width: 3.5px;
            height: 3.5px;
            background: #111;
            border-radius: 50%;
        }
        .pin-num-label {
            font-family: 'Consolas', monospace;
            font-size: 10px;
            font-weight: 700;
            color: #dcfce7;
            margin: 0 4px;
            text-shadow: 0 1px 2px rgba(0,0,0,0.8);
        }

        /* Stamped Metal Bezel (Black / Steel Frame) */
        .lcd-metal-frame {
            background: linear-gradient(180deg, #222626 0%, #151818 40%, #0d0f0f 100%);
            border: 3px solid #323838;
            border-top-color: #454d4d;
            border-bottom-color: #1a1e1e;
            border-radius: 4px;
            padding: 12px 14px;
            box-shadow: 
                0 4px 15px rgba(0,0,0,0.9), 
                inset 0 1px 2px rgba(255,255,255,0.2), 
                inset 0 -1px 2px rgba(0,0,0,0.8);
            position: relative;
        }

        .bezel-tab-top, .bezel-tab-bottom {
            position: absolute;
            left: 20px;
            right: 20px;
            height: 2px;
            background: rgba(255, 255, 255, 0.08);
            border-bottom: 1px solid rgba(0,0,0,0.6);
        }
        .bezel-tab-top { top: 4px; }
        .bezel-tab-bottom { bottom: 4px; }

        /* Liquid Crystal Active Glass Screen */
        .lcd-glass-viewport {
            border: 3px solid #000;
            border-radius: 2px;
            background: #002266;
            box-shadow: inset 0 0 18px rgba(0,0,0,0.9);
            position: relative;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 8px 10px;
        }

        #lcd-matrix-canvas {
            width: 100%;
            height: auto;
            display: block;
            image-rendering: pixelated;
        }

        /* I2C Backpack Silkscreen Banner */
        .lcd-backpack-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 10px;
            padding: 0 4px;
            font-family: 'Consolas', monospace;
            font-size: 11px;
            color: #d1fae5;
            font-weight: 700;
            letter-spacing: 0.5px;
        }
        .i2c-pins {
            display: flex;
            gap: 6px;
        }
        .i2c-pin-badge {
            background: rgba(0, 0, 0, 0.4);
            border: 1px solid rgba(255,255,255,0.25);
            padding: 2px 6px;
            border-radius: 3px;
            color: #fef08a;
            font-size: 10.5px;
        }

        /* Actuator and status indicators */
        .actuator-indicator {
            padding: 8px 12px; border-radius: 8px; font-weight: 700; font-size: 12px;
            display: inline-block; margin: 4px; width: 100%; text-align: center;
        }
        .indicator-on { background: #15803d; color: #bbf7d0; border: 1px solid #22c55e; }
        .indicator-off { background: #1e293b; color: #64748b; border: 1px solid #334155; }
        .indicator-red { background: #991b1b; color: #fecaca; border: 1px solid #ef4444; }

        .serial-console {
            background: #020617; border: 1px solid #1e293b; border-radius: 8px;
            font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; height: 260px;
            overflow-y: auto; padding: 8px; color: #94a3b8;
        }

        .card {
            background: #111827 !important;
            border: 1px solid #1f2937 !important;
            border-radius: 12px !important;
            margin-bottom: 18px !important;
        }
        .card-header {
            border-bottom: 1px solid rgba(255,255,255,0.08) !important;
            padding: 10px 16px !important;
        }
    </style>
</head>
<body class="p-3 p-md-4">
<div class="container-fluid">
    <!-- Header Bar -->
    <div class="d-flex flex-wrap justify-content-between align-items-center mb-3 pb-2 border-bottom border-secondary">
        <div>
            <h3 class="font-weight-bold text-success mb-0"><i class="fas fa-microchip mr-2"></i> ECO-Fi ESP32 Hardware Simulator</h3>
            <p class="text-muted mb-0 small">Authentic 2004A 20x4 I2C Character LCD (HD44780), PCA9685 Chute Servos & Multi-Sensor Airlock</p>
        </div>
        <div class="mt-2 mt-md-0">
            <button class="btn btn-sm btn-outline-warning mr-2" onclick="resetSimSession()"><i class="fas fa-redo mr-1"></i> Reset Session</button>
            <a href="/" class="btn btn-sm btn-outline-success mr-2" target="_blank"><i class="fas fa-wifi mr-1"></i> Open Portal</a>
            <a href="/admin" class="btn btn-sm btn-outline-info" target="_blank"><i class="fas fa-user-shield mr-1"></i> Admin Panel</a>
        </div>
    </div>

    <div class="row">
        <!-- LEFT COLUMN: 20x4 LCD & PCA9685 SERVO ACTUATORS -->
        <div class="col-12 col-xl-6">
            <!-- 1. REALISTIC 2004A 20x4 I2C CHARACTER LCD MODULE -->
            <div class="card">
                <div class="card-header bg-dark d-flex justify-content-between align-items-center">
                    <h3 class="card-title font-weight-bold text-light" style="font-size:14px;">
                        <i class="fas fa-desktop text-info mr-1"></i> 2004A 20x4 Character LCD (HD44780 + PCF8574T @ 0x27)
                    </h3>
                    <div class="d-flex align-items-center">
                        <select id="lcd-theme-select" class="custom-select custom-select-sm" style="width:145px;" onchange="changeLcdTheme(this.value)">
                            <option value="blue">🟦 Blue LED (Default)</option>
                            <option value="yellow">🟨 Yellow-Green</option>
                            <option value="off">⬛ Backlight Off</option>
                        </select>
                    </div>
                </div>
                <div class="card-body p-3">
                    
                    <!-- PHYSICAL 2004A MODULE WRAPPER -->
                    <div class="lcd-module-wrapper">
                        <div class="lcd-pcb">
                            <!-- 4 Corner Mounting Holes -->
                            <div class="pcb-hole hole-tl"></div>
                            <div class="pcb-hole hole-tr"></div>
                            <div class="pcb-hole hole-bl"></div>
                            <div class="pcb-hole hole-br"></div>

                            <!-- Top Gold Pin Header Strip -->
                            <div class="pin-strip-top">
                                <span class="pin-num-label">1</span>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <span class="pin-num-label">16</span>
                            </div>

                            <!-- Black Stamped Steel Metal Frame -->
                            <div class="lcd-metal-frame">
                                <div class="bezel-tab-top"></div>
                                
                                <!-- Active Dot Matrix LCD Glass Screen -->
                                <div class="lcd-glass-viewport" id="lcd-viewport">
                                    <canvas id="lcd-matrix-canvas" width="672" height="210"></canvas>
                                </div>

                                <div class="bezel-tab-bottom"></div>
                            </div>

                            <!-- Bottom Gold Pin Header Strip -->
                            <div class="pin-strip-bottom">
                                <span class="pin-num-label">1</span>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <span class="pin-num-label">16</span>
                            </div>

                            <!-- I2C Bus Silkscreen Details -->
                            <div class="lcd-backpack-bar">
                                <span><i class="fas fa-microchip mr-1"></i> I2C ADDR: 0x27</span>
                                <div class="i2c-pins">
                                    <span class="i2c-pin-badge">GND</span>
                                    <span class="i2c-pin-badge">VCC (5V)</span>
                                    <span class="i2c-pin-badge">SDA: GPIO21</span>
                                    <span class="i2c-pin-badge">SCL: GPIO22</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Custom Message Test Panel -->
                    <div class="p-2 bg-dark rounded border border-secondary">
                        <div class="d-flex justify-content-between align-items-center mb-1">
                            <small class="font-weight-bold text-muted"><i class="fas fa-edit mr-1"></i> Live LCD Text Injection Tester:</small>
                            <div>
                                <button class="btn btn-xs btn-outline-info mr-1" onclick="injectLcdPreset('idle')">Standby</button>
                                <button class="btn btn-xs btn-outline-success mr-1" onclick="injectLcdPreset('gate')">Gate Open</button>
                                <button class="btn btn-xs btn-outline-warning mr-1" onclick="injectLcdPreset('reject')">Tin Reject</button>
                                <button class="btn btn-xs btn-outline-danger mr-1" onclick="injectLcdPreset('full')">Bin Full</button>
                                <button class="btn btn-xs btn-outline-secondary" onclick="injectLcdPreset('config')">Config</button>
                            </div>
                        </div>
                        <div class="row no-gutters">
                            <div class="col-6 pr-1"><input type="text" id="test-l0" class="form-control form-control-sm mb-1" placeholder="Row 0 (max 20 chars)" maxlength="20"></div>
                            <div class="col-6 pl-1"><input type="text" id="test-l1" class="form-control form-control-sm mb-1" placeholder="Row 1 (max 20 chars)" maxlength="20"></div>
                            <div class="col-6 pr-1"><input type="text" id="test-l2" class="form-control form-control-sm" placeholder="Row 2 (max 20 chars)" maxlength="20"></div>
                            <div class="col-6 pl-1"><input type="text" id="test-l3" class="form-control form-control-sm" placeholder="Row 3 (max 20 chars)" maxlength="20"></div>
                        </div>
                        <button class="btn btn-sm btn-info btn-block mt-2 font-weight-bold" onclick="sendCustomLcd()"><i class="fas fa-paper-plane mr-1"></i> Send Custom Text to 20x4 LCD</button>
                    </div>
                </div>
            </div>

            <!-- 2. PCA9685 CHUTE SERVOS & INDICATORS -->
            <div class="card">
                <div class="card-header bg-dark"><h3 class="card-title font-weight-bold text-light" style="font-size:14px;"><i class="fas fa-tachometer-alt text-success mr-1"></i> PCA9685 3-Servo Airlock & Status LEDs</h3></div>
                <div class="card-body p-3">
                    <div class="row text-center mb-2">
                        <div class="col-4">
                            <div class="small text-muted font-weight-bold">Entrance Gate (Ch 0)</div>
                            <span id="ind-gate" class="actuator-indicator indicator-off">CLOSED (0°)</span>
                        </div>
                        <div class="col-4">
                            <div class="small text-muted font-weight-bold">Success Gate (Ch 1)</div>
                            <span id="ind-success" class="actuator-indicator indicator-off">CLOSED (0°)</span>
                        </div>
                        <div class="col-4">
                            <div class="small text-muted font-weight-bold">Reject Gate (Ch 2)</div>
                            <span id="ind-reject" class="actuator-indicator indicator-off">CLOSED (0°)</span>
                        </div>
                    </div>

                    <div class="row text-center">
                        <div class="col-4">
                            <div class="small text-muted font-weight-bold">Active Buzzer (GPIO33)</div>
                            <span id="ind-buzzer" class="actuator-indicator indicator-off">SILENT</span>
                        </div>
                        <div class="col-4">
                            <div class="small text-muted font-weight-bold">🟢 Green LED (GPIO25)</div>
                            <span id="ind-led-green" class="actuator-indicator indicator-off">OFF</span>
                        </div>
                        <div class="col-4">
                            <div class="small text-muted font-weight-bold">🔴 Red LED (GPIO26)</div>
                            <span id="ind-led-red" class="actuator-indicator indicator-off">OFF</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- RIGHT COLUMN: DROP CONTROLS, SENSORS & SERIAL CONSOLE -->
        <div class="col-12 col-xl-6">
            <!-- 3. PHYSICAL DROP SIMULATION CONTROLS -->
            <div class="card">
                <div class="card-header bg-dark"><h3 class="card-title font-weight-bold text-light" style="font-size:14px;"><i class="fas fa-gamepad text-primary mr-1"></i> Simulate Physical Bottle Insertions</h3></div>
                <div class="card-body p-3">
                    <p class="text-muted small mb-2">Trigger authentic multi-sensor validation cycles against the ESP32 firmware logic:</p>
                    <div class="btn-group-vertical w-100">
                        <button class="btn btn-success mb-2 font-weight-bold text-left" onclick="triggerDrop('valid_pet')">
                            <i class="fas fa-wine-bottle mr-1"></i> Drop 1x Valid PET Plastic Bottle (NIR: 1450, Capacitive OK) <span class="badge badge-light float-right">ACCEPT</span>
                        </button>
                        <button class="btn btn-warning mb-2 font-weight-bold text-left" onclick="triggerDrop('metal_can')">
                            <i class="fas fa-drum mr-1"></i> Drop Aluminum / Tin Can (Inductive Metal Sensor LOW) <span class="badge badge-dark float-right">REJECT</span>
                        </button>
                        <button class="btn btn-warning mb-2 font-weight-bold text-left" onclick="triggerDrop('non_plastic')">
                            <i class="fas fa-box-open mr-1"></i> Drop Cardboard / Paper Item (Capacitive HIGH) <span class="badge badge-dark float-right">REJECT</span>
                        </button>
                        <button class="btn btn-danger mb-2 font-weight-bold text-left" onclick="triggerDrop('invalid_polymer')">
                            <i class="fas fa-vial mr-1"></i> Drop PVC / Bad Polymer (AS7263 NIR W: 45 Out of Range) <span class="badge badge-light float-right">REJECT</span>
                        </button>
                        <button class="btn btn-secondary mb-2 font-weight-bold text-left" onclick="triggerDrop('stuck_bottle')">
                            <i class="fas fa-exclamation-triangle mr-1"></i> Simulate Jammed Chute Bottle (Bottom IR Timeout) <span class="badge badge-danger float-right">ERROR</span>
                        </button>
                    </div>

                    <div class="d-flex justify-content-between align-items-center mt-2 pt-2 border-top border-secondary">
                        <span class="small font-weight-bold text-muted">Storage Bin JSN-SR04T Sensor:</span>
                        <div>
                            <button class="btn btn-xs btn-outline-danger mr-1" onclick="setBin(8)"><i class="fas fa-fill mr-1"></i> Set Bin FULL (8cm)</button>
                            <button class="btn btn-xs btn-outline-success" onclick="setBin(60)"><i class="fas fa-check mr-1"></i> Set Bin OK (60cm)</button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 4. REAL-TIME SENSOR BUS TELEMETRY -->
            <div class="card">
                <div class="card-header bg-dark"><h3 class="card-title font-weight-bold text-light" style="font-size:14px;"><i class="fas fa-wave-square text-info mr-1"></i> Real-time Sensor Bus Telemetry</h3></div>
                <div class="card-body p-0">
                    <table class="table table-sm table-striped mb-0 text-light" style="font-size:12px;">
                        <tbody>
                            <tr><td style="width:50%;" class="font-weight-bold">Inductive Metal Sensor (LJ18A3):</td><td><span id="val-metal" class="badge badge-success">NO METAL (HIGH)</span></td></tr>
                            <tr><td class="font-weight-bold">Capacitive Proximity (LJC18A3):</td><td><span id="val-cap" class="badge badge-success">PLASTIC PRESENT (LOW)</span></td></tr>
                            <tr><td class="font-weight-bold">AS7263 NIR Spectrometer (W-Ch):</td><td><strong id="val-nir" class="text-info">1450</strong> <small class="text-muted">(PET Range: 200 - 5000)</small></td></tr>
                            <tr><td class="font-weight-bold">Top IR Chute Sensor (E18-D80NK):</td><td><span id="val-top-ir" class="badge badge-secondary">CLEAR (HIGH)</span></td></tr>
                            <tr><td class="font-weight-bold">Bottom IR Bin Sensor (E18-D80NK):</td><td><span id="val-bot-ir" class="badge badge-secondary">CLEAR (HIGH)</span></td></tr>
                            <tr><td class="font-weight-bold">Ultrasonic Bin Level (JSN-SR04T):</td><td><strong id="val-dist">60 cm</strong> <span class="badge badge-success ml-1">OK</span></td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- 5. UART SERIAL CONSOLE -->
            <div class="card">
                <div class="card-header bg-dark"><h3 class="card-title font-weight-bold text-light" style="font-size:14px;"><i class="fas fa-terminal text-warning mr-1"></i> UART Serial Bridge (/dev/ttyS1 @ 115200 Baud)</h3></div>
                <div class="card-body p-2">
                    <div class="serial-console" id="serial-box"></div>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
// Complete HD44780 ROM 5x8 Dot Matrix Font Table (ASCII 0..127 Array)
const HD44780_FONT = [[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [4, 4, 4, 4, 0, 0, 4, 0], [10, 10, 0, 0, 0, 0, 0, 0], [10, 10, 31, 10, 31, 10, 10, 0], [4, 15, 20, 14, 5, 30, 4, 0], [24, 25, 2, 4, 8, 19, 3, 0], [12, 18, 20, 8, 21, 18, 13, 0], [4, 4, 0, 0, 0, 0, 0, 0], [2, 4, 8, 8, 8, 4, 2, 0], [8, 4, 2, 2, 2, 4, 8, 0], [0, 4, 21, 14, 21, 4, 0, 0], [0, 4, 4, 31, 4, 4, 0, 0], [0, 0, 0, 0, 4, 4, 8, 0], [0, 0, 0, 31, 0, 0, 0, 0], [0, 0, 0, 0, 0, 6, 6, 0], [0, 1, 2, 4, 8, 16, 0, 0], [14, 17, 19, 21, 25, 17, 14, 0], [4, 12, 4, 4, 4, 4, 14, 0], [14, 17, 1, 2, 4, 8, 31, 0], [31, 2, 4, 2, 1, 17, 14, 0], [2, 6, 10, 18, 31, 2, 2, 0], [31, 16, 30, 1, 1, 17, 14, 0], [6, 8, 16, 30, 17, 17, 14, 0], [31, 1, 2, 4, 8, 8, 8, 0], [14, 17, 17, 14, 17, 17, 14, 0], [14, 17, 17, 15, 1, 2, 12, 0], [0, 6, 6, 0, 6, 6, 0, 0], [0, 6, 6, 0, 4, 4, 8, 0], [2, 4, 8, 16, 8, 4, 2, 0], [0, 31, 0, 31, 0, 0, 0, 0], [8, 4, 2, 1, 2, 4, 8, 0], [14, 17, 1, 2, 4, 0, 4, 0], [14, 17, 1, 13, 21, 21, 14, 0], [14, 17, 17, 31, 17, 17, 17, 0], [30, 17, 17, 30, 17, 17, 30, 0], [14, 17, 16, 16, 16, 17, 14, 0], [28, 18, 17, 17, 17, 18, 28, 0], [31, 16, 16, 30, 16, 16, 31, 0], [31, 16, 16, 30, 16, 16, 16, 0], [14, 17, 16, 23, 17, 17, 14, 0], [17, 17, 17, 31, 17, 17, 17, 0], [14, 4, 4, 4, 4, 4, 14, 0], [7, 2, 2, 2, 2, 18, 12, 0], [17, 18, 20, 24, 20, 18, 17, 0], [16, 16, 16, 16, 16, 16, 31, 0], [17, 27, 21, 21, 17, 17, 17, 0], [17, 17, 25, 21, 19, 17, 17, 0], [14, 17, 17, 17, 17, 17, 14, 0], [30, 17, 17, 30, 16, 16, 16, 0], [14, 17, 17, 17, 21, 18, 13, 0], [30, 17, 17, 30, 20, 18, 17, 0], [14, 17, 16, 14, 1, 17, 14, 0], [31, 4, 4, 4, 4, 4, 4, 0], [17, 17, 17, 17, 17, 17, 14, 0], [17, 17, 17, 17, 17, 10, 4, 0], [17, 17, 17, 21, 21, 21, 10, 0], [17, 17, 10, 4, 10, 17, 17, 0], [17, 17, 10, 4, 4, 4, 4, 0], [31, 1, 2, 4, 8, 16, 31, 0], [14, 8, 8, 8, 8, 8, 14, 0], [0, 16, 8, 4, 2, 1, 0, 0], [14, 2, 2, 2, 2, 2, 14, 0], [4, 10, 17, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 31, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 14, 1, 15, 17, 15, 0], [16, 16, 22, 25, 17, 17, 30, 0], [0, 0, 14, 17, 16, 17, 14, 0], [1, 1, 13, 19, 17, 17, 15, 0], [0, 0, 14, 17, 31, 16, 14, 0], [6, 9, 8, 28, 8, 8, 8, 0], [0, 15, 17, 17, 15, 1, 14, 0], [16, 16, 22, 25, 17, 17, 17, 0], [4, 0, 12, 4, 4, 4, 14, 0], [2, 0, 6, 2, 2, 18, 12, 0], [16, 16, 18, 20, 24, 20, 18, 0], [12, 4, 4, 4, 4, 4, 14, 0], [0, 0, 26, 21, 21, 17, 17, 0], [0, 0, 22, 25, 17, 17, 17, 0], [0, 0, 14, 17, 17, 17, 14, 0], [0, 0, 30, 17, 30, 16, 16, 0], [0, 0, 15, 17, 15, 1, 1, 0], [0, 0, 22, 25, 16, 16, 16, 0], [0, 0, 14, 16, 14, 1, 30, 0], [8, 8, 28, 8, 8, 9, 6, 0], [0, 0, 17, 17, 17, 19, 13, 0], [0, 0, 17, 17, 17, 10, 4, 0], [0, 0, 17, 17, 21, 21, 10, 0], [0, 0, 17, 10, 4, 10, 17, 0], [0, 0, 17, 17, 15, 1, 14, 0], [0, 0, 31, 2, 4, 8, 31, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0]];

let currentLcdTheme = 'blue';
let lastLcdLines = ["=== ECO-Fi VENDO ===", "Ready for Deposit   ", "Rate: 1 Bottle = 10m", "Session Bottles: 0  "];

const LCD_THEMES = {
    blue: {
        bg: '#002bb0',
        bgGrad: '#001a70',
        pixelOff: 'rgba(0, 25, 95, 0.45)',
        pixelOn: '#ffffff',
        pixelGlow: 'rgba(255, 255, 255, 0.7)'
    },
    yellow: {
        bg: '#8bb300',
        bgGrad: '#769900',
        pixelOff: 'rgba(100, 130, 0, 0.35)',
        pixelOn: '#122400',
        pixelGlow: 'rgba(18, 36, 0, 0.4)'
    },
    off: {
        bg: '#141818',
        bgGrad: '#0e1111',
        pixelOff: 'rgba(25, 30, 30, 0.6)',
        pixelOn: 'rgba(40, 48, 48, 0.8)',
        pixelGlow: 'transparent'
    }
};

function changeLcdTheme(t) {
    currentLcdTheme = t;
    const vp = document.getElementById('lcd-viewport');
    if (vp) {
        if (t === 'blue') vp.style.background = '#002bb0';
        else if (t === 'yellow') vp.style.background = '#8bb300';
        else vp.style.background = '#141818';
    }
    drawLcdCanvas(lastLcdLines);
}

function drawLcdCanvas(lines) {
    if (lines && lines.length) {
        lastLcdLines = lines;
    }
    const canvas = document.getElementById('lcd-matrix-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    const theme = LCD_THEMES[currentLcdTheme] || LCD_THEMES.blue;

    // 1. Draw LCD Background with subtle gradient
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, theme.bg);
    grad.addColorStop(1, theme.bgGrad);
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, W, H);

    // Exact 20 columns and 4 rows sizing
    const cols = 20;
    const rows = 4;
    const cellW = W / cols;       // 672 / 20 = 33.6px
    const cellH = H / rows;       // 210 / 4 = 52.5px

    const padX = cellW * 0.08;
    const padY = cellH * 0.08;
    const usableW = cellW - (padX * 2);
    const usableH = cellH - (padY * 2);

    const dotCols = 5;
    const dotRows = 8;
    const dotGapX = 1.0;
    const dotGapY = 1.0;
    const dotW = (usableW - ((dotCols - 1) * dotGapX)) / dotCols;
    const dotH = (usableH - ((dotRows - 1) * dotGapY)) / dotRows;

    for (let r = 0; r < rows; r++) {
        const rawLine = (lastLcdLines && lastLcdLines[r]) || "";
        const lineStr = rawLine.padEnd(20, ' ').substring(0, 20);

        for (let c = 0; c < cols; c++) {
            const charCode = lineStr.charCodeAt(c) || 32;
            const glyph = (charCode < HD44780_FONT.length) ? HD44780_FONT[charCode] : HD44780_FONT[32];

            const startX = (c * cellW) + padX;
            const startY = (r * cellH) + padY;

            for (let gr = 0; gr < dotRows; gr++) {
                const bitmask = (glyph && glyph[gr]) || 0;
                for (let gc = 0; gc < dotCols; gc++) {
                    const isBitOn = (bitmask & (1 << (4 - gc))) !== 0;
                    const px = startX + (gc * (dotW + dotGapX));
                    const py = startY + (gr * (dotH + dotGapY));

                    if (isBitOn) {
                        ctx.fillStyle = theme.pixelOn;
                        ctx.shadowColor = theme.pixelGlow;
                        ctx.shadowBlur = (currentLcdTheme === 'blue') ? 2 : 0;
                        ctx.fillRect(px, py, dotW, dotH);
                        ctx.shadowBlur = 0; // reset
                    } else {
                        ctx.fillStyle = theme.pixelOff;
                        ctx.fillRect(px, py, dotW, dotH);
                    }
                }
            }
        }
    }
}

function syncSimulator() {
    fetch('/simulator/api/state').then(r=>r.json()).then(d=>{
        // Actuators
        const elGate = document.getElementById('ind-gate');
        if (elGate) {
            elGate.className = 'actuator-indicator ' + (d.entrance_servo > 45 ? 'indicator-on' : 'indicator-off');
            elGate.innerText = d.entrance_servo > 45 ? 'OPEN (' + d.entrance_servo + '°)' : 'CLOSED (' + d.entrance_servo + '°)';
        }

        const elSuc = document.getElementById('ind-success');
        if (elSuc) {
            elSuc.className = 'actuator-indicator ' + (d.success_servo > 45 ? 'indicator-on' : 'indicator-off');
            elSuc.innerText = d.success_servo > 45 ? 'OPEN (' + d.success_servo + '°)' : 'CLOSED (' + d.success_servo + '°)';
        }

        const elRej = document.getElementById('ind-reject');
        if (elRej) {
            elRej.className = 'actuator-indicator ' + (d.reject_servo > 45 ? 'indicator-red' : 'indicator-off');
            elRej.innerText = d.reject_servo > 45 ? 'OPEN (' + d.reject_servo + '°)' : 'CLOSED (' + d.reject_servo + '°)';
        }

        const elBuzz = document.getElementById('ind-buzzer');
        if (elBuzz) {
            elBuzz.className = 'actuator-indicator ' + (d.buzzer ? 'indicator-red' : 'indicator-off');
            elBuzz.innerText = d.buzzer ? 'BEEPING' : 'SILENT';
        }

        const elGrn = document.getElementById('ind-led-green');
        if (elGrn) {
            elGrn.className = 'actuator-indicator ' + (d.led_green ? 'indicator-on' : 'indicator-off');
            elGrn.innerText = d.led_green ? 'ON (GREEN)' : 'OFF';
        }

        const elRed = document.getElementById('ind-led-red');
        if (elRed) {
            elRed.className = 'actuator-indicator ' + (d.led_red ? 'indicator-red' : 'indicator-off');
            elRed.innerText = d.led_red ? 'ON (RED)' : 'OFF';
        }

        // Draw 20x4 LCD Canvas
        if (d.lcd_lines) {
            drawLcdCanvas(d.lcd_lines);
        }

        // Sensors
        const elMet = document.getElementById('val-metal');
        if (elMet) {
            elMet.className = 'badge ' + (d.prox_metal ? 'badge-danger' : 'badge-success');
            elMet.innerText = d.prox_metal ? 'METAL DETECTED (LOW)' : 'NO METAL (HIGH)';
        }

        const elCap = document.getElementById('val-cap');
        if (elCap) {
            elCap.className = 'badge ' + (d.prox_capacitive ? 'badge-success' : 'badge-danger');
            elCap.innerText = d.prox_capacitive ? 'PLASTIC PRESENT (LOW)' : 'NO DIELECTRIC (HIGH)';
        }

        const elNir = document.getElementById('val-nir');
        if (elNir) elNir.innerText = d.nir_val;

        const elTop = document.getElementById('val-top-ir');
        if (elTop) {
            elTop.className = 'badge ' + (d.top_ir ? 'badge-warning' : 'badge-secondary');
            elTop.innerText = d.top_ir ? 'BEAM BROKEN (LOW)' : 'CLEAR (HIGH)';
        }

        const elBot = document.getElementById('val-bot-ir');
        if (elBot) {
            elBot.className = 'badge ' + (d.bottom_ir ? 'badge-warning' : 'badge-secondary');
            elBot.innerText = d.bottom_ir ? 'BEAM BROKEN (LOW)' : 'CLEAR (HIGH)';
        }

        const elDist = document.getElementById('val-dist');
        if (elDist) elDist.innerText = d.bin_distance_cm + ' cm ' + (d.is_bin_full ? '(FULL!)' : '(OK)');

        // Serial Logs
        const elBox = document.getElementById('serial-box');
        if (elBox && d.serial_logs) {
            let sHtml = '';
            d.serial_logs.forEach(l=>{
                const col = l.dir === 'TX' ? '#34d399' : '#38bdf8';
                sHtml += `<div><span style="color:#64748b;">[${l.time}]</span> <strong style="color:${col};">${l.dir}:</strong> ${l.msg}</div>`;
            });
            elBox.innerHTML = sHtml;
        }
    }).catch(err=>console.error("Sync error:", err));
}

// Initial draw immediately on page load
window.addEventListener('DOMContentLoaded', () => {
    drawLcdCanvas(lastLcdLines);
    syncSimulator();
    setInterval(syncSimulator, 500);
});

function triggerDrop(type) {
    fetch('/simulator/api/trigger', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({item_type: type})
    }).then(()=>setTimeout(syncSimulator, 100));
}

function setBin(dist) {
    fetch('/simulator/api/bin', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({distance_cm: dist})
    }).then(()=>setTimeout(syncSimulator, 100));
}

function resetSimSession() {
    fetch('/simulator/api/reset', {method: 'POST'}).then(()=>syncSimulator());
}

function injectLcdPreset(preset) {
    if (preset === 'idle') {
        document.getElementById('test-l0').value = '=== ECO-Fi VENDO ===';
        document.getElementById('test-l1').value = 'Ready for Deposit   ';
        document.getElementById('test-l2').value = 'Rate: 1 Bottle = 10m';
        document.getElementById('test-l3').value = 'Session Bottles: 0  ';
    } else if (preset === 'gate') {
        document.getElementById('test-l0').value = '=== ECO-Fi VENDO ===';
        document.getElementById('test-l1').value = 'GATE OPEN: INSERT...';
        document.getElementById('test-l2').value = 'Drop within 30s     ';
        document.getElementById('test-l3').value = 'Session Bottles: 0  ';
    } else if (preset === 'reject') {
        document.getElementById('test-l0').value = '=== ECO-Fi VENDO ===';
        document.getElementById('test-l1').value = 'STATUS: REJECTED!   ';
        document.getElementById('test-l2').value = 'Tin/Can Detected    ';
        document.getElementById('test-l3').value = 'Session Bottles: 0  ';
    } else if (preset === 'full') {
        document.getElementById('test-l0').value = '=== ECO-Fi VENDO ===';
        document.getElementById('test-l1').value = 'STATUS: STORAGE FULL';
        document.getElementById('test-l2').value = 'Empty Bin Required  ';
        document.getElementById('test-l3').value = 'Session Bottles: 0  ';
    } else if (preset === 'config') {
        document.getElementById('test-l0').value = '=== ECO-Fi CONFIG ==';
        document.getElementById('test-l1').value = 'WIFI: ECO-Fi-Config ';
        document.getElementById('test-l2').value = 'IP: 192.168.4.1     ';
        document.getElementById('test-l3').value = 'Port: 80 / AP Active';
    }
    sendCustomLcd();
}

function sendCustomLcd() {
    const l0 = document.getElementById('test-l0').value;
    const l1 = document.getElementById('test-l1').value;
    const l2 = document.getElementById('test-l2').value;
    const l3 = document.getElementById('test-l3').value;
    fetch('/simulator/api/lcd', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({line0: l0, line1: l1, line2: l2, line3: l3})
    }).then(()=>syncSimulator());
}
</script>
</body>
</html>
"""
