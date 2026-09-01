# Phase 4 Implementation: Image Build Hardening

**Status:** Completed  
**Target File:** `build_ecofi_img.sh`  
**Date:** August 17, 2026

---

## Implemented Fixes

### 1. BUILD-01: Complete Purge of PisoFi Artifacts
**Issue:** The build script left behind dangerous PisoFi artifacts, including the `ngrok` binary backdoor, hidden `/home/pi/.dat` shell scripts, and legacy PHP binaries like `composer`.
**Fix Implemented:**
- Added rigorous cleanup commands (`rm -rf`) targeting `/home/pi/.dat`, `/usr/local/bin/ngrok`, `/usr/local/bin/pisofier`, and `.mysql_history`. 
- Ensures the final OS image is 100% free of PisoFi tracking and backdoors.

### 2. BUILD-03, NET-01 & BUILD-04: Dynamic LAN & Subnet Mask Fix
**Issue:** The build script hardcoded `eth0` as the LAN interface and limited the subnet to `/24` (254 clients), which could break if the hardware used `eth1` for LAN.
**Fix Implemented:**
- Replaced the hardcoded `/etc/network/interfaces.d/eth0` injection with a robust `/opt/ecofi/setup_network.sh` script.
- The script dynamically detects the WAN interface and assigns `10.0.0.1/19` (supporting ~8000 clients) to the first available non-WAN interface (`eth0`, `eth1`, or `br-lan`).
- It automatically updates `dnsmasq.conf` to bind to the dynamically discovered interface.

### 3. BUILD-08 & BUILD-05: Initialization Services
**Issue:** No boot-time firewall initialization existed, and Python dependencies were missing from the image.
**Fix Implemented:**
- Created `ecofi_firewall.service` (Type=oneshot): Runs `setup_network.sh` and applies default `iptables` rules (FORWARD DROP, captive portal redirects) before the Python portal starts.
- Created `ecofi_firstboot.service` (Type=oneshot): Automatically runs `pip3 install flask werkzeug pyserial` on the very first boot of the hardware, then disables itself.

### 4. GAP-06 & NET-05: Update DNS Hijacking
**Issue:** The base `dnsmasq` config still had stale references to `portal.pisofiapp.com`, breaking captive portal detection on some devices.
**Fix Implemented:**
- Added a `sed` command to replace all instances of `portal.pisofiapp.com` with `10.0.0.1` in `/etc/dnsmasq.conf`.
- Injected `/etc/dnsmasq.d/ecofi_captive.conf` with `address=/#/10.0.0.1` to enforce wildcard DNS hijacking for unauthenticated clients.

### 5. BUILD-06: Log Rotation
**Issue:** `portal.py` logs could grow indefinitely and consume the SD card.
**Fix Implemented:**
- Created `/etc/logrotate.d/ecofi` configuration.
- Automatically compresses and rotates logs daily, keeping a maximum of 3 days and 10MB per file.
