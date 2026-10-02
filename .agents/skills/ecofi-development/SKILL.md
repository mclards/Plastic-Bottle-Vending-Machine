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
   - AI agents must NEVER change, overwrite, or randomize default credentials under any circumstances.
3. **Database Schema Constraint:**
   - Table `pause_budgets` in `/opt/ecofi/vendo_sessions.db` **DOES NOT HAVE AN `updated_at` COLUMN**.
   - Attempting `UPDATE pause_budgets SET ..., updated_at=?` triggers a 503 backend crash.
4. **Live OPi is Ground Truth:**
   - Connect via `tools/opi_access.py` (`from tools.opi_access import connect`).
   - Live hardware is the authoritative benchmark. Always verify service health with `systemctl is-active ecofi_portal.service`.
5. **OS Image Synchronization:**
   - Rebuilding `resources/EcoFi_Opi_v<VERSION>.img` via WSL must 100% reflect the live board state, with matching `.md5` and `.sha256` checksums committed to version control.

---

## 2. Progressive Disclosure References

Detailed domain-specific guides are archived in the `references/` directory:

- [Mobile UI & Responsive Guidelines](./references/mobile_ui_guidelines.md): Monolithic HTML quote escaping (`\'` / `&quot;`), `.card-header.p-0` sub-tab preservation, fluid touch scrolling, iPhone 17 Pro Max scaling, and iOS safe areas (`viewport-fit=cover`).
- [Hardware Architecture & ESP32 Flashing](./references/hardware_and_flashing.md): In-situ UART flashing (`/dev/ttyS3`), GPIO 1 (EN) and GPIO 0 (IO0) bootloader sequencing, protocol frames, and baud rates.
- [AS7263 NIR Spectroscopy Guide](./references/nir_spectroscopy_guide.md): 6-channel NIR calibration research, empirical thresholds (empty air ~24.5 uW/cm², clear PET 35-65 uW/cm², colored glass <22 uW/cm², paper >230 uW/cm²), and multimodal sensor fusion.
- [Deployment & Image Pipeline](./references/deployment_and_image_pipeline.md): Pre-flight checks, automated deploy scripts, QEMU ARM static emulation testing, and WSL build pipeline.
- [Database & Entitlement Schema](./references/database_and_entitlements.md): SQLite table definitions, wallet decommissioning, MAC session binding, time policy, and the 82 regression test suite.

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
1. Change directory to `host/`:
   ```bash
   cd host
   python -m unittest test_entitlement_regressions
   ```
2. Verify all 82 tests pass (`OK`).

---

### Runbook C: Full System Release & Image Rebuilding
1. Increment version string in `VERSION` (e.g., `2.3.15`).
2. Run automated validation:
   ```bash
   python tools/check_python35.py
   python tools/check_js_syntax.py
   cd host && python -m unittest test_entitlement_regressions
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
