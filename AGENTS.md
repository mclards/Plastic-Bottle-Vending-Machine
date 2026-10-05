# VMC ECO-VENDO Smart Reverse Vending Machine — Agent Engineering Manual
> **Student Thesis Project:** *Eco-Vendo: An Empty Bottle-Initiated Internet Access Vending System*  
> **Authoritative Project Memory, Operational Rules & Architectural Skills**  
> Applicable across all AI Agents: **Antigravity (Gemini), Claude, and GPT / Cursor / Copilot**.

---

## 1. System Architecture & Hardware Environment

### Physical Target Hardware
- **Processor & Architecture:** Allwinner H2+ / H3 Quad-core Cortex-A7 (ARMv7 32-bit `armhf`).
- **Board:** Orange Pi One / Orange Pi PC.
- **Operating System:** Armbian / Debian Stretch.
- **TARGET PYTHON RUNTIME: Python 3.5.3**.
  > [!CAUTION]
  > **NEVER USE PYTHON 3.6+ SYNTAX** on target host files or scripts executed on the Orange Pi!
  > - **NO f-strings (`f"..."`)**: Always use standard string concatenation or `"...".format(...)`.
  > - **NO variable type annotations** inside functions (`x: int = 5`).
  > - Any script using f-strings will immediately crash on the Orange Pi with `SyntaxError: invalid syntax`.

### Network Topology & Dual-NIC Architecture
- **WAN (`eth0`):** Connected to upstream ISP Router / Starlink via DHCP client.
- **LAN (`eth1` or USB-Ethernet `usb0`/`enx*`):** Connected to Customer WiFi Access Point.
  - Static IP: `10.0.0.1/19` (Netmask: `255.255.224.0`, Broadcast: `10.0.31.255`).
  - DHCP Range: `10.0.0.100` – `10.0.31.254` (managed authoritatively by `dnsmasq`).
  - DNS Hijacking & Captive Portal Wildcard: Resolves all detection probes (`captive.apple.com`, `connectivitycheck.gstatic.com`, `msftconnecttest.com`) to `10.0.0.1`.
  - Reverse Proxy: **Nginx** listens on port `80`, proxying traffic to the VMC ECO-VENDO Python engine on port `5000`.

---

## 2. Golden Operational Rules

1. **Live OPi is the Ground Truth Reference:**
   - The user runs and tests changes live on the Orange Pi.
   - Always connect using `tools/opi_access.py` (`from tools.opi_access import connect`).
   - When modifications are made to `host/`, deploy them immediately to `/opt/ecofi/` and verify service health (`systemctl restart ecofi_portal.service`).
2. **The OS Image (`resources/EcoFi_Opi_v<VERSION>.img`) Must 100% Match the Live OPi:**
   - All modules in the `.img` must match the live board.
   - Always compute both **MD5** and **SHA-256** checksums after rebuilding.
3. **Admin Panel Danger Confirmations:**
   - All kick, delete, reboot, and disconnect buttons in the Admin Panel must have explicit confirmation prompts before firing.
4. **Authoritative Admin Credentials (PERMANENT RULE):**
   - **Default Username:** `admin`
   - **Default Password:** `admin1234`
   - **Forced Password Change:** The forced password change on first login with default `admin1234` is an intentional security design and MUST be preserved.
   - **STRICT PROHIBITION:** AI agents must NEVER change, overwrite, or randomize the default admin credentials in code, tests, scripts, or database under any circumstances.
5. **Authoritative Stable Version (PERMANENT MEMORY RULE):**
   - **v2.3.18 is definitively confirmed as the stable release baseline** across both the Orange Pi gateway and ESP32 firmwares, embedding authoritative hardware & sensor calibration defaults (NIR W [10 - 120], Weight [20 - 90]g, HX711 cal factor 260, Horiz. bin 18cm @ 3s, Gate 65s, Settle 1000ms, Drop 3100ms, Retrieval 50s, servos 0°/90°).
   - All timing, retrieval state machines, chime synchronization, and scale routines originating from v2.3.17 (commit `87c7cb7`) are preserved.

---

## 3. Frontend & UI Engineering Pitfalls

### Monolithic HTML Variables (`PORTAL_HTML` & `ADMIN_HTML` in `portal.py`)
- The captive portal and admin panel HTML/JS are embedded as **massive, single-line Python string literals** with escaped newlines (`\n`).
- **Single Quote JavaScript Crash Hazard:**
  - Standard HTML attributes inside Python single-quoted strings must NEVER contain raw single quotes `'` inside inline JS (e.g. `onclick="if(confirm('Delete?'))"`).
  - Unescaped quotes break the browser's JavaScript parser, silently killing all modals, tabs, and client controls.
  - **Always escape quotes as `&quot;` or `\'`**, and validate JavaScript syntax using `node --check` after any HTML edit.

### Captive Portal iOS & Android CNA Quirks
- When a user resumes a session or the firewall opens, `iptables` NAT connection tracking state changes, which **instantly severs the phone's active TCP connection** to the captive portal server.
- Consequently, client `fetch('/api/client/pause')` calls often reject with a Network Error **even though the backend successfully granted access**.
- **Rule:** The JavaScript `.catch()` block on captive portal resume/grant actions **must handle success redirection** and must never block the user on a false network error. Include a 1.5s fallback deadman timer and cache-busting timestamp (`t=Date.now()`) so Apple's Captive Network Assistant (CNA) re-evaluates connectivity.

### Captive Portal Auto-Popup & Discovery Architecture
- **RFC 8910 / DHCP Option 114 & RFC 8908:**
  - Option 114 in `dnsmasq.conf` points to `http://10.0.0.1/api/captive-portal`.
  - The endpoint MUST return `Content-Type: application/captive+json` with `{"captive": true/false, "user-portal-url": "http://10.0.0.1/"}`.
  - Serving HTML to Option 114 queries causes iOS to reject RFC 8910 and silently suppress the CNA modal sheet.
- **Apple iCloud Private Relay NXDOMAIN:**
  - DNS MUST return `NXDOMAIN` for `mask.icloud.com` and `mask-h2.icloud.com` (`local=/mask.icloud.com/` in `dnsmasq.conf`) per Apple specifications to disable Private Relay on the captive network.
- **WISPr 2.0 Protocol & `/hotspot.html`:**
  - Apple CNA parses WISPr XML (`<WISPAccessGatewayParam>` with `<LoginURL>http://10.0.0.1/</LoginURL>`).
  - Prepend WISPr XML to `PORTAL_HTML` and expose `/hotspot.html`. Probe requests to `captive.apple.com` must return HTTP 302 redirects to `/hotspot.html`.

### Admin Panel Information Architecture & Sidebar Taxonomy
- The Admin Panel sidebar navigation is authoritatively structured into **5 distinct functional domains**:
  1. **`VENDO OPERATIONS`:** Dashboard & Stats, Active Clients, Voucher Tickets, Rates & Promos (`Rates & Packages`, `Validity & Pauses`).
  2. **`NETWORK & TRAFFIC`:** Network & Interfaces (`sec-network`), Bandwidth & Speed (`sec-bandwidth`), Walled Garden Sites (`sec-walled`), MAC Filtering (`sec-security`).
  3. **`PORTAL & BRANDING`:** Portal & Banners (`sec-portal-custom`), Audio & Chimes (`sec-audio`).
  4. **`HARDWARE & SENSORS`:** ESP32 Hardware & Sensors (`sec-esp32`).
  5. **`SYSTEM & MAINTENANCE`:** Telegram Alerts (`sec-telegram`), System Maintenance (`sec-system`), Hardware Licensing (`sec-licensing`).
- **Rule:** Never dump portal customization, networking, or maintenance items into a generic catch-all group. All 15 sections have direct mapping and active link state tracking in `showSection(secId)`.

---

## 4. Time Entitlement Engine & SQLite Database

### Schema Constraints & Pitfalls
- **Database File on OPi:** `/opt/ecofi/vendo_sessions.db`.
- **`pause_budgets` Table:**
  ```sql
  CREATE TABLE IF NOT EXISTS pause_budgets (
      id TEXT PRIMARY KEY,
      owner_id TEXT NOT NULL REFERENCES credit_owners(id),
      pause_count_max INTEGER,
      used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0),
      created_at INTEGER NOT NULL
  );
  ```
  > [!WARNING]
  > `pause_budgets` **DOES NOT HAVE AN `updated_at` COLUMN!**
  > Attempting `UPDATE pause_budgets SET ..., updated_at=?` throws `sqlite3.OperationalError: no such column: updated_at`, triggering a backend 503 `storage_unavailable` error.
- **PisoFi Alignment (Wallet Decommissioned):**
  - The wallet/member system has been completely decommissioned from VMC ECO-VENDO.
  - Depositing bottles directly adds time to the active timer, unpauses the client, and resets used pauses.
  - MAC binding is permanently authoritative.

---

## 5. Cryptographic Hardware Licensing Engine

Implemented in [`host/license_manager.py`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/host/license_manager.py):
- **Developer Code:** `mclards23`.
- **Machine HWID (32-Hex Characters, 8 Blocks of 4):**
  Derived from: `CPU_Serial | SD_Card_CID | eth0_MAC | mclards23 | VENDOR_SECRET_SALT`.
  Format: `XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX`.
- **Activation Key (32-Hex Characters, 8 Blocks of 4):**
  Derived from: `SHA256(HWID :: TIER :: mclards23 :: VENDOR_SECRET_SALT)`.
  Format: `YYYY-YYYY-YYYY-YYYY-YYYY-YYYY-YYYY-YYYY`.
- **UI State Logic:**
  - When license status is `ACTIVATED`, the activation form (input box + button) is automatically hidden and replaced with a green verified commercial badge.
  - If unlicensed or expired, the form unhides automatically.

---

## 6. Bandwidth Control & Linux Kernel Gaming QoS

Implemented in [`host/gateway_network.py`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/host/gateway_network.py):
- **Traffic Shaping:** Uses Linux `tc` (Traffic Control) HTB and SFQ qdiscs.
- **Gaming QoS:**
  - Marks UDP game packets in `iptables -t mangle PREROUTING` with mark `10` (`0xa`):
    - Mobile Legends: UDP `30000:30010`
    - Riot Games / Valorant: UDP `7000:8000`
    - Valve Steam & Dota 2: UDP `27000:27050`
    - General fast UDP: UDP `5000:5500`
  - Routes marked packets into high-priority queue class `1:10` to guarantee low ping during heavy downloads.

---

## 7. Build Pipeline & Image Creation

- **Build Script:** `build_ecofi_img.sh` executed via WSL Ubuntu (`wsl -d Ubuntu -u root -- bash build_ecofi_img.sh`).
- **Base Image:** `resources/EcoFi_Opi_v2.1.img`.
- **Output Target:** `resources/EcoFi_Opi_v<VERSION>.img` (along with `.md5` and `.sha256` files).
- **Validation:** Runs ARM QEMU static emulator tests (`verify_arm_runtime.py`, route imports, `dnsmasq --test`, `e2fsck`) inside the mounted rootfs before finalizing.

---

## 8. Material Discrimination & AS7263 NIR Spectroscopy

Comprehensive empirical research data archived in [`docs/AS7263_NIR_CALIBRATION_RESEARCH.md`](docs/AS7263_NIR_CALIBRATION_RESEARCH.md):
- **Sensor:** SparkFun AS7263 6-Channel NIR (`0x49`) @ 64x Gain, 50 mA bulb drive, 140 ms integration.
- **Empty Air Baseline:** Bounded at **~24.5 uW/cm²** (W ~ 22 counts, Sum ~ 1000).
- **Clear PET Bottle Walls:** Produce **35–65 uW/cm²** (up to ~200 uW/cm² on corrugated/ribbed plastic).
- **Cellophane / BOPP Labels:** Produce **103–238 uW/cm²** due to diffuse Lambertian scattering.
- **Clear Glass vs Clear PET:** Both exhibit ~4% Fresnel reflectance ($n=1.51$ vs $1.57$) at 860 nm; single-point reflection alone cannot distinguish smooth clear glass from smooth clear PET.
- **Colored Glass (Beer/Wine):** Strongly absorbs NIR ($8.9–17.8\text{ uW/cm}^2$, below empty air). Rejection threshold: `Cal-W < 22.0 uW/cm²`.
- **Cardboard / Paper Cups:** Intense diffuse scattering ($R > 8000$, $\text{Raw Sum} > 15,000$, $\text{Cal-W} > 230\text{ uW/cm}^2$).
- **Multimodal Sensor Fusion:**
  - LJ12A3 Inductive: Rejects aluminum cans and glass bottles with metal crown caps.
  - AS7263 NIR: Verifies polymer presence, rejects colored glass and paper waste.
  - Dual IR (E18-D80NK): Confirms entrance and gravitational drop transit.

---

## 9. ESP32 Firmware, 20x4 LCD Pipeline & Scale Philosophy

- **20x4 LCD Buffer Invalidation Rule:**
  - `char currentLcdLines[4][21]` must be initialized empty (`{ "", "", "", "" }`). Never pre-populate default strings in the global buffer, or `strncmp` matches the blank hardware display and suppresses Rows 1 & 2 on boot.
  - Call `invalidateLcdBuffer()` (`memset(currentLcdLines, 0, sizeof(currentLcdLines))`) on state transitions and `lcd.clear()`.
- **Dynamic Multi-Rate Cycling:**
  - Dynamic rates synced from `portal.py` are grouped into 2-tier pages on Rows 3 & 4 and cycled every 3.5 seconds (`RATE_CYCLE_INTERVAL_MS = 3500`), while Rows 1 & 2 remain fixed.
- **HX711 Scale Calibration Philosophy:**
  - The physical chute cannot freely suspend the entire bottle body.
  - The calibration factor (`260.0`) and weight window (`[20.0 - 90.0]g`) are empirically tuned for operational reliability across commercial bottle shapes and weights, not strict gravimetric measurement. Scale and NIR together make the final determination.

