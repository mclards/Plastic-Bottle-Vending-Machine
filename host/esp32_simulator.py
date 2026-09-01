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
        
        # Live Physical Pipe Flow Tracking
        self.pipe_item_type = "none"    # "pet", "metal", "paper", "pvc", "stuck", "none"
        self.pipe_item_stage = "idle"   # "idle", "intake", "airlock", "scanning", "success_drop", "reject_drop", "stuck_chute"
        self.pipe_scan_active = False

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
            self.pipe_item_stage = "intake"
        
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
            self.pipe_item_stage = "airlock" if dropped else "idle"

        if not dropped:
            # Drop timeout
            self.pipe_item_stage = "idle"
            self.pipe_item_type = "none"
            self._handle_reject("Drop / Sensor Error ", "REJECTED")
            return

        # Settle & Scan in Inspection Airlock
        with self.lock:
            self.pipe_item_stage = "scanning"
            self.pipe_scan_active = True

        self.set_lcd(
            line0="=== ECO-Fi VENDO ===",
            line1="STATUS: SCANNING... ",
            line2="Analyzing Material  ",
            line3=f"Session Bottles: {self.current_session_bottles:<3}"
        )
        time.sleep(self.settle_time_ms / 1000.0)

        with self.lock:
            self.pipe_scan_active = False

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
            with self.lock:
                self.pipe_item_stage = "success_drop"
                self.bottom_ir_triggered = False
                self.success_servo_angle = self.suc_open_angle

            self.set_lcd(
                line0="=== ECO-Fi VENDO ===",
                line1="STATUS: VERIFIED OK ",
                line2="Dropping to bin...  ",
                line3=f"Session Bottles: {self.current_session_bottles:<3}"
            )

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
                with self.lock:
                    self.pipe_item_stage = "idle"
                    self.pipe_item_type = "none"

                self.set_lcd(
                    line0="=== ECO-Fi VENDO ===",
                    line1="Ready for Deposit   ",
                    line2="Rate: 1 Bottle = 10m",
                    line3=f"Session Bottles: {self.current_session_bottles:<3}"
                )
            else:
                # Bottle stuck in chute
                with self.lock:
                    self.pipe_item_stage = "stuck_chute"
                self._handle_reject("Drop / Sensor Error ", "REJECTED")
        else:
            # Reject Sequence: Open Reject Gate (Ch 2)
            with self.lock:
                self.pipe_item_stage = "reject_drop"
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
            self.pipe_item_stage = "idle"
            self.pipe_item_type = "none"
            
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
        with self.lock:
            if item_type == "valid_pet": self.pipe_item_type = "pet"
            elif item_type == "metal_can": self.pipe_item_type = "metal"
            elif item_type == "non_plastic": self.pipe_item_type = "paper"
            elif item_type == "invalid_polymer": self.pipe_item_type = "pvc"
            elif item_type == "stuck_bottle": self.pipe_item_type = "stuck"
            else: self.pipe_item_type = "pet"

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

        threading.Thread(target=_do_drop, daemon=True).start()

    def set_bin_distance(self, distance_cm):
        with self.lock:
            self.bin_distance_cm = distance_cm

    def reset_session(self):
        with self.lock:
            self.current_session_bottles = 0
            self.pipe_item_stage = "idle"
            self.pipe_item_type = "none"
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
                "pipe_item_type": self.pipe_item_type,
                "pipe_item_stage": self.pipe_item_stage,
                "pipe_scan_active": self.pipe_scan_active,
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

        .pin-strip-top, .pin-strip-bottom {
            display: flex;
            justify-content: flex-start;
            align-items: center;
            margin-left: 28px;
            gap: 5px;
        }
        .pin-strip-top { margin-bottom: 6px; }
        .pin-strip-bottom { margin-top: 6px; }

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

        /* ========================================================================= */
        /* POLISHED CUTAWAY MECHANICAL PIPE & CHUTE FLOW SCHEMATIC STYLES            */
        /* ========================================================================= */
        .pipe-schematic-box {
            background: radial-gradient(circle at 50% 25%, #0d1527 0%, #030611 100%);
            border: 2px solid #1e293b;
            border-radius: 12px;
            position: relative;
            height: 520px;
            overflow: hidden;
            box-shadow: inset 0 0 35px rgba(0,0,0,0.95), 0 8px 24px rgba(0,0,0,0.6);
        }

        #pipe-svg-diagram {
            width: 100%;
            height: 100%;
            display: block;
        }

        .pipe-status-overlay {
            position: absolute;
            top: 12px;
            left: 16px;
            right: 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            pointer-events: none;
            z-index: 10;
        }
        .flow-stage-badge {
            background: rgba(15, 23, 42, 0.9);
            border: 1.5px solid #38bdf8;
            padding: 5px 14px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 700;
            color: #38bdf8;
            letter-spacing: 0.5px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.6), inset 0 0 8px rgba(56,189,248,0.2);
            backdrop-filter: blur(4px);
        }

        /* Technical Callout HUD Badges in SVG */
        .cad-label {
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 9.5px;
            font-weight: 700;
            letter-spacing: 0.4px;
        }

        /* Actuator and status indicators */
        .actuator-indicator {
            padding: 6px 10px; border-radius: 6px; font-weight: 700; font-size: 11.5px;
            display: inline-block; margin: 2px; width: 100%; text-align: center;
            transition: all 0.3s ease;
        }
        .indicator-on { background: #15803d; color: #bbf7d0; border: 1px solid #22c55e; box-shadow: 0 0 8px rgba(34,197,94,0.4); }
        .indicator-off { background: #1e293b; color: #64748b; border: 1px solid #334155; }
        .indicator-red { background: #991b1b; color: #fecaca; border: 1px solid #ef4444; box-shadow: 0 0 8px rgba(239,68,68,0.4); }

        .serial-console {
            background: #020617; border: 1px solid #1e293b; border-radius: 8px;
            font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; height: 175px;
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
            <p class="text-muted mb-0 small">Authentic 2004A 20x4 I2C Character LCD, Acrylic Chute Fluid Flow & PCA9685 Airlock Physics</p>
        </div>
        <div class="mt-2 mt-md-0">
            <button class="btn btn-sm btn-outline-warning mr-2" onclick="resetSimSession()"><i class="fas fa-redo mr-1"></i> Reset Session</button>
            <a href="/" class="btn btn-sm btn-outline-success mr-2" target="_blank"><i class="fas fa-wifi mr-1"></i> Open Portal</a>
            <a href="/admin" class="btn btn-sm btn-outline-info" target="_blank"><i class="fas fa-user-shield mr-1"></i> Admin Panel</a>
        </div>
    </div>

    <!-- MAIN TWO-COLUMN SYSTEM VIEW -->
    <div class="row">
        
        <!-- LEFT COLUMN: 2004A 20x4 I2C CHARACTER LCD MODULE & TEST CONTROLS -->
        <div class="col-12 col-xl-5">
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
                    <div class="lcd-module-wrapper">
                        <div class="lcd-pcb">
                            <div class="pcb-hole hole-tl"></div>
                            <div class="pcb-hole hole-tr"></div>
                            <div class="pcb-hole hole-bl"></div>
                            <div class="pcb-hole hole-br"></div>

                            <div class="pin-strip-top">
                                <span class="pin-num-label">1</span>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <span class="pin-num-label">16</span>
                            </div>

                            <div class="lcd-metal-frame">
                                <div class="bezel-tab-top"></div>
                                <div class="lcd-glass-viewport" id="lcd-viewport">
                                    <canvas id="lcd-matrix-canvas" width="672" height="210"></canvas>
                                </div>
                                <div class="bezel-tab-bottom"></div>
                            </div>

                            <div class="pin-strip-bottom">
                                <span class="pin-num-label">1</span>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>
                                <span class="pin-num-label">16</span>
                            </div>

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

            <!-- 2. PHYSICAL DROP SIMULATION CONTROLS -->
            <div class="card">
                <div class="card-header bg-dark"><h3 class="card-title font-weight-bold text-light" style="font-size:14px;"><i class="fas fa-gamepad text-primary mr-1"></i> Trigger Physical Bottle Insertions</h3></div>
                <div class="card-body p-3">
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
        </div>

        <!-- RIGHT COLUMN: CUTAWAY PIPE & CHUTE FLOW SCHEMATIC & TELEMETRY -->
        <div class="col-12 col-xl-7">
            
            <!-- 3. POLISHED CUTAWAY MECHANICAL PIPE FLOW SCHEMATIC -->
            <div class="card">
                <div class="card-header bg-dark d-flex justify-content-between align-items-center">
                    <h3 class="card-title font-weight-bold text-light" style="font-size:14px;">
                        <i class="fas fa-project-diagram text-warning mr-1"></i> Cutaway Mechanical Pipe & Bifurcated Airlock Chute Flow
                    </h3>
                    <span class="badge badge-info" style="font-size:11px;">110mm Clear Acrylic & Polycarbonate Tube</span>
                </div>
                <div class="card-body p-2">
                    <div class="pipe-schematic-box">
                        <!-- Top Flow Status Overlay -->
                        <div class="pipe-status-overlay">
                            <span id="pipe-stage-badge" class="flow-stage-badge"><i class="fas fa-spinner fa-spin mr-1"></i> STAGE: STANDBY / READY</span>
                            <span id="pipe-item-badge" class="badge badge-secondary p-2">CHUTE CLEAR</span>
                        </div>

                        <!-- Full Cutaway SVG Diagram (Polished 800x520 ViewBox) -->
                        <svg id="pipe-svg-diagram" viewBox="0 0 800 520" preserveAspectRatio="xMidYMid meet">
                            <defs>
                                <!-- Acrylic Tube Glass Surface Shader -->
                                <linearGradient id="acrylicGrad" x1="0" y1="0" x2="1" y2="0">
                                    <stop offset="0%" stop-color="rgba(56, 189, 248, 0.35)" />
                                    <stop offset="12%" stop-color="rgba(255, 255, 255, 0.12)" />
                                    <stop offset="45%" stop-color="rgba(15, 23, 42, 0.75)" />
                                    <stop offset="88%" stop-color="rgba(255, 255, 255, 0.08)" />
                                    <stop offset="100%" stop-color="rgba(56, 189, 248, 0.35)" />
                                </linearGradient>

                                <!-- Metallic Clamp Flanges -->
                                <linearGradient id="metalFlangeGrad" x1="0" y1="0" x2="1" y2="0">
                                    <stop offset="0%" stop-color="#475569" />
                                    <stop offset="30%" stop-color="#94a3b8" />
                                    <stop offset="70%" stop-color="#cbd5e1" />
                                    <stop offset="100%" stop-color="#334155" />
                                </linearGradient>

                                <!-- NIR Laser Sweep Spectrum Shader -->
                                <linearGradient id="laserBeamGrad" x1="0" y1="0" x2="1" y2="0">
                                    <stop offset="0%" stop-color="rgba(236, 72, 153, 0.9)" />
                                    <stop offset="25%" stop-color="rgba(168, 85, 247, 0.85)" />
                                    <stop offset="75%" stop-color="rgba(59, 130, 246, 0.85)" />
                                    <stop offset="100%" stop-color="rgba(16, 185, 129, 0.9)" />
                                </linearGradient>

                                <!-- Drop Shadow & Glow Filters -->
                                <filter id="glowFilter" x="-20%" y="-20%" width="140%" height="140%">
                                    <feGaussianBlur stdDeviation="3.5" result="blur" />
                                    <feComposite in="SourceGraphic" in2="blur" operator="over" />
                                </filter>
                            </defs>

                            <!-- ================= INDUSTRIAL CABINET FRAMEWORK ================= -->
                            <!-- Machine Cabinet Frame Outline with Corner Rivets -->
                            <rect x="15" y="15" width="770" height="490" rx="10" fill="#070c18" stroke="#1e293b" stroke-width="2" />
                            <!-- Corner Hardware Screws -->
                            <circle cx="28" cy="28" r="4" fill="#64748b" stroke="#334155" />
                            <circle cx="772" cy="28" r="4" fill="#64748b" stroke="#334155" />
                            <circle cx="28" cy="492" r="4" fill="#64748b" stroke="#334155" />
                            <circle cx="772" cy="492" r="4" fill="#64748b" stroke="#334155" />

                            <!-- Header Technical Silk Title -->
                            <text x="32" y="44" fill="#475569" class="cad-label" font-weight="700">ECO-Fi AIRLOCK & BIFURCATED CHUTE SCHEMATIC</text>
                            <text x="768" y="44" fill="#334155" class="cad-label" text-anchor="end">CAD REF: DWG-ECO-CHUTE-V4</text>

                            <!-- ================= 1. INTAKE FUNNEL & THROAT ================= -->
                            <!-- Funnel Shape -->
                            <path d="M 330 65 L 470 65 L 440 115 L 360 115 Z" fill="url(#acrylicGrad)" stroke="#38bdf8" stroke-width="2" />
                            <!-- Top Flange Collar -->
                            <rect x="325" y="60" width="150" height="8" rx="2" fill="url(#metalFlangeGrad)" stroke="#1e293b" />
                            <rect x="355" y="112" width="90" height="6" rx="1" fill="url(#metalFlangeGrad)" stroke="#1e293b" />

                            <!-- Top IR Beam Emitter / Receiver (E18-D80NK) -->
                            <!-- Left Emitter -->
                            <rect x="315" y="90" width="42" height="16" rx="3" fill="#1e293b" stroke="#f59e0b" stroke-width="1.5" />
                            <circle cx="355" cy="98" r="3" fill="#f59e0b" filter="url(#glowFilter)" />
                            <!-- Right Photodiode -->
                            <rect x="442" y="90" width="16" height="16" rx="3" fill="#1e293b" stroke="#f59e0b" stroke-width="1.5" />
                            <!-- Infrared Optical Ray -->
                            <line id="svg-top-ir-beam" x1="358" y1="98" x2="442" y2="98" stroke="#ef4444" stroke-width="2" stroke-dasharray="3 3" filter="url(#glowFilter)" />

                            <!-- ================= 2. MAIN SENSING / INSPECTION AIRLOCK ================= -->
                            <!-- Transparent Cylinder Body (80mm wide x 150mm tall) -->
                            <rect x="360" y="115" width="80" height="155" fill="url(#acrylicGrad)" stroke="#38bdf8" stroke-width="2" />
                            <!-- Mid Glass Specular Glare Line -->
                            <line x1="375" y1="120" x2="375" y2="265" stroke="rgba(255,255,255,0.18)" stroke-width="2" />

                            <!-- SERVO 0: Entrance Flap (PCA Ch 0) Pivot & Flap -->
                            <g id="svg-servo-ent" transform="translate(360, 122)">
                                <circle cx="0" cy="0" r="6" fill="#f8fafc" stroke="#0f172a" stroke-width="2" />
                                <rect id="svg-ent-flap" x="0" y="-4" width="78" height="8" rx="2" fill="#38bdf8" stroke="#0284c7" stroke-width="1" transform="rotate(0)" />
                            </g>

                            <!-- Sensor 1: Inductive Metal Proximity Sensor (LJ18A3-8-Z/BX) -->
                            <g transform="translate(280, 150)">
                                <!-- M18 Threaded Sensor Body -->
                                <rect x="0" y="0" width="76" height="22" rx="3" fill="#1e293b" stroke="#3b82f6" stroke-width="1.5" />
                                <rect x="64" y="2" width="12" height="18" rx="1" fill="#3b82f6" />
                                <text x="32" y="15" fill="#93c5fd" class="cad-label" text-anchor="middle">LJ18A3 (Fe)</text>
                            </g>
                            <!-- Inductive Magnetic Field Wave -->
                            <circle id="svg-metal-field" cx="360" cy="161" r="16" fill="none" stroke="rgba(59,130,246,0.6)" stroke-width="1.5" stroke-dasharray="3 2" />

                            <!-- Sensor 2: Capacitive Proximity Sensor (LJC18A3-H-Z/BX) -->
                            <g transform="translate(444, 150)">
                                <rect x="0" y="0" width="76" height="22" rx="3" fill="#1e293b" stroke="#10b981" stroke-width="1.5" />
                                <rect x="0" y="2" width="12" height="18" rx="1" fill="#10b981" />
                                <text x="44" y="15" fill="#a7f3d0" class="cad-label" text-anchor="middle">LJC18 (Cap)</text>
                            </g>
                            <!-- Capacitive Dielectric Field Wave -->
                            <circle id="svg-cap-field" cx="440" cy="161" r="16" fill="none" stroke="rgba(16,185,129,0.6)" stroke-width="1.5" stroke-dasharray="3 2" />

                            <!-- Sensor 3: AS7263 NIR Spectrometer (SparkFun I2C @ 0x49) -->
                            <g transform="translate(270, 195)">
                                <rect x="0" y="0" width="86" height="26" rx="4" fill="#1e1b4b" stroke="#a855f7" stroke-width="1.5" />
                                <circle cx="8" cy="8" r="2.5" fill="#fef08a" />
                                <circle cx="8" cy="18" r="2.5" fill="#fef08a" />
                                <text x="48" y="17" fill="#e9d5ff" class="cad-label" text-anchor="middle">AS7263 NIR (PET)</text>
                            </g>
                            <!-- NIR Multi-Spectral Scanning Sweep Beam -->
                            <polygon id="svg-nir-beam" points="360,208 440,188 440,228" fill="url(#laserBeamGrad)" opacity="0" filter="url(#glowFilter)" />

                            <!-- Middle Flange Junction Ring -->
                            <rect x="355" y="268" width="90" height="7" rx="1.5" fill="url(#metalFlangeGrad)" stroke="#1e293b" />

                            <!-- ================= 3. BIFURCATED Y-DIVERTER CHUTE ================= -->
                            
                            <!-- LEFT BRANCH: Reject Exit Chute -> Customer Return Tray -->
                            <path d="M 360 272 L 230 395 L 170 395 L 170 435 L 255 435 L 400 300 Z" fill="url(#acrylicGrad)" stroke="#ef4444" stroke-width="2" />
                            
                            <!-- SERVO 2: Reject Flap (PCA Ch 2) Pivot & Flap -->
                            <g id="svg-servo-rej" transform="translate(365, 276)">
                                <circle cx="0" cy="0" r="5.5" fill="#fecaca" stroke="#991b1b" stroke-width="2" />
                                <rect id="svg-rej-flap" x="0" y="-4" width="44" height="8" rx="2" fill="#ef4444" stroke="#b91c1c" stroke-width="1" transform="rotate(0)" />
                            </g>

                            <!-- Customer Rejection Return Tray -->
                            <g transform="translate(45, 395)">
                                <rect x="0" y="0" width="140" height="90" rx="8" fill="#111827" stroke="#ef4444" stroke-width="2" />
                                <rect x="8" y="8" width="124" height="74" rx="4" fill="#1f2937" stroke="#374151" />
                                <!-- Return Ramp Slant -->
                                <path d="M 12 70 L 120 40 L 120 75 L 12 75 Z" fill="#374151" opacity="0.6" />
                                <text x="70" y="32" fill="#fca5a5" font-family="sans-serif" font-size="11" font-weight="700" text-anchor="middle"><tspan fill="#ef4444">⮌</tspan> RETURN TRAY</text>
                                <text x="70" y="50" fill="#94a3b8" class="cad-label" text-anchor="middle">Customer Eject Slot</text>
                            </g>

                            <!-- RIGHT BRANCH: Success Collection Chute -> Secure Storage Bin -->
                            <path d="M 440 272 L 570 395 L 570 435 L 500 435 L 400 340 L 400 272 Z" fill="url(#acrylicGrad)" stroke="#22c55e" stroke-width="2" />

                            <!-- SERVO 1: Success Flap (PCA Ch 1) Pivot & Flap -->
                            <g id="svg-servo-suc" transform="translate(435, 276)">
                                <circle cx="0" cy="0" r="5.5" fill="#bbf7d0" stroke="#166534" stroke-width="2" />
                                <rect id="svg-suc-flap" x="-44" y="-4" width="44" height="8" rx="2" fill="#22c55e" stroke="#15803d" stroke-width="1" transform="rotate(0)" />
                            </g>

                            <!-- Bottom IR Sensor (E18-D80NK) Verified Drop Detector -->
                            <g transform="translate(490, 390)">
                                <rect x="0" y="0" width="16" height="18" rx="2" fill="#1e293b" stroke="#22c55e" stroke-width="1.5" />
                                <rect x="74" y="0" width="34" height="18" rx="3" fill="#1e293b" stroke="#22c55e" stroke-width="1.5" />
                                <line id="svg-bot-ir-beam" x1="16" y1="9" x2="74" y2="9" stroke="#22c55e" stroke-width="2" stroke-dasharray="3 3" filter="url(#glowFilter)" />
                            </g>

                            <!-- Secure Storage Bin Section -->
                            <g transform="translate(495, 410)">
                                <rect x="0" y="0" width="275" height="75" rx="8" fill="#062212" stroke="#22c55e" stroke-width="2" />
                                <!-- Storage Bin Handles & Top Rim -->
                                <rect x="10" y="6" width="255" height="63" rx="4" fill="#0a331c" stroke="#166534" />
                                <text x="110" y="35" fill="#86efac" font-family="sans-serif" font-size="12" font-weight="700"><tspan fill="#22c55e">✔</tspan> RECYCLE STORAGE BIN</text>
                                <text x="110" y="52" fill="#4ade80" class="cad-label">Internal Capacity OK</text>
                                
                                <!-- JSN-SR04T Waterproof Ultrasonic Sensor Module -->
                                <g transform="translate(210, -18)">
                                    <rect x="0" y="0" width="46" height="20" rx="3" fill="#1e293b" stroke="#38bdf8" stroke-width="1.5" />
                                    <circle cx="14" cy="10" r="5" fill="#0284c7" />
                                    <circle cx="32" cy="10" r="5" fill="#0284c7" />
                                    <!-- Sonar Echo Wave Arcs -->
                                    <path d="M 12 24 Q 23 38 34 24" fill="none" stroke="rgba(56,189,248,0.7)" stroke-width="1.5" />
                                    <path d="M 6 32 Q 23 48 40 32" fill="none" stroke="rgba(56,189,248,0.4)" stroke-width="1.5" />
                                </g>
                            </g>

                            <!-- ================= TECHNICAL CAD LEADER CALLOUT BADGES ================= -->
                            <!-- Callout 1: Top IR -->
                            <line x1="285" y1="98" x2="210" y2="98" stroke="#64748b" stroke-width="1" stroke-dasharray="2 2" />
                            <circle cx="210" cy="98" r="2" fill="#f59e0b" />
                            <text x="200" y="101" fill="#fbbf24" class="cad-label" text-anchor="end">TOP IR (ENTRY BEAM)</text>

                            <!-- Callout 2: Servo 0 -->
                            <line x1="360" y1="122" x2="230" y2="122" stroke="#64748b" stroke-width="1" stroke-dasharray="2 2" />
                            <circle cx="230" cy="122" r="2" fill="#38bdf8" />
                            <text x="220" y="125" fill="#38bdf8" class="cad-label" text-anchor="end">SERVO 0 (AIRLOCK FLAP)</text>

                            <!-- Callout 3: Bottom IR -->
                            <line x1="600" y1="398" x2="660" y2="398" stroke="#64748b" stroke-width="1" stroke-dasharray="2 2" />
                            <circle cx="660" cy="398" r="2" fill="#22c55e" />
                            <text x="670" y="401" fill="#4ade80" class="cad-label">BOTTOM IR (DROP OK)</text>

                            <!-- Callout 4: Servo 1 -->
                            <line x1="435" y1="276" x2="550" y2="276" stroke="#64748b" stroke-width="1" stroke-dasharray="2 2" />
                            <circle cx="550" cy="276" r="2" fill="#22c55e" />
                            <text x="560" y="279" fill="#4ade80" class="cad-label">SERVO 1 (SUCCESS GATE)</text>

                            <!-- Callout 5: Servo 2 -->
                            <line x1="365" y1="276" x2="250" y2="276" stroke="#64748b" stroke-width="1" stroke-dasharray="2 2" />
                            <circle cx="250" cy="276" r="2" fill="#ef4444" />
                            <text x="240" y="279" fill="#f87171" class="cad-label" text-anchor="end">SERVO 2 (REJECT GATE)</text>

                            <!-- ================= ANIMATED BOTTLE / ITEM ELEMENT ================= -->
                            <g id="svg-moving-bottle" transform="translate(388, 70)" opacity="0" style="transition: transform 0.4s ease, opacity 0.3s ease;">
                                <!-- Dynamic Item Shell (PET Bottle by Default) -->
                                <rect id="svg-item-neck" x="5" y="0" width="14" height="7" rx="1.5" fill="#38bdf8" stroke="#0284c7" />
                                <rect id="svg-item-body" x="0" y="7" width="24" height="42" rx="5" fill="#7dd3fc" stroke="#0284c7" stroke-width="1.8" />
                                <line x1="3" y1="20" x2="21" y2="20" stroke="#0369a1" stroke-width="1.2" />
                                <line x1="3" y1="28" x2="21" y2="28" stroke="#0369a1" stroke-width="1.2" />
                                <text id="svg-bottle-label" x="12" y="27" fill="#0369a1" font-family="sans-serif" font-size="7.5" font-weight="700" text-anchor="middle">PET</text>
                            </g>
                        </svg>
                    </div>

                    <!-- Sensor & Servo State Badges Bar -->
                    <div class="row text-center mt-2 no-gutters">
                        <div class="col-3 px-1">
                            <span id="ind-gate" class="actuator-indicator indicator-off">ENTRANCE: CLOSED</span>
                        </div>
                        <div class="col-3 px-1">
                            <span id="ind-success" class="actuator-indicator indicator-off">SUCCESS: CLOSED</span>
                        </div>
                        <div class="col-3 px-1">
                            <span id="ind-reject" class="actuator-indicator indicator-off">REJECT: CLOSED</span>
                        </div>
                        <div class="col-3 px-1">
                            <span id="ind-buzzer" class="actuator-indicator indicator-off">BUZZER: SILENT</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 4. REAL-TIME SENSOR BUS TELEMETRY & UART CONSOLE -->
            <div class="row">
                <div class="col-12 col-md-6">
                    <div class="card mb-3">
                        <div class="card-header bg-dark"><h3 class="card-title font-weight-bold text-light" style="font-size:13px;"><i class="fas fa-wave-square text-info mr-1"></i> Sensor Bus Telemetry</h3></div>
                        <div class="card-body p-0">
                            <table class="table table-sm table-striped mb-0 text-light" style="font-size:11.5px;">
                                <tbody>
                                    <tr><td class="font-weight-bold">Inductive Metal (LJ18A3):</td><td><span id="val-metal" class="badge badge-success">NO METAL</span></td></tr>
                                    <tr><td class="font-weight-bold">Capacitive (LJC18A3):</td><td><span id="val-cap" class="badge badge-success">PLASTIC OK</span></td></tr>
                                    <tr><td class="font-weight-bold">AS7263 NIR W-Ch:</td><td><strong id="val-nir" class="text-info">1450</strong></td></tr>
                                    <tr><td class="font-weight-bold">Top IR Chute:</td><td><span id="val-top-ir" class="badge badge-secondary">CLEAR</span></td></tr>
                                    <tr><td class="font-weight-bold">Bottom IR Bin:</td><td><span id="val-bot-ir" class="badge badge-secondary">CLEAR</span></td></tr>
                                    <tr><td class="font-weight-bold">Bin Level (Ultrasonic):</td><td><strong id="val-dist">60 cm</strong> <span class="badge badge-success ml-1">OK</span></td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
                <div class="col-12 col-md-6">
                    <div class="card mb-3">
                        <div class="card-header bg-dark"><h3 class="card-title font-weight-bold text-light" style="font-size:13px;"><i class="fas fa-terminal text-warning mr-1"></i> UART Serial Bridge</h3></div>
                        <div class="card-body p-1">
                            <div class="serial-console" id="serial-box"></div>
                        </div>
                    </div>
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

    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, theme.bg);
    grad.addColorStop(1, theme.bgGrad);
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, W, H);

    const cols = 20;
    const rows = 4;
    const cellW = W / cols;
    const cellH = H / rows;

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
                        ctx.shadowBlur = 0;
                    } else {
                        ctx.fillStyle = theme.pixelOff;
                        ctx.fillRect(px, py, dotW, dotH);
                    }
                }
            }
        }
    }
}

// Pipe Flow Visualizer Updater
function updatePipeVisualizer(d) {
    // 1. Servo Flap Angles
    const entFlap = document.getElementById('svg-ent-flap');
    if (entFlap) {
        entFlap.setAttribute('transform', 'rotate(' + (d.entrance_servo || 0) + ')');
    }
    const sucFlap = document.getElementById('svg-suc-flap');
    if (sucFlap) {
        sucFlap.setAttribute('transform', 'rotate(' + (-(d.success_servo || 0)) + ')');
    }
    const rejFlap = document.getElementById('svg-rej-flap');
    if (rejFlap) {
        rejFlap.setAttribute('transform', 'rotate(' + (d.reject_servo || 0) + ')');
    }

    // 2. Beams & Scanning Visuals
    const topBeam = document.getElementById('svg-top-ir-beam');
    if (topBeam) {
        topBeam.setAttribute('stroke', d.top_ir ? '#f59e0b' : '#ef4444');
        topBeam.setAttribute('stroke-width', d.top_ir ? '3' : '1.5');
    }
    const botBeam = document.getElementById('svg-bot-ir-beam');
    if (botBeam) {
        botBeam.setAttribute('stroke', d.bottom_ir ? '#f59e0b' : '#22c55e');
        botBeam.setAttribute('stroke-width', d.bottom_ir ? '3' : '1.5');
    }

    const nirBeam = document.getElementById('svg-nir-beam');
    if (nirBeam) {
        nirBeam.setAttribute('opacity', d.pipe_scan_active ? '0.85' : '0');
    }

    // 3. Stage & Item Badges
    const stageBadge = document.getElementById('pipe-stage-badge');
    if (stageBadge) {
        let label = 'STAGE: STANDBY / READY';
        let badgeCol = '#38bdf8';
        if (d.pipe_item_stage === 'intake') { label = 'STAGE 1: GATE OPEN (INSERTING)'; badgeCol = '#38bdf8'; }
        else if (d.pipe_item_stage === 'airlock') { label = 'STAGE 2: AIRLOCK CLOSED (SETTLING)'; badgeCol = '#818cf8'; }
        else if (d.pipe_item_stage === 'scanning') { label = 'STAGE 3: MULTI-SENSOR & NIR SCAN'; badgeCol = '#c084fc'; }
        else if (d.pipe_item_stage === 'success_drop') { label = 'STAGE 4: VERIFIED OK -> DROPPING TO BIN'; badgeCol = '#4ade80'; }
        else if (d.pipe_item_stage === 'reject_drop') { label = 'STAGE 4: REJECTED -> RETURNING ITEM'; badgeCol = '#f87171'; }
        else if (d.pipe_item_stage === 'stuck_chute') { label = 'ALERT: ITEM JAMMED IN CHUTE'; badgeCol = '#fbbf24'; }
        stageBadge.innerText = label;
        stageBadge.style.color = badgeCol;
        stageBadge.style.borderColor = badgeCol;
    }

    const itemBadge = document.getElementById('pipe-item-badge');
    if (itemBadge) {
        let text = 'CHUTE CLEAR';
        let cls = 'badge badge-secondary p-2';
        if (d.pipe_item_type === 'pet') { text = 'ITEM: PET PLASTIC BOTTLE'; cls = 'badge badge-success p-2'; }
        else if (d.pipe_item_type === 'metal') { text = 'ITEM: ALUMINUM / TIN CAN'; cls = 'badge badge-warning p-2'; }
        else if (d.pipe_item_type === 'paper') { text = 'ITEM: CARDBOARD / PAPER'; cls = 'badge badge-warning p-2'; }
        else if (d.pipe_item_type === 'pvc') { text = 'ITEM: NON-PET POLYMER (PVC)'; cls = 'badge badge-danger p-2'; }
        else if (d.pipe_item_type === 'stuck') { text = 'ITEM: JAMMED BOTTLE'; cls = 'badge badge-danger p-2'; }
        itemBadge.innerText = text;
        itemBadge.className = cls;
    }

    // 4. Moving Bottle Animation Position & Skin
    const bottleEl = document.getElementById('svg-moving-bottle');
    const itemNeck = document.getElementById('svg-item-neck');
    const itemBody = document.getElementById('svg-item-body');
    const bottleLabel = document.getElementById('svg-bottle-label');

    if (bottleEl) {
        if (d.pipe_item_stage === 'idle' || d.pipe_item_type === 'none') {
            bottleEl.setAttribute('opacity', '0');
        } else {
            bottleEl.setAttribute('opacity', '1');
            let posX = 388;
            let posY = 70;
            if (d.pipe_item_stage === 'intake') { posX = 388; posY = 75; }
            else if (d.pipe_item_stage === 'airlock') { posX = 388; posY = 135; }
            else if (d.pipe_item_stage === 'scanning') { posX = 388; posY = 185; }
            else if (d.pipe_item_stage === 'success_drop') { posX = 510; posY = 380; }
            else if (d.pipe_item_stage === 'reject_drop') { posX = 190; posY = 380; }
            else if (d.pipe_item_stage === 'stuck_chute') { posX = 400; posY = 285; }

            bottleEl.setAttribute('transform', 'translate(' + posX + ', ' + posY + ')');
            
            // Custom item skin colors
            if (itemNeck && itemBody && bottleLabel) {
                if (d.pipe_item_type === 'metal') {
                    itemNeck.setAttribute('fill', '#94a3b8'); itemNeck.setAttribute('stroke', '#475569');
                    itemBody.setAttribute('fill', '#f59e0b'); itemBody.setAttribute('stroke', '#b45309');
                    bottleLabel.innerText = 'CAN'; bottleLabel.setAttribute('fill', '#78350f');
                } else if (d.pipe_item_type === 'paper') {
                    itemNeck.setAttribute('fill', '#d97706'); itemNeck.setAttribute('stroke', '#92400e');
                    itemBody.setAttribute('fill', '#b45309'); itemBody.setAttribute('stroke', '#78350f');
                    bottleLabel.innerText = 'PPR'; bottleLabel.setAttribute('fill', '#fef3c7');
                } else if (d.pipe_item_type === 'pvc') {
                    itemNeck.setAttribute('fill', '#ef4444'); itemNeck.setAttribute('stroke', '#991b1b');
                    itemBody.setAttribute('fill', '#dc2626'); itemBody.setAttribute('stroke', '#7f1d1d');
                    bottleLabel.innerText = 'PVC'; bottleLabel.setAttribute('fill', '#fee2e2');
                } else {
                    itemNeck.setAttribute('fill', '#38bdf8'); itemNeck.setAttribute('stroke', '#0284c7');
                    itemBody.setAttribute('fill', '#7dd3fc'); itemBody.setAttribute('stroke', '#0284c7');
                    bottleLabel.innerText = 'PET'; bottleLabel.setAttribute('fill', '#0369a1');
                }
            }
        }
    }
}

function syncSimulator() {
    fetch('/simulator/api/state').then(r=>r.json()).then(d=>{
        // 1. Actuator text badges
        const elGate = document.getElementById('ind-gate');
        if (elGate) {
            elGate.className = 'actuator-indicator ' + (d.entrance_servo > 45 ? 'indicator-on' : 'indicator-off');
            elGate.innerText = d.entrance_servo > 45 ? 'ENTRANCE: OPEN (' + d.entrance_servo + '°)' : 'ENTRANCE: CLOSED';
        }

        const elSuc = document.getElementById('ind-success');
        if (elSuc) {
            elSuc.className = 'actuator-indicator ' + (d.success_servo > 45 ? 'indicator-on' : 'indicator-off');
            elSuc.innerText = d.success_servo > 45 ? 'SUCCESS: OPEN (' + d.success_servo + '°)' : 'SUCCESS: CLOSED';
        }

        const elRej = document.getElementById('ind-reject');
        if (elRej) {
            elRej.className = 'actuator-indicator ' + (d.reject_servo > 45 ? 'indicator-red' : 'indicator-off');
            elRej.innerText = d.reject_servo > 45 ? 'REJECT: OPEN (' + d.reject_servo + '°)' : 'REJECT: CLOSED';
        }

        const elBuzz = document.getElementById('ind-buzzer');
        if (elBuzz) {
            elBuzz.className = 'actuator-indicator ' + (d.buzzer ? 'indicator-red' : 'indicator-off');
            elBuzz.innerText = d.buzzer ? 'BUZZER: BEEPING' : 'BUZZER: SILENT';
        }

        // 2. 2004A LCD Matrix Canvas
        if (d.lcd_lines) {
            drawLcdCanvas(d.lcd_lines);
        }

        // 3. Pipe & Chute Schematic Visualizer
        updatePipeVisualizer(d);

        // 4. Sensor telemetry table
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

        // 5. Serial UART console logs
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

// Initial draw immediately on DOM load
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
