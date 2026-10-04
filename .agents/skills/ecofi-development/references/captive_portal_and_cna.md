# Captive Portal & Mobile CNA Network Engineering Reference
> **Domain:** Captive Portal Interception, Apple iOS/macOS Captive Network Assistant (CNA), Android / Windows Network Detection, RFC 8908, RFC 8910, and WISPr 2.0 XML Handshake.

---

## 1. Operating System Captive Portal Detection Mechanisms

Different client operating systems detect captive portals using distinct protocols and probes. The VMC ECO-VENDO stack supports all of them harmoniously.

### A. Apple iOS & macOS (Captive Network Assistant / CNA)
1. **Traditional Probe:** Upon Wi-Fi association, Apple's background daemon (`CaptiveNetworkSupport`) sends an HTTP GET request to `http://captive.apple.com/hotspot-detect.html` (or fallback probe domains `appleiphonecell.com`, `airport.us`, `itools.info`, `ibook.info`, `thinkdifferent.us`).
2. **Online Expectation:** It expects HTTP 200 OK with the exact body:
   ```html
   <HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>
   ```
3. **Captive Trigger:** If the response is an HTTP 302/307 redirect, or returns a non-Success page (or WISPr 2.0 XML), iOS automatically spawns the **Captive Network Assistant (CNA)** modal sheet.
4. **RFC 8910 / DHCP Option 114:** When Option 114 is advertised via DHCP, iOS 14+ queries the URL with `Accept: application/captive+json` expecting an **RFC 8908 Captive Portal JSON API** response.
   > [!CAUTION]
   > **The Option 114 Trap:** If the server advertises Option 114 but responds with HTML (`text/html`), iOS marks the RFC 8910 handshake as malformed and **silently suppresses the automatic CNA popup**, requiring users to manually open Safari. The endpoint MUST return valid `application/captive+json`.

### B. Google Android & ChromeOS
1. **Connectivity Probe:** Sends an HTTP GET request to `http://connectivitycheck.gstatic.com/generate_204` (or `clients3.google.com/generate_204`).
2. **Online Expectation:** HTTP status `204 No Content`.
3. **Captive Trigger:** Any non-204 status (HTTP 302 redirect or HTTP 200 HTML) triggers the Android notification "Sign in to Wi-Fi network".

### C. Microsoft Windows NCSI
1. **Connectivity Probe:** Queries `http://www.msftconnecttest.com/connecttest.txt` and `http://www.msftncsi.com/ncsi.txt`.
2. **Online Expectation:** HTTP 200 OK with `Microsoft Connect Test` or `Microsoft NCSI`.
3. **Captive Trigger:** HTTP 302 redirect opens default browser to the portal.

---

## 2. Core Architectural Pillars in VMC ECO-VENDO

### Pillar 1: RFC 8908 Captive Portal JSON API
Implemented in `host/portal.py` at `@app.route('/api/captive-portal', strict_slashes=False)` and via content negotiation on `@app.route('/')`:
```python
def captive_portal_json_response(client_ip):
    is_online = check_client_online(client_ip)
    sess = ensure_client_session(client_ip)
    rem = max(0, int(sess.get('remaining_seconds', 0)))
    data = {
        'captive': not is_online,
        'user-portal-url': 'http://10.0.0.1/',
        'venue-info-url': 'http://10.0.0.1/'
    }
    if is_online and rem > 0:
        data['seconds-remaining'] = rem
    resp = Response(json.dumps(data), mimetype='application/captive+json', status=200)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp
```
- In `/etc/dnsmasq.conf`:
  ```
  dhcp-option=114,"http://10.0.0.1/api/captive-portal"
  dhcp-option=160,"http://10.0.0.1/api/captive-portal"
  ```
- Uses `strict_slashes=False` to prevent Flask from issuing 308 redirects that can break strict RFC 8910 clients.

### Pillar 2: Apple iCloud Private Relay NXDOMAIN Directive
Apple officially specifies that captive portal networks must instruct iOS to disable iCloud Private Relay for the local network by returning `NXDOMAIN` for its proxy hostnames:
```
# /etc/dnsmasq.conf
local=/mask.icloud.com/
local=/mask-h2.icloud.com/
```
In `dnsmasq`, the `local=/domain/` directive without an IP address causes queries to immediately resolve as `status: NXDOMAIN` in under 1ms. Without this, iOS devices with Private Relay enabled hang on initial connection or suppress the captive portal sheet.

### Pillar 3: WISPr 2.0 XML Protocol & `/hotspot.html`
Apple's `CaptiveNetworkSupport` daemon natively understands WISPr (Wireless Internet Service Provider roaming) XML:
```html
<!--<?xml version="1.0" encoding="UTF-8"?>
<WISPAccessGatewayParam xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xlink="http://www.w3.org/1999/xlink" xlink:noNamespaceSchemaLocation="http://www.wballiance.net/wispr_2_0.xsd">
<Redirect>
<MessageType>100</MessageType>
<ResponseCode>0</ResponseCode>
<VersionHigh>2.0</VersionHigh>
<VersionLow>1.0</VersionLow>
<AccessProcedure>1.0</AccessProcedure>
<AccessLocation>Eco-Vendo</AccessLocation>
<LocationName>Eco-Vendo</LocationName>
<LoginURL>http://10.0.0.1/</LoginURL>
</Redirect>
</WISPAccessGatewayParam>
-->
```
1. `host/portal.py` exposes `@app.route('/hotspot.html')`:
   - If offline: Returns WISPr XML with immediate `<meta http-equiv="refresh" content="0; url=http://10.0.0.1/">`.
   - If online: Returns `<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>`.
2. `PORTAL_HTML` prepends the WISPr XML comment block, ensuring direct IP accesses are also recognized by WISPr parsers.

### Pillar 4: Synthetic Probe Domain Redirection
In `host/portal.py`, requests to `@app.route('/')` checking for synthetic probe hostnames (`captive.apple.com`, `appleiphonecell.com`, `airport.us`, etc.) issue an immediate HTTP 302 redirect to `http://10.0.0.1/hotspot.html` when offline. Serving raw HTML on `captive.apple.com/` confuses the CNA state machine; a 302 redirect cleanly instructs the OS to open the webview modal.

---

## 3. Network & Firewall Topology

```
                  ┌────────────────────────┐
                  │   Upstream ISP / WAN   │
                  │    (eth0 - DHCP)       │
                  └───────────┬────────────┘
                              │
                              ▼
                  ┌────────────────────────┐
                  │    Orange Pi One /     │
                  │  iptables NAT & Filter │
                  └───────────┬────────────┘
                              │
      ┌───────────────────────┴───────────────────────┐
      │                                               │
      ▼ (Port 53 DNS)                                 ▼ (Port 80 HTTP)
┌───────────┐                                   ┌───────────┐
│  dnsmasq  │                                   │   Nginx   │ (Reverse Proxy)
│ (10.0.0.1)│                                   │ (10.0.0.1)│
└─────┬─────┘                                   └─────┬─────┘
      │                                               │ (proxy_pass)
      │ Synthetic probes -> 10.0.0.1                  ▼
      │ mask.icloud.com -> NXDOMAIN             ┌───────────┐
      │                                         │ portal.py │ (:5000)
      │                                         └───────────┘
      │
      ▼
┌────────────────────────┐
│  Customer Wi-Fi AP     │
│   (eth1 - 10.0.0.1/19) │
└────────────────────────┘
```

- **HTTPS (Port 443):** Rejected immediately with `tcp-reset` (`-A ECOFI_INPUT -i eth1 -p tcp -m tcp --dport 443 -j REJECT --reject-with tcp-reset`). This prevents browsers from hanging on SSL timeouts.
- **HTTP (Port 80):** Redirected to local port 80 via `iptables -t nat -A ECOFI_PORTAL -p tcp -m tcp --dport 80 -j REDIRECT --to-ports 80`.
- **Authenticated Clients:** IP and MAC are added to `ipset` `ecofi_auth` and `ecofi_pairs`. NAT redirection is bypassed (`RETURN`), granting raw forwarding to `eth0`.
- **iOS CNA TCP Connection Drop Handling:** When firewall state transitions from captive to authenticated, active NAT connections drop. Portal frontend JavaScript must handle fetch `.catch()` gracefully with cache-busting `?t=Date.now()` and a 1.5s deadman timer.
