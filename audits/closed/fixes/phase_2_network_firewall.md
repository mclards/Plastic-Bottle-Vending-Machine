# Phase 2 Implementation: Network Control & Firewalls

**Status:** Completed  
**Target File:** `host/portal.py`  
**Date:** August 17, 2026

---

## Implemented Fixes

### 1. GAP-02: Enforce Bandwidth Control (`tc`)
**Issue:** `dl_kbps` and `ul_kbps` were tracked in the database but never applied to the network interface, allowing a single user to consume all bandwidth.
**Fix Implemented:**
- Created `setup_bandwidth_control()` to initialize `tc` (Traffic Control) HTB queuing on the active LAN interface (`eth0`, `eth1`, etc.).
- Created `apply_client_bandwidth()` which takes the client's last IP octet as their unique `tc` class mark and limits their speed accordingly.
- Created `remove_client_bandwidth()` to clean up limits when a session ends.
- Updated `update_firewall()` to automatically provision/deprovision these bandwidth limits whenever a client is added to or removed from `ecofi_auth`.

### 2. GAP-03: Auto-Pause on WiFi Disconnect
**Issue:** Users lost time if they disconnected from WiFi or put their phone to sleep, since the timer kept counting down.
**Fix Implemented:**
- Created `get_connected_ips()` which reads the system ARP table to find actively connected MAC addresses.
- Updated `time_daemon()` to run this check every 60 seconds. 
- If an active client disappears from the ARP table, their timer is automatically paused (`is_paused = True`) and they are removed from the firewall.
- When they reconnect, their timer automatically resumes (unless manually paused by an admin).

### 3. GAP-04: Enforce Walled Garden in `iptables`
**Issue:** Walled garden domains were stored in SQLite but no firewall rules allowed traffic to them for unauthenticated users.
**Fix Implemented:**
- Created `apply_walled_garden_and_macs()`.
- Reads `walled_garden` domains from SQLite, resolves them to IPs using Python's `socket.getaddrinfo`, and creates `iptables -t nat -I PREROUTING -d <ip> -j ACCEPT` rules.
- This ensures free educational sites and captive portal detection endpoints are always accessible.

### 4. GAP-07: Enforce MAC Control in `iptables`
**Issue:** Admin MAC whitelist/blacklist UI worked, but the rules weren't applied at the network layer.
**Fix Implemented:**
- In `apply_walled_garden_and_macs()`, reads `mac_control` table.
- Blocks blacklisted MACs natively via `iptables -I FORWARD -m mac --mac-source <mac> -j DROP`.
- Automatically resolves whitelisted MACs to IPs (via ARP) and injects them directly into the `ecofi_auth` ipset so they bypass the portal.

### 5. NET-03: Set `FORWARD` chain default policy to DROP
**Issue:** The default `FORWARD` policy was `ACCEPT`, meaning unauthenticated clients could potentially bypass the portal if they forged DNS/IPs.
**Fix Implemented:**
- Set `iptables -P FORWARD DROP` in `setup_firewall()`.
- Explicitly allowed authenticated clients: `iptables -A FORWARD -m set --match-set ecofi_auth src -j ACCEPT`.
- Allowed essential traffic like DNS (`udp 53`) and established connections to prevent breaking active sessions.
