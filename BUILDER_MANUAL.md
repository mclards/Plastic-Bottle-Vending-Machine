# Smart Eco-Fi Vendo System — Comprehensive Builder Manual
**Version:** 2.0 (Production-Hardened) · **Architecture:** Hybrid ESP32 + Orange Pi Gateway

> [!NOTE]
> This manual is the definitive engineering and deployment guide for assembling, wiring, programming, and deploying the **Smart Eco-Fi Reverse Vending Machine (PBVM)**. The system accepts recyclable PET plastic bottles and trades them for high-speed Wi-Fi hotspot access.

---

## 1. System Architecture

The system utilizes a **Distributed Hybrid Architecture**:
- **ESP32 DevKit V1 (Sub-Controller):** Real-time hardware control, anti-cheat sensor array (NIR Spectrometer, Inductive Metal, Capacitive Proximity, Dual Optical IR), 3-Servo motorized airlock, and I2C LCD.
- **Orange Pi One / Zero 3 / 3B (Core Gateway):** Linux networking, Nginx reverse proxy, Flask web engine, SQLite session accounting, dynamic `ipset` firewall, Linux `tc` HTB per-client bandwidth shaping, Silicon HWID licensing, and Excel reporting.

```mermaid
flowchart TD
    subgraph Hardware Sub-Controller [ESP32 DevKit V1]
        IR1[Top Optical IR Sensor] --> DET[Intake Detection]
        IND[Inductive Metal Proximity] --> VAL[Anti-Fraud Validation]
        CAP[Capacitive Plastic Proximity] --> VAL
        NIR[AS7263 NIR Spectrometer] --> VAL
        IR2[Bottom Optical IR Sensor] --> DROP[Drop Confirmation]
        
        VAL -->|Valid PET Bottle| S_SUC[Success Gate Servo]
        VAL -->|Metal / Non-PET / Fraud| S_REJ[Reject Gate Servo]
        DET --> S_ENT[Entrance Gate Servo]
    end

    subgraph Core Gateway [Orange Pi - Armbian / Linux]
        NGINX[Nginx Reverse Proxy :80] --> FLASK[ECO-Fi Web Engine :5000]
        FLASK --> DB[(SQLite: vendo_sessions.db)]
        FLASK --> FW[Dynamic ipset & iptables Firewall]
        FLASK --> TC[Traffic Control: 3 Mbps Bandwidth Shaper]
        FLASK --> LIC[Silicon HWID Anti-Cloning Engine]
    end
    
    Hardware Sub-Controller -- "JSON UART (115200 Baud)" --> Core Gateway
    Core Gateway -- "Ethernet / TP-Link AP" --> CLIENTS((Connected Wi-Fi Clients))
```

---

## 2. Bill of Materials (BOM)

### Computing & Networking
| Component | Qty | Engineering Purpose |
|---|---|---|
| **Orange Pi One / Zero 3 / 3B** | 1 | Linux gateway, captive portal, SQLite accounting, dynamic firewall |
| **ESP32 DevKit V1 (30-Pin)** | 1 | Real-time sensor processing, anti-fraud pipeline, servo motor control |
| **MicroSD Card (32GB / 64GB Class 10)** | 1 | Operating system storage, SQLite database, offline cache |
| **TP-Link Outdoor Access Point (EAP110/225)** | 1 | Long-range Wi-Fi hotspot coverage |
| **USB-to-UART Serial Cable / Direct UART** | 1 | 115200 Baud JSON frame link between ESP32 and Orange Pi |

### Actuators & Mechanics
| Component | Qty | Engineering Purpose |
|---|---|---|
| **MG996R High-Torque Servo Motor** | 3 | Ch 0: Entrance Gate; Ch 1: Success Gate; Ch 2: Reject Gate |
| **PCA9685 16-Channel 12-Bit PWM Driver** | 1 | Dedicated I2C servo pulse generator (isolates microcontrollers from PWM jitter) |
| **Acrylic / 3D Printed Airlock Chute** | 1 | Mechanical gravity chute housing sensors and servo trapdoors |

### Sensors & User Feedback
| Component | Qty | Engineering Purpose |
|---|---|---|
| **E18-D80NK Optical IR Sensors** | 2 | Top intake trigger (IR #1) and bottom chute drop confirmation (IR #2) |
| **LJ12A3-4-Z/BX Inductive Proximity Sensor** | 1 | Rejects tin cans, aluminum, and metallic objects (NPN-NO, 6–36V) |
| **Capacitive Proximity Sensor (LJC18A3)** | 1 | Verifies non-metallic mass presence (NPN-NO, 6–36V) |
| **AS7263 NIR 6-Channel Spectrometer** | 1 | Near-Infrared optical absorption verification of PET polymer signature |
| **JSN-SR04T Waterproof Ultrasonic Sensor** | 1 | Real-time bin capacity & fill level monitoring |
| **20x4 I2C Character LCD** | 1 | On-machine customer guidance and bottle count display |
| **Active 5V Buzzer & Status LEDs** | 1 | Audible chimes and visual green/red feedback |
| **Push Button (Stainless Steel)** | 1 | Session finish button (held during power-on triggers Config Mode) |

### Power Distribution & Protection
| Component | Qty | Engineering Purpose |
|---|---|---|
| **12V 5A Industrial SMPS Power Supply** | 1 | Main AC-to-DC system power source |
| **XL4015 5A Step-Down Buck Converter** | 1 | Regulates 5.1V logic rail for Orange Pi, ESP32, sensors, and LCD |
| **LM2596 3A Step-Down Buck Converter** | 1 | **Isolated 5.0V motor rail** for MG996R servos (prevents logic brownouts) |
| **10kΩ & 4.7kΩ Resistor Dividers** | 3 | Level-shifts 12V sensor outputs to safe 3.3V ESP32 GPIO logic |

---

## 3. Hardware Wiring & Pin Mapping

> [!WARNING]
> **Strict Power Isolation:** Never connect the MG996R servo power wires to the ESP32 or Orange Pi 5V logic pins. Servos must draw current strictly from the dedicated LM2596 motor buck converter. Ground (GND) must be common across all rails.

### ESP32 Pin Assignment Table

| Module / Signal | ESP32 GPIO | Module Pin | Power Rail | Electrical Notes |
|---|---|---|---|---|
| **Top Optical IR (E18-D80NK #1)** | **GPIO 18** | OUT (Black) | 5.1V Logic (Brown), GND (Blue) | Configured with `INPUT_PULLUP`. Low = Beam Broken. |
| **Bottom Optical IR (E18-D80NK #2)**| **GPIO 19** | OUT (Black) | 5.1V Logic (Brown), GND (Blue) | Configured with `INPUT_PULLUP`. Low = Beam Broken. |
| **Inductive Metal (LJ12A3)** | **GPIO 23** | OUT (Black) | 12V Main (Brown), GND (Blue) | **CRITICAL:** Use 10kΩ / 4.7kΩ voltage divider to drop 12V output to 3.3V! Idle = HIGH, Metal Detected = LOW. |
| **Capacitive Proximity** | **GPIO 15** | OUT (Black) | 12V Main (Brown), GND (Blue) | Voltage divider to 3.3V. Idle = HIGH, Object Detected = LOW. |
| **Ultrasonic Bin Sensor (JSN)** | **GPIO 14** (Trig), **GPIO 12** (Echo) | Trig, Echo | 5.1V Logic (VCC), GND | Level-shift Echo output to 3.3V logic. |
| **PCA9685 PWM Driver** | **GPIO 21** (SDA), **GPIO 22** (SCL) | SDA, SCL | 5.1V Logic (VCC), GND | I2C Address `0x40`. Servos powered via V+ terminal block. |
| **20x4 I2C LCD Display** | **GPIO 21** (SDA), **GPIO 22** (SCL) | SDA, SCL | 5.1V Logic (VCC), GND | I2C Address `0x27` (or `0x3F`). |
| **AS7263 NIR Spectrometer** | **GPIO 21** (SDA), **GPIO 22** (SCL) | SDA, SCL | 3.3V Logic (VIN), GND | I2C Address `0x49`. |
| **Feedback Buzzer** | **GPIO 33** | Signal | 5.1V Logic (VCC), GND | Active buzzer driver. |
| **Status LEDs (Green / Red)** | **GPIO 25** (Green), **GPIO 26** (Red) | Anode | ESP32 GND (via 220Ω resistor) | Visual feedback indicators. |
| **Finish / Config Button** | **GPIO 34** | Terminal 1 | ESP32 GND | Input-only pin with external pull-up. Hold at boot for Config Portal! |

---

## 4. Sub-Controller Firmware (ESP32)

The ESP32 firmware is developed in C++ using **PlatformIO**. It executes a dual-core FreeRTOS pipeline:
- **Core 0:** Sensor sampling, NIR spectroscopy, and 3-servo airlock control.
- **Core 1:** UART JSON messaging with the Orange Pi host and on-board LCD display.

### Flash Firmware via PlatformIO
1. Open the project in VSCode with PlatformIO installed.
2. Connect the ESP32 via Micro-USB.
3. Build and upload firmware:
   ```bash
   pio run --target upload
   ```

### UART Protocol Specification (115200 Baud, 8N1)
* **Outbound Events (ESP32 $\rightarrow$ Host):**
  * `{"event":"CREDIT_ADD", "bottles":1, "sessionTotal":3}` — Valid PET bottle accepted and dropped into bin.
  * `{"event":"REJECTED", "reason":"METAL_DETECTED"}` — Metal object rejected.
  * `{"event":"REJECTED", "reason":"NIR_MISMATCH"}` — Non-PET plastic rejected.
  * `{"event":"BIN_FULL", "distance":5}` — Storage bin capacity exceeded.
  * `{"event":"BIN_OK"}` — Storage bin emptied.
  * `{"event":"CONFIG_SAVED"}` — Calibration settings saved to NVS.
* **Inbound Commands (Host $\rightarrow$ ESP32):**
  * `{"cmd":"OPEN_GATE", "timeout":30}` — Opens entrance gate for customer insertion session.
  * `{"cmd":"CLOSE_GATE"}` — Closes entrance gate immediately.
  * `{"cmd":"SET_CONFIG", ...}` — Calibrates servo angles and sensor thresholds dynamically from Admin Panel.
  * `{"cmd":"TRIGGER_CONFIG"}` — Reboots ESP32 into on-board SoftAP Config Mode.

---

## 5. Orange Pi Firmware & OS Image Builder

We provide an automated, reproducible builder script (`build_ecofi_img.sh` / `build_ecofi_img.bat`) that transforms a base Armbian image into a production-hardened **ECO-Fi OS Image**.

### Automated Image Build (WSL / Linux)
```bash
# Run from repository root in WSL or native Linux:
sudo bash build_ecofi_img.sh
```

### What the Build Script Executes:
1. **Purges Legacy Services & Backdoors:** Strips old PHP daemons, MySQL, Zerotier, Ngrok, and legacy phone-home binaries.
2. **Installs High-Performance Nginx Reverse Proxy:** Captive portal trigger intercepts on Port 80 (`/generate_204`, `/gen_204`, `/connecttest.txt`, `ncsi.txt`) with zero-latency proxy pass to Python Flask on Port 5000.
3. **Static Subnet & Network Configuration:** Assigns static IP `10.0.0.1/19` (pool `10.0.0.2` – `10.0.31.254`), updates `dnsmasq`, enables kernel IP forwarding (`net.ipv4.ip_forward=1`), and sets up WAN NAT masquerade.
4. **Installs System Dependencies:** Automatically installs `flask`, `werkzeug`, `pyserial`, and `openpyxl`.
5. **Registers Systemd Services:**
   * `ecofi_firewall.service` — Automatic `ipset` creation, NAT masquerade, and drop-first firewall rules.
   * `ecofi_portal.service` — Daemonized captive portal running `/opt/ecofi/portal.py`.
   * `ecofi_firstboot.service` — Single-run dependency installer that disables itself upon success.

---

## 6. Captive Portal & Network Engine

### Bandwidth Management (3 Mbps Default)
* **Standard Speed:** **3072 Kbps (3 Mbps) Download / 1536 Kbps (1.5 Mbps) Upload** applied automatically to all authorized users via Linux `tc` Hierarchical Token Bucket (HTB).
* **Per-Client Speed Customization:** Administrators can adjust bandwidth per connected client in real-time from the Admin Panel; speeds persist across restarts in `active_sessions`.
* **Anti-Tethering:** Automatically sets outgoing TTL to `64` (`iptables -t mangle -A POSTROUTING -j TTL --ttl-set 64`) to prevent unauthorized hotspot sharing.

### Dynamic Session Validity & Pause Math
When users pause their Wi-Fi time, the session expiration is computed mathematically based on their remaining balance:
$$\text{Validity}(T) = \min\left(720\text{h},\ \max\left(24\text{h},\ 12\text{h} + 1.2\sqrt{\text{Mins}} + 0.025\times\text{Mins}\right)\right)$$
* **Short sessions (< 1h):** 24-hour expiration window.
* **Large balances (10+ hours):** Up to 30 days expiration window.

### Rates & Promo Curves
* Clean, plain 2-column mobile layout:
  * **1 Bottle** = `10 mins` (Base Rate)
  * **3 Bottles** = `40 mins` (+33% bonus yield)
  * **5 Bottles** = `1h 15m` (+50% bonus yield)
  * **10 Bottles** = `3 Hours` (+80% bonus yield)
* Strict monotonic rate validation prevents pricing conflicts or loopholes.

---

## 7. Master Admin Control Panel (`/admin`)

* **Access:** Navigate to `http://10.0.0.1/admin` (or `http://localhost:5000/admin`).
* **Authentication Guard:** Global session-based authentication blocks unauthenticated direct access to all API routes and data exports.
* **Operations & Accounting Excel Export (`/admin/api/export_xlsx`):**
  * Downloads professional `.xlsx` workbook formatted with 4 dedicated sheets:
    1. *Daily Collections & Environmental Impact* (Bottles recycled, plastics diverted, estimated weight).
    2. *Voucher Inventory* (Codes, duration, status, creation date, redemption user).
    3. *Member Wallets* (Registered usernames, current minute balances, registration timestamps).
    4. *Promo Rate Curves* (Active packages, efficiency rates, and bonus yields).
* **ESP32 Hardware Calibration:** Configure servo travel angles (Entrance, Success, Reject), NIR spectral window, and ultrasonic bin distance with live UART push to hardware NVS.

---

## 8. Calibration & Deployment Checklist

1. **Power Supply Validation:**
   * Verify Logic Rail outputs **5.10V ± 0.05V**.
   * Verify Motor Rail outputs **5.00V ± 0.05V**.
2. **Inductive Metal Sensor (LJ12A3):**
   * Adjust rear trimmer pot until metal bottle caps trigger at 4–6 mm.
3. **Capacitive Proximity Sensor (LJC18A3):**
   * Adjust sensitivity so plastic bottles trigger consistently while avoiding false triggers from the chute frame.
4. **AS7263 NIR Spectrometer Calibration:**
   * Insert sample PET bottles, review W-channel spectral readings in the Admin Hardware tab, and set upper/lower limits.
5. **Optical IR Sensors (E18-D80NK):**
   * Adjust distance screws until beam breaks cleanly on bottle passage.
6. **System Verification Test:**
   * Run full test suite:
     ```bash
     python scratch/run_full_system_test.py
     ```
   * Ensure **19/19 system tests PASS**.

---
*Smart Eco-Fi Reverse Vending Machine — Built for Sustainability, Performance, and Security.*
