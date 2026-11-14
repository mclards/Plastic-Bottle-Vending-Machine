# VMC ECO-VENDO — Bottle-to-WiFi Captive Portal & Admin System

**Version:** `2.3.20`

VMC ECO-VENDO is a Smart Reverse Vending Machine (RVM) captive portal and WiFi management platform (`host/`) that rewards users with metered internet access in exchange for qualified empty PET plastic bottles. It includes a mobile-friendly Customer Captive Portal, a full-featured Admin Control Panel, automated traffic shaping and Gaming QoS, multi-sensor chute verification controls, and an authenticated hardware simulator.

> **Looking for step-by-step instructions?** Read the **[Complete User Guide & Operator Manual](USER_GUIDE.md)** for illustrated walkthroughs of every customer and administrator screen.

---

## Key Capabilities

- **Customer Captive Portal**
  - Live `AVAILABLE INTERNET TIME` countdown (`Dd HHh:MMm:SSs`) and connection status pill (`DISCONNECTED`, `CONNECTED`, `PAUSED`).
  - Interactive `AIRLOCK GATE OPEN` bottle deposit session with a live gate countdown, bottle counter, credited minutes, and one-tap internet activation.
  - Configurable `PAUSE TIME` / `Resume Time` controls showing remaining pauses, expiration (`Valid Until`), and pause auto-resume deadlines.
  - Prepaid voucher redemption (`Eco-XXXX`) and device-to-device time transfer codes.
  - Built-in Rate Schedule and Bottle Acceptance Guide (`Rates`, `Voucher`, `Transfer` tabs).

- **Vendo Operations & Analytics**
  - Real-time KPI Dashboard (Today's Bottles, Lifetime Bottles, Active Clients, License Status), Weekly/Monthly/Yearly charts, Gateway & ESP32 resource telemetry, ESG environmental metrics, Peak Usage Window, and Bin Fill Level.
  - Live Connected Client Sessions table with per-client `+15m`, `Pause`, `Resume`, `Edit`, and `Kick` actions.
  - Batch Prepaid Voucher generator (`1–100` tickets), printable/exportable voucher list, and redemption history.
  - Customizable base rates, promo rate packages, drop timeout, rate templates (`Standard`, `Aggressive`, `Cafe`), and validity/pause policy tiers.

- **Network, Bandwidth & Access Control**
  - WAN/LAN adapter configuration, captive DNS & domain routing, DHCP subnet monitor (`10.0.0.0/19`, `8,190 IPs`), and isolation policies (ISP modem isolation, Starlink dish protection, anti-tethering defense).
  - Fixed per-client bandwidth limits, Dynamic Adaptive Bandwidth Scaling, and Gaming QoS packet prioritization (Mobile Legends, Wild Rift & LoL, Steam & Dota 2, Valorant & Riot, Roblox).
  - Walled Garden pre-authentication allowlist and MAC VIP Whitelist / Blacklist filtering.

- **Portal Branding, Audio & ESP32 Hardware Controls**
  - Custom portal name, tagline, announcement banner, and 4-event audio chimes (Standby Loop, Deposit Chime, Session Complete, Invalid Object Alert) with master volume control.
  - Live 7-stage hardware Chute Sequence Monitor (`S1–S7`), AS7263 6-channel NIR spectroscopy profiles & teaching presets, HX711 load-cell scale calibration & tare, PCA9685 entrance/drop servo testing, ultrasonic bin fill & cycle timers, ESP32 `.bin` firmware flasher, and an authenticated interactive Hardware Simulator (`/simulator`).

- **System Maintenance & Alerts**
  - Automated Telegram Bot notifications (Bin Full alerts and Daily Summaries).
  - Built-in System Verifier & Health diagnostics, SQLite database `.zip`/`.db` backup & integrity-checked restore, safe power controls, filterable System Event Logger, and Hardware ID (`HWID`) licensing.

---

## Repository Structure

```text
.
├── VERSION           # Current release version (2.3.20)
├── README.md         # Project overview and complete UI screenshot gallery
├── USER_GUIDE.md     # Full Customer User Guide & Administrator Operator Manual
├── screenshots/      # Captures of all Customer Portal, Simulator, and Admin views
└── host/             # Captive portal backend, entitlement engine, network/QoS modules, static assets, and tests
    ├── portal.py
    ├── time_portal.py
    ├── time_schema.py
    ├── time_policy.py
    ├── time_storage.py
    ├── transition_engine.py
    ├── gateway_network.py
    ├── system_logger.py
    ├── license_manager.py
    ├── esp32_simulator.py
    ├── static/
    │   └── time_controls.js
    └── test_*.py
```

---

## Running the Test Suite

To verify the entitlement, state transition, voucher, and captive portal backend tests locally (requires Python 3.5+):

```bash
python -B -m unittest discover -s host -p "test_*.py"
```

---

## Portal & Admin Screenshot Gallery

All screenshots below are also documented step-by-step in [USER_GUIDE.md](USER_GUIDE.md).

### 1. Customer Captive Portal & Hardware Simulator

| View | Preview |
| :--- | :--- |
| **Ready / Disconnected Portal & Rates** | ![Customer Portal Ready](screenshots/portal-ready.png) |
| **Airlock Gate Open (Bottle Deposit)** | ![Airlock Deposit Session](screenshots/portal-deposit.png) |
| **Connected Session (`CONNECTED`)** | ![Connected Session](screenshots/portal-connected.png) |
| **Paused Session (`PAUSED`)** | ![Paused Session](screenshots/portal-paused.png) |
| **Voucher Redemption Tab** | ![Voucher Tab](screenshots/portal-voucher.png) |
| **Time Transfer / Share Tab** | ![Time Transfer Tab](screenshots/portal-transfer.png) |
| **ESP32 Hardware Simulator (`/simulator`)** | ![ESP32 Hardware Simulator](screenshots/portal-simulator.png) |

---

### 2. Admin Panel — Vendo Operations

| View | Preview |
| :--- | :--- |
| **Admin Sign-In** | ![Admin Login](screenshots/admin-login.png) |
| **Dashboard & Stats** | ![Dashboard & Stats](screenshots/admin-dashboard.png) |
| **Active Clients** | ![Active Clients](screenshots/admin-clients.png) |
| **Voucher Tickets** | ![Voucher Tickets](screenshots/admin-vouchers.png) |
| **Rates & Packages** | ![Rates & Packages](screenshots/admin-rates.png) |
| **Validity & Pauses** | ![Validity & Pauses](screenshots/admin-validity-pauses.png) |

---

### 3. Admin Panel — Network & Traffic

| View | Preview |
| :--- | :--- |
| **Network — Adapters & WAN/LAN** | ![Adapters & WAN/LAN](screenshots/admin-network-adapters.png) |
| **Network — DNS & Domain** | ![DNS & Domain](screenshots/admin-network-dns.png) |
| **Network — DHCP & Leases** | ![DHCP & Leases](screenshots/admin-network-dhcp.png) |
| **Network — Access Policies** | ![Access Policies](screenshots/admin-network-policies.png) |
| **Bandwidth & Speed + Gaming QoS** | ![Bandwidth & Speed](screenshots/admin-bandwidth.png) |
| **Walled Garden Sites** | ![Walled Garden Sites](screenshots/admin-walled-garden.png) |
| **MAC Filtering (VIP & Blacklist)** | ![MAC Filtering](screenshots/admin-mac-filtering.png) |

---

### 4. Admin Panel — Portal & Branding

| View | Preview |
| :--- | :--- |
| **Portal & Banners** | ![Portal & Banners](screenshots/admin-portal-banners.png) |
| **Audio & Chimes** | ![Audio & Chimes](screenshots/admin-audio-chimes.png) |

---

### 5. Admin Panel — ESP32 Hardware & Sensors

| View | Preview |
| :--- | :--- |
| **ESP32 — Chute Sequence Monitor** | ![Chute Sequence Monitor](screenshots/admin-esp32-sequence.png) |
| **ESP32 — NIR Spectrometer (AS7263)** | ![NIR Spectrometer](screenshots/admin-esp32-nir.png) |
| **ESP32 — Weight Sensor (HX711)** | ![Weight Sensor](screenshots/admin-esp32-weight.png) |
| **ESP32 — Servos & Gates (PCA9685)** | ![Servos & Gates](screenshots/admin-esp32-servos.png) |
| **ESP32 — Timers & Proximity** | ![Timers & Proximity](screenshots/admin-esp32-timers.png) |
| **ESP32 — Firmware Flash** | ![Firmware Flash](screenshots/admin-esp32-firmware.png) |

---

### 6. Admin Panel — System & Maintenance

| View | Preview |
| :--- | :--- |
| **Telegram Automated Alerts** | ![Telegram Alerts](screenshots/admin-telegram.png) |
| **System Verifier & Health** | ![System Verifier](screenshots/admin-system-verifier.png) |
| **Database Backup & Restore** | ![Backup & Database](screenshots/admin-system-backup.png) |
| **Power & Operations** | ![Power & Operations](screenshots/admin-system-power.png) |
| **System Event Logger** | ![System Event Logger](screenshots/admin-system-logs.png) |
| **Hardware Licensing** | ![Hardware Licensing](screenshots/admin-licensing.png) |
