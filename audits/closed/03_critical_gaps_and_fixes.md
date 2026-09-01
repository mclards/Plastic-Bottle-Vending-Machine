# Audit 03: Critical Gaps & Recommended Fixes

**Date:** August 17, 2026  
**Priority Scale:** P0 (must fix before deployment), P1 (should fix), P2 (nice to have)

---

## GAP-01: Sessions Lost on Power Cycle [P0 — CRITICAL]

### Problem
`active_clients` is a Python dictionary stored only in RAM. When `portal.py` restarts (crash, power outage, reboot, or OS update), **every client's remaining time is permanently destroyed**.

The original PisoFi stores all session data in MySQL, which survives reboots. It also runs `pauseconnections.php` on boot to pause (not delete) all active sessions, so users keep their time.

### Impact
- Users who paid via bottle deposits lose all their remaining WiFi time
- No way to recover or dispute — the data is gone
- On unreliable power grids (common in Philippines), this could happen multiple times per day

### Recommended Fix
```python
# In portal.py — Add session persistence

import atexit

def save_sessions_to_db():
    """Persist active_clients to SQLite for crash recovery."""
    with active_clients_lock:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS active_sessions (
                ip TEXT PRIMARY KEY,
                mac TEXT,
                remaining_seconds INTEGER,
                is_paused INTEGER,
                dl_kbps INTEGER,
                ul_kbps INTEGER,
                pending_bottles INTEGER,
                saved_at REAL
            )''')
            c.execute("DELETE FROM active_sessions")
            for ip, s in active_clients.items():
                c.execute("""INSERT INTO active_sessions 
                    (ip, mac, remaining_seconds, is_paused, dl_kbps, ul_kbps, pending_bottles, saved_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ip, s["mac"], s["remaining_seconds"], 
                     1 if s.get("is_paused") else 0,
                     s.get("dl_kbps", 2048), s.get("ul_kbps", 1024),
                     s.get("pending_bottles", 0), time.time()))
            conn.commit()

def restore_sessions_from_db():
    """Restore sessions from SQLite on startup. All restored sessions start paused."""
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM active_sessions")
        rows = c.fetchall()
        with active_clients_lock:
            for row in rows:
                ip, mac, remaining, is_paused, dl, ul, pending, saved_at = row
                if remaining > 0:
                    active_clients[ip] = {
                        "mac": mac,
                        "remaining_seconds": remaining,
                        "is_paused": True,  # Always pause on restore (like PisoFi)
                        "dl_kbps": dl,
                        "ul_kbps": ul,
                        "pending_bottles": pending
                    }
        c.execute("DELETE FROM active_sessions")  # Clean up after restore
        conn.commit()

# Register atexit handler
atexit.register(save_sessions_to_db)

# In time_daemon(), add periodic save every 30 seconds:
#   if int(time.time()) % 30 == 0:
#       save_sessions_to_db()

# On startup (before app.run):
#   restore_sessions_from_db()
```

---

## GAP-02: No Bandwidth Control (`tc`) [P0 — CRITICAL]

### Problem
PisoFi enforces per-client download/upload speed limits using Linux `tc` (traffic control) with HTB (Hierarchical Token Bucket) queuing. It creates a unique traffic class per connected client with their allocated bandwidth.

ECO-Fi tracks `dl_kbps` and `ul_kbps` per client session, but **never executes any `tc` commands**. All clients share the full internet pipe equally.

### Impact
- One user streaming 4K video (25+ Mbps) will starve all other users
- Admin-configured speed limits in the dashboard have no effect
- MAC control speed overrides are stored but never applied

### Recommended Fix
```python
# In portal.py — Add bandwidth enforcement

def setup_bandwidth_control(interface="eth0"):
    """Initialize tc HTB qdisc on the LAN interface."""
    if platform.system() == "Windows":
        return
    try:
        # Clear existing rules
        subprocess.run(f"tc qdisc del dev {interface} root 2>/dev/null", shell=True)
        # Create root HTB qdisc
        subprocess.run(f"tc qdisc add dev {interface} root handle 1: htb default 99", 
                       shell=True, check=True)
        # Create parent class (full pipe)
        subprocess.run(f"tc class add dev {interface} parent 1: classid 1:1 htb rate 100mbit",
                       shell=True, check=True)
        # Default class for unauthenticated traffic (heavily throttled)
        subprocess.run(f"tc class add dev {interface} parent 1:1 classid 1:99 htb rate 64kbit ceil 128kbit",
                       shell=True, check=True)
    except Exception:
        pass

def apply_client_bandwidth(ip, dl_kbps, ul_kbps, mark):
    """Create a tc class for a specific client."""
    if platform.system() == "Windows":
        return
    interface = "eth0"
    try:
        # Create client class
        subprocess.run(
            f"tc class replace dev {interface} parent 1:1 classid 1:{mark} "
            f"htb rate {dl_kbps}kbit ceil {dl_kbps}kbit",
            shell=True, check=True)
        # Add filter to match client IP
        subprocess.run(
            f"tc filter replace dev {interface} protocol ip parent 1: prio {mark} "
            f"u32 match ip dst {ip}/32 flowid 1:{mark}",
            shell=True, check=True)
    except Exception:
        pass

def remove_client_bandwidth(ip, mark):
    """Remove a client's tc class."""
    if platform.system() == "Windows":
        return
    interface = "eth0"
    try:
        subprocess.run(
            f"tc class del dev {interface} parent 1:1 classid 1:{mark}",
            shell=True)
    except Exception:
        pass
```

---

## GAP-03: No Auto-Pause on WiFi Disconnect [P1]

### Problem
PisoFi's `inspector` daemon checks the ARP table every 60 seconds. If a paying client's MAC address disappears from the ARP table (meaning they've disconnected from WiFi), their timer is automatically paused. When they reconnect, the inspector detects their MAC reappearing and resumes their timer.

ECO-Fi only supports manual pause via the UI button. If a user's phone goes to sleep, they walk out of range, or they switch to mobile data, their timer keeps counting down.

### Impact
- Users lose paid time when their device sleeps or goes out of range
- Particularly painful for users who deposit bottles, then go home to charge their phone — they return to find their time expired

### Recommended Fix
```python
# In time_daemon() — Add ARP check every 60 seconds

def get_connected_ips():
    """Read the ARP table to find currently connected IPs."""
    if platform.system() == "Windows":
        return set()
    try:
        result = subprocess.run("arp -n | grep -v incomplete | awk '{print $1}'",
                                shell=True, capture_output=True, text=True)
        ips = set(result.stdout.strip().split('\n'))
        ips.discard('Address')  # Remove header
        return ips
    except Exception:
        return set()

# Inside time_daemon(), add after the decrement loop:
# Every 60 seconds:
#   connected_ips = get_connected_ips()
#   with active_clients_lock:
#       for ip, sess in active_clients.items():
#           if sess["remaining_seconds"] > 0:
#               if ip not in connected_ips and not sess.get("is_paused"):
#                   sess["is_paused"] = True
#                   sync_client_firewall(ip)
#               elif ip in connected_ips and sess.get("is_paused") and not sess.get("admin_paused"):
#                   sess["is_paused"] = False
#                   sync_client_firewall(ip)
```

---

## GAP-04: Walled Garden Not Enforced in Firewall [P1]

### Problem
The `walled_garden` table in SQLite stores domains that should be freely accessible to all users (even unauthenticated ones). The admin CRUD endpoints work perfectly. However, **no `iptables` rules are ever created** to actually allow traffic to these domains.

PisoFi uses the `site_control` binary which reads allowed domains from MySQL and creates explicit `iptables -A FORWARD -d <resolved_ip> -j ACCEPT` rules.

### Impact
- Captive portal detection may fail on some devices if probe domains are blocked
- Admin-configured free sites (educational sites, government portals) are not actually accessible

### Recommended Fix
```python
# In setup_firewall() — Add walled garden enforcement

def apply_walled_garden():
    """Create iptables ACCEPT rules for walled garden domains."""
    if platform.system() == "Windows":
        return
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT domain FROM walled_garden")
        domains = [row[0] for row in c.fetchall()]
    
    for domain in domains:
        try:
            # Resolve domain to IPs and allow traffic
            import socket
            ips = socket.getaddrinfo(domain, None)
            for ip_info in ips:
                ip = ip_info[4][0]
                subprocess.run(
                    f"iptables -t nat -I PREROUTING -d {ip} -j ACCEPT -m comment "
                    f"--comment 'walled_garden:{domain}' 2>/dev/null",
                    shell=True)
        except Exception:
            pass
```

---

## GAP-05: No DAD / Network Interface Recovery [P2]

### Problem
PisoFi's `inspector` monitors `/var/log/syslog` for two critical network failures:
1. **DAD (Duplicate Address Detection)** — Another device on the network has claimed `10.0.0.1`
2. **"DHCP packet received on [interface] which has no address"** — The LAN interface lost its IP configuration

When detected, it automatically restarts `networking`, `dnsmasq`, and `pisofi_cron`.

### Impact
- On rare network glitches, the portal could silently go offline
- Users would connect to WiFi but get no captive portal page
- Requires physical reboot to fix (no remote access)

### Recommended Fix
```python
# In time_daemon() or a separate thread — Check every 5 minutes

def check_network_health():
    """Monitor for network configuration loss."""
    if platform.system() == "Windows":
        return
    try:
        # Check if our IP is still configured
        result = subprocess.run("ip addr show eth0 | grep 10.0.0.1",
                                shell=True, capture_output=True, text=True)
        if "10.0.0.1" not in result.stdout:
            logging.warning("Network health: eth0 lost 10.0.0.1 — recovering...")
            subprocess.run("systemctl restart networking", shell=True)
            subprocess.run("systemctl restart dnsmasq", shell=True)
            time.sleep(5)
            setup_firewall()
    except Exception:
        pass
```

---

## GAP-06: DNS Hijacking Not Configured [P1]

### Problem
PisoFi uses dnsmasq `address=` directives to hijack `portal.pisofiapp.com` to `10.0.0.1`. It also sets DHCP options 160 and 114 to `http://portal.pisofiapp.com` which triggers captive portal detection on many devices.

ECO-Fi's `build_ecofi_img.sh` does **not modify the existing dnsmasq configuration**. The original dnsmasq config still has `portal.pisofiapp.com` references, and our Nginx redirects go to `http://10.0.0.1/` directly.

### Impact
- If a device resolves `portal.pisofiapp.com` externally, it will get a real IP (not our portal)
- DHCP options 160/114 still point to the old PisoFi domain
- Some Android devices may not trigger the captive portal popup

### Recommended Fix
In `build_ecofi_img.sh`, add a step to update dnsmasq.conf:
```bash
# Update dnsmasq configuration
cat << 'EOF' > "$MOUNT_DIR/etc/dnsmasq.d/ecofi.conf"
# ECO-Fi DNS Hijack Configuration
address=/#/10.0.0.1
dhcp-option=160,http://10.0.0.1
dhcp-option=114,http://10.0.0.1
EOF

# Remove old PisoFi dnsmasq configs
rm -f "$MOUNT_DIR/etc/dnsmasq.d/pisofi_"* 2>/dev/null || true
```

---

## GAP-07: MAC Control Not Enforced in Firewall [P2]

### Problem
The `mac_control` table stores MAC addresses with their type (whitelist/blacklist) and bandwidth overrides. The admin CRUD works, but no `iptables` rules are created to actually block blacklisted MACs or allow whitelisted ones to bypass authentication.

### Recommended Fix
```python
def apply_mac_control():
    """Create iptables rules for MAC whitelist/blacklist."""
    if platform.system() == "Windows":
        return
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT mac, type FROM mac_control")
        for mac, mac_type in c.fetchall():
            if mac_type == "block":
                subprocess.run(
                    f"iptables -I FORWARD -m mac --mac-source {mac} -j DROP",
                    shell=True)
            elif mac_type == "whitelist":
                subprocess.run(
                    f"ipset add ecofi_auth $(arp -n | grep '{mac}' | awk '{{print $1}}') -exist",
                    shell=True)
```
