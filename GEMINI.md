# VMC ECO-VENDO Project Rules for Gemini / Antigravity
> This repository uses **`AGENTS.md`** as the authoritative source of truth.

### Primary Directives for Gemini / Antigravity
1. **Reference Document:** Review [`AGENTS.md`](./AGENTS.md) for full hardware, database, captive portal, and licensing specifications.
2. **Python 3.5.3 Strictness:** Target platform is Python 3.5.3 on 32-bit ARM. Never use f-strings or modern Python 3.6+ features on target code in `host/`.
3. **Database Integrity:**
   - Table `pause_budgets` does NOT have an `updated_at` column.
   - Database operations must pass `test_entitlement_regressions.py` (all 87 tests).
4. **Live Deployment Reference:**
   - Live OPi is the debugging ground truth.
   - When deploying to `/opt/ecofi/`, restart `ecofi_portal.service` and verify status.
   - Ensure the OS release image in `resources/` is rebuilt to match the live system.
5. **NIR Material Discrimination Reference:**
   - Empirical AS7263 calibration and spectroscopy research is archived in [`docs/AS7263_NIR_CALIBRATION_RESEARCH.md`](docs/AS7263_NIR_CALIBRATION_RESEARCH.md).
   - Empty air baseline is ~24.5 uW/cm², clear PET is 35–65 uW/cm², colored glass absorbs (<22 uW/cm²), and paper spikes (>220 uW/cm²).
6. **Authoritative Admin Credentials (PERMANENT RULE):**
   - **Default Username:** `admin`
   - **Default Password:** `admin1234`
   - AI agents must NEVER modify, overwrite, or randomize the default admin credentials in code, tests, scripts, or database under any circumstances.
7. **Authoritative Golden Release Baseline (PERMANENT RULE):**
   - **v2.3.19 is definitively confirmed as the stable release** across both the Orange Pi gateway and ESP32 firmwares, preserving all timing, retrieval state machines, chime synchronization, and scale baseline routines originating from v2.3.17 (commit `87c7cb7`) and v2.3.18.
   - Authoritative hardware & sensor calibration defaults: NIR W [10 - 120], Weight [20 - 90]g, HX711 cal factor 260, Horiz. bin 18cm @ 3s, Gate 65s, Settle 1000ms, Drop 7500ms, Retrieval 50s, servos 0°/90°.
   - Key stable features: 7-stage chute sequence pipeline, drop flap anti-crush jam prevention on timeout, trimmed-mean HX711 weight filtering with cradle deadweight-aware tare, and non-blocking credit journal boot recovery.
8. **Captive Portal CNA Auto-Popup Protocol (PERMANENT RULE):**
   - DHCP Option 114 MUST point to RFC 8908 JSON API (`http://10.0.0.1/api/captive-portal`). Never return raw HTML to Option 114 queries.
   - DNS MUST return `NXDOMAIN` for Apple iCloud Private Relay (`local=/mask.icloud.com/` and `local=/mask-h2.icloud.com/`).
   - Standard WISPr 2.0 XML handshake must be embedded in `PORTAL_HTML` and served at `/hotspot.html`.
9. **ESP32 20x4 LCD Buffer Invalidation (PERMANENT RULE):**
   - In `src/main.cpp`, `currentLcdLines` must be initialized empty; call `invalidateLcdBuffer()` on screen clears or state changes to ensure Rows 1 & 2 are never dropped. Dynamic rates cycle on Rows 3 & 4 every 3.5s.
10. **Admin Panel Sidebar Information Architecture (PERMANENT RULE):**
   - The Admin Panel sidebar navigation is authoritatively structured into 5 distinct functional domains: `VENDO OPERATIONS`, `NETWORK & TRAFFIC`, `PORTAL & BRANDING`, `HARDWARE & SENSORS`, and `SYSTEM & MAINTENANCE`.
   - Never lump portal branding, traffic/networking, or OS maintenance items into a generic catch-all group. Ensure all 15 sections have direct mapping and active link state tracking in `showSection(secId)`.
11. **Live Firmware Ground-Truth Rule (PERMANENT RULE):**
   - When creating or building firmware (`firmware.bin`, `esp32_firmware_factory.bin`), AI agents must strictly follow what is flashed and running inside the live physical Orange Pi and ESP32 hardware. Never introduce speculative algorithm changes or diverging calibrations in firmware builds that deviate from the running live system ground truth.



