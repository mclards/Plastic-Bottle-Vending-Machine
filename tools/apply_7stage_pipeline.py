#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_7stage_pipeline.py
Authoritatively transforms host/portal.py to:
  1. Position Chute Sequence Monitor as the first active tab in #sec-esp32.
  2. Embed the 7-stage chute sequence HTML pane (S1 Gate -> S2 Intake -> S3 Inductive -> S4 Scale -> S5 NIR -> S6 Drop Servo -> S7 Drop PIR).
  3. Embed the 7-row Stage Latency Budget table + Total Duration row.
  4. Embed the latest sequence_logger_client.js engine.
  5. Add backend packet dispatch for PROX_SAMPLE, PROX_TEST, INTAKE, WEIGHT_SAMPLE, NIR_SAMPLE, and DROP_ACTUATED.
  6. Add /admin/api/esp32/test_prox route and support is_metal in /admin/api/esp32/sequence/simulate.
  7. Strict Python 3.5.3 compatibility with zero unescaped newlines in ADMIN_HTML.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL_PY = os.path.join(BASE_DIR, 'host', 'portal.py')
JS_CLIENT_PATH = os.path.join(BASE_DIR, 'tools', 'sequence_logger_client.js')

def get_7stage_pane_html_escaped():
    pane_lines = [
        '            <!-- TAB 1: CHUTE SEQUENCE & SENSOR MONITOR (COMPACT 1-PAGE) -->',
        '            <div class="tab-pane fade show active" id="tab-cal-sequence" role="tabpanel">',
        '              <!-- UNIFIED PIPELINE CARD (HEADER + PROGRESS + 7 FLOW STAGES) -->',
        '              <div class="card mb-2" style="background: var(--eco-card); border: 1px solid var(--eco-border); border-radius: 8px;">',
        '                <div class="card-header py-1 px-3 d-flex align-items-center justify-content-between flex-wrap" style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border); gap: 6px; min-height: 40px;">',
        '                  <div class="d-flex align-items-center flex-wrap" style="gap: 6px;">',
        '                    <i class="fas fa-stream mr-1" style="color: #10b981; font-size: 1.1rem;"></i>',
        '                    <span class="font-weight-bold" style="font-size: 13.5px; color: var(--eco-text-main);">Live Scanning Sequence</span>',
        '                    <span id="seq-status-badge" class="badge badge-secondary font-weight-bold ml-1" style="font-size: 11.5px; display: inline-block; min-width: 74px; text-align: center; padding: 3.5px 8px; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span>',
        '                    <span class="badge px-2 py-1 text-muted" style="background: var(--eco-card); border: 1px solid var(--eco-border); font-size: 11.5px; border-radius: 4px;"><i class="fas fa-fingerprint mr-1" style="color: var(--eco-border);"></i>Session: <span id="seq-session-id" class="font-mono font-weight-bold" style="color: var(--eco-text-main);">--</span></span>',
        '                  </div>',
        '                  <div class="d-flex align-items-center" style="gap: 5px;">',
        '                    <span class="badge px-2 py-1 font-mono font-weight-bold text-warning" style="background: var(--eco-card); border: 1px solid var(--eco-border); font-size: 12px; min-width: 88px; text-align: center; border-radius: 4px;"><i class="fas fa-stopwatch text-warning mr-1"></i><span id="seq-elapsed-timer">0.00 s</span></span>',
        '                    <button class="btn btn-xs btn-outline-success font-weight-bold px-2 py-1" onclick="simulateChuteSequence(true, false)" title="Simulate PET bottle deposit sequence" style="font-size: 11.5px; border-radius: 4px; min-height: 28px; padding: 3.5px 10px;"><i class="fas fa-play mr-1"></i>Simulate PET</button>',
        '                    <button class="btn btn-xs btn-outline-warning font-weight-bold px-2 py-1 ml-1" onclick="simulateChuteSequence(false, true)" title="Simulate tin can / metal reject" style="font-size: 11.5px; border-radius: 4px; min-height: 28px; padding: 3.5px 10px;"><i class="fas fa-magnet mr-1"></i>Simulate Metal</button>',
        '                    <button class="btn btn-xs btn-outline-danger font-weight-bold px-2 py-1 ml-1" onclick="simulateChuteSequence(false, false)" title="Simulate non-PET reject sequence" style="font-size: 11.5px; border-radius: 4px; min-height: 28px; padding: 3.5px 10px;"><i class="fas fa-ban mr-1"></i>Simulate Non-PET</button>',
        '                    <button class="btn btn-xs btn-outline-secondary font-weight-bold px-2 py-1 ml-1" onclick="resetChuteSequence()" title="Reset chute sequence to idle" style="font-size: 11.5px; border-radius: 4px; min-height: 28px; padding: 3.5px 10px;"><i class="fas fa-redo-alt mr-1"></i>Reset</button>',
        '                  </div>',
        '                </div>',
        '                <!-- Micro 3px Animated Progress Line -->',
        '                <div class="progress" style="height: 3px; background: var(--eco-border); border-radius: 0;">',
        '                  <div id="seq-progress-bar" class="progress-bar bg-success progress-bar-striped progress-bar-animated" role="progressbar" style="width: 0%; transition: width 0.25s ease;"></div>',
        '                </div>',
        '                <!-- Horizontal 7-Stage Sensor Strip (Sequence: Gate -> Top IR -> Inductive -> Scale -> NIR -> Flap -> Bottom IR) -->',
        '                <div class="card-body p-2" style="background: var(--eco-card);">',
        '                  <div class="d-flex align-items-center justify-content-between flex-nowrap" style="gap: 6px; overflow-x: auto;">',
        '                    <!-- STAGE 1: Servo Entrance Gate -->',
        '                    <div id="seq-node-1" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 120px; transition: all 0.2s ease;">',
        '                      <div class="d-flex justify-content-between align-items-center px-1">',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S1</span>',
        '                        <span id="seq-s1-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>',
        '                      </div>',
        '                      <div class="my-1"><i id="seq-s1-icon" class="fas fa-door-closed text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">Servo Gate</div>',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s1-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Closed (0°)</span></div>',
        '                    </div>',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-1" class="fas fa-chevron-right text-muted" style="font-size: 1rem;"></i></div>',
        '                    <!-- STAGE 2: PIR Intake (Top IR) -->',
        '                    <div id="seq-node-2" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 120px; transition: all 0.2s ease;">',
        '                      <div class="d-flex justify-content-between align-items-center px-1">',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S2</span>',
        '                        <span id="seq-s2-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>',
        '                      </div>',
        '                      <div class="my-1"><i id="seq-s2-icon" class="fas fa-radiation text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">PIR Intake</div>',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s2-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">--</span></div>',
        '                    </div>',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-2" class="fas fa-chevron-right text-muted" style="font-size: 1rem;"></i></div>',
        '                    <!-- STAGE 3: Inductive Sensor (LJ12A3 Metal Check) -->',
        '                    <div id="seq-node-3" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 120px; transition: all 0.2s ease;">',
        '                      <div class="d-flex justify-content-between align-items-center px-1">',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S3</span>',
        '                        <span id="seq-s3-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>',
        '                      </div>',
        '                      <div class="my-1"><i id="seq-s3-icon" class="fas fa-magnet text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">Inductive Sensor</div>',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s3-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">--</span></div>',
        '                    </div>',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-3" class="fas fa-chevron-right text-muted" style="font-size: 1rem;"></i></div>',
        '                    <!-- STAGE 4: HX711 Scale -->',
        '                    <div id="seq-node-4" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 120px; transition: all 0.2s ease;">',
        '                      <div class="d-flex justify-content-between align-items-center px-1">',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S4</span>',
        '                        <span id="seq-s4-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>',
        '                      </div>',
        '                      <div class="my-1"><i id="seq-s4-icon" class="fas fa-balance-scale text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">HX711 Scale</div>',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s4-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">--</span></div>',
        '                    </div>',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-4" class="fas fa-chevron-right text-muted" style="font-size: 1rem;"></i></div>',
        '                    <!-- STAGE 5: NIR Spectrometer -->',
        '                    <div id="seq-node-5" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 125px; transition: all 0.2s ease;">',
        '                      <div class="d-flex justify-content-between align-items-center px-1">',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S5</span>',
        '                        <span id="seq-s5-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>',
        '                      </div>',
        '                      <div class="my-1"><i id="seq-s5-icon" class="fas fa-eye text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">NIR Sensor</div>',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s5-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">--</span></div>',
        '                    </div>',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-5" class="fas fa-chevron-right text-muted" style="font-size: 1rem;"></i></div>',
        '                    <!-- STAGE 6: Drop Exit Flap -->',
        '                    <div id="seq-node-6" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 120px; transition: all 0.2s ease;">',
        '                      <div class="d-flex justify-content-between align-items-center px-1">',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S6</span>',
        '                        <span id="seq-s6-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>',
        '                      </div>',
        '                      <div class="my-1"><i id="seq-s6-icon" class="fas fa-box-open text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">Drop Servo</div>',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s6-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Closed (0°)</span></div>',
        '                    </div>',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-6" class="fas fa-chevron-right text-muted" style="font-size: 1rem;"></i></div>',
        '                    <!-- STAGE 7: PIR Drop Transit -->',
        '                    <div id="seq-node-7" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 120px; transition: all 0.2s ease;">',
        '                      <div class="d-flex justify-content-between align-items-center px-1">',
        '                        <span class="badge font-mono font-weight-bold" style="font-size: 11px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-muted); padding: 2px 6px; border-radius: 3px;">S7</span>',
        '                        <span id="seq-s7-lat" class="font-mono" style="font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: var(--eco-card); border: 1px solid var(--eco-border); color: var(--eco-text-main); min-width: 44px; text-align: right; display: inline-block;">--</span>',
        '                      </div>',
        '                      <div class="my-1"><i id="seq-s7-icon" class="fas fa-check-circle text-muted" style="font-size: 1.35rem; transition: color 0.2s;"></i></div>',
        '                      <div class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main); line-height: 1.25;">Drop PIR Clear</div>',
        '                      <div class="mt-1" style="width: 100%;"><span id="seq-s7-state" class="badge badge-secondary" style="display: block; width: 100%; text-align: center; font-size: 12px; font-weight: 700; padding: 3.5px 4px; border-radius: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">--</span></div>',
        '                    </div>',
        '                  </div>',
        '                </div>',
        '              </div>',
        '              <!-- BOTTOM GRID (TIMING TABLE LEFT + HIGH-CONTRAST EVENT TERMINAL RIGHT) IN 1 ROW -->',
        '              <div class="row align-items-stretch" style="margin-top: 4px; margin-bottom: 0;">',
        '                <!-- LEFT: Stage Latency Budget Table -->',
        '                <div class="col-lg-5 col-12 mb-2 pr-lg-1 d-flex flex-column">',
        '                  <div id="card-latency-budget" class="card mb-0 flex-grow-1" style="background: var(--eco-card); border: 1px solid var(--eco-border); border-radius: 8px;">',
        '                    <div class="card-header py-1 px-2 d-flex align-items-center justify-content-between" style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border); min-height: 34px;">',
        '                      <div class="d-flex align-items-center">',
        '                        <i class="fas fa-stopwatch-20 mr-1 text-warning" style="font-size: 1.05rem;"></i>',
        '                        <span class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main);">Stage Latency Budget</span>',
        '                      </div>',
        '                      <span class="badge badge-secondary px-1 font-mono" style="font-size: 10px;">ms</span>',
        '                    </div>',
        '                    <div class="card-body p-0" style="background: var(--eco-card);">',
        '                      <div class="table-responsive m-0">',
        '                        <table class="table table-sm m-0" style="font-size: 11.5px; color: var(--eco-text-main);">',
        '                          <thead>',
        '                            <tr style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border);">',
        '                              <th style="padding: 5px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Stage</th>',
        '                              <th class="text-center" style="padding: 5px 6px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Budget</th>',
        '                              <th class="text-center" style="padding: 5px 6px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Actual</th>',
        '                              <th class="text-center" style="padding: 5px 8px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; width: 78px;">Status</th>',
        '                            </tr>',
        '                          </thead>',
        '                          <tbody>',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">',
        '                              <td style="padding: 5px 8px; font-weight: 600;">1. Servo Gate Open</td>',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">150 - 300 ms</td>',
        '                              <td id="row-s1-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>',
        '                              <td id="row-s1-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>',
        '                            </tr>',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">',
        '                              <td style="padding: 5px 8px; font-weight: 600;">2. PIR Intake Intrusion</td>',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">200 - 8000 ms</td>',
        '                              <td id="row-s2-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>',
        '                              <td id="row-s2-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>',
        '                            </tr>',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">',
        '                              <td style="padding: 5px 8px; font-weight: 600;">3. Inductive Metal Check</td>',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">10 - 50 ms</td>',
        '                              <td id="row-s3-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>',
        '                              <td id="row-s3-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>',
        '                            </tr>',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">',
        '                              <td style="padding: 5px 8px; font-weight: 600;">4. HX711 Scale Mass</td>',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">100 - 350 ms</td>',
        '                              <td id="row-s4-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>',
        '                              <td id="row-s4-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>',
        '                            </tr>',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">',
        '                              <td style="padding: 5px 8px; font-weight: 600;">5. AS7263 NIR Spectrometry</td>',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">140 - 400 ms</td>',
        '                              <td id="row-s5-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>',
        '                              <td id="row-s5-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>',
        '                            </tr>',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">',
        '                              <td style="padding: 5px 8px; font-weight: 600;">6. Drop Exit Servo</td>',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">100 - 300 ms</td>',
        '                              <td id="row-s6-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>',
        '                              <td id="row-s6-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>',
        '                            </tr>',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">',
        '                              <td style="padding: 5px 8px; font-weight: 600;">7. PIR Drop Transit Clear</td>',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">250 - 3100 ms</td>',
        '                              <td id="row-s7-lat" class="text-center font-mono font-weight-bold" style="padding: 5px 6px; color: var(--eco-text-main); font-size: 12px;">--</td>',
        '                              <td id="row-s7-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>',
        '                            </tr>',
        '                            <tr style="background: var(--eco-card-sub); font-weight: 700;">',
        '                              <td style="padding: 5px 8px; color: var(--eco-text-main); font-size: 12px;">Total Duration</td>',
        '                              <td class="text-center text-muted font-mono" style="padding: 5px 6px; font-size: 11px;">~1.5 - 4.5 s</td>',
        '                              <td id="row-tot-lat" class="text-center font-mono text-muted font-weight-bold" style="padding: 5px 6px; font-size: 12px;">--</td>',
        '                              <td id="row-tot-stat" class="text-center" style="padding: 4px 6px; width: 78px;"><span id="badge-tot-stat" class="badge badge-secondary" style="display: inline-block; width: 68px; text-align: center; font-size: 11.5px; font-weight: 700; padding: 3px 0; border-radius: 4px; letter-spacing: 0.5px;">IDLE</span></td>',
        '                            </tr>',
        '                          </tbody>',
        '                        </table>',
        '                      </div>',
        '                    </div>',
        '                  </div>',
        '                </div>',
        '                <!-- RIGHT: Theme-Native Chute Diagnostics Event Stream Terminal -->',
        '                <div class="col-lg-7 col-12 mb-2 pl-lg-1 d-flex flex-column">',
        '                  <div id="card-chute-terminal" class="card mb-0 flex-grow-1 d-flex flex-column" style="background: var(--eco-card); border: 1px solid var(--eco-border); border-radius: 8px; min-height: 0;">',
        '                    <div class="card-header py-1 px-2 d-flex align-items-center justify-content-between flex-wrap" style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border); min-height: 34px; gap: 4px;">',
        '                      <div class="d-flex align-items-center">',
        '                        <i class="fas fa-terminal text-success mr-1" style="font-size: 1.05rem;"></i>',
        '                        <span class="font-weight-bold" style="font-size: 13px; color: var(--eco-text-main);">Chute Diagnostics Event Stream</span>',
        '                        <span class="badge badge-success px-1 py-0 ml-2 font-mono" style="font-size: 10px;">LIVE</span>',
        '                      </div>',
        '                      <div class="d-flex align-items-center" style="gap: 4px;">',
        '                        <button class="btn btn-xs btn-outline-info px-2 py-1 font-weight-bold" onclick="copySequenceTerminalLogs()" title="Copy event log stream" style="font-size: 11.5px; border-radius: 4px; padding: 3px 9px;"><i class="fas fa-copy mr-1"></i>Copy</button>',
        '                        <a href="/admin/api/system/logs/download" class="btn btn-xs btn-outline-success px-2 py-1 font-weight-bold" title="Download log file (.LOG)" style="font-size: 11.5px; border-radius: 4px; padding: 3px 9px;"><i class="fas fa-download mr-1"></i>Download</a>',
        '                        <button class="btn btn-xs btn-outline-danger px-2 py-1 font-weight-bold" onclick="clearSystemLogs()" title="Clear live logs" style="font-size: 11.5px; border-radius: 4px; padding: 3px 9px;"><i class="fas fa-trash-alt mr-1"></i>Clear</button>',
        '                      </div>',
        '                    </div>',
        '                    <div class="card-body p-2 d-flex flex-column flex-grow-1" style="background: var(--eco-card); border-radius: 0 0 8px 8px; min-height: 0; overflow: hidden;">',
        '                      <div id="seq-log-terminal" style="font-family: Consolas, &quot;Liberation Mono&quot;, monospace; font-size: 12px; flex: 1 1 0; min-height: 0; overflow-y: auto; color: var(--eco-text-main); line-height: 1.5; white-space: pre-wrap; word-break: break-all; padding: 6px 8px; background: var(--eco-card-sub); border: 1px solid var(--eco-border); border-radius: 6px; scrollbar-width: thin; scrollbar-color: var(--eco-border) var(--eco-card-sub);">',
        '                        <span class="text-muted font-mono font-weight-bold">[System Ready] Awaiting bottle deposit sequence...</span>',
        '                      </div>',
        '                    </div>',
        '                  </div>',
        '                </div>',
        '              </div>',
        '            </div>'
    ]
    # Join with escaped newlines so that no raw newline byte is introduced into single-quoted string
    return '\\n'.join(pane_lines) + '\\n'

def load_and_escape_js():
    with open(JS_CLIENT_PATH, 'r', encoding='utf-8') as f:
        js_raw = f.read()
    return js_raw.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')

def main():
    print("Reading host/portal.py...")
    with open(PORTAL_PY, 'r', encoding='utf-8') as f:
        content = f.read()

    # Step 1: Reorder pills so Chute Sequence Monitor is first and active
    old_pills = (
        '          <ul class="nav nav-pills" id="esp32-cal-pills" role="tablist" style="gap: 6px; padding-bottom: 10px;">\\n'
        '            <li class="nav-item">\\n'
        '              <a class="nav-link active" id="pill-nir-tab" data-toggle="pill" href="#tab-cal-nir" role="tab" style="font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 6px;">\\n'
        '                <i class="fas fa-microscope mr-1 text-warning"></i> NIR Spectrometer\\n'
        '              </a>\\n'
        '            </li>\\n'
        '            <li class="nav-item">\\n'
        '              <a class="nav-link" id="pill-scale-tab" data-toggle="pill" href="#tab-cal-scale" role="tab" style="font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 6px;">\\n'
        '                <i class="fas fa-balance-scale mr-1 text-info"></i> Weight Sensor\\n'
        '              </a>\\n'
        '            </li>\\n'
        '            <li class="nav-item">\\n'
        '              <a class="nav-link" id="pill-servos-tab" data-toggle="pill" href="#tab-cal-servos" role="tab" style="font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 6px;">\\n'
        '                <i class="fas fa-cogs mr-1 text-success"></i> Servos & Gates\\n'
        '              </a>\\n'
        '            </li>\\n'
        '            <li class="nav-item">\\n'
        '              <a class="nav-link" id="pill-timing-tab" data-toggle="pill" href="#tab-cal-timing" role="tab" style="font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 6px;">\\n'
        '                <i class="fas fa-stopwatch mr-1 text-primary"></i> Timers & Proximity\\n'
        '              </a>\\n'
        '            </li>\\n'
        '            <li class="nav-item">\\n'
        '              <a class="nav-link" id="pill-firmware-tab" data-toggle="pill" href="#tab-cal-firmware" role="tab" style="font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 6px;">\\n'
        '                <i class="fas fa-bolt mr-1 text-info"></i> Firmware Flash\\n'
        '              </a>\\n'
        '            </li>\\n'
        '            <li class="nav-item">\\n'
        '              <a class="nav-link" id="pill-sequence-tab" data-toggle="pill" href="#tab-cal-sequence" role="tab" style="font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 6px;">\\n'
        '                <i class="fas fa-stream mr-1 text-success"></i> Chute Sequence Monitor\\n'
        '              </a>\\n'
        '            </li>\\n'
        '          </ul>'
    )

    new_pills = (
        '          <ul class="nav nav-pills" id="esp32-cal-pills" role="tablist" style="gap: 6px; padding-bottom: 10px;">\\n'
        '            <li class="nav-item">\\n'
        '              <a class="nav-link active" id="pill-sequence-tab" data-toggle="pill" href="#tab-cal-sequence" role="tab" style="font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 6px;">\\n'
        '                <i class="fas fa-stream mr-1 text-success"></i> Chute Sequence Monitor\\n'
        '              </a>\\n'
        '            </li>\\n'
        '            <li class="nav-item">\\n'
        '              <a class="nav-link" id="pill-nir-tab" data-toggle="pill" href="#tab-cal-nir" role="tab" style="font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 6px;">\\n'
        '                <i class="fas fa-microscope mr-1 text-warning"></i> NIR Spectrometer\\n'
        '              </a>\\n'
        '            </li>\\n'
        '            <li class="nav-item">\\n'
        '              <a class="nav-link" id="pill-scale-tab" data-toggle="pill" href="#tab-cal-scale" role="tab" style="font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 6px;">\\n'
        '                <i class="fas fa-balance-scale mr-1 text-info"></i> Weight Sensor\\n'
        '              </a>\\n'
        '            </li>\\n'
        '            <li class="nav-item">\\n'
        '              <a class="nav-link" id="pill-servos-tab" data-toggle="pill" href="#tab-cal-servos" role="tab" style="font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 6px;">\\n'
        '                <i class="fas fa-cogs mr-1 text-success"></i> Servos & Gates\\n'
        '              </a>\\n'
        '            </li>\\n'
        '            <li class="nav-item">\\n'
        '              <a class="nav-link" id="pill-timing-tab" data-toggle="pill" href="#tab-cal-timing" role="tab" style="font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 6px;">\\n'
        '                <i class="fas fa-stopwatch mr-1 text-primary"></i> Timers & Proximity\\n'
        '              </a>\\n'
        '            </li>\\n'
        '            <li class="nav-item">\\n'
        '              <a class="nav-link" id="pill-firmware-tab" data-toggle="pill" href="#tab-cal-firmware" role="tab" style="font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 6px;">\\n'
        '                <i class="fas fa-bolt mr-1 text-info"></i> Firmware Flash\\n'
        '              </a>\\n'
        '            </li>\\n'
        '          </ul>'
    )

    if old_pills in content:
        content = content.replace(old_pills, new_pills)
        print("1. Pills reordered successfully (Chute Sequence Monitor placed first).")
    else:
        print("WARNING: old_pills pattern not found in portal.py!")

    # Step 2: Remove old sequence tab pane from the end
    old_seq_start = '<!-- TAB 6: CHUTE SEQUENCE & SENSOR MONITOR (COMPACT 1-PAGE) -->'
    old_seq_end = '<!-- Global Card Footer: Save & Status -->'
    idx_s6 = content.find(old_seq_start)
    idx_foot = content.find(old_seq_end, idx_s6)
    if idx_s6 != -1 and idx_foot != -1:
        content = content[:idx_s6] + content[idx_foot:]
        print("2. Removed old Tab 6 from the end of the tabs container.")
    else:
        print("WARNING: old Tab 6 sequence pane boundaries not found!")

    # Step 3: Deactivate tab-cal-nir (change 'show active' to '')
    old_nir_pane_header = '<!-- TAB 1: NIR SPECTROMETER (AS7263) -->\\n            <div class="tab-pane fade show active" id="tab-cal-nir" role="tabpanel">'
    new_nir_pane_header = '<!-- TAB 2: NIR SPECTROMETER (AS7263) -->\\n            <div class="tab-pane fade" id="tab-cal-nir" role="tabpanel">'
    if old_nir_pane_header in content:
        content = content.replace(old_nir_pane_header, new_nir_pane_header)
        print("3. Deactivated NIR tab pane (active moved to Sequence Monitor).")
    else:
        print("WARNING: old_nir_pane_header not found in portal.py!")

    # Step 4: Insert 7-stage sequence pane right after <div class="tab-content" id="esp32-cal-tabContent">\n\n
    tab_content_start = '<div class="tab-content" id="esp32-cal-tabContent">\\n\\n'
    idx_tc = content.find(tab_content_start)
    if idx_tc != -1:
        insert_pos = idx_tc + len(tab_content_start)
        escaped_7stage_html = get_7stage_pane_html_escaped()
        content = content[:insert_pos] + escaped_7stage_html + '\\n' + content[insert_pos:]
        print("4. Inserted 7-stage sequence HTML pane as the FIRST tab pane.")
    else:
        print("ERROR: tab-content container not found in portal.py!")
        sys.exit(1)

    # Step 5: Replace client JS engine
    js_start = "// --- CHUTE SEQUENCE & SYSTEM LOGGER CLIENT ENGINE ---"
    idx_j1 = content.find(js_start)
    if idx_j1 != -1:
        end_of_js = content.find("\\n\\n</script>", idx_j1)
        if end_of_js != -1:
            escaped_js = load_and_escape_js()
            content = content[:idx_j1] + escaped_js + content[end_of_js:]
            print("5. Embedded latest sequence_logger_client.js engine.")
        else:
            print("WARNING: end_of_js marker not found!")
    else:
        print("WARNING: js_start marker not found!")

    # Step 6: Backend handling in handle_physical_esp32_packet
    # Insert PROX_SAMPLE, PROX_TEST, INTAKE, WEIGHT_SAMPLE, NIR_SAMPLE, and DROP_ACTUATED
    packet_target = "    elif ev == 'NIR_TEST':"
    packet_replacement = (
        "    elif ev in ('PROX_SAMPLE', 'PROX_TEST'):\n"
        "        data['timestamp'] = time.time()\n"
        "        physical_esp32_state['last_prox'] = data\n"
        "        physical_esp32_state['last_event'] = 'Proximity ({})'.format('Metal' if data.get('metal') else 'Clear')\n"
        "        try:\n"
        "            chute_tracker.on_prox_scan(data)\n"
        "        except Exception:\n"
        "            pass\n"
        "    elif ev == 'INTAKE':\n"
        "        try:\n"
        "            chute_tracker.on_intake_triggered()\n"
        "        except Exception:\n"
        "            pass\n"
        "    elif ev == 'WEIGHT_SAMPLE':\n"
        "        try:\n"
        "            chute_tracker.on_weight_scan(data)\n"
        "        except Exception:\n"
        "            pass\n"
        "    elif ev == 'NIR_SAMPLE':\n"
        "        try:\n"
        "            chute_tracker.on_nir_scan(data)\n"
        "        except Exception:\n"
        "            pass\n"
        "    elif ev == 'DROP_ACTUATED':\n"
        "        try:\n"
        "            chute_tracker.on_drop_actuated(angle=data.get('angle', 90), action=data.get('action', 'ACCEPT'))\n"
        "        except Exception:\n"
        "            pass\n"
        "    elif ev == 'NIR_TEST':"
    )
    if packet_target in content:
        content = content.replace(packet_target, packet_replacement, 1)
        print("6. Added PROX_SAMPLE / INTAKE / NIR_SAMPLE / DROP_ACTUATED event handlers to handle_physical_esp32_packet.")
    else:
        print("WARNING: packet_target not found in handle_physical_esp32_packet!")

    # Step 7: Update /admin/api/esp32/sequence/simulate to parse is_metal
    sim_old = (
        "@app.route('/admin/api/esp32/sequence/simulate', methods=['POST'])\n"
        "def admin_api_esp32_sequence_simulate():\n"
        "    if not session.get('admin_logged_in'):\n"
        "        return (jsonify({'error': 'unauthorized'}), 401)\n"
        "    data = request.get_json(silent=True) or {}\n"
        "    is_pet = bool(data.get('is_pet', True))\n"
        "    weight = float(data.get('weight', 35.0))\n"
        "    cal_w = float(data.get('cal_w', 48.5))\n"
        "    chute_tracker.simulate_scan(is_pet=is_pet, weight=weight, cal_w=cal_w)\n"
        "    return jsonify({'success': True, 'state': chute_tracker.get_state()})"
    )

    sim_new = (
        "@app.route('/admin/api/esp32/sequence/simulate', methods=['POST'])\n"
        "def admin_api_esp32_sequence_simulate():\n"
        "    if not session.get('admin_logged_in'):\n"
        "        return (jsonify({'error': 'unauthorized'}), 401)\n"
        "    data = request.get_json(silent=True) or {}\n"
        "    is_pet = bool(data.get('is_pet', True))\n"
        "    is_metal = bool(data.get('is_metal', False))\n"
        "    weight = float(data.get('weight', 35.0))\n"
        "    cal_w = float(data.get('cal_w', 48.5))\n"
        "    chute_tracker.simulate_scan(is_metal=is_metal, is_pet=is_pet, weight=weight, cal_w=cal_w)\n"
        "    return jsonify({'success': True, 'state': chute_tracker.get_state()})\n\n"
        "@app.route('/admin/api/esp32/test_prox', methods=['POST'])\n"
        "def admin_api_esp32_test_prox():\n"
        "    if not session.get('admin_logged_in'):\n"
        "        return (jsonify({'error': 'unauthorized'}), 401)\n"
        "    if physical_esp32_serial and physical_esp32_serial.is_open:\n"
        "        try:\n"
        "            send_to_physical_esp32({'cmd': 'TEST_PROX'})\n"
        "            return jsonify({'success': True, 'message': 'Proximity test command sent'})\n"
        "        except Exception as e:\n"
        "            return (jsonify({'error': str(e)}), 500)\n"
        "    return (jsonify({'error': 'ESP32 not connected'}), 503)"
    )

    if sim_old in content:
        content = content.replace(sim_old, sim_new)
        print("7. Updated sequence simulation endpoint with is_metal and added /admin/api/esp32/test_prox.")
    else:
        print("WARNING: sim_old pattern not found in portal.py!")

    with open(PORTAL_PY, 'w', encoding='utf-8') as f:
        f.write(content)
    print("host/portal.py successfully updated and saved!")

if __name__ == '__main__':
    main()

