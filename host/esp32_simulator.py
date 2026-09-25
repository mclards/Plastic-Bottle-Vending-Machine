import os
import uuid
import time
import threading
import json
from collections import deque

# Keep these physical bounds aligned with src/machine_config.h.
HARDWARE_BOUNDS={
    'bin_full_threshold_cm':(1,400,15),'pet_nir_w_min':(0,65535,30),
    'pet_nir_w_max':(1,65535,220),'entrance_gate_timeout':(1,600,60),
    'settle_time_ms':(1,30000,500),'success_drop_tout_ms':(1,30000,3000),
    'retrieval_timeout_s':(5,300,45),'require_nir_sensor':(0,1,1),
    'require_weight_sensor':(0,1,0),'min_bottle_weight_g':(1,1000,10),
    'max_bottle_weight_g':(1,2000,65),'weight_cal_factor':(1,50000,420)}
for _prefix in ('ent','suc'):
    for _state,_default in (('open',90),('close',0)):
        HARDWARE_BOUNDS[_prefix+'_'+_state+'_angle']=(0,180,_default)


def validate_hardware_config(data,current=None):
    if not isinstance(data,dict):raise ValueError('Invalid hardware configuration')
    if set(data)-set(HARDWARE_BOUNDS)-{'cmd'}:raise ValueError('Unknown hardware setting')
    values={key:(current or {}).get(key,limits[2]) for key,limits in HARDWARE_BOUNDS.items()}
    values.update({key:value for key,value in data.items() if key in HARDWARE_BOUNDS})
    for key,(low,high,default) in HARDWARE_BOUNDS.items():
        value=values[key]
        if isinstance(value,bool):raise ValueError('Invalid '+key)
        try:parsed=int(value)
        except (ValueError,TypeError,OverflowError):raise ValueError('Invalid '+key)
        if str(parsed)!=str(value) or not low<=parsed<=high:raise ValueError('Invalid '+key)
        values[key]=parsed
    if values['pet_nir_w_min']>=values['pet_nir_w_max']:raise ValueError('NIR minimum must be below maximum')
    if values['min_bottle_weight_g']>=values['max_bottle_weight_g']:raise ValueError('Minimum weight must be below maximum')
    return values


class ESP32Simulator:

    def __init__(self, on_serial_output_callback=None, journal_path=None, start_worker=True):
        self.on_serial_output_callback = on_serial_output_callback
        self.bin_full_threshold_cm = 15
        self.pet_nir_w_min = 30
        self.pet_nir_w_max = 220
        self.entrance_gate_timeout = 60
        self.settle_time_ms = 500
        self.success_drop_tout_ms = 3000
        self.retrieval_timeout_s = 45
        self.require_nir_sensor = 1
        self.require_weight_sensor = 0
        self.min_bottle_weight_g = 10
        self.max_bottle_weight_g = 65
        self.weight_cal_factor = 420
        self.measured_weight_g = 22.0
        self.ent_open_angle = 90
        self.ent_close_angle = 0
        self.suc_open_angle = 90
        self.suc_close_angle = 0
        self.entrance_servo_angle = 0
        self.success_servo_angle = 0
        self.buzzer_state = False
        self.led_green = False
        self.led_red = False
        self.bin_distance_cm = 60
        self.is_bin_full = False
        self.top_ir_triggered = False
        self.bottom_ir_triggered = False
        self.prox_metal_detected = False
        self.nir_spectrometer_val = 85
        self.pipe_item_type = 'none'
        self.pipe_item_stage = 'idle'
        self.pipe_scan_active = False
        self.current_session_bottles = 0
        self.entrance_gate_requested = False
        self.force_gate_close = False
        self.lcd_lines = ['=== VMC ECO-VENDO ==', 'Ready for Deposit   ', 'Rate: 1 Bottle = 10m', 'Session Bottles: 0  ']
        self.oled_text = 'VMC ECO-VENDO Ready'
        self.serial_logs = deque(maxlen=100)
        self.lock = threading.RLock()
        self.pending_config = None
        self.journal_path = journal_path or os.path.join(os.path.dirname(__file__), 'deposit_journal.json')
        self.journal = {'device':uuid.uuid4().hex,'sequence':0,'session_id':None,'pending':None,'total':0}
        if os.path.exists(self.journal_path):
            with open(self.journal_path) as stream:self.journal=json.load(stream)
        self.current_session_bottles=self.journal['total']
        self.last_receipt_send=0
        self.ap_active = False
        self.ap_stations = 0
        self.ap_started_at = 0
        self.running = True
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        if start_worker:self.worker_thread.start()

    def stop(self):
        self.running = False

    def set_lcd(self, line0=None, line1=None, line2=None, line3=None):
        with self.lock:
            if line0 is not None:
                self.lcd_lines[0] = str(line0).ljust(20)[:20]
            if line1 is not None:
                self.lcd_lines[1] = str(line1).ljust(20)[:20]
            if line2 is not None:
                self.lcd_lines[2] = str(line2).ljust(20)[:20]
            if line3 is not None:
                self.lcd_lines[3] = str(line3).ljust(20)[:20]

    def log_serial(self, direction, msg):
        timestamp = time.strftime('%H:%M:%S')
        entry = {'time': timestamp, 'dir': direction, 'msg': msg}
        with self.lock:
            self.serial_logs.append(entry)
        if direction == 'TX' and self.on_serial_output_callback:
            try:
                self.on_serial_output_callback(msg)
            except Exception as e:
                print('[ESP32 Simulator] Error in serial callback: {}'.format(e))

    def send_uart(self, payload):
        msg = json.dumps(payload)
        self.log_serial('TX', msg)

    def receive_uart(self, raw_str):
        self.log_serial('RX',raw_str.strip())
        try:
            data=json.loads(raw_str);cmd=data.get('cmd')
            if cmd=='OPEN_GATE':
                with self.lock:
                    sid=data.get('session_id')
                    if data.get('protocol')!=2 or not isinstance(sid,str) or not 1<=len(sid)<=36:return
                    if self.journal['pending']:return
                    if self.pipe_item_stage not in ('idle','intake') and sid!=self.journal['session_id']:return
                    if self.journal['session_id']!=sid:
                        self.journal['session_id']=sid;self.journal['total']=0
                        self.current_session_bottles=0;self._save_journal()
                self.open_entrance_gate(data.get('timeout',self.entrance_gate_timeout))
            elif cmd=='CREDIT_ACK':
                with self.lock:
                    pending=self.journal['pending']
                    if data.get('protocol')==2 and pending and data.get('event_id')==pending['event_id'] and data.get('session_id')==pending['session_id']:
                        self.journal['pending']=None
                        try:self._save_journal()
                        except Exception:
                            self.journal['pending']=pending
                            raise
            elif cmd=='CLOSE_GATE':
                self.close_entrance_gate()
            elif cmd=='TEST_SERVO':
                channel = data.get('channel', -1)
                angle = data.get('angle', -1)
                if 0 <= channel <= 1 and 0 <= angle <= 180:
                    self.send_uart({'event': 'SERVO_TEST_OK', 'channel': channel, 'angle': angle})
                else:
                    self.send_uart({'event': 'SERVO_TEST_REJECTED'})
            elif cmd=='SET_AP':
                enable = bool(data.get('enable', True))
                self.ap_active = enable
                self.ap_stations = 0
                self.ap_started_at = time.time() if enable else 0
                self.send_uart({'event': 'AP_STATUS', 'active': self.ap_active, 'stations': 0})
            elif cmd=='TRIGGER_CONFIG':
                self.ap_active = True
                self.ap_stations = 0
                self.ap_started_at = time.time()
                self.send_uart({'event': 'AP_STATUS', 'active': True, 'stations': 0})
                self.close_entrance_gate()
                self.set_lcd(line0='=== VMC CONFIG ====',line1='WIFI: VMC-Config    ',line2='IP: 192.168.4.1     ',line3='Port: 80 / AP Active')
            elif cmd=='SET_CONFIG':
                with self.lock:
                    current=self.pending_config or {key:getattr(self,key) for key in HARDWARE_BOUNDS}
                    self.pending_config=validate_hardware_config(data,current)
            elif cmd=='TEST_NIR':
                is_pet = self.pet_nir_w_min <= self.nir_spectrometer_val <= self.pet_nir_w_max
                self.send_uart({
                    'event': 'NIR_TEST', 'success': True,
                    'r': 120, 's': 150, 't': 180, 'u': 210, 'v': 240, 'w': int(self.nir_spectrometer_val),
                    'calibrated_w': float(self.nir_spectrometer_val),
                    'temp_c': 28, 'pet_min': self.pet_nir_w_min, 'pet_max': self.pet_nir_w_max,
                    'is_pet': is_pet
                })
            elif cmd=='TEST_WEIGHT':
                wt = getattr(self, 'measured_weight_g', 22.0)
                self.send_uart({
                    'event': 'WEIGHT_TEST', 'success': True,
                    'weight_g': float(wt),
                    'min_g': self.min_bottle_weight_g,
                    'max_g': self.max_bottle_weight_g,
                    'cal_factor': self.weight_cal_factor
                })
            elif cmd=='TARE_WEIGHT':
                self.measured_weight_g = 0.0
                self.send_uart({'event': 'TARE_OK', 'success': True})
        except Exception:
            self.close_entrance_gate()
            raise

    def _save_journal(self):
        temporary=self.journal_path+'.tmp'
        with open(temporary,'w') as stream:
            json.dump(self.journal,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,self.journal_path)

    def record_bottle(self):
        with self.lock:
            if self.journal['pending'] or not self.journal['session_id']:raise ValueError('deposit_not_ready')
            self.journal['sequence']+=1;self.journal['total']+=1
            self.journal['pending']={'event':'CREDIT_ADD','protocol':2,'bottles':1,
                'session_id':self.journal['session_id'],'event_id':self.journal['device']+':'+str(self.journal['sequence']),
                'sessionTotal':self.journal['total']}
            self._save_journal()
            self.current_session_bottles=self.journal['total']
        self.replay_receipt()

    def replay_receipt(self):
        with self.lock:pending=self.journal['pending']
        if pending:
            self.send_uart(dict(pending));self.last_receipt_send=time.monotonic()

    def open_entrance_gate(self, timeout=60):
        with self.lock:
            if self.journal['pending'] or not self.journal['session_id']:return
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

    def apply_pending_config(self):
        with self.lock:
            if self.pending_config is None:return
            for key,value in self.pending_config.items():setattr(self,key,value)
            self.pending_config=None
            self.entrance_servo_angle=self.ent_close_angle
            self.success_servo_angle=self.suc_close_angle
        self.send_uart({'event':'CONFIG_SAVED'})

    def _run_loop(self):
        last_ultrasonic_check = time.time()
        last_bin_state = False
        while self.running:
            self.apply_pending_config()
            now = time.time()
            if time.monotonic()-self.last_receipt_send>=1:self.replay_receipt()
            if now - last_ultrasonic_check >= 1.0:
                currently_full = 0 < self.bin_distance_cm < self.bin_full_threshold_cm
                if currently_full != last_bin_state:
                    self.is_bin_full = currently_full
                    last_bin_state = currently_full
                    if currently_full:
                        self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='STATUS: STORAGE FULL', line2='Empty Bin Required  ', line3='Session Bottles: {:<3}'.format(self.current_session_bottles))
                        self.led_red = True
                        self.send_uart({'event': 'BIN_FULL'})
                    else:
                        self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='Ready for Deposit   ', line2='Rate: 1 Bottle = 10m', line3='Session Bottles: {:<3}'.format(self.current_session_bottles))
                        self.led_red = False
                        self.send_uart({'event': 'BIN_OK'})
                last_ultrasonic_check = now
            if self.is_bin_full:
                time.sleep(0.1)
                continue
            if self.ap_active and self.ap_stations == 0 and self.ap_started_at and (now - self.ap_started_at >= 60.0):
                self.ap_active = False
                self.send_uart({'event': 'AP_STATUS', 'active': False, 'stations': 0})
            if self.entrance_gate_requested:
                self.entrance_gate_requested = False
                self._handle_entrance_cycle()
            time.sleep(0.05)

    def _handle_entrance_cycle(self):
        with self.lock:
            self.entrance_servo_angle = self.ent_open_angle
            self.top_ir_triggered = False
            self.force_gate_close = False
            self.led_green = False
            self.led_red = False
            self.pipe_item_stage = 'intake'
        self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='GATE OPEN: INSERT...', line2='Drop within {}s   '.format(self.entrance_gate_timeout), line3='Session Bottles: {:<3}'.format(self.current_session_bottles))
        start_time = time.time()
        dropped = False
        was_forced = False
        while time.time() - start_time < self.entrance_gate_timeout:
            if self.top_ir_triggered or self.force_gate_close:
                if self.top_ir_triggered:
                    dropped = True
                if self.force_gate_close:
                    was_forced = True
                break
            time.sleep(0.02)
        with self.lock:
            self.entrance_servo_angle = self.ent_close_angle
            self.force_gate_close = False
            self.pipe_item_stage = 'airlock' if dropped else 'idle'
        if not dropped:
            with self.lock:
                self.pipe_item_stage = 'idle'
                self.pipe_item_type = 'none'
                self.entrance_servo_angle = self.ent_close_angle
                self.led_green = False
                self.led_red = False
            self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='Ready for Deposit   ', line2='Rate: 1 Bottle = 10m', line3='Session Bottles: {:<3}'.format(self.current_session_bottles))
            if not was_forced:
                self.send_uart({'event': 'TIMEOUT', 'session_id':self.journal['session_id'], 'protocol':2})
            return
        with self.lock:
            self.pipe_item_stage = 'scanning'
            self.pipe_scan_active = True
        self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='STATUS: SCANNING... ', line2='Analyzing Material  ', line3='Session Bottles: {:<3}'.format(self.current_session_bottles))
        time.sleep(self.settle_time_ms / 1000.0)
        with self.lock:
            self.pipe_scan_active = False
        is_valid = True
        reject_display = 'Invalid Material    '
        reason = 'invalid_material'
        desc = 'Invalid Material'
        if self.prox_metal_detected:
            is_valid = False
            reject_display = 'Tin Can Detected    '
            reason = 'tin_can'
            desc = 'Tin Can Detected'
        elif self.require_nir_sensor and not (self.pet_nir_w_min <= self.nir_spectrometer_val <= self.pet_nir_w_max):
            is_valid = False
            if self.nir_spectrometer_val < 22.0:
                reject_display = 'Colored Glass Bottle'
                reason = 'colored_glass'
                desc = 'Colored Glass Bottle'
            elif self.nir_spectrometer_val > 220.0:
                reject_display = 'Cardboard / Paper   '
                reason = 'paper_cup'
                desc = 'Paper / Cardboard Waste'
            else:
                reject_display = 'Invalid Material NIR'
                reason = 'invalid_polymer'
                desc = 'Invalid Material NIR'
        elif self.require_weight_sensor and not (self.min_bottle_weight_g <= self.measured_weight_g <= self.max_bottle_weight_g):
            is_valid = False
            if self.measured_weight_g > self.max_bottle_weight_g:
                reject_display = 'Heavy Glass / Liquid'
                reason = 'overweight_liquid'
                desc = 'Heavy Glass / Liquid'
            else:
                reject_display = 'Underweight Object  '
                reason = 'underweight_trash'
                desc = 'Underweight Object'
        if is_valid:
            self.led_green = True
            with self.lock:
                self.pipe_item_stage = 'success_drop'
                self.bottom_ir_triggered = False
                self.success_servo_angle = self.suc_open_angle
            self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='STATUS: VERIFIED OK ', line2='Dropping to bin...  ', line3='Session Bottles: {:<3}'.format(self.current_session_bottles))
            gate_open_time = time.time()
            passed_drop = False
            while time.time() - gate_open_time < self.success_drop_tout_ms / 1000.0:
                if self.bottom_ir_triggered:
                    passed_drop = True
                    break
                time.sleep(0.02)
            with self.lock:
                self.success_servo_angle = self.suc_close_angle
            if passed_drop:
                self.record_bottle()
                self.buzz(duration_sec=0.12, pulses=2)
                self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='STATUS: BOTTLE SAVED', line2='Ready for Deposit   ', line3='Session Bottles: {:<3}'.format(self.current_session_bottles))
                time.sleep(1.2)
                self.led_green = False
                with self.lock:
                    self.pipe_item_stage = 'idle'
                    self.pipe_item_type = 'none'
                self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='Ready for Deposit   ', line2='Rate: 1 Bottle = 10m', line3='Session Bottles: {:<3}'.format(self.current_session_bottles))
            else:
                with self.lock:
                    self.pipe_item_stage = 'stuck_chute'
                self._handle_reject('drop_timeout', 'Drop Sensor Timeout / Jam')
        else:
            with self.lock:
                self.pipe_item_stage = 'awaiting_retrieval'
            self._handle_reject(reason, desc)

    def _handle_reject(self, reason, desc):
        self.led_red = True
        self.led_green = False
        display_line = desc[:20]
        self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='STATUS: REJECTED!   ', line2=display_line.ljust(20), line3='Please Remove Item  ')
        with self.lock:
            self.success_servo_angle = self.suc_close_angle
            self.entrance_servo_angle = self.ent_open_angle
            self.pipe_item_stage = 'awaiting_retrieval'
        self.send_uart({'event': 'REJECTED', 'session_id': self.journal['session_id'], 'protocol': 2, 'reason': reason, 'desc': desc})
        self.buzz(duration_sec=0.6, pulses=1)

        timeout = float(getattr(self, 'retrieval_timeout_s', 45))
        start_time = time.time()
        cleared = False
        self.force_retrieval_timeout = False
        while time.time() - start_time < timeout:
            if not self.running or self.force_gate_close or getattr(self, 'force_retrieval_timeout', False):
                break
            if not self.top_ir_triggered and not self.prox_metal_detected and self.measured_weight_g < 5.0:
                cleared = True
                break
            time.sleep(0.1)

        forced_tout = getattr(self, 'force_retrieval_timeout', False)
        self.force_retrieval_timeout = False
        self.led_red = False
        if cleared and not self.force_gate_close and not forced_tout:
            self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='STATUS: ITEM REMOVED', line2='Slot Cleared! Ready ', line3='Insert Valid Bottle ')
            self.send_uart({'event': 'ITEM_CLEARED', 'session_id': self.journal['session_id'], 'protocol': 2})
            self.buzz(duration_sec=0.1, pulses=1)
            with self.lock:
                self.entrance_servo_angle = self.ent_open_angle
                self.pipe_item_stage = 'intake'
                self.pipe_item_type = 'none'
        else:
            with self.lock:
                self.entrance_servo_angle = self.ent_close_angle
                self.pipe_item_stage = 'idle'
                self.pipe_item_type = 'none'
            self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='STATUS: TIMEOUT     ', line2='Item Not Retrieved  ', line3='Session on Hold     ')
            self.send_uart({'event': 'RETRIEVAL_TIMEOUT', 'session_id': self.journal['session_id'], 'protocol': 2})
            self.buzz(duration_sec=0.6, pulses=1)

    def retrieve_item(self):
        with self.lock:
            self.top_ir_triggered = False
            self.prox_metal_detected = False
            self.measured_weight_g = 0.0
            self.pipe_item_stage = 'intake'
            self.pipe_item_type = 'none'
            self.entrance_servo_angle = self.ent_open_angle
        self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='STATUS: ITEM REMOVED', line2='Slot Cleared! Ready ', line3='Insert Valid Bottle ')
        return True

    def trigger_retrieval_timeout(self):
        with self.lock:
            self.force_retrieval_timeout = True
        return True

    def press_finish_button(self):
        with self.lock:
            sid = self.journal.get('session_id') or 'sim_session'
            self.entrance_servo_angle = self.ent_close_angle
            self.success_servo_angle = self.suc_close_angle
            self.led_green = False
            self.led_red = False
            self.top_ir_triggered = False
            self.bottom_ir_triggered = False
            self.pipe_item_stage = 'idle'
            self.pipe_item_type = 'none'
        self.set_lcd(line0='=== VMC ECO-VENDO ==', line1='FINISHING SESSION...', line2='Saving Credits...   ', line3='Rate: 1 Bottle = 10m')
        self.buzz(duration_sec=0.1, pulses=1)
        self.send_uart({'event': 'FINISH', 'session_id': sid, 'protocol': 2})
        return True

    def simulate_insert(self, item_type='valid_pet'):
        with self.lock:
            if item_type == 'valid_pet':
                self.pipe_item_type = 'pet'
            elif item_type == 'metal_can':
                self.pipe_item_type = 'metal'
            elif item_type == 'non_plastic':
                self.pipe_item_type = 'paper'
            elif item_type == 'colored_glass':
                self.pipe_item_type = 'cglass'
            elif item_type == 'glass_bottle':
                self.pipe_item_type = 'glass'
            elif item_type == 'stuck_bottle':
                self.pipe_item_type = 'stuck'
            else:
                self.pipe_item_type = 'pet'
        if not self.entrance_gate_requested and self.entrance_servo_angle == self.ent_close_angle:
            self.open_entrance_gate(timeout=15)
            time.sleep(0.1)

        def _do_drop():
            time.sleep(0.3)
            if item_type == 'timeout_cheat':
                return
            with self.lock:
                self.top_ir_triggered = True
            if item_type == 'valid_pet':
                self.prox_metal_detected = False
                self.nir_spectrometer_val = 85.0
                self.measured_weight_g = 22.0

                def _trigger_bottom():
                    time.sleep(0.9)
                    with self.lock:
                        self.bottom_ir_triggered = True
                threading.Thread(target=_trigger_bottom, daemon=True).start()
            elif item_type == 'metal_can':
                self.prox_metal_detected = True
                self.nir_spectrometer_val = 85.0
                self.measured_weight_g = 15.0
            elif item_type == 'non_plastic':
                self.prox_metal_detected = False
                self.nir_spectrometer_val = 240.0
                self.measured_weight_g = 18.0
            elif item_type == 'colored_glass':
                self.prox_metal_detected = False
                self.nir_spectrometer_val = 12.0
                self.measured_weight_g = 320.0
            elif item_type == 'glass_bottle':
                self.prox_metal_detected = False
                self.nir_spectrometer_val = 55.0
                self.measured_weight_g = 340.0
            elif item_type == 'stuck_bottle':
                self.prox_metal_detected = False
                self.nir_spectrometer_val = 85.0
                self.measured_weight_g = 22.0
        threading.Thread(target=_do_drop, daemon=True).start()

    def set_bin_distance(self, distance_cm):
        with self.lock:
            self.bin_distance_cm = distance_cm

    def reset_session(self, force=True):
        with self.lock:
            if force:
                self.journal['total'] = 0
                self.current_session_bottles = 0
                if os.path.exists(self.journal_path):
                    try:
                        with open(self.journal_path, 'w') as stream:
                            json.dump(self.journal, stream)
                    except Exception:
                        pass
            else:
                self.current_session_bottles = self.journal['total']
            self.pipe_item_stage = 'idle'
            self.pipe_item_type = 'none'
            self.set_lcd(line3='Session Bottles: 0  ')

    def get_state(self):
        with self.lock:
            return {'lcd_lines': list(self.lcd_lines), 'oled_text': self.oled_text, 'entrance_servo': self.entrance_servo_angle, 'success_servo': self.success_servo_angle, 'buzzer': self.buzzer_state, 'led_green': self.led_green, 'led_red': self.led_red, 'bin_distance_cm': self.bin_distance_cm, 'is_bin_full': self.is_bin_full, 'top_ir': self.top_ir_triggered, 'bottom_ir': self.bottom_ir_triggered, 'prox_metal': self.prox_metal_detected, 'nir_val': self.nir_spectrometer_val, 'measured_weight_g': self.measured_weight_g, 'require_weight_sensor': self.require_weight_sensor, 'pipe_item_type': self.pipe_item_type, 'pipe_item_stage': self.pipe_item_stage, 'pipe_scan_active': self.pipe_scan_active, 'session_bottles': self.current_session_bottles, 'gate_requested': self.entrance_gate_requested, 'require_nir_sensor': self.require_nir_sensor, 'pca9685_ready': True, 'spectrometer_ready': True, 'hx711_ready': True, 'hardware_ready': True, 'ap_active': self.ap_active, 'ap_stations': self.ap_stations, 'serial_logs': list(self.serial_logs)}

    def render_simulator_html(self):
        return '<!DOCTYPE html>\n<html lang="en">\n<head>\n    <meta charset="UTF-8">\n    <title>VMC ECO-VENDO ESP32 Hardware Simulator & 2004A 20x4 I2C LCD Display</title>\n    <meta name="viewport" content="width=device-width, initial-scale=1">\n    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">\n    <link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">\n    <link rel="shortcut icon" href="/static/favicon.ico">\n    <link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">\n    <link rel="stylesheet" href="/static/vendor/fontawesome/css/all.min.css">\n    <link rel="stylesheet" href="/static/vendor/adminlte/css/adminlte.min.css">\n    <script src="/static/vendor/jquery/jquery.min.js"></script>\n    <style>\n        body { \n            background: #090d16; \n            color: #f8fafc; \n            font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif;\n            min-height: 100vh;\n        }\n\n        /* Outer FR4 Industrial Green PCB */\n        .lcd-module-wrapper {\n            max-width: 680px;\n            margin: 0 auto 16px;\n        }\n\n        .lcd-pcb {\n            background-color: #0d6e38;\n            background-image: \n                radial-gradient(circle at 10% 20%, rgba(255,255,255,0.06) 0%, transparent 40%),\n                linear-gradient(135deg, #13773e 0%, #0d6834 50%, #085227 100%);\n            border: 2px solid #06401e;\n            border-radius: 6px;\n            padding: 14px 18px;\n            box-shadow: 0 16px 40px rgba(0, 0, 0, 0.8), inset 0 1px 1px rgba(255,255,255,0.25);\n            position: relative;\n            user-select: none;\n        }\n\n        .pcb-hole {\n            position: absolute;\n            width: 14px;\n            height: 14px;\n            border-radius: 50%;\n            background: #050b07;\n            border: 2px solid #b4cbb7;\n            box-shadow: inset 0 1px 3px rgba(0,0,0,0.9), 0 0 1px rgba(255,255,255,0.4);\n        }\n        .hole-tl { top: 8px; left: 8px; }\n        .hole-tr { top: 8px; right: 8px; }\n        .hole-bl { bottom: 8px; left: 8px; }\n        .hole-br { bottom: 8px; right: 8px; }\n\n        .pin-strip-top, .pin-strip-bottom {\n            display: flex;\n            justify-content: flex-start;\n            align-items: center;\n            margin-left: 28px;\n            gap: 5px;\n        }\n        .pin-strip-top { margin-bottom: 6px; }\n        .pin-strip-bottom { margin-top: 6px; }\n\n        .gold-pad {\n            width: 9px;\n            height: 14px;\n            background: linear-gradient(180deg, #fef08a 0%, #ca8a04 100%);\n            border: 1px solid #78350f;\n            border-radius: 1.5px;\n            box-shadow: inset 0 1px 1px rgba(255,255,255,0.6);\n            display: flex;\n            align-items: center;\n            justify-content: center;\n        }\n        .gold-pad::after {\n            content: \'\';\n            width: 3.5px;\n            height: 3.5px;\n            background: #111;\n            border-radius: 50%;\n        }\n        .pin-num-label {\n            font-family: \'Consolas\', monospace;\n            font-size: 10px;\n            font-weight: 700;\n            color: #dcfce7;\n            margin: 0 4px;\n            text-shadow: 0 1px 2px rgba(0,0,0,0.8);\n        }\n\n        .lcd-metal-frame {\n            background: linear-gradient(180deg, #222626 0%, #151818 40%, #0d0f0f 100%);\n            border: 3px solid #323838;\n            border-top-color: #454d4d;\n            border-bottom-color: #1a1e1e;\n            border-radius: 4px;\n            padding: 12px 14px;\n            box-shadow: \n                0 4px 15px rgba(0,0,0,0.9), \n                inset 0 1px 2px rgba(255,255,255,0.2), \n                inset 0 -1px 2px rgba(0,0,0,0.8);\n            position: relative;\n        }\n\n        .bezel-tab-top, .bezel-tab-bottom {\n            position: absolute;\n            left: 20px;\n            right: 20px;\n            height: 2px;\n            background: rgba(255, 255, 255, 0.08);\n            border-bottom: 1px solid rgba(0,0,0,0.6);\n        }\n        .bezel-tab-top { top: 4px; }\n        .bezel-tab-bottom { bottom: 4px; }\n\n        .lcd-glass-viewport {\n            border: 3px solid #000;\n            border-radius: 2px;\n            background: #002266;\n            box-shadow: inset 0 0 18px rgba(0,0,0,0.9);\n            position: relative;\n            overflow: hidden;\n            display: flex;\n            align-items: center;\n            justify-content: center;\n            padding: 8px 10px;\n        }\n\n        #lcd-matrix-canvas {\n            width: 100%;\n            height: auto;\n            display: block;\n            image-rendering: pixelated;\n        }\n\n        .lcd-backpack-bar {\n            display: flex;\n            justify-content: space-between;\n            align-items: center;\n            margin-top: 10px;\n            padding: 0 4px;\n            font-family: \'Consolas\', monospace;\n            font-size: 11px;\n            color: #d1fae5;\n            font-weight: 700;\n            letter-spacing: 0.5px;\n        }\n        .i2c-pins {\n            display: flex;\n            gap: 6px;\n        }\n        .i2c-pin-badge {\n            background: rgba(0, 0, 0, 0.4);\n            border: 1px solid rgba(255,255,255,0.25);\n            padding: 2px 6px;\n            border-radius: 3px;\n            color: #fef08a;\n            font-size: 10.5px;\n        }\n\n        /* Technical Schematic Box */\n        .pipe-schematic-box {\n            background: radial-gradient(circle at 50% 25%, #0d1527 0%, #030611 100%);\n            border: 2px solid #1e293b;\n            border-radius: 12px;\n            position: relative;\n            height: 520px;\n            overflow: hidden;\n            box-shadow: inset 0 0 35px rgba(0,0,0,0.95), 0 8px 24px rgba(0,0,0,0.6);\n        }\n\n        #pipe-svg-diagram {\n            width: 100%;\n            height: 100%;\n            display: block;\n        }\n\n        .pipe-status-overlay {\n            position: absolute;\n            top: 12px;\n            left: 16px;\n            right: 16px;\n            display: flex;\n            justify-content: space-between;\n            align-items: center;\n            pointer-events: none;\n            z-index: 10;\n        }\n        .flow-stage-badge {\n            background: rgba(15, 23, 42, 0.9);\n            border: 1.5px solid #38bdf8;\n            padding: 5px 14px;\n            border-radius: 20px;\n            font-size: 12px;\n            font-weight: 700;\n            color: #38bdf8;\n            letter-spacing: 0.5px;\n            backdrop-filter: blur(4px);\n        }\n\n        .cad-label {\n            font-family: \'Consolas\', \'Courier New\', monospace;\n            font-size: 9.5px;\n            font-weight: 700;\n            letter-spacing: 0.4px;\n        }\n\n        .actuator-indicator {\n            padding: 6px 10px; border-radius: 6px; font-weight: 700; font-size: 11.5px;\n            display: inline-block; margin: 2px; width: 100%; text-align: center;\n            transition: all 0.3s ease;\n        }\n        .indicator-on { background: #15803d; color: #bbf7d0; border: 1px solid #22c55e; }\n        .indicator-off { background: #1e293b; color: #64748b; border: 1px solid #334155; }\n        .indicator-red { background: #991b1b; color: #fecaca; border: 1px solid #ef4444; }\n\n        .serial-console {\n            background: #020617; border: 1px solid #1e293b; border-radius: 8px;\n            font-family: \'Consolas\', \'Courier New\', monospace; font-size: 12px; height: 175px;\n            overflow-y: auto; padding: 8px; color: #94a3b8;\n        }\n\n        .card {\n            background: #111827 !important;\n            border: 1px solid #1f2937 !important;\n            border-radius: 12px !important;\n            margin-bottom: 18px !important;\n        }\n        .card-header {\n            border-bottom: 1px solid rgba(255,255,255,0.08) !important;\n            padding: 10px 16px !important;\n        }\n    </style>\n</head>\n<body class="p-3 p-md-4">\n<div class="container-fluid">\n    <!-- Header Bar -->\n    <div class="d-flex flex-wrap justify-content-between align-items-center mb-3 pb-2 border-bottom border-secondary">\n        <div>\n            <h3 class="font-weight-bold text-success mb-0"><i class="fas fa-microchip mr-2"></i> VMC ECO-VENDO ESP32 Hardware Simulator</h3>\n            <p class="text-muted mb-0 small">Authentic 2004A Character LCD, AS7263 NIR Spectroscopy, HX711 Load Cell & Direct Airlock Gravity Chute</p>\n        </div>\n        <div class="mt-2 mt-md-0">\n            <button class="btn btn-sm btn-outline-warning mr-2" onclick="resetSimSession()"><i class="fas fa-redo mr-1"></i> Reset Session</button>\n            <a href="/" class="btn btn-sm btn-outline-success mr-2" target="_blank"><i class="fas fa-wifi mr-1"></i> Open Portal</a>\n            <a href="/admin" class="btn btn-sm btn-outline-info" target="_blank"><i class="fas fa-user-shield mr-1"></i> Admin Panel</a>\n        </div>\n    </div>\n\n    <!-- MAIN TWO-COLUMN SYSTEM VIEW -->\n    <div class="row">\n        \n        <!-- LEFT COLUMN: 2004A 20x4 I2C CHARACTER LCD MODULE & TEST CONTROLS -->\n        <div class="col-12 col-xl-5">\n            <!-- 1. REALISTIC 2004A 20x4 I2C CHARACTER LCD MODULE -->\n            <div class="card">\n                <div class="card-header bg-dark d-flex justify-content-between align-items-center">\n                    <h3 class="card-title font-weight-bold text-light" style="font-size:14px;">\n                        <i class="fas fa-desktop text-info mr-1"></i> 2004A 20x4 Character LCD (HD44780 + PCF8574T @ 0x27)\n                    </h3>\n                    <div class="d-flex align-items-center">\n                        <select id="lcd-theme-select" class="custom-select custom-select-sm" style="width:145px;" onchange="changeLcdTheme(this.value)">\n                            <option value="blue">\U0001f7e6 Blue LED (Default)</option>\n                            <option value="yellow">\U0001f7e8 Yellow-Green</option>\n                            <option value="off">\u2b1b Backlight Off</option>\n                        </select>\n                    </div>\n                </div>\n                <div class="card-body p-3">\n                    <div class="lcd-module-wrapper">\n                        <div class="lcd-pcb">\n                            <div class="pcb-hole hole-tl"></div>\n                            <div class="pcb-hole hole-tr"></div>\n                            <div class="pcb-hole hole-bl"></div>\n                            <div class="pcb-hole hole-br"></div>\n\n                            <div class="pin-strip-top">\n                                <span class="pin-num-label">1</span>\n                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>\n                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>\n                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>\n                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>\n                                <span class="pin-num-label">16</span>\n                            </div>\n\n                            <div class="lcd-metal-frame">\n                                <div class="bezel-tab-top"></div>\n                                <div class="lcd-glass-viewport" id="lcd-viewport">\n                                    <canvas id="lcd-matrix-canvas" width="672" height="210"></canvas>\n                                </div>\n                                <div class="bezel-tab-bottom"></div>\n                            </div>\n\n                            <div class="pin-strip-bottom">\n                                <span class="pin-num-label">1</span>\n                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>\n                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>\n                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>\n                                <div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div><div class="gold-pad"></div>\n                                <span class="pin-num-label">16</span>\n                            </div>\n\n                            <div class="lcd-backpack-bar">\n                                <span><i class="fas fa-microchip mr-1"></i> I2C ADDR: 0x27</span>\n                                <div class="i2c-pins">\n                                    <span class="i2c-pin-badge">GND</span>\n                                    <span class="i2c-pin-badge">VCC (5V)</span>\n                                    <span class="i2c-pin-badge">SDA: GPIO21</span>\n                                    <span class="i2c-pin-badge">SCL: GPIO22</span>\n                                </div>\n                            </div>\n                        </div>\n                    </div>\n\n                    <!-- Custom Message Test Panel -->\n                    <div class="p-2 bg-dark rounded border border-secondary">\n                        <div class="d-flex justify-content-between align-items-center mb-1">\n                            <small class="font-weight-bold text-muted"><i class="fas fa-edit mr-1"></i> Live LCD Text Injection Tester:</small>\n                            <div>\n                                <button class="btn btn-xs btn-outline-info mr-1" onclick="injectLcdPreset(\'idle\')">Standby</button>\n                                <button class="btn btn-xs btn-outline-success mr-1" onclick="injectLcdPreset(\'gate\')">Gate Open</button>\n                                <button class="btn btn-xs btn-outline-warning mr-1" onclick="injectLcdPreset(\'reject\')">Tin Reject</button>\n                                <button class="btn btn-xs btn-outline-danger mr-1" onclick="injectLcdPreset(\'full\')">Bin Full</button>\n                                <button class="btn btn-xs btn-outline-secondary" onclick="injectLcdPreset(\'config\')">Config</button>\n                            </div>\n                        </div>\n                        <div class="row no-gutters">\n                            <div class="col-6 pr-1"><input type="text" id="test-l0" class="form-control form-control-sm mb-1" placeholder="Row 0 (max 20 chars)" maxlength="20"></div>\n                            <div class="col-6 pl-1"><input type="text" id="test-l1" class="form-control form-control-sm mb-1" placeholder="Row 1 (max 20 chars)" maxlength="20"></div>\n                            <div class="col-6 pr-1"><input type="text" id="test-l2" class="form-control form-control-sm" placeholder="Row 2 (max 20 chars)" maxlength="20"></div>\n                            <div class="col-6 pl-1"><input type="text" id="test-l3" class="form-control form-control-sm" placeholder="Row 3 (max 20 chars)" maxlength="20"></div>\n                        </div>\n                        <button class="btn btn-sm btn-info btn-block mt-2 font-weight-bold" onclick="sendCustomLcd()"><i class="fas fa-paper-plane mr-1"></i> Send Custom Text to 20x4 LCD</button>\n                    </div>\n                </div>\n            </div>\n\n            <!-- 2. PHYSICAL DROP SIMULATION CONTROLS -->\n            <div class="card">\n                <div class="card-header bg-dark"><h3 class="card-title font-weight-bold text-light" style="font-size:14px;"><i class="fas fa-gamepad text-primary mr-1"></i> Trigger Physical Bottle Insertions</h3></div>\n                <div class="card-body p-3">\n                    <div class="btn-group-vertical w-100">\n                        <button class="btn btn-success mb-2 font-weight-bold text-left" onclick="triggerDrop(\'valid_pet\')">\n                            <i class="fas fa-wine-bottle mr-1"></i> Drop 1x Valid PET Plastic Bottle (NIR: 85 uW/cm\xb2, Mass: 22g) <span class="badge badge-light float-right">ACCEPT</span>\n                        </button>\n                        <button class="btn btn-warning mb-2 font-weight-bold text-left" onclick="triggerDrop(\'metal_can\')">\n                            <i class="fas fa-drum mr-1"></i> Drop Aluminum / Tin Can (LJ12A3 Inductive Metal LOW) <span class="badge badge-dark float-right">REJECT</span>\n                        </button>\n                        <button class="btn btn-warning mb-2 font-weight-bold text-left" onclick="triggerDrop(\'non_plastic\')">\n                            <i class="fas fa-box-open mr-1"></i> Drop Cardboard / Paper Item (NIR Scatter &gt; 230 uW/cm\xb2) <span class="badge badge-dark float-right">REJECT</span>\n                        </button>\n                        <button class="btn btn-danger mb-2 font-weight-bold text-left" onclick="triggerDrop(\'colored_glass\')">\n                            <i class="fas fa-wine-bottle mr-1"></i> Drop Colored Beer/Wine Glass (AS7263 NIR &lt; 22.0 uW/cm\xb2) <span class="badge badge-light float-right">REJECT</span>\n                        </button>\n                        <button class="btn btn-warning mb-2 font-weight-bold text-left" onclick="triggerDrop(\'glass_bottle\')">\n                            <i class="fas fa-wine-glass-alt mr-1"></i> Drop Heavy Clear Glass Bottle (Mass: 320g &gt; 65g) <span class="badge badge-dark float-right">REJECT</span>\n                        </button>\n                        <button class="btn btn-secondary mb-2 font-weight-bold text-left" onclick="triggerDrop(\'stuck_bottle\')">\n                            <i class="fas fa-exclamation-triangle mr-1"></i> Simulate Jammed Chute Bottle (Bottom IR Timeout) <span class="badge badge-danger float-right">ERROR</span>\n                        </button>\n                        <button class="btn btn-outline-warning mb-2 font-weight-bold text-left" onclick="retrieveItemNow()">\n                            <i class="fas fa-hand-holding mr-1"></i> Take Back / Clear Item from Cradle <span class="badge badge-warning float-right">RETRIEVE</span>\n                        </button>\n                        <button class="btn btn-outline-danger mb-2 font-weight-bold text-left" onclick="triggerRetrievalTimeout()">\n                            <i class="fas fa-user-clock mr-1"></i> Simulate Retrieval Timeout Expired <span class="badge badge-danger float-right">TIMEOUT</span>\n                        </button>\n                        <button class="btn btn-outline-info mb-2 font-weight-bold text-left" onclick="pressFinishButton()">\n                            <i class="fas fa-flag-checkered mr-1"></i> Press Session Finish Button (GPIO 34) <span class="badge badge-info float-right">FINISH</span>\n                        </button>\n                    </div>\n\n                    <div class="d-flex justify-content-between align-items-center mt-2 pt-2 border-top border-secondary">\n                        <span class="small font-weight-bold text-muted">Storage Bin JSN-SR04T Sensor:</span>\n                        <div>\n                            <button class="btn btn-xs btn-outline-danger mr-1" onclick="setBin(8)"><i class="fas fa-fill mr-1"></i> Set Bin FULL (8cm)</button>\n                            <button class="btn btn-xs btn-outline-success" onclick="setBin(60)"><i class="fas fa-check mr-1"></i> Set Bin OK (60cm)</button>\n                        </div>\n                    </div>\n                </div>\n            </div>\n        </div>\n\n        <!-- RIGHT COLUMN: CUTAWAY PIPE & DIRECT DROP SCHEMATIC & TELEMETRY -->\n        <div class="col-12 col-xl-7">\n            \n            <!-- 3. DIRECT DROP GRAVITY AIRLOCK CHUTE SCHEMATIC -->\n            <div class="card">\n                <div class="card-header bg-dark d-flex justify-content-between align-items-center">\n                    <h3 class="card-title font-weight-bold text-light" style="font-size:14px;">\n                        <i class="fas fa-project-diagram text-warning mr-1"></i> Cutaway Mechanical Pipe & Direct Gravity Drop Airlock Flow\n                    </h3>\n                    <span class="badge badge-info" style="font-size:11px;">110mm Clear Acrylic & Polycarbonate Chute</span>\n                </div>\n                <div class="card-body p-2">\n                    <div class="pipe-schematic-box">\n                        <!-- Top Flow Status Overlay -->\n                        <div class="pipe-status-overlay">\n                            <span id="pipe-stage-badge" class="flow-stage-badge"><i class="fas fa-spinner fa-spin mr-1"></i> STAGE: STANDBY / READY</span>\n                            <span id="pipe-item-badge" class="badge badge-secondary p-2">CHUTE CLEAR</span>\n                        </div>\n\n                        <!-- Full Cutaway SVG Diagram (800x520 ViewBox) -->\n                        <svg id="pipe-svg-diagram" viewBox="0 0 800 520" preserveAspectRatio="xMidYMid meet">\n                            <defs>\n                                <linearGradient id="acrylicGrad" x1="0" y1="0" x2="1" y2="0">\n                                    <stop offset="0%" stop-color="rgba(56, 189, 248, 0.35)" />\n                                    <stop offset="12%" stop-color="rgba(255, 255, 255, 0.12)" />\n                                    <stop offset="45%" stop-color="rgba(15, 23, 42, 0.75)" />\n                                    <stop offset="88%" stop-color="rgba(255, 255, 255, 0.08)" />\n                                    <stop offset="100%" stop-color="rgba(56, 189, 248, 0.35)" />\n                                </linearGradient>\n\n                                <linearGradient id="metalFlangeGrad" x1="0" y1="0" x2="1" y2="0">\n                                    <stop offset="0%" stop-color="#475569" />\n                                    <stop offset="30%" stop-color="#94a3b8" />\n                                    <stop offset="70%" stop-color="#cbd5e1" />\n                                    <stop offset="100%" stop-color="#334155" />\n                                </linearGradient>\n\n                                <linearGradient id="laserBeamGrad" x1="0" y1="0" x2="1" y2="0">\n                                    <stop offset="0%" stop-color="rgba(236, 72, 153, 0.9)" />\n                                    <stop offset="25%" stop-color="rgba(168, 85, 247, 0.85)" />\n                                    <stop offset="75%" stop-color="rgba(59, 130, 246, 0.85)" />\n                                    <stop offset="100%" stop-color="rgba(16, 185, 129, 0.9)" />\n                                </linearGradient>\n\n                                <filter id="glowFilter" x="-20%" y="-20%" width="140%" height="140%">\n                                    <feGaussianBlur stdDeviation="3.5" result="blur" />\n                                    <feComposite in="SourceGraphic" in2="blur" operator="over" />\n                                </filter>\n                            </defs>\n\n                            <!-- Machine Cabinet Frame -->\n                            <rect x="15" y="15" width="770" height="490" rx="10" fill="#070c18" stroke="#1e293b" stroke-width="2" />\n                            <circle cx="28" cy="28" r="4" fill="#64748b" stroke="#334155" />\n                            <circle cx="772" cy="28" r="4" fill="#64748b" stroke="#334155" />\n                            <circle cx="28" cy="492" r="4" fill="#64748b" stroke="#334155" />\n                            <circle cx="772" cy="492" r="4" fill="#64748b" stroke="#334155" />\n\n                            <text x="32" y="44" fill="#475569" class="cad-label" font-weight="700">VMC ECO-VENDO AIRLOCK & DIRECT DROP CHUTE SCHEMATIC</text>\n                            <text x="768" y="44" fill="#334155" class="cad-label" text-anchor="end">CAD REF: DWG-ECOVENDO-V5-DIRECT</text>\n\n                            <!-- LEFT HARDWARE HUD: ARCHITECTURAL CONTRACT -->\n                            <g transform="translate(35, 240)">\n                                <rect x="0" y="0" width="285" height="235" rx="8" fill="#0d1527" stroke="#38bdf8" stroke-width="1.5" />\n                                <rect x="6" y="6" width="273" height="223" rx="5" fill="#020617" />\n                                <text x="18" y="32" fill="#38bdf8" font-family="sans-serif" font-size="12" font-weight="700">HARDWARE ARCHITECTURE</text>\n                                <text x="18" y="55" fill="#94a3b8" class="cad-label">\u2022 PCA9685 Ch 0: Entrance / Retrieval Gate</text>\n                                <text x="18" y="73" fill="#94a3b8" class="cad-label">\u2022 PCA9685 Ch 1: Storage Bin Drop Gate</text>\n                                <text x="18" y="91" fill="#94a3b8" class="cad-label">\u2022 LJ12A3: Inductive Metal Proximity (27)</text>\n                                <text x="18" y="109" fill="#94a3b8" class="cad-label">\u2022 AS7263: 6-Ch NIR Polymer Spectrum (0x49)</text>\n                                <text x="18" y="127" fill="#94a3b8" class="cad-label">\u2022 HX711: Load Cell Mass Platform (18/19)</text>\n                                <text x="18" y="145" fill="#94a3b8" class="cad-label">\u2022 Dual E18-D80NK: Entrance & Drop IR (32/33)</text>\n                                <text x="18" y="163" fill="#94a3b8" class="cad-label">\u2022 JSN-SR04T: Storage Bin Ultrasonic (25/26)</text>\n                                <text x="18" y="185" fill="#22c55e" class="cad-label" font-weight="700">\u2714 REJECTION: Manual Retrieval via Ch 0</text>\n                                <text x="18" y="202" fill="#38bdf8" class="cad-label">\u2714 Zero Diverter Flap \u2022 Zero Jam Risk</text>\n                            </g>\n\n                            <!-- ================= 1. INTAKE FUNNEL & THROAT ================= -->\n                            <path d="M 330 65 L 470 65 L 440 115 L 360 115 Z" fill="url(#acrylicGrad)" stroke="#38bdf8" stroke-width="2" />\n                            <rect x="325" y="60" width="150" height="8" rx="2" fill="url(#metalFlangeGrad)" stroke="#1e293b" />\n                            <rect x="355" y="112" width="90" height="6" rx="1" fill="url(#metalFlangeGrad)" stroke="#1e293b" />\n\n                            <!-- Top IR Beam (E18-D80NK GPIO 32) -->\n                            <rect x="315" y="90" width="42" height="16" rx="3" fill="#1e293b" stroke="#f59e0b" stroke-width="1.5" />\n                            <circle cx="355" cy="98" r="3" fill="#f59e0b" filter="url(#glowFilter)" />\n                            <rect x="442" y="90" width="16" height="16" rx="3" fill="#1e293b" stroke="#f59e0b" stroke-width="1.5" />\n                            <line id="svg-top-ir-beam" x1="358" y1="98" x2="442" y2="98" stroke="#ef4444" stroke-width="2" stroke-dasharray="3 3" filter="url(#glowFilter)" />\n\n                            <!-- ================= 2. MAIN INSPECTION CRADLE ================= -->\n                            <rect x="360" y="115" width="80" height="155" fill="url(#acrylicGrad)" stroke="#38bdf8" stroke-width="2" />\n                            <line x1="375" y1="120" x2="375" y2="265" stroke="rgba(255,255,255,0.18)" stroke-width="2" />\n\n                            <!-- SERVO 0: Entrance Gate (PCA Ch 0) -->\n                            <g id="svg-servo-ent" transform="translate(360, 122)">\n                                <circle cx="0" cy="0" r="6" fill="#f8fafc" stroke="#0f172a" stroke-width="2" />\n                                <rect id="svg-ent-flap" x="0" y="-4" width="78" height="8" rx="2" fill="#38bdf8" stroke="#0284c7" stroke-width="1" transform="rotate(0)" />\n                            </g>\n\n                            <!-- Sensor 1: Inductive Metal Proximity (LJ12A3 GPIO 27) -->\n                            <g transform="translate(265, 148)">\n                                <rect x="0" y="0" width="90" height="24" rx="3" fill="#1e293b" stroke="#3b82f6" stroke-width="1.5" />\n                                <rect x="78" y="2" width="10" height="20" rx="1" fill="#3b82f6" />\n                                <text x="38" y="16" fill="#93c5fd" class="cad-label" text-anchor="middle">LJ12A3 (Metal)</text>\n                            </g>\n                            <circle id="svg-metal-field" cx="360" cy="160" r="16" fill="none" stroke="rgba(59,130,246,0.6)" stroke-width="1.5" stroke-dasharray="3 2" />\n\n                            <!-- Sensor 2: HX711 Load Cell Weight Platform (GPIO 18/19) -->\n                            <g transform="translate(444, 148)">\n                                <rect x="0" y="0" width="95" height="24" rx="3" fill="#1e293b" stroke="#10b981" stroke-width="1.5" />\n                                <rect x="0" y="2" width="10" height="20" rx="1" fill="#10b981" />\n                                <text x="52" y="16" fill="#a7f3d0" class="cad-label" text-anchor="middle">HX711 (Mass)</text>\n                            </g>\n\n                            <!-- Sensor 3: AS7263 NIR Spectrometer (I2C @ 0x49) -->\n                            <g transform="translate(255, 195)">\n                                <rect x="0" y="0" width="100" height="26" rx="4" fill="#1e1b4b" stroke="#a855f7" stroke-width="1.5" />\n                                <circle cx="8" cy="8" r="2.5" fill="#fef08a" />\n                                <circle cx="8" cy="18" r="2.5" fill="#fef08a" />\n                                <text x="54" y="17" fill="#e9d5ff" class="cad-label" text-anchor="middle">AS7263 NIR (0x49)</text>\n                            </g>\n                            <polygon id="svg-nir-beam" points="360,208 440,188 440,228" fill="url(#laserBeamGrad)" opacity="0" filter="url(#glowFilter)" />\n\n                            <!-- Flange Ring -->\n                            <rect x="355" y="268" width="90" height="7" rx="1.5" fill="url(#metalFlangeGrad)" stroke="#1e293b" />\n\n                            <!-- ================= 3. DIRECT DROP CHUTE & SUCCESS FLAP ================= -->\n                            <!-- Single Direct Acrylic Tube straight down to storage bin -->\n                            <rect x="360" y="272" width="80" height="135" fill="url(#acrylicGrad)" stroke="#22c55e" stroke-width="2" />\n                            <line x1="375" y1="275" x2="375" y2="400" stroke="rgba(255,255,255,0.18)" stroke-width="2" />\n\n                            <!-- SERVO 1: Success Drop Flap (PCA Ch 1) -->\n                            <g id="svg-servo-suc" transform="translate(435, 276)">\n                                <circle cx="0" cy="0" r="5.5" fill="#bbf7d0" stroke="#166534" stroke-width="2" />\n                                <rect id="svg-suc-flap" x="-74" y="-4" width="74" height="8" rx="2" fill="#22c55e" stroke="#15803d" stroke-width="1" transform="rotate(0)" />\n                            </g>\n\n                            <!-- Bottom IR Sensor (E18-D80NK GPIO 33) Drop Verification -->\n                            <g transform="translate(340, 350)">\n                                <rect x="0" y="0" width="16" height="18" rx="2" fill="#1e293b" stroke="#22c55e" stroke-width="1.5" />\n                                <rect x="104" y="0" width="34" height="18" rx="3" fill="#1e293b" stroke="#22c55e" stroke-width="1.5" />\n                                <line id="svg-bot-ir-beam" x1="16" y1="9" x2="104" y2="9" stroke="#22c55e" stroke-width="2" stroke-dasharray="3 3" filter="url(#glowFilter)" />\n                            </g>\n\n                            <!-- Storage Bin Section -->\n                            <g transform="translate(330, 405)">\n                                <rect x="0" y="0" width="440" height="80" rx="8" fill="#062212" stroke="#22c55e" stroke-width="2" />\n                                <rect x="10" y="6" width="420" height="68" rx="4" fill="#0a331c" stroke="#166534" />\n                                <text x="130" y="38" fill="#86efac" font-family="sans-serif" font-size="13" font-weight="700"><tspan fill="#22c55e">\u2714</tspan> RECYCLE STORAGE BIN</text>\n                                <text x="130" y="56" fill="#4ade80" class="cad-label">Direct Gravity Drop Transit OK</text>\n                                \n                                <!-- JSN-SR04T Ultrasonic Sensor Module -->\n                                <g transform="translate(350, -18)">\n                                    <rect x="0" y="0" width="56" height="22" rx="3" fill="#1e293b" stroke="#38bdf8" stroke-width="1.5" />\n                                    <circle cx="16" cy="11" r="5" fill="#0284c7" />\n                                    <circle cx="40" cy="11" r="5" fill="#0284c7" />\n                                    <path d="M 14 26 Q 28 40 42 26" fill="none" stroke="rgba(56,189,248,0.7)" stroke-width="1.5" />\n                                </g>\n                            </g>\n\n                            <!-- TECHNICAL CALLOUT BADGES -->\n                            <line x1="285" y1="98" x2="210" y2="98" stroke="#64748b" stroke-width="1" stroke-dasharray="2 2" />\n                            <circle cx="210" cy="98" r="2" fill="#f59e0b" />\n                            <text x="200" y="101" fill="#fbbf24" class="cad-label" text-anchor="end">TOP IR: GPIO 32 (ENTRY)</text>\n\n                            <line x1="360" y1="122" x2="230" y2="122" stroke="#64748b" stroke-width="1" stroke-dasharray="2 2" />\n                            <circle cx="230" cy="122" r="2" fill="#38bdf8" />\n                            <text x="220" y="125" fill="#38bdf8" class="cad-label" text-anchor="end">SERVO 0: PCA CH 0 (ENTRANCE)</text>\n\n                            <line x1="435" y1="276" x2="550" y2="276" stroke="#64748b" stroke-width="1" stroke-dasharray="2 2" />\n                            <circle cx="550" cy="276" r="2" fill="#22c55e" />\n                            <text x="560" y="279" fill="#4ade80" class="cad-label">SERVO 1: PCA CH 1 (DROP FLAP)</text>\n\n                            <line x1="480" y1="358" x2="570" y2="358" stroke="#64748b" stroke-width="1" stroke-dasharray="2 2" />\n                            <circle cx="570" cy="358" r="2" fill="#22c55e" />\n                            <text x="580" y="361" fill="#4ade80" class="cad-label">BOTTOM IR: GPIO 33 (DROP OK)</text>\n\n                            <!-- ================= ANIMATED BOTTLE / ITEM ELEMENT ================= -->\n                            <g id="svg-moving-bottle" transform="translate(388, 70)" opacity="0" style="transition: transform 0.4s ease, opacity 0.3s ease;">\n                                <rect id="svg-item-neck" x="5" y="0" width="14" height="7" rx="1.5" fill="#38bdf8" stroke="#0284c7" />\n                                <rect id="svg-item-body" x="0" y="7" width="24" height="42" rx="5" fill="#7dd3fc" stroke="#0284c7" stroke-width="1.8" />\n                                <line x1="3" y1="20" x2="21" y2="20" stroke="#0369a1" stroke-width="1.2" />\n                                <line x1="3" y1="28" x2="21" y2="28" stroke="#0369a1" stroke-width="1.2" />\n                                <text id="svg-bottle-label" x="12" y="27" fill="#0369a1" font-family="sans-serif" font-size="7.5" font-weight="700" text-anchor="middle">PET</text>\n                            </g>\n                        </svg>\n                    </div>\n\n                    <!-- Sensor & Servo State Badges Bar (4 Equal Columns) -->\n                    <div class="row text-center mt-2 no-gutters">\n                        <div class="col-3 px-1">\n                            <span id="ind-gate" class="actuator-indicator indicator-off">ENTRANCE (CH 0): CLOSED</span>\n                        </div>\n                        <div class="col-3 px-1">\n                            <span id="ind-success" class="actuator-indicator indicator-off">SUCCESS (CH 1): CLOSED</span>\n                        </div>\n                        <div class="col-3 px-1">\n                            <span id="ind-leds" class="actuator-indicator indicator-off">PANEL LEDS: OFF</span>\n                        </div>\n                        <div class="col-3 px-1">\n                            <span id="ind-buzzer" class="actuator-indicator indicator-off">BUZZER: SILENT</span>\n                        </div>\n                    </div>\n                </div>\n            </div>\n\n            <!-- 4. REAL-TIME SENSOR BUS TELEMETRY & UART CONSOLE -->\n            <div class="row">\n                <div class="col-12 col-md-6">\n                    <div class="card mb-3">\n                        <div class="card-header bg-dark"><h3 class="card-title font-weight-bold text-light" style="font-size:13px;"><i class="fas fa-wave-square text-info mr-1"></i> Sensor Bus Telemetry</h3></div>\n                        <div class="card-body p-0">\n                            <table class="table table-sm table-striped mb-0 text-light" style="font-size:11.5px;">\n                                <tbody>\n                                    <tr><td class="font-weight-bold">Inductive Metal (LJ12A3 - GPIO 27):</td><td><span id="val-metal" class="badge badge-success">NO METAL (HIGH)</span></td></tr>\n                                    <tr><td class="font-weight-bold">AS7263 NIR Spectrometer (I2C 0x49):</td><td><strong id="val-nir" class="text-info">85.0</strong> <span id="badge-nir" class="badge badge-success ml-1">PET RANGE</span></td></tr>\n                                    <tr><td class="font-weight-bold">HX711 Load Cell (GPIO 18/19):</td><td><strong id="val-weight" class="text-info">22.0 g</strong> <span id="badge-weight" class="badge badge-success ml-1">10-65g OK</span></td></tr>\n                                    <tr><td class="font-weight-bold">Top Intake IR (E18 - GPIO 32):</td><td><span id="val-top-ir" class="badge badge-secondary">CLEAR (HIGH)</span></td></tr>\n                                    <tr><td class="font-weight-bold">Bottom Drop IR (E18 - GPIO 33):</td><td><span id="val-bot-ir" class="badge badge-secondary">CLEAR (HIGH)</span></td></tr>\n                                    <tr><td class="font-weight-bold">Storage Bin (JSN-SR04T - GPIO 25/26):</td><td><strong id="val-dist">60 cm</strong> <span id="badge-dist" class="badge badge-success ml-1">OK</span></td></tr>\n                                </tbody>\n                            </table>\n                        </div>\n                    </div>\n                </div>\n                <div class="col-12 col-md-6">\n                    <div class="card mb-3">\n                        <div class="card-header bg-dark"><h3 class="card-title font-weight-bold text-light" style="font-size:13px;"><i class="fas fa-terminal text-warning mr-1"></i> UART Serial Bridge</h3></div>\n                        <div class="card-body p-1">\n                            <div class="serial-console" id="serial-box"></div>\n                        </div>\n                    </div>\n                </div>\n            </div>\n\n        </div>\n    </div>\n</div>\n\n<script>\n// Complete HD44780 ROM 5x8 Dot Matrix Font Table (ASCII 0..127 Array)\nconst HD44780_FONT = [[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [4, 4, 4, 4, 0, 0, 4, 0], [10, 10, 0, 0, 0, 0, 0, 0], [10, 10, 31, 10, 31, 10, 10, 0], [4, 15, 20, 14, 5, 30, 4, 0], [24, 25, 2, 4, 8, 19, 3, 0], [12, 18, 20, 8, 21, 18, 13, 0], [4, 4, 0, 0, 0, 0, 0, 0], [2, 4, 8, 8, 8, 4, 2, 0], [8, 4, 2, 2, 2, 4, 8, 0], [0, 4, 21, 14, 21, 4, 0, 0], [0, 4, 4, 31, 4, 4, 0, 0], [0, 0, 0, 0, 4, 4, 8, 0], [0, 0, 0, 31, 0, 0, 0, 0], [0, 0, 0, 0, 0, 6, 6, 0], [0, 1, 2, 4, 8, 16, 0, 0], [14, 17, 19, 21, 25, 17, 14, 0], [4, 12, 4, 4, 4, 4, 14, 0], [14, 17, 1, 2, 4, 8, 31, 0], [31, 2, 4, 2, 1, 17, 14, 0], [2, 6, 10, 18, 31, 2, 2, 0], [31, 16, 30, 1, 1, 17, 14, 0], [6, 8, 16, 30, 17, 17, 14, 0], [31, 1, 2, 4, 8, 8, 8, 0], [14, 17, 17, 14, 17, 17, 14, 0], [14, 17, 17, 15, 1, 2, 12, 0], [0, 6, 6, 0, 6, 6, 0, 0], [0, 6, 6, 0, 4, 4, 8, 0], [2, 4, 8, 16, 8, 4, 2, 0], [0, 31, 0, 31, 0, 0, 0, 0], [8, 4, 2, 1, 2, 4, 8, 0], [14, 17, 1, 2, 4, 0, 4, 0], [14, 17, 1, 13, 21, 21, 14, 0], [14, 17, 17, 31, 17, 17, 17, 0], [30, 17, 17, 30, 17, 17, 30, 0], [14, 17, 16, 16, 16, 17, 14, 0], [28, 18, 17, 17, 17, 18, 28, 0], [31, 16, 16, 30, 16, 16, 31, 0], [31, 16, 16, 30, 16, 16, 16, 0], [14, 17, 16, 23, 17, 17, 14, 0], [17, 17, 17, 31, 17, 17, 17, 0], [14, 4, 4, 4, 4, 4, 14, 0], [7, 2, 2, 2, 2, 18, 12, 0], [17, 18, 20, 24, 20, 18, 17, 0], [16, 16, 16, 16, 16, 16, 31, 0], [17, 27, 21, 21, 17, 17, 17, 0], [17, 17, 25, 21, 19, 17, 17, 0], [14, 17, 17, 17, 17, 17, 14, 0], [30, 17, 17, 30, 16, 16, 16, 0], [14, 17, 17, 17, 21, 18, 13, 0], [30, 17, 17, 30, 20, 18, 17, 0], [14, 17, 16, 14, 1, 17, 14, 0], [31, 4, 4, 4, 4, 4, 4, 0], [17, 17, 17, 17, 17, 17, 14, 0], [17, 17, 17, 17, 17, 10, 4, 0], [17, 17, 17, 21, 21, 21, 10, 0], [17, 17, 10, 4, 10, 17, 17, 0], [17, 17, 10, 4, 4, 4, 4, 0], [31, 1, 2, 4, 8, 16, 31, 0], [14, 8, 8, 8, 8, 8, 14, 0], [0, 16, 8, 4, 2, 1, 0, 0], [14, 2, 2, 2, 2, 2, 14, 0], [4, 10, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 31, 0], [4, 4, 0, 0, 0, 0, 0, 0], [0, 0, 14, 1, 15, 17, 15, 0], [16, 16, 22, 25, 17, 17, 22, 0], [0, 0, 14, 16, 16, 17, 14, 0], [1, 1, 13, 19, 17, 17, 15, 0], [0, 0, 14, 17, 31, 16, 14, 0], [6, 9, 8, 28, 8, 8, 8, 0], [0, 0, 15, 17, 15, 1, 14, 0], [16, 16, 22, 25, 17, 17, 17, 0], [4, 0, 12, 4, 4, 4, 14, 0], [2, 0, 6, 2, 2, 18, 12, 0], [16, 16, 18, 20, 24, 20, 18, 0], [12, 4, 4, 4, 4, 4, 14, 0], [0, 0, 26, 21, 21, 17, 17, 0], [0, 0, 22, 25, 17, 17, 17, 0], [0, 0, 14, 17, 17, 17, 14, 0], [0, 0, 22, 25, 30, 16, 16, 0], [0, 0, 13, 19, 15, 1, 1, 0], [0, 0, 22, 25, 16, 16, 16, 0], [0, 0, 14, 16, 14, 1, 30, 0], [8, 8, 28, 8, 8, 9, 6, 0], [0, 0, 17, 17, 17, 19, 13, 0], [0, 0, 17, 17, 17, 10, 4, 0], [0, 0, 17, 17, 21, 21, 10, 0], [0, 0, 17, 10, 4, 10, 17, 0], [0, 0, 17, 17, 15, 1, 14, 0], [0, 0, 31, 2, 4, 8, 31, 0], [2, 4, 4, 8, 4, 4, 2, 0], [4, 4, 4, 4, 4, 4, 4, 0], [8, 4, 4, 2, 4, 4, 8, 0], [0, 0, 0, 0, 0, 0, 0, 0], [31, 31, 31, 31, 31, 31, 31, 31]];\n\nlet currentTheme = \'blue\';\nconst THEMES = {\n    \'blue\': { bg: \'#002266\', on: \'#e6f0ff\', off: \'#003399\', glow: \'rgba(230, 240, 255, 0.45)\' },\n    \'yellow\': { bg: \'#2b3305\', on: \'#fef08a\', off: \'#3e4a07\', glow: \'rgba(254, 240, 138, 0.4)\' },\n    \'off\': { bg: \'#10141a\', on: \'#222933\', off: \'#151a22\', glow: \'transparent\' }\n};\n\nlet lastLcdLines = [\'=== VMC ECO-VENDO ==\', \'Ready for Deposit   \', \'Rate: 1 Bottle = 10m\', \'Session Bottles: 0  \'];\n\nfunction changeLcdTheme(val) {\n    currentTheme = val;\n    const vp = document.getElementById(\'lcd-viewport\');\n    if (vp) vp.style.background = THEMES[currentTheme].bg;\n    drawLcdCanvas(lastLcdLines);\n}\n\nfunction drawLcdCanvas(lines) {\n    lastLcdLines = lines;\n    const canvas = document.getElementById(\'lcd-matrix-canvas\');\n    if (!canvas) return;\n    const ctx = canvas.getContext(\'2d\');\n    const cols = 20, rows = 4;\n    const dotW = 3, dotH = 4, dotGap = 1;\n    const charW = dotW * 5 + dotGap * 4;\n    const charH = dotH * 8 + dotGap * 7;\n    const padX = 12, padY = 12, charGapX = 6, charGapY = 9;\n\n    ctx.fillStyle = THEMES[currentTheme].bg;\n    ctx.fillRect(0, 0, canvas.width, canvas.height);\n\n    for (let r = 0; r < rows; r++) {\n        const text = (lines[r] || \'\').padEnd(cols, \' \').substring(0, cols);\n        for (let c = 0; c < cols; c++) {\n            const code = text.charCodeAt(c) || 32;\n            const fontRow = HD44780_FONT[code < 128 ? code : 32] || HD44780_FONT[32];\n            const startX = padX + c * (charW + charGapX);\n            const startY = padY + r * (charH + charGapY);\n\n            for (let py = 0; py < 8; py++) {\n                const rowBits = fontRow[py];\n                for (let px = 0; px < 5; px++) {\n                    const isLit = (rowBits & (1 << (4 - px))) !== 0;\n                    const dx = startX + px * (dotW + dotGap);\n                    const dy = startY + py * (dotH + dotGap);\n\n                    ctx.fillStyle = isLit ? THEMES[currentTheme].on : THEMES[currentTheme].off;\n                    if (isLit && currentTheme !== \'off\') {\n                        ctx.shadowColor = THEMES[currentTheme].glow;\n                        ctx.shadowBlur = 4;\n                    } else {\n                        ctx.shadowColor = \'transparent\';\n                        ctx.shadowBlur = 0;\n                    }\n                    ctx.fillRect(dx, dy, dotW, dotH);\n                }\n            }\n        }\n    }\n    ctx.shadowColor = \'transparent\';\n    ctx.shadowBlur = 0;\n}\n\nfunction updatePipeVisualizer(d) {\n    const entFlap = document.getElementById(\'svg-ent-flap\');\n    if (entFlap) {\n        entFlap.setAttribute(\'transform\', \'rotate(\' + (d.entrance_servo || 0) + \')\');\n    }\n    const sucFlap = document.getElementById(\'svg-suc-flap\');\n    if (sucFlap) {\n        sucFlap.setAttribute(\'transform\', \'rotate(\' + (-(d.success_servo || 0)) + \')\');\n    }\n\n    const topBeam = document.getElementById(\'svg-top-ir-beam\');\n    if (topBeam) {\n        topBeam.setAttribute(\'stroke\', d.top_ir ? \'#f59e0b\' : \'#ef4444\');\n        topBeam.setAttribute(\'stroke-width\', d.top_ir ? \'3\' : \'1.5\');\n    }\n    const botBeam = document.getElementById(\'svg-bot-ir-beam\');\n    if (botBeam) {\n        botBeam.setAttribute(\'stroke\', d.bottom_ir ? \'#f59e0b\' : \'#22c55e\');\n        botBeam.setAttribute(\'stroke-width\', d.bottom_ir ? \'3\' : \'1.5\');\n    }\n\n    const nirBeam = document.getElementById(\'svg-nir-beam\');\n    if (nirBeam) {\n        nirBeam.setAttribute(\'opacity\', d.pipe_scan_active ? \'0.85\' : \'0\');\n    }\n\n    const stageBadge = document.getElementById(\'pipe-stage-badge\');\n    if (stageBadge) {\n        let label = \'STAGE: STANDBY / READY\';\n        let badgeCol = \'#38bdf8\';\n        if (d.pipe_item_stage === \'intake\') { label = \'STAGE 1: GATE OPEN (INSERTING)\'; badgeCol = \'#38bdf8\'; }\n        else if (d.pipe_item_stage === \'airlock\') { label = \'STAGE 2: AIRLOCK CLOSED (SETTLING)\'; badgeCol = \'#818cf8\'; }\n        else if (d.pipe_item_stage === \'scanning\') { label = \'STAGE 3: NIR & WEIGHT SENSOR SCAN\'; badgeCol = \'#c084fc\'; }\n        else if (d.pipe_item_stage === \'success_drop\') { label = \'STAGE 4: VERIFIED OK -> DROPPING TO BIN\'; badgeCol = \'#4ade80\'; }\n        else if (d.pipe_item_stage === \'awaiting_retrieval\') { label = \'STAGE 4: REJECTED -> PLEASE REMOVE ITEM\'; badgeCol = \'#f87171\'; }\n        else if (d.pipe_item_stage === \'stuck_chute\') { label = \'ALERT: ITEM JAMMED IN CHUTE\'; badgeCol = \'#fbbf24\'; }\n        stageBadge.innerText = label;\n        stageBadge.style.color = badgeCol;\n        stageBadge.style.borderColor = badgeCol;\n    }\n\n    const itemBadge = document.getElementById(\'pipe-item-badge\');\n    if (itemBadge) {\n        let text = \'CHUTE CLEAR\';\n        let cls = \'badge badge-secondary p-2\';\n        if (d.pipe_item_type === \'pet\') { text = \'ITEM: CLEAR PET BOTTLE\'; cls = \'badge badge-success p-2\'; }\n        else if (d.pipe_item_type === \'metal\') { text = \'ITEM: ALUMINUM / TIN CAN\'; cls = \'badge badge-warning p-2\'; }\n        else if (d.pipe_item_type === \'paper\') { text = \'ITEM: CARDBOARD / PAPER\'; cls = \'badge badge-warning p-2\'; }\n        else if (d.pipe_item_type === \'pvc\') { text = \'ITEM: NON-PET POLYMER (PVC)\'; cls = \'badge badge-danger p-2\'; }\n        else if (d.pipe_item_type === \'glass\') { text = \'ITEM: GLASS BOTTLE\'; cls = \'badge badge-warning p-2\'; }\n        else if (d.pipe_item_type === \'stuck\') { text = \'ITEM: JAMMED BOTTLE\'; cls = \'badge badge-danger p-2\'; }\n        itemBadge.innerText = text;\n        itemBadge.className = cls;\n    }\n\n    const bottleEl = document.getElementById(\'svg-moving-bottle\');\n    const itemNeck = document.getElementById(\'svg-item-neck\');\n    const itemBody = document.getElementById(\'svg-item-body\');\n    const bottleLabel = document.getElementById(\'svg-bottle-label\');\n\n    if (bottleEl) {\n        if (d.pipe_item_stage === \'idle\' || d.pipe_item_type === \'none\') {\n            bottleEl.setAttribute(\'opacity\', \'0\');\n        } else {\n            bottleEl.setAttribute(\'opacity\', \'1\');\n            let posX = 388;\n            let posY = 70;\n            if (d.pipe_item_stage === \'intake\') { posX = 388; posY = 75; }\n            else if (d.pipe_item_stage === \'airlock\') { posX = 388; posY = 135; }\n            else if (d.pipe_item_stage === \'scanning\') { posX = 388; posY = 185; }\n            else if (d.pipe_item_stage === \'success_drop\') { posX = 388; posY = 370; }\n            else if (d.pipe_item_stage === \'awaiting_retrieval\') { posX = 388; posY = 150; }\n            else if (d.pipe_item_stage === \'stuck_chute\') { posX = 388; posY = 270; }\n\n            bottleEl.setAttribute(\'transform\', \'translate(\' + posX + \', \' + posY + \')\');\n            \n            if (itemNeck && itemBody && bottleLabel) {\n                if (d.pipe_item_type === \'metal\') {\n                    itemNeck.setAttribute(\'fill\', \'#94a3b8\'); itemNeck.setAttribute(\'stroke\', \'#475569\');\n                    itemBody.setAttribute(\'fill\', \'#f59e0b\'); itemBody.setAttribute(\'stroke\', \'#b45309\');\n                    bottleLabel.innerText = \'CAN\'; bottleLabel.setAttribute(\'fill\', \'#78350f\');\n                } else if (d.pipe_item_type === \'paper\') {\n                    itemNeck.setAttribute(\'fill\', \'#d97706\'); itemNeck.setAttribute(\'stroke\', \'#92400e\');\n                    itemBody.setAttribute(\'fill\', \'#b45309\'); itemBody.setAttribute(\'stroke\', \'#78350f\');\n                    bottleLabel.innerText = \'PPR\'; bottleLabel.setAttribute(\'fill\', \'#fef3c7\');\n                } else if (d.pipe_item_type === \'pvc\') {\n                    itemNeck.setAttribute(\'fill\', \'#ef4444\'); itemNeck.setAttribute(\'stroke\', \'#991b1b\');\n                    itemBody.setAttribute(\'fill\', \'#dc2626\'); itemBody.setAttribute(\'stroke\', \'#7f1d1d\');\n                    bottleLabel.innerText = \'PVC\'; bottleLabel.setAttribute(\'fill\', \'#fee2e2\');\n                } else if (d.pipe_item_type === \'glass\') {\n                    itemNeck.setAttribute(\'fill\', \'#a7f3d0\'); itemNeck.setAttribute(\'stroke\', \'#059669\');\n                    itemBody.setAttribute(\'fill\', \'#34d399\'); itemBody.setAttribute(\'stroke\', \'#047857\');\n                    bottleLabel.innerText = \'GLS\'; bottleLabel.setAttribute(\'fill\', \'#064e3b\');\n                } else {\n                    itemNeck.setAttribute(\'fill\', \'#38bdf8\'); itemNeck.setAttribute(\'stroke\', \'#0284c7\');\n                    itemBody.setAttribute(\'fill\', \'#7dd3fc\'); itemBody.setAttribute(\'stroke\', \'#0284c7\');\n                    bottleLabel.innerText = \'PET\'; bottleLabel.setAttribute(\'fill\', \'#0369a1\');\n                }\n            }\n        }\n    }\n}\n\nfunction syncSimulator() {\n    fetch(\'/simulator/api/state\').then(r=>r.json()).then(d=>{\n        const elGate = document.getElementById(\'ind-gate\');\n        if (elGate) {\n            elGate.className = \'actuator-indicator \' + (d.entrance_servo > 45 ? \'indicator-on\' : \'indicator-off\');\n            elGate.innerText = d.entrance_servo > 45 ? \'ENTRANCE (CH 0): OPEN (\' + d.entrance_servo + \'\xb0)\' : \'ENTRANCE (CH 0): CLOSED\';\n        }\n\n        const elSuc = document.getElementById(\'ind-success\');\n        if (elSuc) {\n            elSuc.className = \'actuator-indicator \' + (d.success_servo > 45 ? \'indicator-on\' : \'indicator-off\');\n            elSuc.innerText = d.success_servo > 45 ? \'SUCCESS (CH 1): OPEN (\' + d.success_servo + \'\xb0)\' : \'SUCCESS (CH 1): CLOSED\';\n        }\n\n        const elLeds = document.getElementById(\'ind-leds\');\n        if (elLeds) {\n            if (d.led_red) {\n                elLeds.className = \'actuator-indicator indicator-red\';\n                elLeds.innerText = \'RED LED: ALERT ON\';\n            } else if (d.led_green) {\n                elLeds.className = \'actuator-indicator indicator-on\';\n                elLeds.innerText = \'GREEN LED: ACTIVE\';\n            } else {\n                elLeds.className = \'actuator-indicator indicator-off\';\n                elLeds.innerText = \'PANEL LEDS: OFF\';\n            }\n        }\n\n        const elBuzz = document.getElementById(\'ind-buzzer\');\n        if (elBuzz) {\n            elBuzz.className = \'actuator-indicator \' + (d.buzzer ? \'indicator-red\' : \'indicator-off\');\n            elBuzz.innerText = d.buzzer ? \'BUZZER: BEEPING\' : \'BUZZER: SILENT\';\n        }\n\n        if (d.lcd_lines) {\n            drawLcdCanvas(d.lcd_lines);\n        }\n\n        updatePipeVisualizer(d);\n\n        const elMet = document.getElementById(\'val-metal\');\n        if (elMet) {\n            elMet.className = \'badge \' + (d.prox_metal ? \'badge-danger\' : \'badge-success\');\n            elMet.innerText = d.prox_metal ? \'METAL DETECTED (LOW)\' : \'NO METAL (HIGH)\';\n        }\n\n        const elNir = document.getElementById(\'val-nir\');\n        if (elNir) elNir.innerText = (d.nir_val !== undefined ? d.nir_val.toFixed(1) : \'85.0\') + \' uW/cm\xb2\';\n        \n        const bNir = document.getElementById(\'badge-nir\');\n        if (bNir) {\n            const ok = (d.nir_val >= 30 && d.nir_val <= 220);\n            bNir.className = \'badge \' + (ok ? \'badge-success\' : \'badge-danger\') + \' ml-1\';\n            bNir.innerText = ok ? \'PET RANGE\' : \'OUT OF RANGE\';\n        }\n\n        const elWeight = document.getElementById(\'val-weight\');\n        if (elWeight) elWeight.innerText = (d.measured_weight_g !== undefined ? d.measured_weight_g.toFixed(1) : \'22.0\') + \' g\';\n\n        const bWeight = document.getElementById(\'badge-weight\');\n        if (bWeight) {\n            const ok = (d.measured_weight_g >= 10 && d.measured_weight_g <= 65);\n            bWeight.className = \'badge \' + (ok ? \'badge-success\' : \'badge-warning\') + \' ml-1\';\n            bWeight.innerText = ok ? \'10-65g OK\' : (d.measured_weight_g > 65 ? \'OVERWEIGHT\' : \'EMPTY/LIGHT\');\n        }\n\n        const elTop = document.getElementById(\'val-top-ir\');\n        if (elTop) {\n            elTop.className = \'badge \' + (d.top_ir ? \'badge-warning\' : \'badge-secondary\');\n            elTop.innerText = d.top_ir ? \'BEAM BROKEN (LOW)\' : \'CLEAR (HIGH)\';\n        }\n\n        const elBot = document.getElementById(\'val-bot-ir\');\n        if (elBot) {\n            elBot.className = \'badge \' + (d.bottom_ir ? \'badge-warning\' : \'badge-secondary\');\n            elBot.innerText = d.bottom_ir ? \'BEAM BROKEN (LOW)\' : \'CLEAR (HIGH)\';\n        }\n\n        const elDist = document.getElementById(\'val-dist\');\n        if (elDist) elDist.innerText = d.bin_distance_cm + \' cm\';\n        \n        const bDist = document.getElementById(\'badge-dist\');\n        if (bDist) {\n            bDist.className = \'badge \' + (d.is_bin_full ? \'badge-danger\' : \'badge-success\') + \' ml-1\';\n            bDist.innerText = d.is_bin_full ? \'FULL!\' : \'OK\';\n        }\n\n        const elBox = document.getElementById(\'serial-box\');\n        if (elBox && d.serial_logs) {\n            let sHtml = \'\';\n            d.serial_logs.forEach(l=>{\n                const col = l.dir === \'TX\' ? \'#34d399\' : \'#38bdf8\';\n                sHtml += `<div><span style="color:#64748b;">[${l.time}]</span> <strong style="color:${col};">${l.dir}:</strong> ${l.msg}</div>`;\n            });\n            elBox.innerHTML = sHtml;\n        }\n    }).catch(err=>console.error("Sync error:", err));\n}\n\nwindow.addEventListener(\'DOMContentLoaded\', () => {\n    drawLcdCanvas(lastLcdLines);\n    syncSimulator();\n    setInterval(syncSimulator, 500);\n});\n\nfunction triggerDrop(type) {\n    fetch(\'/simulator/api/trigger\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({item_type: type})\n    }).then(()=>setTimeout(syncSimulator, 100));\n}\n\nfunction setBin(dist) {\n    fetch(\'/simulator/api/bin\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({distance_cm: dist})\n    }).then(()=>setTimeout(syncSimulator, 100));\n}\n\nfunction resetSimSession() {\n    fetch(\'/simulator/api/reset\', {method: \'POST\'}).then(()=>syncSimulator());\n}\n\nfunction retrieveItemNow() {\n    fetch(\'/simulator/api/retrieve\', {method: \'POST\'}).then(()=>syncSimulator());\n}\n\nfunction triggerRetrievalTimeout() {\n    fetch(\'/simulator/api/timeout\', {method: \'POST\'}).then(()=>syncSimulator());\n}\n\nfunction pressFinishButton() {\n    fetch(\'/simulator/api/finish\', {method: \'POST\'}).then(()=>syncSimulator());\n}\n\nfunction injectLcdPreset(preset) {\n    if (preset === \'idle\') {\n        document.getElementById(\'test-l0\').value = \'=== VMC ECO-VENDO ==\';\n        document.getElementById(\'test-l1\').value = \'Ready for Deposit   \';\n        document.getElementById(\'test-l2\').value = \'Rate: 1 Bottle = 10m\';\n        document.getElementById(\'test-l3\').value = \'Session Bottles: 0  \';\n    } else if (preset === \'gate\') {\n        document.getElementById(\'test-l0\').value = \'=== VMC ECO-VENDO ==\';\n        document.getElementById(\'test-l1\').value = \'GATE OPEN: INSERT...\';\n        document.getElementById(\'test-l2\').value = \'Drop within 60s     \';\n        document.getElementById(\'test-l3\').value = \'Session Bottles: 0  \';\n    } else if (preset === \'reject\') {\n        document.getElementById(\'test-l0\').value = \'=== VMC ECO-VENDO ==\';\n        document.getElementById(\'test-l1\').value = \'STATUS: REJECTED!   \';\n        document.getElementById(\'test-l2\').value = \'Tin Can Detected    \';\n        document.getElementById(\'test-l3\').value = \'Please Remove Item  \';\n    } else if (preset === \'full\') {\n        document.getElementById(\'test-l0\').value = \'=== VMC ECO-VENDO ==\';\n        document.getElementById(\'test-l1\').value = \'STATUS: STORAGE FULL\';\n        document.getElementById(\'test-l2\').value = \'Empty Bin Required  \';\n        document.getElementById(\'test-l3\').value = \'Session Bottles: 0  \';\n    } else if (preset === \'config\') {\n        document.getElementById(\'test-l0\').value = \'=== VMC CONFIG ====\';\n        document.getElementById(\'test-l1\').value = \'WIFI: VMC-Config    \';\n        document.getElementById(\'test-l2\').value = \'IP: 192.168.4.1     \';\n        document.getElementById(\'test-l3\').value = \'Port: 80 / AP Active\';\n    }\n    sendCustomLcd();\n}\n\nfunction sendCustomLcd() {\n    const l0 = document.getElementById(\'test-l0\').value;\n    const l1 = document.getElementById(\'test-l1\').value;\n    const l2 = document.getElementById(\'test-l2\').value;\n    const l3 = document.getElementById(\'test-l3\').value;\n    fetch(\'/simulator/api/lcd\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({line0: l0, line1: l1, line2: l2, line3: l3})\n    }).then(()=>syncSimulator());\n}\n</script>\n</body>\n</html>\n'
