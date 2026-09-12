# Eco-Fi Smart Reverse Vending Machine — Agent Engineering Manual
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
  - Reverse Proxy: **Nginx** listens on port `80`, proxying traffic to the Eco-Fi Python engine on port `5000`.

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
  - The wallet/member system has been completely decommissioned from Eco-Fi.
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
