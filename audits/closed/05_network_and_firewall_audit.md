# Audit 05: Network & Firewall Configuration Audit

**Date:** August 17, 2026  
**Scope:** Network topology, firewall rules, captive portal detection, DNS, and IP addressing

---

## Network Topology

### Original PisoFi Network Layout
```
[Internet] ──► [WAN port (eth0/ppp0)] ──► [Orange Pi One] ──► [LAN port (eth1)] ──► [WiFi AP Bridge] ──► [Clients]
                                                │
                                          10.0.0.1 (gateway)
                                          DHCP: 10.0.0.100 - 10.0.31.254 (/19)
                                          DNS: 1.1.1.1, 1.0.0.1
```

### ECO-Fi Network Layout
```
[Internet] ──► [WAN port (eth0)] ──► [Orange Pi One] ──► [LAN/WiFi Bridge] ──► [Clients]
                                            │
                                            │──► [ESP32 via USB Serial]
                                      10.0.0.1 (gateway)
                                      DHCP: inherited from base image
```

### Finding NET-01: Interface Name Mismatch [MEDIUM]
**Issue:** The original PisoFi uses `eth1` as the LAN-facing interface (configured in dnsmasq.conf). Our `build_ecofi_img.sh` configures `eth0` in `/etc/network/interfaces.d/eth0` as the static `10.0.0.1` address.

On the actual Orange Pi One hardware:
- `eth0` is the single built-in Ethernet port (used as WAN by PisoFi)
- `eth1` is typically a USB Ethernet adapter used as the LAN bridge to the WiFi AP
- Some setups use `br-lan` as a bridge interface

**Risk:** If `eth0` is set to `10.0.0.1` but is actually the WAN port, the device will have no internet uplink.

**Recommended Fix:**
```bash
# In build_ecofi_img.sh — detect the correct interface dynamically
# Or configure both eth0 and eth1 with fallback:
cat << 'EOF' > "$MOUNT_DIR/opt/ecofi/setup_network.sh"
#!/bin/bash
# Determine LAN interface (the one NOT connected to internet)
WAN=$(ip route | grep default | awk '{print $5}')
for iface in eth0 eth1 br-lan; do
    if [[ "$iface" != "$WAN" ]] && ip link show "$iface" &>/dev/null; then
        ip addr flush dev "$iface"
        ip addr add 10.0.0.1/19 dev "$iface"
        ip link set "$iface" up
        echo "LAN interface set: $iface = 10.0.0.1"
        break
    fi
done
EOF
chmod +x "$MOUNT_DIR/opt/ecofi/setup_network.sh"
```

---

## Captive Portal Detection Chain

### How Captive Portal Detection Works

When a device connects to WiFi, it sends HTTP probes to detect internet connectivity:

| OS | Probe URL | Expected Response |
|---|---|---|
| Android | `http://connectivitycheck.gstatic.com/generate_204` | HTTP 204 (No Content) |
| Android (alt) | `http://clients3.google.com/generate_204` | HTTP 204 |
| iOS/macOS | `http://captive.apple.com/hotspot-detect.html` | `<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>` |
| Windows 10/11 | `http://www.msftconnecttest.com/connecttest.txt` | `Microsoft Connect Test` |
| Windows (alt) | `http://www.msftncsi.com/ncsi.txt` | `Microsoft NCSI` |
| ChromeOS | Same as Android | Same as Android |

If the device gets a **302 redirect** instead of the expected response, it knows it's behind a captive portal and displays the login page.

### Original PisoFi Detection Flow
```
Client HTTP Probe
    │
    ▼
[dnsmasq] resolves ALL domains to 10.0.0.1 (except whitelisted)
    │
    ▼
[Nginx port 80] catches probe URL
    │
    ▼
Returns 302 → http://portal.pisofiapp.com
    │
    ▼
[dnsmasq] resolves portal.pisofiapp.com → 10.0.0.1
    │
    ▼
[Nginx port 80] serves PHP captive portal page
```

### ECO-Fi Detection Flow
```
Client HTTP Probe
    │
    ▼
[dnsmasq] resolves domain to real IP (or 10.0.0.1 if old config remains)
    │
    ▼
[iptables PREROUTING] redirects port 80 traffic to 10.0.0.1 (if rules exist)
    │
    ▼
[Nginx port 80] catches probe URL
    │
    ▼
Returns 302 → http://10.0.0.1/
    │
    ▼
[Nginx port 80] proxies to Flask portal.py:5000
```

### Finding NET-02: Captive Portal May Not Trigger [HIGH]
**Issue:** ECO-Fi relies entirely on Nginx location-based intercepts for captive portal detection. But this only works if the client's DNS query for the probe domain resolves to `10.0.0.1` in the first place. Without a DNS hijack (dnsmasq `address=/#/10.0.0.1`), the probe will go to the real Google/Apple server, bypass our Nginx entirely, and the device will think it has internet access — **no captive portal popup will appear**.

**Current State:** The original dnsmasq.conf in the base image has `address=/portal.pisofiapp.com/10.0.0.1` but does NOT have a wildcard hijack. It relies on the individual `address=` entries for known probe domains.

**Recommended Fix:** Add to `build_ecofi_img.sh`:
```bash
# Inject ECO-Fi dnsmasq override
cat << 'EOF' > "$MOUNT_DIR/etc/dnsmasq.d/ecofi_captive.conf"
# Hijack ALL DNS to 10.0.0.1 for unauthenticated clients
# Authenticated clients bypass DNS hijack via ipset
address=/#/10.0.0.1
EOF
```

However, this alone would break internet for authenticated users. The proper solution is to use `ipset`-based DNS routing:
```bash
# Only hijack DNS for unauthenticated clients
# iptables rule: redirect DNS (port 53) to local dnsmasq unless in ecofi_auth
iptables -t nat -A PREROUTING -m set ! --match-set ecofi_auth src -p udp --dport 53 -j REDIRECT --to-port 53
iptables -t nat -A PREROUTING -m set ! --match-set ecofi_auth src -p tcp --dport 80 -j REDIRECT --to-port 80
```

---

## Firewall Rule Comparison

### Original PisoFi Firewall Architecture
```
[PREROUTING/nat]
    │
    ├── Authenticated clients (via per-client iptables marks) → ACCEPT → Internet
    │
    └── Unauthenticated clients → Redirect to portal (port 80 → PHP)

[FORWARD]
    │
    ├── Authenticated clients → ACCEPT (with tc bandwidth class)
    │
    └── Unauthenticated clients → DROP (except walled garden / DNS)

[POSTROUTING/mangle]
    │
    └── TTL --ttl-set 64 (anti-tethering)
```

### ECO-Fi Firewall Architecture (Current)
```
[PREROUTING/nat]
    │
    ├── ipset ecofi_auth match → ACCEPT → Internet
    │
    └── Everything else → Hits Nginx → Portal

[POSTROUTING/mangle]
    │
    └── TTL --ttl-set 64 (anti-tethering)
```

### Finding NET-03: Missing FORWARD Chain Rules [HIGH]
**Issue:** ECO-Fi only manages the PREROUTING chain (via ipset). It does not set up FORWARD chain rules to explicitly DROP traffic from unauthenticated clients. On most systems, the default FORWARD policy is ACCEPT, which means **unauthenticated clients may still be able to access the internet** via direct IP connections (bypassing DNS/HTTP).

**Recommended Fix:**
```python
def setup_firewall():
    # ... existing ipset and PREROUTING rules ...
    
    # Set FORWARD default policy to DROP
    subprocess.run("iptables -P FORWARD DROP", shell=True)
    
    # Allow authenticated clients to forward
    subprocess.run("iptables -A FORWARD -m set --match-set ecofi_auth src -j ACCEPT", shell=True)
    
    # Allow established/related connections back
    subprocess.run("iptables -A FORWARD -m state --state ESTABLISHED,RELATED -j ACCEPT", shell=True)
    
    # Allow DNS for everyone (needed for captive portal detection)
    subprocess.run("iptables -A FORWARD -p udp --dport 53 -j ACCEPT", shell=True)
```

---

## IP Address Audit

### Finding NET-04: Consistent Use of 10.0.0.1 [✅ PASS]

| Component | IP Used | Status |
|-----------|---------|--------|
| Nginx captive portal redirects | `10.0.0.1` | ✅ Correct |
| `build_ecofi_img.sh` static IP | `10.0.0.1` | ✅ Correct |
| `setup_firewall()` in portal.py | N/A (uses ipset, not IP-specific) | ✅ Correct |
| dnsmasq `listen-address` (base image) | `10.0.0.1` | ✅ Inherited |
| Nginx proxy_pass | `127.0.0.1:5000` | ✅ Correct (local loopback) |

---

## DNS Configuration Audit

### Finding NET-05: Stale PisoFi DNS References [MEDIUM]
**Issue:** The base image's dnsmasq.conf still contains:
```
domain=portal.pisofiapp.com
local=/portal.pisofiapp.com/
address=/portal.pisofiapp.com/10.0.0.1
dhcp-option=160,http://portal.pisofiapp.com
dhcp-option=114,http://portal.pisofiapp.com
```

These references to `portal.pisofiapp.com` should be updated to either `10.0.0.1` or a new ECO-Fi domain.

**Recommended Fix:** Add to `build_ecofi_img.sh`:
```bash
# Replace PisoFi domain references in dnsmasq config
sed -i 's/portal.pisofiapp.com/10.0.0.1/g' "$MOUNT_DIR/etc/dnsmasq.conf"
```
