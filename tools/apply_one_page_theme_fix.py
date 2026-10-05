#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-Page & Theme-Native UI Refactor for:
1. Chute Sequence Monitor (#tab-cal-sequence)
2. System Event Logger (#sys-tab-logs)
Strictly adheres to:
- Python 3.5.3 (no f-strings)
- Monolithic string escaping rules in ADMIN_HTML
- Zero captive portal modifications
"""

import os
import sys

PORTAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'host', 'portal.py')
JS_CLIENT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sequence_logger_client.js')

def build_compact_sequence_pane():
    # Compact, 1-page layout matching Eco-Vendo themes (var(--eco-card), var(--eco-card-sub), etc.)
    # Escaped newlines ready for embedding into ADMIN_HTML
    lines = [
        '            <!-- TAB 6: CHUTE SEQUENCE & SENSOR MONITOR (COMPACT 1-PAGE) -->\\n',
        '            <div class="tab-pane fade" id="tab-cal-sequence" role="tabpanel">\\n',
        '              <!-- UNIFIED PIPELINE CARD (HEADER + PROGRESS + 6 FLOW STAGES) -->\\n',
        '              <div class="card mb-2" style="background: var(--eco-card); border: 1px solid var(--eco-border); border-radius: 8px;">\\n',
        '                <div class="card-header py-1 px-3 d-flex align-items-center justify-content-between flex-wrap" style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border); gap: 6px; min-height: 38px;">\\n',
        '                  <div class="d-flex align-items-center flex-wrap" style="gap: 6px;">\\n',
        '                    <i class="fas fa-stream mr-1" style="color: #10b981; font-size: 1.05rem;"></i>\\n',
        '                    <span class="font-weight-bold" style="font-size: 12.5px; color: var(--eco-text-main);">Live Scanning Sequence</span>\\n',
        '                    <span id="seq-status-badge" class="badge badge-secondary px-2 py-0 font-weight-bold" style="font-size: 10.5px;">IDLE</span>\\n',
        '                    <span class="badge px-2 py-0 text-muted" style="background: var(--eco-card); border: 1px solid var(--eco-border); font-size: 10px;"><i class="fas fa-fingerprint mr-1"></i>Session: <span id="seq-session-id" class="font-mono" style="color: var(--eco-text-main);">--</span></span>\\n',
        '                  </div>\\n',
        '                  <div class="d-flex align-items-center" style="gap: 5px;">\\n',
        '                    <span class="badge px-2 py-0 font-mono text-warning font-weight-bold" style="background: var(--eco-card); border: 1px solid var(--eco-border); font-size: 11px;"><i class="fas fa-stopwatch mr-1"></i><span id="seq-elapsed-timer">0.00 s</span></span>\\n',
        '                    <button class="btn btn-xs btn-outline-success font-weight-bold px-2 py-0" onclick="simulateChuteSequence(true)" title="Simulate PET bottle scan"><i class="fas fa-play mr-1"></i>Simulate PET</button>\\n',
        '                    <button class="btn btn-xs btn-outline-danger font-weight-bold px-2 py-0 ml-1" onclick="simulateChuteSequence(false)" title="Simulate reject"><i class="fas fa-times mr-1"></i>Reject</button>\\n',
        '                    <button class="btn btn-xs btn-outline-secondary px-2 py-0 ml-1" onclick="resetChuteSequence()" title="Reset to idle"><i class="fas fa-undo"></i></button>\\n',
        '                  </div>\\n',
        '                </div>\\n',
        '                <!-- Micro 3px Animated Progress Line -->\\n',
        '                <div class="progress" style="height: 3px; background: var(--eco-border); border-radius: 0;">\\n',
        '                  <div id="seq-progress-bar" class="progress-bar bg-success progress-bar-striped progress-bar-animated" role="progressbar" style="width: 0%; transition: width 0.25s ease;"></div>\\n',
        '                </div>\\n',
        '                <!-- Horizontal 6-Stage Sensor Strip -->\\n',
        '                <div class="card-body p-2" style="background: var(--eco-card);">\\n',
        '                  <div class="d-flex align-items-center justify-content-between flex-nowrap" style="gap: 4px; overflow-x: auto;">\\n',
        '                    <!-- STAGE 1: Servo Entrance Gate -->\\n',
        '                    <div id="seq-node-1" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 115px; transition: all 0.2s ease;">\\n',
        '                      <div class="d-flex justify-content-between align-items-center px-1">\\n',
        '                        <span class="badge text-muted font-mono" style="font-size: 8.5px; background: var(--eco-card); border: 1px solid var(--eco-border);">S1</span>\\n',
        '                        <span id="seq-s1-lat" class="font-mono text-muted" style="font-size: 9px;">0ms</span>\\n',
        '                      </div>\\n',
        '                      <div class="my-0"><i id="seq-s1-icon" class="fas fa-door-closed fa-lg text-muted" style="transition: color 0.2s;"></i></div>\\n',
        '                      <div class="font-weight-bold" style="font-size: 11px; color: var(--eco-text-main); line-height: 1.2;">Servo Gate</div>\\n',
        '                      <div class="mt-0"><span id="seq-s1-state" class="badge badge-secondary px-1 py-0" style="font-size: 9px;">Closed (0deg)</span></div>\\n',
        '                    </div>\\n',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-1" class="fas fa-chevron-right text-muted" style="font-size: 0.85rem;"></i></div>\\n',
        '                    <!-- STAGE 2: PIR Intake (Top IR) -->\\n',
        '                    <div id="seq-node-2" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 115px; transition: all 0.2s ease;">\\n',
        '                      <div class="d-flex justify-content-between align-items-center px-1">\\n',
        '                        <span class="badge text-muted font-mono" style="font-size: 8.5px; background: var(--eco-card); border: 1px solid var(--eco-border);">S2</span>\\n',
        '                        <span id="seq-s2-lat" class="font-mono text-muted" style="font-size: 9px;">--</span>\\n',
        '                      </div>\\n',
        '                      <div class="my-0"><i id="seq-s2-icon" class="fas fa-radiation fa-lg text-muted" style="transition: color 0.2s;"></i></div>\\n',
        '                      <div class="font-weight-bold" style="font-size: 11px; color: var(--eco-text-main); line-height: 1.2;">PIR Intake</div>\\n',
        '                      <div class="mt-0"><span id="seq-s2-state" class="badge badge-secondary px-1 py-0" style="font-size: 9px;">Clear (HIGH)</span></div>\\n',
        '                    </div>\\n',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-2" class="fas fa-chevron-right text-muted" style="font-size: 0.85rem;"></i></div>\\n',
        '                    <!-- STAGE 3: NIR Spectrometer -->\\n',
        '                    <div id="seq-node-3" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 135px; transition: all 0.2s ease;">\\n',
        '                      <div class="d-flex justify-content-between align-items-center px-1">\\n',
        '                        <span class="badge text-muted font-mono" style="font-size: 8.5px; background: var(--eco-card); border: 1px solid var(--eco-border);">S3</span>\\n',
        '                        <span id="seq-s3-lat" class="font-mono text-muted" style="font-size: 9px;">--</span>\\n',
        '                      </div>\\n',
        '                      <div class="my-0"><i id="seq-s3-icon" class="fas fa-microscope fa-lg text-muted" style="transition: color 0.2s;"></i></div>\\n',
        '                      <div class="font-weight-bold" style="font-size: 11px; color: var(--eco-text-main); line-height: 1.2;">NIR Sensor</div>\\n',
        '                      <div class="mt-0"><span id="seq-s3-state" class="badge badge-secondary px-1 py-0 font-mono" style="font-size: 9px;">Cal-W: --</span></div>\\n',
        '                    </div>\\n',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-3" class="fas fa-chevron-right text-muted" style="font-size: 0.85rem;"></i></div>\\n',
        '                    <!-- STAGE 4: HX711 Scale -->\\n',
        '                    <div id="seq-node-4" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 125px; transition: all 0.2s ease;">\\n',
        '                      <div class="d-flex justify-content-between align-items-center px-1">\\n',
        '                        <span class="badge text-muted font-mono" style="font-size: 8.5px; background: var(--eco-card); border: 1px solid var(--eco-border);">S4</span>\\n',
        '                        <span id="seq-s4-lat" class="font-mono text-muted" style="font-size: 9px;">--</span>\\n',
        '                      </div>\\n',
        '                      <div class="my-0"><i id="seq-s4-icon" class="fas fa-balance-scale fa-lg text-muted" style="transition: color 0.2s;"></i></div>\\n',
        '                      <div class="font-weight-bold" style="font-size: 11px; color: var(--eco-text-main); line-height: 1.2;">HX711 Scale</div>\\n',
        '                      <div class="mt-0"><span id="seq-s4-state" class="badge badge-secondary px-1 py-0 font-mono" style="font-size: 9px;">Mass: -- g</span></div>\\n',
        '                    </div>\\n',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-4" class="fas fa-chevron-right text-muted" style="font-size: 0.85rem;"></i></div>\\n',
        '                    <!-- STAGE 5: Drop Exit Flap -->\\n',
        '                    <div id="seq-node-5" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 115px; transition: all 0.2s ease;">\\n',
        '                      <div class="d-flex justify-content-between align-items-center px-1">\\n',
        '                        <span class="badge text-muted font-mono" style="font-size: 8.5px; background: var(--eco-card); border: 1px solid var(--eco-border);">S5</span>\\n',
        '                        <span id="seq-s5-lat" class="font-mono text-muted" style="font-size: 9px;">--</span>\\n',
        '                      </div>\\n',
        '                      <div class="my-0"><i id="seq-s5-icon" class="fas fa-box-open fa-lg text-muted" style="transition: color 0.2s;"></i></div>\\n',
        '                      <div class="font-weight-bold" style="font-size: 11px; color: var(--eco-text-main); line-height: 1.2;">Drop Servo</div>\\n',
        '                      <div class="mt-0"><span id="seq-s5-state" class="badge badge-secondary px-1 py-0" style="font-size: 9px;">Neutral (0deg)</span></div>\\n',
        '                    </div>\\n',
        '                    <div class="seq-arrow text-center px-0"><i id="seq-arr-5" class="fas fa-chevron-right text-muted" style="font-size: 0.85rem;"></i></div>\\n',
        '                    <!-- STAGE 6: PIR Drop Transit -->\\n',
        '                    <div id="seq-node-6" class="seq-card flex-fill text-center p-1 rounded" style="background: var(--eco-card-sub); border: 1px solid var(--eco-border); min-width: 115px; transition: all 0.2s ease;">\\n',
        '                      <div class="d-flex justify-content-between align-items-center px-1">\\n',
        '                        <span class="badge text-muted font-mono" style="font-size: 8.5px; background: var(--eco-card); border: 1px solid var(--eco-border);">S6</span>\\n',
        '                        <span id="seq-s6-lat" class="font-mono text-muted" style="font-size: 9px;">--</span>\\n',
        '                      </div>\\n',
        '                      <div class="my-0"><i id="seq-s6-icon" class="fas fa-check-circle fa-lg text-muted" style="transition: color 0.2s;"></i></div>\\n',
        '                      <div class="font-weight-bold" style="font-size: 11px; color: var(--eco-text-main); line-height: 1.2;">Drop PIR Clear</div>\\n',
        '                      <div class="mt-0"><span id="seq-s6-state" class="badge badge-secondary px-1 py-0" style="font-size: 9px;">Awaiting</span></div>\\n',
        '                    </div>\\n',
        '                  </div>\\n',
        '                </div>\\n',
        '              </div>\\n',
        '              <!-- BOTTOM GRID (TIMING TABLE LEFT + EVENT TERMINAL RIGHT) IN 1 ROW -->\\n',
        '              <div class="row" style="margin-top: 4px; margin-bottom: 0;">\\n',
        '                <!-- LEFT: Stage Latency Budget Table -->\\n',
        '                <div class="col-lg-5 col-12 mb-2 pr-lg-1">\\n',
        '                  <div class="card h-100 mb-0" style="background: var(--eco-card); border: 1px solid var(--eco-border); border-radius: 8px;">\\n',
        '                    <div class="card-header py-1 px-2 d-flex align-items-center justify-content-between" style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border); min-height: 32px;">\\n',
        '                      <div class="d-flex align-items-center">\\n',
        '                        <i class="fas fa-stopwatch-20 mr-1 text-warning" style="font-size: 0.95rem;"></i>\\n',
        '                        <span class="font-weight-bold" style="font-size: 11.5px; color: var(--eco-text-main);">Stage Latency Budget</span>\\n',
        '                      </div>\\n',
        '                      <span class="badge badge-secondary px-1 font-mono" style="font-size: 9px;">ms</span>\\n',
        '                    </div>\\n',
        '                    <div class="card-body p-0" style="background: var(--eco-card);">\\n',
        '                      <div class="table-responsive m-0">\\n',
        '                        <table class="table table-sm m-0" style="font-size: 10.5px; color: var(--eco-text-main);">\\n',
        '                          <thead>\\n',
        '                            <tr style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <th style="padding: 2px 6px;">Stage</th>\\n',
        '                              <th class="text-center" style="padding: 2px 4px;">Budget</th>\\n',
        '                              <th class="text-center" style="padding: 2px 4px;">Actual</th>\\n',
        '                              <th class="text-right" style="padding: 2px 6px;">Status</th>\\n',
        '                            </tr>\\n',
        '                          </thead>\\n',
        '                          <tbody>\\n',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <td style="padding: 2px 6px;">1. Servo Gate Open</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 2px 4px;">150-300ms</td>\\n',
        '                              <td id="row-s1-lat" class="text-center font-mono font-weight-bold" style="padding: 2px 4px; color: var(--eco-text-main);">--</td>\\n',
        '                              <td id="row-s1-stat" class="text-right" style="padding: 2px 6px;"><span class="badge badge-secondary px-1 py-0" style="font-size: 9px;">WAIT</span></td>\\n',
        '                            </tr>\\n',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <td style="padding: 2px 6px;">2. PIR Intake Intrusion</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 2px 4px;">200-8000ms</td>\\n',
        '                              <td id="row-s2-lat" class="text-center font-mono font-weight-bold" style="padding: 2px 4px; color: var(--eco-text-main);">--</td>\\n',
        '                              <td id="row-s2-stat" class="text-right" style="padding: 2px 6px;"><span class="badge badge-secondary px-1 py-0" style="font-size: 9px;">WAIT</span></td>\\n',
        '                            </tr>\\n',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <td style="padding: 2px 6px;">3. AS7263 NIR Spectroscopy</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 2px 4px;">140-400ms</td>\\n',
        '                              <td id="row-s3-lat" class="text-center font-mono font-weight-bold" style="padding: 2px 4px; color: var(--eco-text-main);">--</td>\\n',
        '                              <td id="row-s3-stat" class="text-right" style="padding: 2px 6px;"><span class="badge badge-secondary px-1 py-0" style="font-size: 9px;">WAIT</span></td>\\n',
        '                            </tr>\\n',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <td style="padding: 2px 6px;">4. HX711 Gravimetric Mass</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 2px 4px;">100-350ms</td>\\n',
        '                              <td id="row-s4-lat" class="text-center font-mono font-weight-bold" style="padding: 2px 4px; color: var(--eco-text-main);">--</td>\\n',
        '                              <td id="row-s4-stat" class="text-right" style="padding: 2px 6px;"><span class="badge badge-secondary px-1 py-0" style="font-size: 9px;">WAIT</span></td>\\n',
        '                            </tr>\\n',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <td style="padding: 2px 6px;">5. Drop Exit Servo</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 2px 4px;">100-300ms</td>\\n',
        '                              <td id="row-s5-lat" class="text-center font-mono font-weight-bold" style="padding: 2px 4px; color: var(--eco-text-main);">--</td>\\n',
        '                              <td id="row-s5-stat" class="text-right" style="padding: 2px 6px;"><span class="badge badge-secondary px-1 py-0" style="font-size: 9px;">WAIT</span></td>\\n',
        '                            </tr>\\n',
        '                            <tr style="border-bottom: 1px solid var(--eco-border);">\\n',
        '                              <td style="padding: 2px 6px;">6. PIR Drop Transit Clear</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 2px 4px;">250-3100ms</td>\\n',
        '                              <td id="row-s6-lat" class="text-center font-mono font-weight-bold" style="padding: 2px 4px; color: var(--eco-text-main);">--</td>\\n',
        '                              <td id="row-s6-stat" class="text-right" style="padding: 2px 6px;"><span class="badge badge-secondary px-1 py-0" style="font-size: 9px;">WAIT</span></td>\\n',
        '                            </tr>\\n',
        '                            <tr style="background: var(--eco-card-sub); font-weight: 700;">\\n',
        '                              <td style="padding: 2px 6px; color: var(--eco-text-main);">Total Duration</td>\\n',
        '                              <td class="text-center text-muted font-mono" style="padding: 2px 4px;">~1.5-4.5s</td>\\n',
        '                              <td id="row-tot-lat" class="text-center font-mono text-success" style="padding: 2px 4px;">0ms</td>\\n',
        '                              <td id="row-tot-stat" class="text-right" style="padding: 2px 6px;"><span id="badge-tot-stat" class="badge badge-secondary px-1 py-0" style="font-size: 9px;">IDLE</span></td>\\n',
        '                            </tr>\\n',
        '                          </tbody>\\n',
        '                        </table>\\n',
        '                      </div>\\n',
        '                    </div>\\n',
        '                  </div>\\n',
        '                </div>\\n',
        '                <!-- RIGHT: Fine-Timestamped Chute Log Terminal -->\\n',
        '                <div class="col-lg-7 col-12 mb-2 pl-lg-1">\\n',
        '                  <div class="card h-100 mb-0" style="background: var(--eco-card); border: 1px solid var(--eco-border); border-radius: 8px;">\\n',
        '                    <div class="card-header py-1 px-2 d-flex align-items-center justify-content-between flex-wrap" style="background: var(--eco-card-sub); border-bottom: 1px solid var(--eco-border); min-height: 32px; gap: 4px;">\\n',
        '                      <div class="d-flex align-items-center">\\n',
        '                        <i class="fas fa-terminal text-success mr-1" style="font-size: 0.95rem;"></i>\\n',
        '                        <span class="font-weight-bold" style="font-size: 11.5px; color: var(--eco-text-main);">Chute Diagnostics Event Stream</span>\\n',
        '                      </div>\\n',
        '                      <div class="d-flex align-items-center" style="gap: 4px;">\\n',
        '                        <button class="btn btn-xs btn-outline-info px-1 py-0" onclick="copySequenceTerminalLogs()" title="Copy logs" style="font-size: 10px;"><i class="fas fa-copy mr-1"></i>Copy</button>\\n',
        '                        <a href="/admin/api/system/logs/download" class="btn btn-xs btn-outline-success px-1 py-0" title="Download .LOG" style="font-size: 10px;"><i class="fas fa-download mr-1"></i>Download</a>\\n',
        '                        <button class="btn btn-xs btn-outline-danger px-1 py-0" onclick="clearSystemLogs()" title="Clear logs" style="font-size: 10px;"><i class="fas fa-trash"></i></button>\\n',
        '                      </div>\\n',
        '                    </div>\\n',
        '                    <div class="card-body p-1" style="background: #0f172a; border-radius: 0 0 8px 8px;">\\n',
        '                      <div id="seq-log-terminal" style="font-family: Consolas, monospace; font-size: 10.5px; height: 145px; overflow-y: auto; color: #a7f3d0; line-height: 1.45; white-space: pre-wrap; word-break: break-all; padding: 4px 6px;">\\n',
        '                        <span style="color: #64748b;">[System Ready] Awaiting bottle deposit sequence...</span>\\n',
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
        '                    <i class="fas fa-terminal text-info mr-1"></i>\\n',
        '                    <span class="m-0 font-weight-bold" style="font-size: 12.5px; color: var(--eco-text-main);">System Event Log Inspector</span>\\n',
        '                    <span class="badge border text-info px-2 py-0 font-mono" style="font-size: 10px; background: var(--eco-card); border-color: var(--eco-border) !important;">/opt/ecofi/system_events.log</span>\\n',
        '                  </div>\\n',
        '                  <div class="d-flex align-items-center flex-wrap" style="gap: 6px;">\\n',
        '                    <select id="sys-log-cat" class="form-control form-control-sm text-xs py-0" style="width: auto; height: 26px; background: var(--eco-card); color: var(--eco-text-main); border-color: var(--eco-border);" onchange="fetchSystemLogs(true)">\\n',
        '                      <option value="ALL">All Categories</option>\\n',
        '                      <option value="CHUTE">Chute Sequence</option>\\n',
        '                      <option value="SENSOR">Sensors (NIR, Scale, IR)</option>\\n',
        '                      <option value="ACTUATOR">Servos &amp; Actuators</option>\\n',
        '                      <option value="SESSION">Deposit Sessions</option>\\n',
        '                      <option value="SYSTEM">System &amp; Microcontroller</option>\\n',
        '                      <option value="NETWORK">Network &amp; Firewall</option>\\n',
        '                    </select>\\n',
        '                    <select id="sys-log-lvl" class="form-control form-control-sm text-xs py-0" style="width: auto; height: 26px; background: var(--eco-card); color: var(--eco-text-main); border-color: var(--eco-border);" onchange="fetchSystemLogs(true)">\\n',
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
        '                <div class="card-body p-2" style="background: #0f172a; border-radius: 0 0 8px 8px;">\\n',
        '                  <div id="sys-log-terminal" style="font-family: Consolas, monospace; font-size: 11px; height: 380px; overflow-y: auto; color: #cbd5e1; line-height: 1.5; white-space: pre-wrap; word-break: break-all; padding: 4px 6px;">\\n',
        '                    <span class="text-muted">Loading system event logs...</span>\\n',
        '                  </div>\\n',
        '                </div>\\n',
        '              </div>\\n',
        '            </div>\\n'
    ]
    return ''.join(lines)


def load_and_escape_js():
    with open(JS_CLIENT_PATH, 'r', encoding='utf-8') as f:
        js_raw = f.read()
    return js_raw.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')


def main():
    print("Reading host/portal.py...")
    with open(PORTAL_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # --- 1. Replace Sequence Pane ---
    start_seq = '<!-- TAB 6: CHUTE SEQUENCE & SENSOR MONITOR -->'
    end_seq = '<!-- Global Card Footer: Save & Status -->'
    idx_seq1 = content.find(start_seq)
    idx_seq2 = content.find(end_seq, idx_seq1)
    if idx_seq1 == -1 or idx_seq2 == -1:
        print("ERROR: sequence pane boundaries not found!")
        sys.exit(1)

    new_seq_html = build_compact_sequence_pane()
    content = content[:idx_seq1] + new_seq_html + content[idx_seq2:]
    print("1. Replaced sequence monitor pane with compact one-page theme layout.")

    # --- 2. Replace System Logs Pane ---
    start_sys = '<!-- SUB-TAB 4: SYSTEM EVENT LOGGER -->'
    idx_sys1 = content.find(start_sys)
    if idx_sys1 != -1:
        end_sys = '</div>\\n\\n          </div>\\n        </div>\\n      </div>\\n    </div>'
        idx_sys2 = content.find(end_sys, idx_sys1)
        if idx_sys2 != -1:
            new_sys_html = build_theme_sys_logs_pane()
            content = content[:idx_sys1] + new_sys_html + content[idx_sys2 + 7:]
            print("2. Replaced system logger pane with theme-native layout.")

    # --- 3. Replace JavaScript Client Engine ---
    js_start = "// --- CHUTE SEQUENCE & SYSTEM LOGGER CLIENT ENGINE ---"
    js_end = "applyTheme(savedTheme, false);\\n} catch(e) {}\\n\\n"
    idx_j1 = content.find(js_start)
    if idx_j1 != -1:
        idx_j2 = content.find(js_end)
        if idx_j2 != -1:
            end_of_js = content.find("\\n\\n</script>", idx_j1)
            escaped_js = load_and_escape_js()
            content = content[:idx_j1] + escaped_js + content[end_of_js:]
            print("3. Updated client JavaScript engine with theme-aware node styles.")

    with open(PORTAL_PATH, 'w', encoding='utf-8') as f:
        f.write(content)
    print("host/portal.py successfully updated!")

if __name__ == '__main__':
    main()
