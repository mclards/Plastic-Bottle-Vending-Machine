# Audit 06: Build Script (`build_ecofi_img.sh`) Audit

**Date:** August 17, 2026  
**File:** [build_ecofi_img.sh](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/build_ecofi_img.sh)

---

## Overview

The build script takes the original PisoFi OS image, copies it, mounts the ext4 partition, purges all legacy PisoFi services, injects ECO-Fi software, and configures Nginx + systemd for our portal.

---

## Step-by-Step Analysis

### Step 1: Image Copy ✅
```bash
cp "$BASE_IMG" "$TARGET_IMG"
```
**Assessment:** Correct. Creates a working copy so the original image is preserved.

### Step 2: Mount ext4 Partition ✅
```bash
mount -o loop,offset=4194304 "$TARGET_IMG" "$MOUNT_DIR"
```
**Assessment:** Correct. Offset `4194304` = `8192 × 512` bytes, which is the standard partition offset for Orange Pi images.

### Step 3: Purge Legacy PisoFi ⚠️ INCOMPLETE
```bash
rm -f "$MOUNT_DIR/etc/systemd/system/pisofi_"*
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/pisofi_"*
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/zerotier-one.service"
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/php7.0-fpm.service"
rm -rf "$MOUNT_DIR/var/www/html/pisofi"
rm -rf "$MOUNT_DIR/var/www/html/"*
rm -rf "$MOUNT_DIR/.cache"
```

**Finding BUILD-01: Incomplete Purge** [MEDIUM]

The following PisoFi artifacts are **NOT removed**:
- `/home/pi/.dat/` — All hidden shell scripts and obfuscated PHP (the entire `/home/pi/.dat/devnull/.../` tree)
- `/usr/local/bin/pisofier` — The legacy firewall manager (16KB bash script)
- `/usr/local/bin/pisofi_resetconnections` — Legacy bandwidth recalculator (7KB)
- `/usr/local/bin/site_control` — Legacy domain controller
- `/usr/local/bin/mac_control` — Legacy MAC controller
- `/usr/local/bin/reset_pins` — Legacy GPIO init
- `/usr/local/bin/ngrok` — 30MB Ngrok binary (remote access backdoor!)
- `/usr/local/bin/composer` — 1.9MB PHP dependency manager (unnecessary)
- `/usr/local/bin/cmd-runner.py` — Python command runner
- `/usr/src/pfi/` — PisoFi backup configs
- `/home/pi/.ngrok2/` — Ngrok configuration

**Recommended additions:**
```bash
# Complete purge of PisoFi remnants
rm -rf "$MOUNT_DIR/home/pi/.dat" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/pisofier" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/pisofi_resetconnections" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/site_control" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/mac_control" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/reset_pins" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/ngrok" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/composer" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/cmd-runner.py" 2>/dev/null || true
rm -rf "$MOUNT_DIR/usr/src/pfi" 2>/dev/null || true
rm -rf "$MOUNT_DIR/home/pi/.ngrok2" 2>/dev/null || true
rm -f "$MOUNT_DIR/home/pi/.git-credentials" 2>/dev/null || true
rm -f "$MOUNT_DIR/home/pi/.mysql_history" 2>/dev/null || true
```

### Step 4: Nginx Configuration ✅
**Assessment:** Correct and well-structured.

Intercepts configured:
- `/generate_204` → Android
- `/gen_204` → Android alt
- `/ncsi.txt` → Windows
- `/connecttest.txt` → Windows alt
- `/hotspot-detect.html` → iOS/macOS
- `/canonical.html` → Firefox
- `/connectivitycheck.gstatic.com` → Android (domain-as-path)
- `/connectivitycheck.android.com` → Android alt
- `/msftconnecttest.com` → Windows (domain-as-path)

All redirect to `http://10.0.0.1/` ✅

Static assets served directly from `/opt/ecofi/static/` with 7-day cache ✅  
Proxy to `127.0.0.1:5000` with proper headers ✅

**Finding BUILD-02: Missing Nginx WebSocket Proxy** [LOW]
The original PisoFi had a WebSocket proxy on `/ws` to port 8080. While ECO-Fi uses polling instead of WebSocket, if WebSocket support is ever added, a `/ws` location block would need to be added.

### Step 4.5: Static IP Configuration ✅ (Added by our patch)
```bash
cat << 'EOF' > "$MOUNT_DIR/etc/network/interfaces.d/eth0"
auto eth0
iface eth0 inet static
    address 10.0.0.1
    netmask 255.255.255.0
EOF
```

**Finding BUILD-03: Subnet Mask Too Restrictive** [MEDIUM]
The original PisoFi uses a `/19` subnet (`255.255.224.0`) supporting ~8,000 clients. Our config uses `/24` (`255.255.255.0`) supporting only 254 clients.

**Recommended Fix:**
```bash
    netmask 255.255.224.0
```

**Finding BUILD-04: Wrong Interface Name** [HIGH]
As noted in the Network Audit (NET-01), `eth0` may be the WAN port on the Orange Pi One. The LAN interface is typically `eth1`. This needs to be verified against the actual hardware.

### Step 5: Software Injection ✅
```bash
cp "$SOURCE_HOST/portal.py" "$MOUNT_DIR/opt/ecofi/"
cp "$SOURCE_HOST/license_manager.py" "$MOUNT_DIR/opt/ecofi/"
cp "$SOURCE_HOST/esp32_simulator.py" "$MOUNT_DIR/opt/ecofi/"
```

**Finding BUILD-05: Missing Python Dependencies** [HIGH]
The script copies Python files but **does not install pip dependencies**. `portal.py` requires:
- `flask`
- `werkzeug`
- `pyserial` (for hardware serial)

The base PisoFi image has Python 3 but likely not Flask.

**Recommended Fix:**
```bash
# Install Python dependencies into the image
chroot "$MOUNT_DIR" /bin/bash -c "pip3 install flask werkzeug pyserial 2>/dev/null || \
    python3 -m pip install flask werkzeug pyserial"
```

Or bundle the dependencies:
```bash
# Pre-install on host and copy site-packages
pip3 install --target="$MOUNT_DIR/opt/ecofi/lib" flask werkzeug pyserial
# Then in ecofi_portal.service, set PYTHONPATH=/opt/ecofi/lib
```

### Step 6: Systemd Services ✅ (patched — daemon.py removed)
Only `ecofi_portal.service` remains after our patch.

**Finding BUILD-06: No Log Rotation** [LOW]
The portal service has `Restart=always RestartSec=3` which is correct. However, there is no log rotation configured. If `portal.py` writes to stdout/stderr, the journal will grow unbounded.

**Recommended Addition:**
```bash
# Add log rotation
cat << 'EOF' > "$MOUNT_DIR/etc/logrotate.d/ecofi"
/opt/ecofi/*.log {
    daily
    rotate 3
    compress
    missingok
    notifempty
    maxsize 10M
}
EOF
```

---

## Missing Build Steps

### BUILD-07: No dnsmasq Configuration Update [HIGH]
The script does not modify dnsmasq.conf. The base image still has:
- `interface=eth1` (may be correct for the hardware but needs verification)
- `domain=portal.pisofiapp.com` (stale PisoFi reference)
- `dhcp-option=160,http://portal.pisofiapp.com` (stale)

**Recommended Fix:** See Network Audit (NET-05).

### BUILD-08: No Firewall Initialization Script [MEDIUM]
The original PisoFi has a dedicated `pisofi_rules.service` that runs on boot to flush and reinitialize all iptables rules. ECO-Fi's `setup_firewall()` runs inside `portal.py`'s `time_daemon()`, which means firewall rules are not applied until the Python app starts.

**Recommended Fix:** Add a lightweight systemd service that runs before `ecofi_portal.service`:
```bash
cat << 'EOF' > "$MOUNT_DIR/etc/systemd/system/ecofi_firewall.service"
[Unit]
Description=ECO-Fi Firewall Initialization
Before=ecofi_portal.service
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c "ipset create ecofi_auth hash:ip timeout 86400 -exist; \
    iptables -P FORWARD DROP; \
    iptables -A FORWARD -m set --match-set ecofi_auth src -j ACCEPT; \
    iptables -A FORWARD -m state --state ESTABLISHED,RELATED -j ACCEPT; \
    iptables -A FORWARD -p udp --dport 53 -j ACCEPT; \
    iptables -t nat -A PREROUTING -m set ! --match-set ecofi_auth src -p tcp --dport 80 -j REDIRECT --to-port 80"
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
```

### BUILD-09: No Filesystem Resize on First Boot [LOW]
The original PisoFi's startup script calls `systemctl start armbian-resize-filesystem` to expand the partition to fill the entire SD card on first boot. Our build script does not ensure this service is enabled.

**Assessment:** This is likely already handled by the Armbian base, but worth verifying.

---

## Build Script Quality Summary

| Aspect | Status | Notes |
|--------|--------|-------|
| Image copy | ✅ Good | Preserves original |
| Mount/unmount | ✅ Good | Correct offset, clean unmount |
| PisoFi purge | ⚠️ Incomplete | Misses hidden scripts, ngrok, binaries |
| Nginx config | ✅ Good | All captive portal intercepts covered |
| Static IP | ⚠️ Issues | Wrong subnet mask, possibly wrong interface |
| Software copy | ⚠️ Partial | Missing pip dependencies |
| Systemd services | ✅ Good | Clean single-service design |
| dnsmasq update | ❌ Missing | Stale PisoFi domain references remain |
| Firewall init | ❌ Missing | No standalone firewall service |
| Sync/unmount | ✅ Good | `sync` before unmount |
