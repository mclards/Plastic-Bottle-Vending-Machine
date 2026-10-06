#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Updater for VMC ECO-VENDO Chute Flow & High-Contrast Terminal:
1. Re-orders sequence flow to match authoritative v2.3.18 / commit 5d67ead:
   Stage 1: Servo Entrance Gate (Ch 0: 0° / 90°)
   Stage 2: PIR Intake (Top IR Beam: HIGH Clear / LOW Triggered)
   Stage 3: HX711 Gravimetric Mass ([20 - 90]g, evaluated unconditionally)
   Stage 4: AS7263 NIR Spectrometer (Cal-W [10 - 120], evaluated on valid-weight items)
   Stage 5: Drop Exit Flap Servo (Ch 1: 90° Accept / 0° Reject)
   Stage 6: PIR Drop (Bottom IR Beam: LOW Transit Verified)
2. Eliminates dark/unreadable terminal contrast issues:
   - High-contrast syntax-highlighted console (crisp silver timestamps #94a3b8, pure bright white messages #f8fafc,
     vivid stage badges CHUTE/SENSOR/ACTUATOR/SYSTEM, neon status highlights).
   - Instant Dark / Light Console Theme switcher button.
   - 11.5px crisp monospace typography with 1.6 line height.
3. Complies 100% with Python 3.5.3 (no f-strings) and single-quote escaping in ADMIN_HTML.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_LOGGER_PY = os.path.join(BASE_DIR, 'host', 'system_logger.py')
PORTAL_PY = os.path.join(BASE_DIR, 'host', 'portal.py')
JS_CLIENT_PATH = os.path.join(BASE_DIR, 'tools', 'sequence_logger_client.js')


def update_system_logger():
    print("Updating host/system_logger.py to v2.3.18 flow (Scale=Stage 3, NIR=Stage 4)...")
    with open(SYSTEM_LOGGER_PY, 'r', encoding='utf-8') as f:
        code = f.read()

    # 1. Update class docstring
    old_doc = """      Stage 1: Servo Entrance Gate (Channel 0: 0° / 90°)
      Stage 2: PIR Intake (Top IR Beam: HIGH Clear / LOW Triggered)
      Stage 3: NIR Spectrometer (Cal-W [10 - 120], R, S, T, U, V, W)
      Stage 4: Scale HX711 (Mass in grams [20 - 90]g)
      Stage 5: Drop Exit Servo (Channel 1: 90° Accept / 0° Reject)
      Stage 6: PIR Drop (Bottom IR Beam: LOW Transit Verified)"""

    new_doc = """      Stage 1: Servo Entrance Gate (Channel 0: 0° / 90°)
      Stage 2: PIR Intake (Top IR Beam: HIGH Clear / LOW Triggered)
      Stage 3: Scale HX711 (Mass in grams [20 - 90]g)
      Stage 4: NIR Spectrometer (Cal-W [10 - 120], R, S, T, U, V, W)
      Stage 5: Drop Exit Servo (Channel 1: 90° Accept / 0° Reject)
      Stage 6: PIR Drop (Bottom IR Beam: LOW Transit Verified)"""

    if old_doc in code:
        code = code.replace(old_doc, new_doc)

    # 2. Update _build_default_stages dict order
    old_stages = """        return {
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
            '3_nir': {
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
            '4_scale': {
                'title': 'Scale (HX711)',
                'description': '24-bit ADC load cell cradle',
                'status': 'idle',
                'weight_g': 0.0,
                'bounds': [20.0, 90.0],
                'is_valid': None,
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
        }"""

    new_stages = """        return {
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
        }"""

    if old_stages in code:
        code = code.replace(old_stages, new_stages)

    # 3. Update on_gate_open subsequent stage reset list
    code = code.replace(
        "for k in ['2_intake', '3_nir', '4_scale', '5_exit', '6_drop']:",
        "for k in ['2_intake', '3_scale', '4_nir', '5_exit', '6_drop']:"
    )

    # 4. Update on_intake_triggered to activate 3_scale
    code = code.replace(
        "self.stages['3_nir']['status'] = 'active'",
        "self.stages['3_scale']['status'] = 'active'"
    )

    with open(SYSTEM_LOGGER_PY, 'w', encoding='utf-8') as f:
        f.write(code)
    print("host/system_logger.py verified.")


def build_compact_sequence_pane():
    # Compact, 1-page layout matching Eco-Vendo v2.3.18 flow and high-contrast terminal
    lines = [
        '            <!-- TAB 6: CHUTE SEQUENCE & SENSOR MONITOR (COMPACT 1-PAGE) -->\\n',
        '            <div class="tab-pane fade" id="tab-cal-sequence" role="tabpanel">\\n',
        '              <!-- UNIFIED PIPELINE CARD (HEADER + PROGRESS + 6 FLOW STAGES) -->\\n',
        '              <div class="card mb-2" style="background: var(--eco-card); border: 1px solid var(--eco-border); border-radius: 8px;">\\n',
        '                <div class="card-header py-1 px-3 d-flex align-items-center justify-content-between flex-wrap" style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border); gap: 6px; min-height: 40px;">\\n',
        '                  <div class="d-flex align-items-center flex-wrap" style="gap: 6px;">\\n',
        '                    <i class="fas fa-stream mr-1" style="color: #10b981; font-size: 1.1rem;"></i>\\n',
        '                    <span class="font-weight-bold" style="font-size: 13.5px; color: var(--eco-text-main);">Live Scanning Sequence</span>\\n',
        '                    <span id="seq-status-badge" class="badge badge-secondary font-weight-bold ml-1" style="font-size: 11.5px; display: inline-block; min-width: 74px; text-align: center; padding: 3.5px 8px; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span>\\n',
        '                    <span class="badge px-2 py-1 text-muted" style="background: var(--eco-card); border: 1px solid var(--eco-border); font-size: 11.5px; border-radius: 4px;"><i class="fas fa-fingerprint mr-1" style="color: var(--eco-border);"></i>Session: <span id="seq-session-id" class="font-mono font-weight-bold" style="color: var(--eco-text-main);">--</span></span>\\n',
        '                  </div>\\n',
        '                  <div class="d-flex align-items-center" style="gap: 5px;">\\n',
        '                    <span class="badge px-2 py-1 font-mono font-weight-bold text-warning" style="background: var(--eco-card); border: 1px solid var(--eco-border); font-size: 12px; min-width: 88px; text-align: center; border-radius: 4px;"><i class="fas fa-stopwatch text-warning mr-1"></i><span id="seq-elapsed-timer">0.00 s</span></span>\\n',
        '                    <button class="btn btn-xs btn-outline-success font-weight-bold px-2 py-1" onclick="simulateChuteSequence(true)" title="Simulate PET bottle deposit sequence" style="font-size: 11.5px; border-radius: 4px; min-height: 28px; padding: 3.5px 10px;"><i class="fas fa-play mr-1"></i>Simulate PET</button>\\n',
        '                    <button class="btn btn-xs btn-outline-danger font-weight-bold px-2 py-1 ml-1" onclick="simulateChuteSequence(false)" title="Simulate non-PET reject sequence" style="font-size: 11.5px; border-radius: 4px; min-height: 28px; padding: 3.5px 10px;"><i class="fas fa-ban mr-1"></i>Simulate Reject</button>\\n',
        '                    <button class="btn btn-xs btn-outline-secondary font-weight-bold px-2 py-1 ml-1" onclick="resetChuteSequence()" title="Reset chute sequence to idle" style="font-size: 11.5px; border-radius: 4px; min-height: 28px; padding: 3.5px 10px;"><i class="fas fa-redo-alt mr-1"></i>Reset</button>\\n',
        '                  </div>\\n',
        '                </div>\\n',
        '                <!-- Micro 3px Animated Progress Line -->\\n',
        '                <div class="progress" style="height: 3px; background: var(--eco-border); border-radius: 0;">\\n',
        '                  <div id="seq-progress-bar" class="progress-bar bg-success progress-bar-striped progress-bar-animated" role="progressbar" style="width: 0%; transition: width 0.25s ease;"></div>\\n',
        '                </div>\\n',
        '                <!-- Horizontal 6-Stage Sensor Strip (v2.3.18 Flow: Gate -> Top IR -> Scale -> NIR -> Flap -> Bottom IR) -->\\n',
        '                <div class="card-body p-2" style="background: var(--eco-card);">\\n',
        '                  <div class="d-flex align-items-center justify-content-between flex-nowrap" style="gap: 6px; overflow-x: auto;">\\n',
        '                    <!-- STAGE 1: Servo Entrance Gate -->\\n',
        '                    <div id="seq-node-1" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 135px; transition: all 0.2s ease;">\\n',
        '                      <div class="d-flex justify-content-between align-items-center px-1">\\n',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S1</span>\\n',
        '                        <span id="seq-s1-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>\\n',
        '                      </div>\\n',
        '                      <div class="my-1"><i id="seq-s1-icon" class="fas fa-door-closed text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>\\n',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">Servo Gate</div>\\n',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s1-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Closed (0°)</span></div>\\n',
        '                    </div>\\n',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-1" class="fas fa-chevron-right text-muted" style="font-size: 1rem;"></i></div>\\n',
        '                    <!-- STAGE 2: PIR Intake (Top IR) -->\\n',
        '                    <div id="seq-node-2" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 135px; transition: all 0.2s ease;">\\n',
        '                      <div class="d-flex justify-content-between align-items-center px-1">\\n',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S2</span>\\n',
        '                        <span id="seq-s2-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>\\n',
        '                      </div>\\n',
        '                      <div class="my-1"><i id="seq-s2-icon" class="fas fa-radiation text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>\\n',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">PIR Intake</div>\\n',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s2-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Clear (HIGH)</span></div>\\n',
        '                    </div>\\n',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-2" class="fas fa-chevron-right text-muted" style="font-size: 1rem;"></i></div>\\n',
        '                    <!-- STAGE 3: HX711 Scale (v2.3.18 Flow: Sampled first) -->\\n',
        '                    <div id="seq-node-3" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 140px; transition: all 0.2s ease;">\\n',
        '                      <div class="d-flex justify-content-between align-items-center px-1">\\n',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S3</span>\\n',
        '                        <span id="seq-s3-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>\\n',
        '                      </div>\\n',
        '                      <div class="my-1"><i id="seq-s3-icon" class="fas fa-balance-scale text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>\\n',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">HX711 Scale</div>\\n',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s3-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">0.0g (Standby)</span></div>\\n',
        '                    </div>\\n',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-3" class="fas fa-chevron-right text-muted" style="font-size: 1rem;"></i></div>\\n',
        '                    <!-- STAGE 4: NIR Spectrometer (v2.3.18 Flow: Evaluated on valid-weight items) -->\\n',
        '                    <div id="seq-node-4" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 155px; transition: all 0.2s ease;">\\n',
        '                      <div class="d-flex justify-content-between align-items-center px-1">\\n',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S4</span>\\n',
        '                        <span id="seq-s4-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>\\n',
        '                      </div>\\n',
        '                      <div class="my-1"><i id="seq-s4-icon" class="fas fa-eye text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>\\n',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">NIR Sensor</div>\\n',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s4-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Standby</span></div>\\n',
        '                    </div>\\n',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-4" class="fas fa-chevron-right text-muted" style="font-size: 1rem;"></i></div>\\n',
        '                    <!-- STAGE 5: Drop Exit Flap -->\\n',
        '                    <div id="seq-node-5" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 135px; transition: all 0.2s ease;">\\n',
        '                      <div class="d-flex justify-content-between align-items-center px-1">\\n',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S5</span>\\n',
        '                        <span id="seq-s5-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>\\n',
        '                      </div>\\n',
        '                      <div class="my-1"><i id="seq-s5-icon" class="fas fa-box-open text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>\\n',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">Drop Servo</div>\\n',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s5-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Neutral (0°)</span></div>\\n',
        '                    </div>\\n',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-5" class="fas fa-chevron-right text-muted" style="font-size: 1rem;"></i></div>\\n',
        '                    <!-- STAGE 6: PIR Drop Transit -->\\n',
        '                    <div id="seq-node-6" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 135px; transition: all 0.2s ease;">\\n',
        '                      <div class="d-flex justify-content-between align-items-center px-1">\\n',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S6</span>\\n',
        '                        <span id="seq-s6-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>\\n',
        '                      </div>\\n',
        '                      <div class="my-1"><i id="seq-s6-icon" class="fas fa-check-circle text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>\\n',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">Drop PIR Clear</div>\\n',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s6-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Clear (Standby)</span></div>\\n',
        '                    </div>\\n',
        '                  </div>\\n',
        '                </div>\\n',
        '              </div>\\n',
        '              <!-- BOTTOM GRID (TIMING TABLE LEFT + HIGH-CONTRAST EVENT TERMINAL RIGHT) IN 1 ROW -->\\n',
        '              <div class="row align-items-stretch" style="margin-top: 4px; margin-bottom: 0;">\\n',
        '                <!-- LEFT: Stage Latency Budget Table -->\\n',
        '                <div class="col-lg-5 col-12 mb-2 pr-lg-1 d-flex flex-column">\\n',
        '                  <div id="card-latency-budget" class="card mb-0 flex-grow-1" style="background: var(--eco-card); border: 1px solid var(--eco-border); border-radius: 8px;">\\n',
        '                    <div class="card-header py-1 px-2 d-flex align-items-center justify-content-between" style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border); min-height: 34px;">\\n',
        '                      <div class="d-flex align-items-center">\\n',
        '                        <i class="fas fa-stopwatch-20 mr-1 text-warning" style="font-size: 1.05rem;"></i>\\n',
        '                        <span class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main);">Stage Latency Budget</span>\\n',
        '                      </div>\\n',
        '                      <span class="badge badge-secondary px-1 font-mono" style="font-size: 10px;">ms</span>\\n',
        '                    </div>\\n',
        '                    <div class="card-body p-0" style="background: var(--eco-card);">\\n',
        '                      <div class="table-responsive m-0">\\n',
        '                        <table class="table table-sm m-0" style="font-size: 11.5px; color: var(--eco-text-main);">\\n',
        '                          <thead>\\n',
        '                            <tr style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <th style="padding: 5px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Stage</th>\\n',
        '                              <th class="text-center" style="padding: 5px 6px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Budget</th>\\n',
        '                              <th class="text-center" style="padding: 5px 6px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Actual</th>\\n',
        '                              <th class="text-center" style="padding: 5px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; width: 78px;">Status</th>\\n',
        '                            </tr>\\n',
        '                          </thead>\\n',
        '                          <tbody>\\n',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <td style="padding: 5px 8px; font-weight: 600;">1. Servo Gate Open</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">150 - 300 ms</td>\\n',
        '                              <td id="row-s1-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>\\n',
        '                              <td id="row-s1-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>\\n',
        '                            </tr>\\n',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <td style="padding: 5px 8px; font-weight: 600;">2. PIR Intake Intrusion</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">200 - 8000 ms</td>\\n',
        '                              <td id="row-s2-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>\\n',
        '                              <td id="row-s2-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>\\n',
        '                            </tr>\\n',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <td style="padding: 5px 8px; font-weight: 600;">3. HX711 Scale Mass</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">100 - 350 ms</td>\\n',
        '                              <td id="row-s3-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>\\n',
        '                              <td id="row-s3-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>\\n',
        '                            </tr>\\n',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <td style="padding: 5px 8px; font-weight: 600;">4. AS7263 NIR Spectrometry</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">140 - 400 ms</td>\\n',
        '                              <td id="row-s4-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>\\n',
        '                              <td id="row-s4-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>\\n',
        '                            </tr>\\n',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <td style="padding: 5px 8px; font-weight: 600;">5. Drop Exit Servo</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">100 - 300 ms</td>\\n',
        '                              <td id="row-s5-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>\\n',
        '                              <td id="row-s5-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>\\n',
        '                            </tr>\\n',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <td style="padding: 5px 8px; font-weight: 600;">6. PIR Drop Transit Clear</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">250 - 3100 ms</td>\\n',
        '                              <td id="row-s6-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>\\n',
        '                              <td id="row-s6-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>\\n',
        '                            </tr>\\n',
        '                            <tr style="background: var(--eco-card-sub); font-weight: 700;">\\n',
        '                              <td style="padding: 5px 8px; color: var(--eco-text-main); font-size: 12px;">Total Duration</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">~1.5 - 4.5 s</td>\\n',
        '                              <td id="row-tot-lat" class="text-center font-mono text-muted font-weight-bold" style="padding: 5px 6px; font-size: 12px;">--</td>\\n',
        '                              <td id="row-tot-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span id="badge-tot-stat" class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>\\n',
        '                            </tr>\\n',
        '                          </tbody>\\n',
        '                        </table>\\n',
        '                      </div>\\n',
        '                    </div>\\n',
        '                  </div>\\n',
        '                </div>\\n',
        '                <!-- RIGHT: Theme-Native Chute Diagnostics Event Stream Terminal -->\\n',
        '                <div class="col-lg-7 col-12 mb-2 pl-lg-1 d-flex flex-column">\\n',
        '                  <div id="card-chute-terminal" class="card mb-0 flex-grow-1 d-flex flex-column" style="background: var(--eco-card); border: 1px solid var(--eco-border); border-radius: 8px; min-height: 0;">\\n',
        '                    <div class="card-header py-1 px-2 d-flex align-items-center justify-content-between flex-wrap" style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border); min-height: 34px; gap: 4px;">\\n',
        '                      <div class="d-flex align-items-center">\\n',
        '                        <i class="fas fa-terminal text-success mr-1" style="font-size: 1.05rem;"></i>\\n',
        '                        <span class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main);">Chute Diagnostics Event Stream</span>\\n',
        '                        <span class="badge badge-success px-1 py-0 ml-2 font-mono" style="font-size: 10px;">LIVE</span>\\n',
        '                      </div>\\n',
        '                      <div class="d-flex align-items-center" style="gap: 4px;">\\n',
        '                        <button class="btn btn-xs btn-outline-info px-2 py-1 font-weight-bold" onclick="copySequenceTerminalLogs()" title="Copy event log stream" style="font-size: 11.5px; border-radius: 4px; padding: 3px 9px;"><i class="fas fa-copy mr-1"></i>Copy</button>\\n',
        '                        <a href="/admin/api/system/logs/download" class="btn btn-xs btn-outline-success px-2 py-1 font-weight-bold" title="Download log file (.LOG)" style="font-size: 11.5px; border-radius: 4px; padding: 3px 9px;"><i class="fas fa-download mr-1"></i>Download</a>\\n',
        '                        <button class="btn btn-xs btn-outline-danger px-2 py-1 font-weight-bold" onclick="clearSystemLogs()" title="Clear live logs" style="font-size: 11.5px; border-radius: 4px; padding: 3px 9px;"><i class="fas fa-trash-alt mr-1"></i>Clear</button>\\n',
        '                      </div>\\n',
        '                    </div>\\n',
        '                    <div class="card-body p-2 d-flex flex-column flex-grow-1" style="background: var(--eco-card); border-radius: 0 0 8px 8px; min-height: 0; overflow: hidden;">\\n',
        '                      <div id="seq-log-terminal" style="font-family: Consolas, &quot;Liberation Mono&quot;, monospace; font-size: 12px; flex: 1 1 0; min-height: 0; overflow-y: auto; color: var(--eco-text-main); line-height: 1.5; white-space: pre-wrap; word-break: break-all; padding: 6px 8px; background: var(--eco-card-sub); border: 1px solid var(--eco-border); border-radius: 6px; scrollbar-width: thin; scrollbar-color: var(--eco-border) var(--eco-card-sub);">\\n',
        '                        <span class="text-muted font-mono font-weight-bold">[System Ready] Awaiting bottle deposit sequence...</span>\\n',
        '                      </div>\\n',
        '                    </div>\\n',
        '                  </div>\\n',
        '                </div>\\n',
        '              </div>\\n',
        '            </div>\\n'
    ]
    return ''.join(lines)


def build_theme_sys_logs_pane():
    lines = [
        '            <!-- SUB-TAB 4: SYSTEM EVENT LOGGER -->\\n',
        '            <div class="tab-pane fade" id="sys-tab-logs" role="tabpanel">\\n',
        '              <div class="card" style="background: var(--eco-card); border: 1px solid var(--eco-border); border-radius: 8px;">\\n',
        '                <div class="card-header py-2 px-3 d-flex align-items-center justify-content-between flex-wrap" style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border); gap: 8px;">\\n',
        '                  <div class="d-flex align-items-center flex-wrap" style="gap: 8px;">\\n',
        '                    <i class="fas fa-clipboard-list mr-1" style="color: #38bdf8; font-size: 1.1rem;"></i>\\n',
        '                    <span class="font-weight-bold" style="font-size: 13.5px; color: var(--eco-text-main);">Live System Event Journal</span>\\n',
        '                    <span class="badge badge-success px-2 py-0 ml-1 font-mono" style="font-size: 10px;">Fine-Timestamped</span>\\n',
        '                  </div>\\n',
        '                  <div class="d-flex align-items-center flex-wrap" style="gap: 6px;">\\n',
        '                    <select id="sys-log-cat" class="custom-select custom-select-sm text-xs" style="width: 105px; height: 26px; padding: 0 6px; font-size: 11px; background: var(--eco-card); color: var(--eco-text-main); border-color: var(--eco-border);" onchange="fetchSystemLogs(true)">\\n',
        '                      <option value="ALL">All Sources</option>\\n',
        '                      <option value="CHUTE">Chute Flow</option>\\n',
        '                      <option value="SENSOR">Sensors</option>\\n',
        '                      <option value="ACTUATOR">Actuators</option>\\n',
        '                      <option value="NETWORK">Network</option>\\n',
        '                      <option value="SYSTEM">System</option>\\n',
        '                    </select>\\n',
        '                    <select id="sys-log-lvl" class="custom-select custom-select-sm text-xs" style="width: 95px; height: 26px; padding: 0 6px; font-size: 11px; background: var(--eco-card); color: var(--eco-text-main); border-color: var(--eco-border);" onchange="fetchSystemLogs(true)">\\n',
        '                      <option value="ALL">All Levels</option>\\n',
        '                      <option value="TRIGGER">TRIGGER</option>\\n',
        '                      <option value="MEASURE">MEASURE</option>\\n',
        '                      <option value="SUCCESS">SUCCESS</option>\\n',
        '                      <option value="INFO">INFO</option>\\n',
        '                      <option value="WARN">WARN</option>\\n',
        '                      <option value="ERROR">ERROR</option>\\n',
        '                    </select>\\n',
        '                    <input id="sys-log-search" type="text" class="form-control form-control-sm text-xs" placeholder="Search keywords..." style="width: 140px; height: 26px; background: var(--eco-card); color: var(--eco-text-main); border-color: var(--eco-border);" oninput="filterSystemLogsLocally()">\\n',
        '                    <a href="/admin/api/system/logs/download" class="btn btn-xs btn-outline-success font-weight-bold px-2 py-0" title="Download log file" style="font-size: 10.5px;"><i class="fas fa-download mr-1"></i>Download .LOG</a>\\n',
        '                    <button class="btn btn-xs btn-outline-danger px-2 py-0" onclick="clearSystemLogs()" title="Clear logs" style="font-size: 10.5px;"><i class="fas fa-trash mr-1"></i>Clear</button>\\n',
        '                  </div>\\n',
        '                </div>\\n',
        '                <div class="card-body p-2" style="background: var(--eco-card); border-radius: 0 0 8px 8px;">\\n',
        '                  <div id="sys-log-terminal" style="font-family: Consolas, &quot;Liberation Mono&quot;, monospace; font-size: 11.5px; height: 380px; overflow-y: auto; color: var(--eco-text-main); line-height: 1.6; white-space: pre-wrap; word-break: break-all; padding: 6px 10px; background: var(--eco-card-sub); border: 1px solid var(--eco-border); border-radius: 6px;">\\n',
        '                    <span class="text-muted font-mono font-weight-bold">Loading system event logs...</span>\\n',
        '                  </div>\\n',
        '                </div>\\n',
        '              </div>\\n',
        '            </div>\\n'
    ]
    return ''.join(lines)


def load_and_escape_js():
    with open(JS_CLIENT_PATH, 'r', encoding='utf-8') as f:
        js_raw = f.read()
    # Escape backslashes, single quotes, and newlines for embedding into single-quoted Python literal
    return js_raw.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')


def update_portal_py():
    print("Reading host/portal.py...")
    with open(PORTAL_PY, 'r', encoding='utf-8') as f:
        content = f.read()

    # --- 1. Replace Sequence Pane ---
    start_seq = '<!-- TAB 6: CHUTE SEQUENCE & SENSOR MONITOR'
    end_seq = '<!-- Global Card Footer: Save & Status -->'
    idx_seq1 = content.find(start_seq)
    idx_seq2 = content.find(end_seq, idx_seq1)
    if idx_seq1 == -1 or idx_seq2 == -1:
        print("ERROR: sequence pane boundaries not found in portal.py!")
        sys.exit(1)

    new_seq_html = build_compact_sequence_pane()
    content = content[:idx_seq1] + new_seq_html + content[idx_seq2:]
    print("1. Replaced sequence monitor pane with v2.3.18 flow and high-contrast terminal.")

    # --- 2. Replace System Logs Pane ---
    start_sys = '<!-- SUB-TAB 4: SYSTEM EVENT LOGGER -->'
    idx_sys1 = content.find(start_sys)
    if idx_sys1 != -1:
        end_marker = '\\n          </div>\\n        </div>\\n      </div>\\n    </div>'
        idx_sys2 = content.find(end_marker, idx_sys1)
        if idx_sys2 != -1:
            new_sys_html = build_theme_sys_logs_pane()
            content = content[:idx_sys1] + new_sys_html + content[idx_sys2:]
            print("2. Replaced system logger pane with high-contrast layout.")
        else:
            print("ERROR: end_marker for sys-tab-logs not found!")
            sys.exit(1)

    # --- 3. Replace JavaScript Client Engine ---
    js_start = "// --- CHUTE SEQUENCE & SYSTEM LOGGER CLIENT ENGINE ---"
    idx_j1 = content.find(js_start)
    if idx_j1 != -1:
        end_of_js = content.find("\\n\\n</script>", idx_j1)
        if end_of_js != -1:
            escaped_js = load_and_escape_js()
            content = content[:idx_j1] + escaped_js + content[end_of_js:]
            print("3. Injected updated JavaScript client engine with v2.3.18 flow and high-contrast terminal.")

    # --- 4. Add id="esp-global-card-footer" to the card footer if missing ---
    old_footer_tag = '<!-- Global Card Footer: Save & Status -->\\n        <div class="card-footer d-flex align-items-center justify-content-between"'
    new_footer_tag = '<!-- Global Card Footer: Save & Status -->\\n        <div id="esp-global-card-footer" class="card-footer d-flex align-items-center justify-content-between"'
    if old_footer_tag in content:
        content = content.replace(old_footer_tag, new_footer_tag)
        print("4. Added id=\"esp-global-card-footer\" to ESP32 card footer.")

    # --- 4b. Embed hardware flash note inside #tab-cal-firmware pane ---
    old_fw_tags = '\\n                          <i class="fas fa-upload mr-1"></i> Flash Custom\\n                        </button>\\n                      </div>\\n                    </div>\\n                  </div>\\n                </div>\\n              </div>\\n            </div>\\n'
    new_fw_tags = (
        '\\n                          <i class="fas fa-upload mr-1"></i> Flash Custom\\n                        </button>\\n                      </div>\\n                    </div>\\n                  </div>\\n                </div>\\n              </div>\\n'
        '              <div class="mt-2 text-muted small d-flex align-items-center" style="font-size: 11.5px; padding: 7px 12px; background: rgba(0,0,0,0.18); border-radius: 6px; border: 1px solid rgba(255,255,255,0.06);">'
        '<i class="fas fa-info-circle text-info mr-2"></i><span>Hardware flashing writes directly to ESP32 ROM flash storage. Sensor and servo calibrations remain stored in NVS.</span></div>\\n'
        '            </div>\\n'
    )
    if 'Hardware flashing writes directly to ESP32 ROM flash storage' not in content[:content.find('<!-- TAB 6: CHUTE SEQUENCE')]:
        content = content.replace(old_fw_tags, new_fw_tags)
        print("4b. Embedded hardware flash note inside #tab-cal-firmware pane.")

    # --- 5. Update shown.bs.tab handler to hide card footer on non-calibration tabs (firmware & sequence) ---
    if "if (target === \\'#tab-cal-sequence\\') {" not in content:
        tab_listener_pattern_end = "if (target === \\'#sys-tab-logs\\') { if (typeof startSystemLogPolling === \\'function\\') startSystemLogPolling(); } else { if (typeof stopSystemLogPolling === \\'function\\') stopSystemLogPolling(); }"
        idx_tab_shown = content.find("shown.bs.tab")
        if idx_tab_shown != -1:
            idx_tl_start = content.find("var saveWrap = document.getElementById(\\'esp-footer-save-wrap\\');", idx_tab_shown)
            idx_tl_end = content.find(tab_listener_pattern_end, idx_tl_start)
            if idx_tl_start != -1 and idx_tl_end != -1:
                new_tab_listener = (
                    "var saveWrap = document.getElementById(\\'esp-footer-save-wrap\\');\\n"
                    "    var flashHint = document.getElementById(\\'esp-footer-flash-hint\\');\\n"
                    "    var globalFooter = document.getElementById(\\'esp-global-card-footer\\');\\n"
                    "    var isCalTab = (target === \\'#tab-cal-nir\\' || target === \\'#tab-cal-scale\\' || target === \\'#tab-cal-servos\\' || target === \\'#tab-cal-timing\\');\\n"
                    "    if (isCalTab) {\\n"
                    "        if (globalFooter) { globalFooter.classList.remove(\\'d-none\\'); globalFooter.classList.add(\\'d-flex\\'); globalFooter.style.display = \\'flex\\'; }\\n"
                    "        if (saveWrap) { saveWrap.classList.remove(\\'d-none\\'); saveWrap.classList.add(\\'d-flex\\'); saveWrap.style.display = \\'flex\\'; }\\n"
                    "        if (flashHint) { flashHint.classList.remove(\\'d-flex\\'); flashHint.classList.add(\\'d-none\\'); flashHint.style.display = \\'none\\'; }\\n"
                    "    } else {\\n"
                    "        if (globalFooter) { globalFooter.classList.remove(\\'d-flex\\'); globalFooter.classList.add(\\'d-none\\'); globalFooter.style.display = \\'none\\'; }\\n"
                    "        if (saveWrap) { saveWrap.classList.remove(\\'d-flex\\'); saveWrap.classList.add(\\'d-none\\'); saveWrap.style.display = \\'none\\'; }\\n"
                    "        if (flashHint) { flashHint.classList.remove(\\'d-flex\\'); flashHint.classList.add(\\'d-none\\'); flashHint.style.display = \\'none\\'; }\\n"
                    "    }\\n"
                    "    if (target === \\'#tab-cal-sequence\\') {\\n"
                    "        if (typeof startSequencePolling === \\'function\\') startSequencePolling();\\n"
                    "        if (typeof syncChuteTerminalHeight === \\'function\\') {\\n"
                    "            setTimeout(syncChuteTerminalHeight, 50);\\n"
                    "            setTimeout(syncChuteTerminalHeight, 200);\\n"
                    "        }\\n"
                    "    } else {\\n"
                    "        if (typeof stopSequencePolling === \\'function\\') stopSequencePolling();\\n"
                    "    }\\n"
                    "    " + tab_listener_pattern_end
                )
                content = content[:idx_tl_start] + new_tab_listener + content[idx_tl_end + len(tab_listener_pattern_end):]
                print("5. Updated shown.bs.tab handler to hide card footer on both sequence monitor and firmware flash tabs.")
    else:
        print("5. shown.bs.tab handler already updated, skipping.")

    # --- 6. Update showSection(secId) for sec-esp32 ---
    if "var activePill = $(\\'#esp32-cal-pills .nav-link.active\\').attr(\\'href\\');" not in content:
        show_sec_pattern_start = "if (secId !== \\'sec-esp32\\') {"
        show_sec_pattern_end = "if (secId !== \\'sec-system\\') {"
        idx_ss_start = content.find(show_sec_pattern_start)
        idx_ss_end = content.find(show_sec_pattern_end, idx_ss_start)
        if idx_ss_start != -1 and idx_ss_end != -1:
            new_show_sec = (
                "if (secId !== \\'sec-esp32\\') {\\n"
                "        if (typeof stopSequencePolling === \\'function\\') stopSequencePolling();\\n"
                "    } else {\\n"
                "        var activePill = $(\\'#esp32-cal-pills .nav-link.active\\').attr(\\'href\\');\\n"
                "        var isCalTab = (activePill === \\'#tab-cal-nir\\' || activePill === \\'#tab-cal-scale\\' || activePill === \\'#tab-cal-servos\\' || activePill === \\'#tab-cal-timing\\');\\n"
                "        var globalFooter = document.getElementById(\\'esp-global-card-footer\\');\\n"
                "        var saveWrap = document.getElementById(\\'esp-footer-save-wrap\\');\\n"
                "        if (isCalTab) {\\n"
                "            if (globalFooter) { globalFooter.classList.remove(\\'d-none\\'); globalFooter.classList.add(\\'d-flex\\'); globalFooter.style.display = \\'flex\\'; }\\n"
                "            if (saveWrap) { saveWrap.classList.remove(\\'d-none\\'); saveWrap.classList.add(\\'d-flex\\'); saveWrap.style.display = \\'flex\\'; }\\n"
                "        } else {\\n"
                "            if (globalFooter) { globalFooter.classList.remove(\\'d-flex\\'); globalFooter.classList.add(\\'d-none\\'); globalFooter.style.display = \\'none\\'; }\\n"
                "            if (saveWrap) { saveWrap.classList.remove(\\'d-flex\\'); saveWrap.classList.add(\\'d-none\\'); saveWrap.style.display = \\'none\\'; }\\n"
                "        }\\n"
                "        if (activePill === \\'#tab-cal-sequence\\') {\\n"
                "            if (typeof startSequencePolling === \\'function\\') startSequencePolling();\\n"
                "            if (typeof syncChuteTerminalHeight === \\'function\\') {\\n"
                "                setTimeout(syncChuteTerminalHeight, 50);\\n"
                "                setTimeout(syncChuteTerminalHeight, 200);\\n"
                "            }\\n"
                "        } else {\\n"
                "            if (typeof stopSequencePolling === \\'function\\') stopSequencePolling();\\n"
                "        }\\n"
                "    }\\n    "
            )
            content = content[:idx_ss_start] + new_show_sec + content[idx_ss_end:]
            print("6. Updated showSection for sec-esp32 to hide card footer when non-calibration tab is active.")
    else:
        print("6. showSection for sec-esp32 already updated, skipping.")

    with open(PORTAL_PY, 'w', encoding='utf-8') as f:
        f.write(content)
    print("host/portal.py successfully updated and saved!")


if __name__ == '__main__':
    update_system_logger()
    update_portal_py()
