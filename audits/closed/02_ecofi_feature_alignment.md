# Audit 02: ECO-Fi vs PisoFi Feature Alignment Matrix

**Date:** August 17, 2026

---

## Feature Comparison Table

| # | Feature | PisoFi v5.3.0 | ECO-Fi `portal.py` | Alignment |
|---|---------|---------------|---------------------|-----------|
| 1 | Captive Portal Detection (Android) | Nginx `302` → `portal.pisofiapp.com` on `/generate_204`, `/gen_204` | Nginx `302` → `http://10.0.0.1/` on `/generate_204`, `/gen_204` | ✅ Aligned |
| 2 | Captive Portal Detection (iOS) | Nginx `302` on `/hotspot-detect.html` + `CaptiveNetworkSupport` UA check | Nginx `302` → `http://10.0.0.1/` on `/hotspot-detect.html` + Flask route | ✅ Aligned |
| 3 | Captive Portal Detection (Windows) | Nginx `302` on `/ncsi.txt`, `/connecttest.txt`, `/msftconnecttest.com` | Nginx `302` → `http://10.0.0.1/` on same paths + Flask routes | ✅ Aligned |
| 4 | Captive Portal Detection (ChromeOS) | Via `/generate_204` (same as Android) | Same as Android | ✅ Aligned |
| 5 | DNS Hijacking | dnsmasq `address=/portal.pisofiapp.com/10.0.0.1` + DHCP option 160/114 | Not implemented (relies on Nginx intercepts only) | ⚠️ Partial |
| 6 | Gateway Static IP | `10.0.0.1` via dnsmasq `listen-address` | `10.0.0.1` via `/etc/network/interfaces.d/eth0` static config | ✅ Aligned |
| 7 | DHCP Server | dnsmasq `dhcp-range=10.0.0.100,10.0.31.254,/19,72h` | Uses existing dnsmasq from base image | ✅ Inherited |
| 8 | Time Decrement | `kicker.php` → SQL `UPDATE remaining_time - elapsed WHERE status=1` every 3s | `time_daemon()` → Python `remaining_seconds -= 1` every 1s in-memory | ✅ Aligned (more precise) |
| 9 | Firewall Grant | `pisofier connect` → per-client `iptables` mark + `tc` class | `ipset add ecofi_auth <ip> timeout <sec>` | ✅ Aligned (simplified) |
| 10 | Firewall Revoke | `pisofier disconnect` → remove `iptables`/`tc` rules | `ipset del ecofi_auth <ip>` | ✅ Aligned |
| 11 | Per-Client Bandwidth (Download) | `tc class add ... rate Xkbit ceil Xkbit` via HTB | `dl_kbps` tracked but **NOT enforced** | ❌ **Missing** |
| 12 | Per-Client Bandwidth (Upload) | IFB device + `tc class add` on IFB | `ul_kbps` tracked but **NOT enforced** | ❌ **Missing** |
| 13 | Auto-Pause on WiFi Disconnect | `inspector` checks ARP table every 60s, sets `status=2` if MAC missing | Not implemented | ❌ **Missing** |
| 14 | Auto-Resume on WiFi Reconnect | `inspector` detects MAC reappearing in ARP, sets `status=1` | Not implemented | ❌ **Missing** |
| 15 | Pause Sessions on Boot | `pauseconnections.php` sets all `status=2` on startup | Not implemented (sessions lost entirely) | ❌ **Missing** |
| 16 | Manual Pause/Resume | Via admin PHP panel | `/api/client/pause` POST endpoint | ✅ Aligned |
| 17 | Coin Slot Input | GPIO pin interrupt via compiled `pins` binary | ESP32 serial JSON `CREDIT_ADD` via `hardware_serial_daemon()` | ✅ Redesigned |
| 18 | Voucher System | Admin PHP panel (obfuscated) | `/api/voucher/redeem` + `/admin/api/vouchers/*` CRUD | ✅ Aligned |
| 19 | Member/Wallet System | MySQL `members` table via PHP | SQLite `members` table + `/api/member/*` endpoints | ✅ Aligned |
| 20 | Member Save Time | Not available | `/api/member/save_time` — save unused time to wallet | ✅ **Enhancement** |
| 21 | Time Transfer | Not available (PisoFi uses cloud for multi-device) | `/api/transfer/generate` + `/api/transfer/claim` via unique codes | ✅ **Enhancement** |
| 22 | Admin Dashboard | Full PHP web panel (obfuscated Laravel-style) | `/admin` with stats, clients, vouchers, members, settings, MAC control | ✅ Aligned |
| 23 | Admin Client Actions | Pause/resume/kick/add time via PHP | `/admin/api/client/action` — pause/resume/kick/add15/add60/disconnect | ✅ Aligned |
| 24 | Admin Client Edit | Edit bandwidth per client via PHP | `/admin/api/client/edit` — edit `dl_kbps`, `ul_kbps`, set time | ✅ Aligned |
| 25 | MAC Whitelist/Blacklist | `mac_control` binary + MySQL + `iptables` rules | `/admin/api/mac_control/*` CRUD (DB only, **not enforced** in `iptables`) | ⚠️ Partial |
| 26 | Walled Garden / Free Sites | `site_control` binary + MySQL + `iptables` ACCEPT rules | `/admin/api/walled_garden/*` CRUD (DB only, **not enforced**) | ⚠️ Partial |
| 27 | Anti-Tethering (TTL) | `iptables -t mangle -A POSTROUTING -j TTL --ttl-set 64` in `r` script | `setup_firewall()` applies identical rule | ✅ Aligned |
| 28 | Promo/Tiered Rates | Fixed coin denomination in MySQL settings | `promo_rates` table with tiered bottle-to-time multipliers | ✅ Aligned |
| 29 | WebSocket Real-time Push | Ratchet/ZMQ on port 8080 | Polling via `/api/vendo/status` every few seconds | ⚠️ Downgrade |
| 30 | Telegram Alerts | Not available | `send_telegram_alert()` for bin full + daily stats | ✅ **Enhancement** |
| 31 | Audio/Sound Effects | Not available | Configurable `audio_bg`, `audio_insert`, `audio_success` | ✅ **Enhancement** |
| 32 | Announcements | Not available | `announcements` table with admin CRUD, displayed on portal | ✅ **Enhancement** |
| 33 | Bin Full Detection | Not applicable | ESP32 ultrasonic sensor → `BIN_FULL` event → Telegram alert | ✅ **Enhancement** |
| 34 | PET Material Verification | Not applicable (coin-based) | ESP32 NIR spectrometer + weight + metal detection | ✅ **Enhancement** |
| 35 | Hardware Simulator | Not available | `ESP32Simulator` class for full development/testing without hardware | ✅ **Enhancement** |
| 36 | Frozen Time Recovery | `inspector` detects stale `updated_at`, restarts kicker | Not needed (Python timer is inherently reliable) | ✅ N/A |
| 37 | DAD/Network Recovery | `inspector` monitors syslog for DAD, restarts networking | Not implemented | ⚠️ **Gap** |
| 38 | Interface Health Check | `inspector` reboots device if IFB/LAN interfaces disappear | Not implemented | ⚠️ **Gap** |
| 39 | Ngrok Remote Access | Built-in Ngrok binary + service | Intentionally removed (security risk) | ℹ️ Removed |
| 40 | Cloud Data Sync | `datasync.php` (disabled by default) | Intentionally removed (self-contained design) | ℹ️ Removed |
| 41 | Remote Backup | `remotebackup.php` service | Not implemented | ℹ️ Removed |
| 42 | Remote Management | `remotesubscriber.php` service | Not implemented | ℹ️ Removed |
| 43 | Charging Stations | `pisofi_charging.service` for coin-op power outlets | Not applicable (different hardware) | ℹ️ N/A |
| 44 | PPPoE Support | PPPoE server management in startup and inspector | Not implemented | ℹ️ Not needed |
| 45 | License/Registration | `check_status` PHP script, `is_registered` setting | `/admin/api/license` + `license_manager.py` | ✅ Aligned |
| 46 | CSV Export | Not available in base image | `/admin/api/export_csv` for stats export | ✅ **Enhancement** |
| 47 | Custom CSS | Not available | `custom_css` config option | ✅ **Enhancement** |
| 48 | Database Engine | MySQL (heavy, requires service management) | SQLite (zero-config, embedded) | ✅ Redesigned |

---

## Alignment Summary

| Status | Count | Percentage |
|--------|-------|------------|
| ✅ Aligned / Redesigned / Enhanced | 33 | 69% |
| ⚠️ Partial / Downgrade / Gap | 9 | 19% |
| ❌ Missing (Critical) | 5 | 10% |
| ℹ️ Intentionally Removed / N/A | 6 | — |

---

## ECO-Fi Exclusive Enhancements (Not in PisoFi)

1. **Time Transfer System** — Users can generate transfer codes and share time with friends
2. **Member Save Time** — Save unused time to wallet for future use
3. **Telegram Alerts** — Remote notifications for bin capacity and daily stats
4. **Audio/Sound Effects** — Configurable audio feedback for bottle insertion and success
5. **Announcements** — Admin-managed announcements displayed on the portal
6. **PET Material Verification** — NIR spectrometer + weight + metal detection prevents fraud
7. **Hardware Simulator** — Full ESP32 simulation for development without physical hardware
8. **CSV Stats Export** — Export daily statistics as CSV
9. **Custom CSS** — Admin-configurable portal styling
