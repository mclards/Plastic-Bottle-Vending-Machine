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

All screenshots below are also documented step-by-step in [USER_GUIDE.md](USER_GUIDE.md). Click any preview to view the full-resolution image.

### 1. Customer Captive Portal & Hardware Simulator

| Ready & Rates | Airlock Gate Open (Deposit) | Connected Session (`CONNECTED`) |
| :---: | :---: | :---: |
| <img src="screenshots/portal-ready.png" alt="Customer Portal Ready" width="220"> | <img src="screenshots/portal-deposit.png" alt="Airlock Deposit Session" width="220"> | <img src="screenshots/portal-connected.png" alt="Connected Session" width="220"> |

| Paused Session (`PAUSED`) | Voucher Redemption Tab | Time Transfer / Share Tab |
| :---: | :---: | :---: |
| <img src="screenshots/portal-paused.png" alt="Paused Session" width="220"> | <img src="screenshots/portal-voucher.png" alt="Voucher Tab" width="220"> | <img src="screenshots/portal-transfer.png" alt="Time Transfer Tab" width="220"> |

**ESP32 Hardware Simulator (`/simulator`)**

<img src="screenshots/portal-simulator.png" alt="ESP32 Hardware Simulator" width="560">

---

### 2. Admin Panel — Vendo Operations

| Admin Sign-In | Dashboard & Stats |
| :---: | :---: |
| <img src="screenshots/admin-login.png" alt="Admin Login" width="380"> | <img src="screenshots/admin-dashboard.png" alt="Dashboard & Stats" width="380"> |

| Active Clients | Voucher Tickets |
| :---: | :---: |
| <img src="screenshots/admin-clients.png" alt="Active Clients" width="380"> | <img src="screenshots/admin-vouchers.png" alt="Voucher Tickets" width="380"> |

| Rates & Packages | Validity & Pauses |
| :---: | :---: |
| <img src="screenshots/admin-rates.png" alt="Rates & Packages" width="380"> | <img src="screenshots/admin-validity-pauses.png" alt="Validity & Pauses" width="380"> |

---

### 3. Admin Panel — Network & Traffic

| Network — Adapters & WAN/LAN | Network — DNS & Domain |
| :---: | :---: |
| <img src="screenshots/admin-network-adapters.png" alt="Adapters & WAN/LAN" width="380"> | <img src="screenshots/admin-network-dns.png" alt="DNS & Domain" width="380"> |

| Network — DHCP & Leases | Network — Access Policies |
| :---: | :---: |
| <img src="screenshots/admin-network-dhcp.png" alt="DHCP & Leases" width="380"> | <img src="screenshots/admin-network-policies.png" alt="Access Policies" width="380"> |

| Bandwidth & Speed + Gaming QoS | Walled Garden Sites |
| :---: | :---: |
| <img src="screenshots/admin-bandwidth.png" alt="Bandwidth & Speed" width="380"> | <img src="screenshots/admin-walled-garden.png" alt="Walled Garden Sites" width="380"> |

| MAC Filtering (VIP & Blacklist) | |
| :---: | :---: |
| <img src="screenshots/admin-mac-filtering.png" alt="MAC Filtering" width="380"> | |

---

### 4. Admin Panel — Portal & Branding

| Portal & Banners | Audio & Chimes |
| :---: | :---: |
| <img src="screenshots/admin-portal-banners.png" alt="Portal & Banners" width="380"> | <img src="screenshots/admin-audio-chimes.png" alt="Audio & Chimes" width="380"> |

---

### 5. Admin Panel — ESP32 Hardware & Sensors

| Chute Sequence Monitor | NIR Spectrometer (AS7263) |
| :---: | :---: |
| <img src="screenshots/admin-esp32-sequence.png" alt="Chute Sequence Monitor" width="380"> | <img src="screenshots/admin-esp32-nir.png" alt="NIR Spectrometer" width="380"> |

| Weight Sensor (HX711) | Servos & Gates (PCA9685) |
| :---: | :---: |
| <img src="screenshots/admin-esp32-weight.png" alt="Weight Sensor" width="380"> | <img src="screenshots/admin-esp32-servos.png" alt="Servos & Gates" width="380"> |

| Timers & Proximity | Firmware Flash |
| :---: | :---: |
| <img src="screenshots/admin-esp32-timers.png" alt="Timers & Proximity" width="380"> | <img src="screenshots/admin-esp32-firmware.png" alt="Firmware Flash" width="380"> |

---

### 6. Admin Panel — System & Maintenance

| Telegram Automated Alerts | System Verifier & Health |
| :---: | :---: |
| <img src="screenshots/admin-telegram.png" alt="Telegram Alerts" width="380"> | <img src="screenshots/admin-system-verifier.png" alt="System Verifier" width="380"> |

| Database Backup & Restore | Power & Operations |
| :---: | :---: |
| <img src="screenshots/admin-system-backup.png" alt="Backup & Database" width="380"> | <img src="screenshots/admin-system-power.png" alt="Power & Operations" width="380"> |

| System Event Logger | Hardware Licensing |
| :---: | :---: |
| <img src="screenshots/admin-system-logs.png" alt="System Event Logger" width="380"> | <img src="screenshots/admin-licensing.png" alt="Hardware Licensing" width="380"> |
