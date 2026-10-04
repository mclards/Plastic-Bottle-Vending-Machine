---
name: ecofi-development
description: >-
  Authoritative guidance, runbooks, and architectural reference for developing,
  testing, and deploying software on the VMC ECO-VENDO Reverse Vending Machine Orange Pi One stack.
  Activate when developing, debugging, testing, deploying, or building images for VMC ECO-VENDO.
---

# VMC ECO-VENDO Reverse Vending Machine Development Skill

> **Student Thesis Project:** *Eco-Vendo: An Empty Bottle-Initiated Internet Access Vending System*  
> **Target Board:** Orange Pi One / PC (Allwinner H2+/H3 Quad-Core Cortex-A7, 32-bit ARMv7)  
> **Runtime Environment:** Armbian / Debian Stretch, **Python 3.5.3**

---

## 1. Quick Reference & Core Inviolables

1. **Target Python 3.5.3 Strictness:**
   - **NEVER USE PYTHON 3.6+ SYNTAX** in `host/` code executed on the Orange Pi.
   - **NO f-strings (`f"..."`)**: Use `"...".format(...)` or string concatenation.
   - **NO variable type annotations** inside functions.
   - Run `python tools/check_python35.py` before any commit.
2. **Authoritative Admin Credentials (PERMANENT RULE):**
   - Username: `admin` | Password: `admin1234`
   - Forced password change on first login with default `admin1234` must be preserved.
   - AI agents must NEVER change, overwrite, or randomize default credentials under any circumstances.
3. **Database Schema Constraint:**
   - Table `pause_budgets` in `/opt/ecofi/vendo_sessions.db` **DOES NOT HAVE AN `updated_at` COLUMN**.
   - Attempting `UPDATE pause_budgets SET ..., updated_at=?` triggers a 503 backend crash.
4. **Authoritative Stable Version Baseline (v2.3.18):**
   - **v2.3.18 is definitively confirmed as the stable baseline** across gateway and ESP32 firmwares.
   - Authoritative calibration defaults: NIR Cal-W `[10.0 - 120.0]`, Weight `[20.0 - 90.0]g`, HX711 cal factor `260.0`, Horiz. bin `18cm @ 3s`, Gate `65s`, Settle `1000ms`, Drop `3100ms`, Retrieval `50s`, servos `0°`/`90°`.
   - Never modify core state machines or scale routines without benchmarking against commit `87c7cb7`.
5. **Captive Portal & CNA Auto-Popup Protocol:**
   - DHCP Option 114 MUST point to an RFC 8908 JSON endpoint (`http://10.0.0.1/api/captive-portal`) returning `application/captive+json`. Never serve raw HTML to Option 114 probes or iOS suppresses the CNA modal sheet.
   - DNS MUST return `NXDOMAIN` for Apple iCloud Private Relay (`local=/mask.icloud.com/` and `local=/mask-h2.icloud.com/`).
   - WISPr 2.0 XML handshake (`<WISPAccessGatewayParam>`) must be present in `PORTAL_HTML` and `/hotspot.html`.
6. **Live OPi is Ground Truth:**
   - Connect via `tools/opi_access.py` (`from tools.opi_access import connect`).
   - Live hardware is the authoritative benchmark. Always verify service health with `systemctl is-active ecofi_portal.service`.
7. **OS Image Synchronization:**
   - Rebuilding `resources/EcoFi_Opi_v<VERSION>.img` via WSL must 100% reflect the live board state, with matching `.md5` and `.sha256` checksums committed to version control.

---

## 2. Progressive Disclosure References

Detailed domain-specific guides are archived in the `references/` directory:

- [Captive Portal & Mobile CNA Reference](./references/captive_portal_and_cna.md): Apple iOS CNA auto-popup sheet, RFC 8908 JSON API, RFC 8910 Option 114, iCloud Private Relay NXDOMAIN, WISPr 2.0 XML protocol, and DNS/firewall interception.
- [ESP32 Firmware & 20x4 LCD Display](./references/esp32_firmware_and_lcd.md): LCD 20x4 buffer invalidation (`invalidateLcdBuffer()`), dynamic multi-rate cycling (3.5s page rotation), HX711 scale calibration philosophy, and PlatformIO in-situ UART flashing.
- [Mobile UI & Responsive Guidelines](./references/mobile_ui_guidelines.md): Monolithic HTML quote escaping (`\'` / `&quot;`), `.card-header.p-0` sub-tab preservation, fluid touch scrolling, iPhone 17 Pro Max scaling, and iOS safe areas (`viewport-fit=cover`).
- [Hardware Architecture & ESP32 Flashing](./references/hardware_and_flashing.md): In-situ UART flashing (`/dev/ttyS3`), GPIO 1 (EN) and GPIO 0 (IO0) bootloader sequencing, protocol frames, and baud rates.
- [AS7263 NIR Spectroscopy Guide](./references/nir_spectroscopy_guide.md): 6-channel NIR calibration research, empirical thresholds (empty air ~24.5 uW/cm², clear PET 35-65 uW/cm², colored glass <22 uW/cm², paper >230 uW/cm²), and multimodal sensor fusion.
- [Deployment & Image Pipeline](./references/deployment_and_image_pipeline.md): Pre-flight checks, automated deploy scripts, QEMU ARM static emulation testing, and WSL build pipeline.
- [Database & Entitlement Schema](./references/database_and_entitlements.md): SQLite table definitions, wallet decommissioning, MAC session binding, time policy, and the 87 regression test suite.
- [High-Speed Performance & Caching Engine](./references/performance_and_caching.md): Sub-150ms TTFB, 97.5% image payload reduction (<300 KB budget), template AST precompilation (220x speedup), kernel `/proc/net/arp` fast path, and Nginx persistent keepalive upstream.

---

## 3. Operational Runbooks

### Runbook A: Modifying & Testing Portal / Admin UI
1. Edit HTML/JS embedded variables in `host/portal.py`.
2. Ensure all inline JavaScript single quotes are escaped (`\'` or `&quot;`).
3. Run JavaScript syntax validation:
   ```bash
   python tools/check_js_syntax.py
   ```
4. Run Python 3.5 compatibility check:
   ```bash
   python tools/check_python35.py
   ```
5. Deploy host modules to live Orange Pi:
   ```bash
   python tools/deploy_to_opi.py --host-only
   ```
6. Verify live service state:
   ```bash
   python -c "from tools.opi_access import connect; c = connect(); _, o, _ = c.exec_command('systemctl is-active ecofi_portal.service'); print(o.read().decode().strip()); c.close()"
   ```

---

### Runbook B: Running Entitlement Regression Tests
1. Change directory to root and execute:
   ```bash
   python host/test_entitlement_regressions.py
   ```
2. Verify all 87 tests pass (`OK`).

---

### Runbook C: Modifying & Flashing ESP32 Firmware & LCD
1. Edit firmware source in `src/main.cpp`.
2. Compile firmware binary using PlatformIO:
   ```bash
   pio run
   ```
3. Update release binaries:
   ```bash
   cp .pio/build/esp32dev/firmware.bin resources/firmware.bin
   cp .pio/build/esp32dev/firmware.bin resources/esp32_firmware_factory.bin
   ```
4. Deploy and flash to ESP32 on live Orange Pi:
   ```bash
   python tools/deploy_to_opi.py
   ```
5. Run automated hardware smoke tests:
   ```bash
   python tools/smoke_test.py
   ```

---

### Runbook D: Diagnosing Captive Portal & Mobile CNA Probes
1. Inspect live Nginx and DNS probe requests on the Orange Pi:
   ```bash
   python -c "from tools.opi_access import connect; c = connect(); _, o, _ = c.exec_command('tail -n 20 /var/log/nginx/ecofi_access.log'); print(o.read().decode()); c.close()"
   ```
2. Test RFC 8908 JSON API endpoint:
   ```bash
   python -c "from tools.opi_access import connect; c = connect(); _, o, _ = c.exec_command('curl -s -i http://127.0.0.1/api/captive-portal'); print(o.read().decode()); c.close()"
   ```
   Must return HTTP 200 OK with `Content-Type: application/captive+json`.
3. Verify Apple probe 302 redirection:
   ```bash
   python -c "from tools.opi_access import connect; c = connect(); _, o, _ = c.exec_command('curl -s -i -H \"Host: captive.apple.com\" http://127.0.0.1/hotspot-detect.html'); print(o.read().decode()); c.close()"
   ```
   Must return HTTP 302 FOUND with `Location: http://10.0.0.1/hotspot.html`.
4. Verify iCloud Private Relay NXDOMAIN:
   ```bash
   python -c "from tools.opi_access import connect; c = connect(); _, o, _ = c.exec_command('dig @127.0.0.1 mask.icloud.com'); print(o.read().decode()); c.close()"
   ```
   Must return `status: NXDOMAIN`.

---

### Runbook E: Full System Release & Image Rebuilding
1. Increment version string in `VERSION` (e.g., `2.3.18`).
2. Run automated validation:
   ```bash
   python tools/check_python35.py
   python tools/check_js_syntax.py
   python host/test_entitlement_regressions.py
   ```
3. Deploy to live hardware:
   ```bash
   python tools/deploy_to_opi.py
   ```
4. Rebuild the release OS image in WSL Ubuntu:
   ```bash
   wsl -d Ubuntu -u root -- bash -c "cd /mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine && bash build_ecofi_img.sh"
   ```
5. Commit code, `VERSION`, and updated `.md5` / `.sha256` checksums.
