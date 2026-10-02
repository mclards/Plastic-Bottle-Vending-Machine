# Deployment, Testing & OS Image Rebuilding Pipeline
> **Target Release:** `resources/EcoFi_Opi_v<VERSION>.img` | **Live Target:** Orange Pi One at `10.0.0.1` / Tailscale.

---

## 1. Pre-Deployment Validation Checklist

Always execute these three automated validation checks before deploying code or rebuilding images:

```bash
# 1. Python 3.5.3 syntax check (strict enforcement: NO f-strings, NO inside-function type hints)
python tools/check_python35.py

# 2. JavaScript syntax check via Node.js for embedded HTML string literals
python tools/check_js_syntax.py

# 3. Comprehensive SQLite entitlement regression test suite (all 82 tests must pass)
cd host && python -m unittest test_entitlement_regressions
```

---

## 2. Deploying to the Live Orange Pi

The live Orange Pi hardware is the authoritative ground-truth reference for VMC ECO-VENDO.

### Deployment Commands
```bash
# Deploy host software only (restarts ecofi_portal.service, skips ESP32 reflashing):
python tools/deploy_to_opi.py --host-only

# Full deployment (uploads host modules AND flashes ESP32 via GPIO flasher):
python tools/deploy_to_opi.py
```

### Post-Deployment Health Verification
```bash
# Verify portal service is active:
python -c "from tools.opi_access import connect; c = connect(); _, o, _ = c.exec_command('systemctl is-active ecofi_portal.service'); print(o.read().decode().strip()); c.close()"

# Verify /api/status responds:
python -c "from tools.opi_access import connect; c = connect(); _, o, _ = c.exec_command('curl -s http://127.0.0.1:5000/api/status'); print(o.read().decode()[:200]); c.close()"
```

---

## 3. OS Image Rebuilding Pipeline

Whenever host code, configuration, or firmware changes are finalized, the release image must be rebuilt:

```bash
# 1. Bump the release version in VERSION file:
echo "2.3.15" > VERSION

# 2. Execute the root build script inside WSL Ubuntu:
wsl -d Ubuntu -u root -- bash -c "cd /mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine && bash build_ecofi_img.sh"
```

### Build Pipeline Stages
1. **Base Copy:** Mounts clean baseline image `resources/EcoFi_Opi_v2.1.img`.
2. **Purge:** Completely removes legacy PisoFi scripts, backdoors, MySQL, and daemons.
3. **Network Configuration:** Enforces `eth0` WAN (DHCP) + `eth1` LAN (10.0.0.1/19 static + `dnsmasq` captive portal).
4. **Injection:** Injects Python 3.5 wheels, `ipset`, `iptables`, and `/opt/ecofi/` modules.
5. **ARM QEMU Emulation Verification:**
   - Compiles all injected Python modules under simulated 32-bit ARM Python 3.5.3.
   - Runs `verify_arm_runtime.py` inside rootfs.
   - Tests `dnsmasq --test --conf-file=/etc/dnsmasq.conf`.
6. **Filesystem Check & Checksums:** Runs `e2fsck -f -n` and produces `.md5` and `.sha256` checksums.

---

## 4. Permanent Inviolable Rules
1. **Authoritative Admin Credentials:**
   - Username: `admin` | Password: `admin1234`
   - AI agents must NEVER change or randomize default credentials.
2. **Database Integrity:**
   - `pause_budgets` does NOT have an `updated_at` column.
3. **Commit Artifacts:**
   - Commit `.md5` and `.sha256` files. The raw `.img` is gitignored due to size (>3GB).
