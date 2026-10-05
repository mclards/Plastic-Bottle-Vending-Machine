#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VMC ECO-VENDO Reverse Vending Machine - System Event Logger & Chute Sequence Tracker
Strictly Python 3.5.3 compatible (NO f-strings, NO inside-function type annotations).

Authoritative Logging & Physical Chute Sequence Diagnostic Engine:
Stages:
  1. Servo Entrance Gate (PCA9685 Ch 0: 0° Closed -> 90° Open)
  2. PIR Intake (Top IR Beam / PIN_IR_TOP GPIO 32: Beam Break LOW)
  3. NIR Spectrometer (SparkFun AS7263 6-Channel: Cal-W [10 - 120] uW/cm², R/S/T/U/V/W)
  4. Scale (HX711 24-bit ADC: Weight in grams [20 - 90]g)
  5. Drop Exit Servo (PCA9685 Ch 1: 90° Drop Accept / 0° Chute Return)
  6. PIR Drop (Bottom IR Beam / PIN_IR_BOTTOM GPIO 33: Beam Break LOW Transit Verified)
"""

import os
import sys
import time
import json
import re
import threading
from datetime import datetime
from collections import deque

# Determine persistent log file path
_DEFAULT_LOG_DIR = '/opt/ecofi' if sys.platform.startswith('linux') else os.path.dirname(os.path.abspath(__file__))
_LOG_FILE_PATH = os.path.join(_DEFAULT_LOG_DIR, 'system_events.log')


def _format_now_ms():
    """Returns YYYY-MM-DD HH:MM:SS.mmm without matching regex word boundaries."""
    n = datetime.now()
    return n.strftime('%Y-%m-%d %H:%M:%S') + '.{:03d}'.format(int(n.microsecond / 1000))


def _format_time_ms():
    """Returns HH:MM:SS.mmm."""
    n = datetime.now()
    return n.strftime('%H:%M:%S') + '.{:03d}'.format(int(n.microsecond / 1000))


class SystemLogger(object):
    """
    Thread-safe system event logger with millisecond precision timestamps,
    persistent file append, and high-speed in-memory circular buffer for UI polling.
    """

    def __init__(self, log_path=None, maxlen=1000):
        self.log_path = log_path or _LOG_FILE_PATH
        self.maxlen = maxlen
        self._lock = threading.Lock()
        self._counter = 0
        self._entries = deque(maxlen=self.maxlen)
        self._init_file()

    def _init_file(self):
        try:
            log_dir = os.path.dirname(self.log_path)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir)
            if not os.path.exists(self.log_path):
                with open(self.log_path, 'a', encoding='utf-8') as f:
                    now_str = _format_now_ms()
                    f.write("{} [INFO] [SYSTEM] System event logger initialized\n".format(now_str))
            else:
                # Preload existing historical lines into in-memory ring buffer
                with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                for line in lines[-self.maxlen:]:
                    line = line.strip()
                    if not line:
                        continue
                    m = re.match(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+\[([A-Z]+)\]\s+\[([A-Z0-9_\-]+)\]\s+(.*)$', line)
                    if m:
                        ts_str = m.group(1)
                        lvl = m.group(2)
                        cat = m.group(3)
                        body = m.group(4)
                        details = {}
                        if ' | details=' in body:
                            parts = body.split(' | details=', 1)
                            body = parts[0]
                            try:
                                details = json.loads(parts[1])
                            except Exception:
                                pass
                        self._counter += 1
                        self._entries.append({
                            'id': self._counter,
                            'timestamp': ts_str,
                            'ts': ts_str,
                            'epoch': 0.0,
                            'level': lvl,
                            'lvl': lvl,
                            'category': cat,
                            'cat': cat,
                            'message': body,
                            'msg': body,
                            'raw': line,
                            'details': details
                        })
        except Exception as e:
            sys.stderr.write("SystemLogger init file error: {}\n".format(e))

    def log(self, category, message, details=None, level='INFO'):
        """
        Record a fine-timestamped event.
        level: INFO, TRIGGER, MEASURE, SUCCESS, WARN, ERROR, DEBUG
        category: CHUTE, SENSOR, ACTUATOR, SESSION, AUTH, SYSTEM, NETWORK
        """
        ts_str = _format_now_ms()
        epoch_ts = time.time()

        with self._lock:
            self._counter += 1
            entry_id = self._counter
            entry = {
                'id': entry_id,
                'timestamp': ts_str,
                'ts': ts_str,
                'epoch': epoch_ts,
                'level': str(level).upper(),
                'lvl': str(level).upper(),
                'category': str(category).upper(),
                'cat': str(category).upper(),
                'message': str(message),
                'msg': str(message),
                'raw': "{} [{}] [{}] {}".format(ts_str, str(level).upper(), str(category).upper(), message),
                'details': details if isinstance(details, dict) else {}
            }
            self._entries.append(entry)

        # Append to disk log file
        try:
            detail_str = ""
            if details and isinstance(details, dict):
                detail_str = " | details=" + json.dumps(details)
            line = "{} [{}] [{}] {}{}\n".format(ts_str, level.upper(), category.upper(), message, detail_str)
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(line)
        except Exception:
            pass

        return entry

    def trigger(self, category, message, details=None):
        return self.log(category, message, details, level='TRIGGER')

    def measure(self, category, message, details=None):
        return self.log(category, message, details, level='MEASURE')

    def info(self, category, message, details=None):
        return self.log(category, message, details, level='INFO')

    def success(self, category, message, details=None):
        return self.log(category, message, details, level='SUCCESS')

    def warn(self, category, message, details=None):
        return self.log(category, message, details, level='WARN')

    def error(self, category, message, details=None):
        return self.log(category, message, details, level='ERROR')

    def debug(self, category, message, details=None):
        return self.log(category, message, details, level='DEBUG')

    def get_entries(self, since_id=0, limit=200, category=None, level=None, search=None):
        """Retrieve recent entries matching optional filters."""
        result = []
        cat_filter = str(category).upper() if category and category != 'ALL' else None
        lvl_filter = str(level).upper() if level and level != 'ALL' else None
        search_filter = str(search).lower() if search else None

        with self._lock:
            for item in self._entries:
                if item['id'] <= since_id:
                    continue
                if cat_filter and item['category'] != cat_filter:
                    continue
                if lvl_filter and item['level'] != lvl_filter:
                    continue
                if search_filter:
                    msg_txt = (item['message'] + ' ' + json.dumps(item.get('details', {}))).lower()
                    if search_filter not in msg_txt:
                        continue
                result.append(item)

        if limit and len(result) > limit:
            result = result[-limit:]
        return result

    def clear(self):
        with self._lock:
            self._entries.clear()
        try:
            with open(self.log_path, 'w', encoding='utf-8') as f:
                now_str = _format_now_ms()
                f.write("{} [INFO] [SYSTEM] System log cleared by administrator\n".format(now_str))
        except Exception:
            pass
        return True

    def get_file_path(self):
        return self.log_path


class ChuteSequenceTracker(object):
    """
    Live state engine tracking the 6 physical scanning stages:
      Stage 1: Servo Entrance Gate (Channel 0: 0° / 90°)
      Stage 2: PIR Intake (Top IR Beam: HIGH Clear / LOW Triggered)
      Stage 3: Scale HX711 (Mass in grams [20 - 90]g)
      Stage 4: NIR Spectrometer (Cal-W [10 - 120], R, S, T, U, V, W)
      Stage 5: Drop Exit Servo (Channel 1: 90° Accept / 0° Reject)
      Stage 6: PIR Drop (Bottom IR Beam: LOW Transit Verified)
    """

    def __init__(self, logger=None):
        self.logger = logger or get_system_logger()
        self._lock = threading.Lock()
        self.status = "IDLE"  # IDLE, SCANNING, PASSED, REJECTED, TIMEOUT
        self.current_stage = 0
        self.active_session_id = None
        self.start_epoch = 0.0
        self.last_event_epoch = 0.0
        self.total_elapsed_ms = 0
        self.rejection_reason = None
        self.rejection_code = None
        self.bottles_processed = 0

        self.stages = self._build_default_stages()

    def _build_default_stages(self):
        return {
            '1_gate': {
                'title': 'Servo Entrance Gate',
                'description': 'PCA9685 Channel 0 entrance hatch',
                'status': 'idle',  # idle, active, passed, failed
                'angle': 0,
                'target_angle': 90,
                'timestamp': None,
                'latency_ms': 0
            },
            '2_intake': {
                'title': 'PIR Intake (Top IR)',
                'description': 'E18-D80NK beam break (GPIO 32)',
                'status': 'idle',
                'raw_state': 'HIGH (Clear)',
                'triggered': False,
                'timestamp': None,
                'latency_ms': 0
            },
            '3_scale': {
                'title': 'Scale (HX711)',
                'description': '24-bit ADC load cell cradle',
                'status': 'idle',
                'weight_g': 0.0,
                'bounds': [20.0, 90.0],
                'is_valid': None,
                'timestamp': None,
                'latency_ms': 0
            },
            '4_nir': {
                'title': 'NIR Spectrometer',
                'description': 'SparkFun AS7263 multi-spectral sensor',
                'status': 'idle',
                'cal_w': 0.0,
                'bounds': [10.0, 120.0],
                'channels': {'r': 0, 's': 0, 't': 0, 'u': 0, 'v': 0, 'w': 0},
                'ratios': {'wv': 0.0, 'sr': 0.0, 'tw': 0.0},
                'is_pet': None,
                'timestamp': None,
                'latency_ms': 0
            },
            '5_exit': {
                'title': 'Drop Exit Servo',
                'description': 'PCA9685 Channel 1 routing flap',
                'status': 'idle',
                'angle': 0,
                'action': None,  # ACCEPT, REJECT
                'timestamp': None,
                'latency_ms': 0
            },
            '6_drop': {
                'title': 'PIR Drop (Bottom IR)',
                'description': 'E18-D80NK gravitational transit (GPIO 33)',
                'status': 'idle',
                'transit_verified': False,
                'timestamp': None,
                'latency_ms': 0
            }
        }

    def reset(self):
        with self._lock:
            self.status = "IDLE"
            self.current_stage = 0
            self.active_session_id = None
            self.start_epoch = 0.0
            self.last_event_epoch = 0.0
            self.total_elapsed_ms = 0
            self.rejection_reason = None
            self.rejection_code = None
            self.stages = self._build_default_stages()
        self.logger.info("CHUTE", "Chute sequence monitor reset to IDLE")
        return self.get_state()

    def on_gate_open(self, session_id=None, angle=90):
        """Stage 1: Entrance gate opens."""
        now = time.time()
        now_str = _format_time_ms()
        with self._lock:
            self.status = "SCANNING"
            self.current_stage = 1
            self.active_session_id = session_id or self.active_session_id or 'LIVE_SESSION'
            self.start_epoch = now
            self.last_event_epoch = now
            self.total_elapsed_ms = 0
            self.rejection_reason = None
            self.rejection_code = None

            # Reset subsequent stages
            for k in ['2_intake', '3_scale', '4_nir', '5_exit', '6_drop']:
                self.stages[k]['status'] = 'waiting'
                self.stages[k]['timestamp'] = None
                self.stages[k]['latency_ms'] = 0

            self.stages['1_gate']['status'] = 'active'
            self.stages['1_gate']['angle'] = angle
            self.stages['1_gate']['timestamp'] = now_str
            self.stages['1_gate']['latency_ms'] = 0

        self.logger.trigger("CHUTE", "STAGE 1: Servo Entrance Gate OPENED (Angle: {}°)".format(angle),
                            {'stage': 1, 'angle': angle, 'session_id': session_id})

    def on_intake_triggered(self, session_id=None):
        """Stage 2: PIR Intake (Top IR) triggers."""
        now = time.time()
        now_str = _format_time_ms()
        with self._lock:
            delta_ms = int((now - self.last_event_epoch) * 1000) if self.last_event_epoch > 0 else 0
            self.last_event_epoch = now
            self.total_elapsed_ms = int((now - self.start_epoch) * 1000) if self.start_epoch > 0 else 0

            self.current_stage = 2
            self.stages['1_gate']['status'] = 'passed'

            self.stages['2_intake']['status'] = 'passed'
            self.stages['2_intake']['raw_state'] = 'LOW (Triggered)'
            self.stages['2_intake']['triggered'] = True
            self.stages['2_intake']['timestamp'] = now_str
            self.stages['2_intake']['latency_ms'] = delta_ms

            self.stages['3_scale']['status'] = 'active'

        self.logger.trigger("SENSOR", "STAGE 2: PIR Intake (Top IR Beam) TRIGGERED - Bottle inserted (+{}ms)".format(delta_ms),
                            {'stage': 2, 'latency_ms': delta_ms})

    def on_nir_scan(self, nir_data):
        """Stage 4: NIR Spectrometer measurement (v2.3.18 flow)."""
        now = time.time()
        now_str = _format_time_ms()
        cal_w = float(nir_data.get('calibrated_w', 0.0) or 0.0)
        is_pet = bool(nir_data.get('is_pet', False))
        channels = {
            'r': int(nir_data.get('r', 0) or 0),
            's': int(nir_data.get('s', 0) or 0),
            't': int(nir_data.get('t', 0) or 0),
            'u': int(nir_data.get('u', 0) or 0),
            'v': int(nir_data.get('v', 0) or 0),
            'w': int(nir_data.get('w', 0) or 0)
        }
        ratios = {
            'wv': round(float(nir_data.get('wv_ratio', 0.0) or 0.0), 2),
            'sr': round(float(nir_data.get('sr_ratio', 0.0) or 0.0), 2),
            'tw': round(float(nir_data.get('tw_ratio', 0.0) or 0.0), 2)
        }

        with self._lock:
            delta_ms = int((now - self.last_event_epoch) * 1000) if self.last_event_epoch > 0 else 0
            self.last_event_epoch = now
            self.total_elapsed_ms = int((now - self.start_epoch) * 1000) if self.start_epoch > 0 else 0

            self.current_stage = 4
            self.stages['4_nir']['status'] = 'passed' if is_pet else 'failed'
            self.stages['4_nir']['cal_w'] = round(cal_w, 2)
            self.stages['4_nir']['channels'] = channels
            self.stages['4_nir']['ratios'] = ratios
            self.stages['4_nir']['is_pet'] = is_pet
            self.stages['4_nir']['timestamp'] = now_str
            self.stages['4_nir']['latency_ms'] = delta_ms

            if is_pet:
                self.stages['5_exit']['status'] = 'active'
            else:
                self.status = "REJECTED"
                self.rejection_reason = nir_data.get('reason', 'NIR Spectrum Rejection')
                self.rejection_code = nir_data.get('reason_code', 'invalid_nir')

        verdict = "PET Plastic Confirmed" if is_pet else "REJECT ({})".format(nir_data.get('reason', 'Non-PET'))
        self.logger.measure("SENSOR", "STAGE 4: NIR Spectrometer Scan - Cal-W={:.2f} µW/cm² -> {} (+{}ms)".format(
            cal_w, verdict, delta_ms), {'stage': 4, 'cal_w': cal_w, 'is_pet': is_pet, 'latency_ms': delta_ms, 'channels': channels})

    def on_weight_scan(self, weight_data):
        """Stage 3: Scale HX711 measurement (v2.3.18 flow)."""
        now = time.time()
        now_str = _format_time_ms()
        weight_g = float(weight_data.get('weight_g', 0.0) or 0.0)
        is_valid = bool(weight_data.get('is_valid', True))
        if 'is_valid' not in weight_data:
            bounds = self.stages['3_scale']['bounds']
            is_valid = (bounds[0] <= weight_g <= bounds[1])

        with self._lock:
            delta_ms = int((now - self.last_event_epoch) * 1000) if self.last_event_epoch > 0 else 0
            self.last_event_epoch = now
            self.total_elapsed_ms = int((now - self.start_epoch) * 1000) if self.start_epoch > 0 else 0

            self.current_stage = 3
            self.stages['3_scale']['status'] = 'passed' if is_valid else 'failed'
            self.stages['3_scale']['weight_g'] = round(weight_g, 1)
            self.stages['3_scale']['is_valid'] = is_valid
            self.stages['3_scale']['timestamp'] = now_str
            self.stages['3_scale']['latency_ms'] = delta_ms

            if is_valid:
                self.stages['4_nir']['status'] = 'active'
            else:
                self.status = "REJECTED"
                self.rejection_reason = "Weight Out of Bounds ({:.1f}g)".format(weight_g)
                self.rejection_code = "invalid_weight"

        verdict = "Weight Authentic" if is_valid else "Weight Out of Bounds"
        self.logger.measure("SENSOR", "STAGE 3: Scale (HX711) - Mass: {:.1f}g -> {} (+{}ms)".format(
            weight_g, verdict, delta_ms), {'stage': 3, 'weight_g': weight_g, 'is_valid': is_valid, 'latency_ms': delta_ms})

    def on_drop_actuated(self, action='ACCEPT', angle=90):
        """Stage 5: Drop exit servo actuated."""
        now = time.time()
        now_str = _format_time_ms()
        with self._lock:
            delta_ms = int((now - self.last_event_epoch) * 1000) if self.last_event_epoch > 0 else 0
            self.last_event_epoch = now
            self.total_elapsed_ms = int((now - self.start_epoch) * 1000) if self.start_epoch > 0 else 0

            self.current_stage = 5
            self.stages['5_exit']['status'] = 'passed' if action == 'ACCEPT' else 'active'
            self.stages['5_exit']['angle'] = angle
            self.stages['5_exit']['action'] = action
            self.stages['5_exit']['timestamp'] = now_str
            self.stages['5_exit']['latency_ms'] = delta_ms

            self.stages['6_drop']['status'] = 'active'

        self.logger.trigger("ACTUATOR", "STAGE 5: Drop Exit Servo ACTUATED - Action: {} (Angle: {}°) (+{}ms)".format(
            action, angle, delta_ms), {'stage': 5, 'action': action, 'angle': angle, 'latency_ms': delta_ms})

    def on_bottle_dropped(self, bottles_count=1):
        """Stage 6: PIR Drop (Bottom IR) confirms bottle transit into storage bin."""
        now = time.time()
        now_str = _format_time_ms()
        with self._lock:
            delta_ms = int((now - self.last_event_epoch) * 1000) if self.last_event_epoch > 0 else 0
            self.last_event_epoch = now
            self.total_elapsed_ms = int((now - self.start_epoch) * 1000) if self.start_epoch > 0 else 0

            self.current_stage = 6
            self.status = "PASSED"
            self.bottles_processed += bottles_count

            self.stages['6_drop']['status'] = 'passed'
            self.stages['6_drop']['transit_verified'] = True
            self.stages['6_drop']['timestamp'] = now_str
            self.stages['6_drop']['latency_ms'] = delta_ms

        self.logger.success("CHUTE", "STAGE 6: PIR Drop (Bottom IR Beam) TRANSIT CONFIRMED - Bottle saved! Total: {}ms".format(
            self.total_elapsed_ms), {'stage': 6, 'total_elapsed_ms': self.total_elapsed_ms, 'latency_ms': delta_ms})

    def on_rejected(self, reason='Item Rejected', code=None):
        """Item rejected event."""
        now = time.time()
        now_str = _format_time_ms()
        with self._lock:
            delta_ms = int((now - self.last_event_epoch) * 1000) if self.last_event_epoch > 0 else 0
            self.status = "REJECTED"
            self.rejection_reason = reason
            self.rejection_code = code or 'item_rejected'
            self.stages['5_exit']['action'] = 'REJECT'
            self.stages['5_exit']['status'] = 'failed'
            self.stages['5_exit']['timestamp'] = now_str
            self.stages['5_exit']['latency_ms'] = delta_ms

        self.logger.warn("CHUTE", "Chute Classification REJECTED: {} (+{}ms)".format(reason, delta_ms),
                         {'reason': reason, 'code': code, 'latency_ms': delta_ms})

    def on_timeout(self, reason='Intake Timeout'):
        """Timeout reached."""
        with self._lock:
            self.status = "TIMEOUT"
            self.rejection_reason = reason
            self.rejection_code = "timeout"

        self.logger.warn("CHUTE", "Chute Sequence TIMEOUT: {}".format(reason), {'reason': reason})

    def on_gate_closed(self):
        """Entrance gate closed."""
        with self._lock:
            self.stages['1_gate']['angle'] = 0
            if self.status == 'SCANNING' and self.current_stage == 1:
                self.stages['1_gate']['status'] = 'passed'

        self.logger.info("CHUTE", "Servo Entrance Gate CLOSED (Angle: 0°)")

    def get_state(self):
        """Returns deep snapshot of current state."""
        with self._lock:
            now = time.time()
            elapsed = self.total_elapsed_ms
            if self.status == 'SCANNING' and self.start_epoch > 0:
                elapsed = int((now - self.start_epoch) * 1000)

            recent_logs = self.logger.get_entries(limit=40, category='CHUTE')
            if len(recent_logs) < 15:
                recent_logs = self.logger.get_entries(limit=40)

            st_copy = json.loads(json.dumps(self.stages))
            for k, st in st_copy.items():
                st['elapsed_ms'] = st.get('latency_ms', 0)
                if k == '1_gate':
                    st['detail'] = 'Open ({}°)'.format(st.get('angle', 0)) if st.get('angle', 0) > 0 else 'Closed (0°)'
                elif k == '2_intake':
                    st['detail'] = 'Intrusion (LOW)' if st.get('triggered') else 'Clear (HIGH)'
                elif k == '3_scale':
                    st['detail'] = 'Mass: {:.1f}g ({})'.format(st.get('weight_g', 0.0), 'Valid' if st.get('is_valid') else 'Wait')
                elif k == '4_nir':
                    st['detail'] = 'Cal-W: {:.1f} uW/cm2 ({})'.format(st.get('cal_w', 0.0), 'PET' if st.get('is_pet') else 'REJECT')
                elif k == '5_exit':
                    st['detail'] = '{} ({}°)'.format(st.get('action') or 'Flap', st.get('angle', 0))
                elif k == '6_drop':
                    st['detail'] = 'Transit OK (+1)' if st.get('transit_verified') else 'Awaiting Fall'

            events_copy = []
            for r in recent_logs:
                events_copy.append({
                    'id': r.get('id'),
                    'ts': r.get('timestamp') or r.get('ts'),
                    'timestamp': r.get('timestamp'),
                    'lvl': r.get('level') or r.get('lvl'),
                    'level': r.get('level'),
                    'cat': r.get('category') or r.get('cat'),
                    'category': r.get('category'),
                    'stage': r.get('category') or 'SYS',
                    'msg': r.get('message') or r.get('msg'),
                    'message': r.get('message'),
                    'raw': r.get('raw') or "{} [{}] [{}] {}".format(r.get('timestamp'), r.get('level'), r.get('category'), r.get('message'))
                })

            return {
                'status': self.status,
                'current_stage': self.current_stage,
                'active_session_id': self.active_session_id,
                'total_elapsed_ms': elapsed,
                'rejection_reason': self.rejection_reason,
                'rejection_code': self.rejection_code,
                'bottles_processed': self.bottles_processed,
                'stages': st_copy,
                'events': events_copy,
                'recent_logs': events_copy
            }

    def simulate_scan(self, is_pet=True, weight=35.0, cal_w=48.5, delay_ms=400):
        """
        Executes a background simulation of the 6-stage physical scanning sequence.
        Permits testing and visual verification without physical hardware actuation.
        """
        def _sim_worker():
            try:
                sec = delay_ms / 1000.0
                # Stage 1: Entrance Gate Open
                self.on_gate_open('SIM_SESSION', angle=90)
                time.sleep(sec * 0.9)

                # Stage 2: PIR Intake Trigger
                self.on_intake_triggered('SIM_SESSION')
                time.sleep(sec * 0.6)

                # Stage 3: Scale Weight Scan (v2.3.18 flow: evaluated first)
                scale_payload = {
                    'weight_g': weight,
                    'is_valid': (20.0 <= weight <= 90.0)
                }
                self.on_weight_scan(scale_payload)
                time.sleep(sec * 0.6)

                if not scale_payload['is_valid']:
                    self.on_drop_actuated('REJECT', angle=0)
                    time.sleep(sec * 0.5)
                    self.on_rejected('Weight Out of Range ({:.1f}g)'.format(weight), 'weight_reject')
                    time.sleep(sec * 0.5)
                    self.on_gate_closed()
                    return

                # Stage 4: NIR Spectrometer Scan (evaluated on valid-weight items)
                nir_payload = {
                    'calibrated_w': cal_w,
                    'is_pet': is_pet,
                    'reason': 'Authentic Clear PET' if is_pet else 'Non-PET Reflection Profile',
                    'reason_code': 'clear_pet' if is_pet else 'opaque_material',
                    'r': 245 if is_pet else 890,
                    's': 310 if is_pet else 1200,
                    't': 450 if is_pet else 1800,
                    'u': 520 if is_pet else 2200,
                    'v': 680 if is_pet else 3100,
                    'w': int(cal_w * 10),
                    'wv_ratio': 1.05 if is_pet else 0.45,
                    'sr_ratio': 1.26 if is_pet else 1.34,
                    'tw_ratio': 0.92 if is_pet else 0.58
                }
                self.on_nir_scan(nir_payload)
                time.sleep(sec * 0.6)

                if not is_pet:
                    # Non-PET rejection flow
                    self.on_drop_actuated('REJECT', angle=0)
                    time.sleep(sec * 0.5)
                    self.on_rejected('Non-PET Reflection Profile', 'nir_reject')
                    time.sleep(sec * 0.5)
                    self.on_gate_closed()
                    return

                # Stage 5: Drop Exit Flap Actuation
                self.on_drop_actuated('ACCEPT', angle=90)
                time.sleep(sec * 0.8)

                # Stage 6: PIR Drop Bottom IR Transit Confirmation
                self.on_bottle_dropped(1)
                time.sleep(sec * 0.5)
                self.on_gate_closed()
            except Exception as ex:
                self.logger.error("CHUTE", "Simulation worker error: {}".format(ex))

        t = threading.Thread(target=_sim_worker)
        t.daemon = True
        t.start()
        return True


# Global Singletons
_LOGGER_INSTANCE = None
_TRACKER_INSTANCE = None
_INIT_LOCK = threading.Lock()

def get_system_logger():
    global _LOGGER_INSTANCE
    if _LOGGER_INSTANCE is None:
        with _INIT_LOCK:
            if _LOGGER_INSTANCE is None:
                _LOGGER_INSTANCE = SystemLogger()
    return _LOGGER_INSTANCE

def get_chute_tracker():
    global _TRACKER_INSTANCE
    if _TRACKER_INSTANCE is None:
        with _INIT_LOCK:
            if _TRACKER_INSTANCE is None:
                _TRACKER_INSTANCE = ChuteSequenceTracker(get_system_logger())
    return _TRACKER_INSTANCE
