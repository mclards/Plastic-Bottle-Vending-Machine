#ifndef INDEX_HTML_H
#define INDEX_HTML_H

const char* index_html PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ECO-Fi Hardware Configuration</title>
    <style>
        :root {
            --bg: #f8fafc;
            --card-bg: #ffffff;
            --border: #e2e8f0;
            --border-focus: #2563eb;
            --text-main: #0f172a;
            --text-muted: #64748b;
            --btn-bg: #0f172a;
            --btn-hover: #1e293b;
            --btn-text: #ffffff;
            --section-title: #475569;
            --sub-bg: #f8fafc;
            --sub-border: #e2e8f0;
        }

        @media (prefers-color-scheme: dark) {
            :root {
                --bg: #090d16;
                --card-bg: #111827;
                --border: #1f2937;
                --border-focus: #3b82f6;
                --text-main: #f9fafb;
                --text-muted: #9ca3af;
                --btn-bg: #2563eb;
                --btn-hover: #1d4ed8;
                --btn-text: #ffffff;
                --section-title: #cbd5e1;
                --sub-bg: #0b1120;
                --sub-border: #1e293b;
            }
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text-main);
            padding: 24px 16px 48px;
            display: flex;
            justify-content: center;
            line-height: 1.5;
        }

        .container {
            width: 100%;
            max-width: 500px;
        }

        .header {
            text-align: center;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border);
        }

        .header h1 {
            font-size: 19px;
            font-weight: 700;
            color: var(--text-main);
            letter-spacing: -0.01em;
        }

        .header p {
            font-size: 13px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        .section {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 18px 20px;
            margin-bottom: 16px;
        }

        .section-title {
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--section-title);
            margin-bottom: 14px;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--sub-border);
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .field-group {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .field {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }

        .field-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }

        .servo-block {
            background: var(--sub-bg);
            border: 1px solid var(--sub-border);
            border-radius: 6px;
            padding: 12px;
            margin-top: 10px;
        }

        .servo-block:first-of-type {
            margin-top: 0;
        }

        .servo-label {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-main);
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        label {
            font-size: 12px;
            font-weight: 500;
            color: var(--text-muted);
        }

        input[type="number"] {
            width: 100%;
            font-family: inherit;
            font-size: 14px;
            color: var(--text-main);
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 8px 10px;
            transition: border-color 0.15s, box-shadow 0.15s;
        }

        input[type="number"]:focus {
            outline: none;
            border-color: var(--border-focus);
            box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.2);
        }

        .section-footer {
            display: flex;
            justify-content: center;
            margin-top: 16px;
            padding-top: 12px;
            border-top: 1px solid var(--sub-border);
        }

        .btn-sm {
            width: 100%;
            font-family: inherit;
            font-size: 13px;
            font-weight: 600;
            color: var(--btn-text);
            background-color: var(--btn-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 9px 16px;
            cursor: pointer;
            transition: all 0.15s ease;
            text-align: center;
            box-sizing: border-box;
        }

        .btn-sm:hover {
            background-color: var(--btn-hover);
        }

        .btn-sm:active {
            opacity: 0.85;
        }

        .btn-sm.saved {
            background-color: #10b981;
            border-color: #10b981;
            color: #ffffff;
        }

        .footer-note {
            text-align: center;
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 16px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>ECO-Fi Hardware Configuration</h1>
            <p>ESP32 Controller Parameters &amp; Servo Calibration</p>
        </div>

        <!-- Sensors & Detection -->
        <form action="/save" method="POST" class="section">
            <div class="section-title"><span>📡</span> Sensors &amp; Detection</div>
            <div class="field-group">
                <div class="field">
                    <label for="bin_cm">Bin Full Distance Threshold (cm)</label>
                    <input type="number" id="bin_cm" name="bin_cm" value="%BIN_CM%" min="1" max="200" required>
                </div>
                <div class="field">
                    <label for="ent_tout">Entrance Door Timeout (seconds)</label>
                    <input type="number" id="ent_tout" name="ent_tout" value="%ENT_TOUT%" min="5" max="120" required>
                </div>
            </div>
            <div class="section-footer">
                <button type="submit" class="btn-sm">Save</button>
            </div>
        </form>

        <!-- Timings -->
        <form action="/save" method="POST" class="section">
            <div class="section-title"><span>⏱️</span> Timings &amp; Delays</div>
            <div class="field-group">
                <div class="field">
                    <label for="stl_ms">Airlock Stabilization Settle Time (ms)</label>
                    <input type="number" id="stl_ms" name="stl_ms" value="%STL_MS%" min="50" max="5000" step="50" required>
                </div>
                <div class="field">
                    <label for="suc_tout">Success Chute Drop Timeout (ms)</label>
                    <input type="number" id="suc_tout" name="suc_tout" value="%SUC_TOUT%" min="500" max="10000" step="100" required>
                </div>
                <div class="field">
                    <label for="rej_time">Reject Flap Hold Time (ms)</label>
                    <input type="number" id="rej_time" name="rej_time" value="%REJ_TIME%" min="500" max="10000" step="100" required>
                </div>
            </div>
            <div class="section-footer">
                <button type="submit" class="btn-sm">Save</button>
            </div>
        </form>

        <!-- Optical Sensor -->
        <form action="/save" method="POST" class="section">
            <div class="section-title"><span>🔬</span> AS7263 NIR Spectrometer</div>
            <div class="field-row">
                <div class="field">
                    <label for="nir_min">Min PET Threshold</label>
                    <input type="number" id="nir_min" name="nir_min" value="%NIR_MIN%" min="0" max="65535" required>
                </div>
                <div class="field">
                    <label for="nir_max">Max PET Threshold</label>
                    <input type="number" id="nir_max" name="nir_max" value="%NIR_MAX%" min="0" max="65535" required>
                </div>
            </div>
            <div class="section-footer">
                <button type="submit" class="btn-sm">Save</button>
            </div>
        </form>

        <!-- Servo Actuators -->
        <form action="/save" method="POST" class="section">
            <div class="section-title"><span>⚙️</span> PCA9685 Servo Positions (0–180°)</div>
            
            <div class="servo-block">
                <div class="servo-label"><span>⚙️</span> Channel 0 — Entrance Gate</div>
                <div class="field-row">
                    <div class="field">
                        <label for="ent_open">Open Angle (°)</label>
                        <input type="number" id="ent_open" name="ent_open" value="%ENT_OPEN%" min="0" max="180" required>
                    </div>
                    <div class="field">
                        <label for="ent_close">Close Angle (°)</label>
                        <input type="number" id="ent_close" name="ent_close" value="%ENT_CLOSE%" min="0" max="180" required>
                    </div>
                </div>
            </div>

            <div class="servo-block">
                <div class="servo-label"><span>✅</span> Channel 1 — Success Flap</div>
                <div class="field-row">
                    <div class="field">
                        <label for="suc_open">Open Angle (°)</label>
                        <input type="number" id="suc_open" name="suc_open" value="%SUC_OPEN%" min="0" max="180" required>
                    </div>
                    <div class="field">
                        <label for="suc_close">Close Angle (°)</label>
                        <input type="number" id="suc_close" name="suc_close" value="%SUC_CLOSE%" min="0" max="180" required>
                    </div>
                </div>
            </div>

            <div class="servo-block">
                <div class="servo-label"><span>❌</span> Channel 2 — Reject Flap</div>
                <div class="field-row">
                    <div class="field">
                        <label for="rej_open">Open Angle (°)</label>
                        <input type="number" id="rej_open" name="rej_open" value="%REJ_OPEN%" min="0" max="180" required>
                    </div>
                    <div class="field">
                        <label for="rej_close">Close Angle (°)</label>
                        <input type="number" id="rej_close" name="rej_close" value="%REJ_CLOSE%" min="0" max="180" required>
                    </div>
                </div>
            </div>

            <div class="section-footer">
                <button type="submit" class="btn-sm">Save</button>
            </div>
        </form>

        <div class="footer-note">Parameters are stored in non-volatile flash memory.</div>
    </div>

    <script>
        document.querySelectorAll('form').forEach(form => {
            form.addEventListener('submit', function(e) {
                e.preventDefault();
                const btn = this.querySelector('button[type="submit"]');
                const origText = btn.textContent;
                btn.textContent = 'Saving...';
                btn.disabled = true;
                fetch('/save', {
                    method: 'POST',
                    body: new FormData(this)
                }).then(() => {
                    btn.textContent = 'Saved ✓';
                    btn.classList.add('saved');
                    setTimeout(() => {
                        btn.textContent = origText;
                        btn.classList.remove('saved');
                        btn.disabled = false;
                    }, 1200);
                }).catch(() => {
                    btn.textContent = origText;
                    btn.disabled = false;
                    form.submit();
                });
            });
        });
    </script>
</body>
</html>
)rawliteral";

#endif
