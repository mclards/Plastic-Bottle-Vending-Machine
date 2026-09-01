# Technical Architecture: ESP32 Sub-Controller (Eco-Fi Vending)

This document provides a highly detailed, isolated breakdown of the **ESP32 Sub-Controller's** architecture. The ESP32 is strictly responsible for real-time hardware interfacing, fraud-prevention logic, and serial event broadcasting to the upstream host (Orange Pi).

---

## 1. Sub-Controller Role & Boundaries

The ESP32 operates entirely decoupled from network authorization, accounting, or firewall rules. Its only responsibilities are:
1. Managing the physical airlock (3 Servos via PCA9685).
2. Verifying object authenticity (Optical + Metal + NIR).
3. Broadcasting immutable JSON events over UART when a bottle is successfully accepted or rejected.
4. Providing a self-contained Wi-Fi Captive Portal for purely mechanical calibration.

---

## 2. Modes of Operation

The ESP32 utilizes a physical button (`GPIO 34`) to determine its boot state, completely isolating the heavy Wi-Fi stack from the time-sensitive vending logic.

### Mode A: Hardware Config Portal (Single Core)
**Trigger:** `GPIO 34` held `LOW` during boot.
- **Networking:** Spawns a SoftAP (`ECO-Fi-Hardware-Config`) and a DNS Server (`192.168.4.1`) on Core 0.
- **Web UI:** Serves a mobile-responsive captive portal.
- **Persistence:** Allows the technician to adjust `MachineConfig` variables (Servo Open/Close angles, NIR thresholds, Bin Distance) and saves them permanently into Non-Volatile Storage (NVS) using the `Preferences.h` library.
- **Safety:** The main vending loop is entirely suspended. Real-time testing of servo angles snaps the hardware into position immediately upon saving.

### Mode B: Vending & Validation (Dual Core)
**Trigger:** Default boot (no button held).
- **Core 0 (SensorTask):** Dedicated exclusively to polling IR sensors, triggering the AS7263 NIR spectrometer, and modulating the 3 I2C servos via the PCA9685.
- **Core 1 (CommTask):** Dedicated to handling UI feedback (20x4 LCD, OLED, Buzzer, LEDs) and formatting/sending JSON payloads over the Serial bus to the Orange Pi.

---

## 3. The 3-Servo Airlock Mechanism

The mechanical flow is designed to trap the object, validate it, and sort it without allowing the user to retrieve it if it fails.

| Gate Name | Channel | Action |
| :--- | :--- | :--- |
| **Entrance Gate** | `PCA Ch 0` | Opens upon UART request. Closes immediately after Top IR is broken. |
| **Success Gate** | `PCA Ch 1` | Back wall of the airlock. Opens only if Metal sensor is HIGH (idle, no metal) and NIR falls within expected thresholds. Drops object into bin. |
| **Reject Gate** | `PCA Ch 2` | Floor of the airlock. Opens downward if the Metal sensor is LOW (metal detected), capacitive fails, or NIR is out of range. Kicks object into return tray. |

---

## 4. OPi Interconnect Protocol (UART JSON)

The ESP32 interfaces with the Orange Pi Host exclusively via a 3-wire UART connection (TX, RX, GND) at `115200` baud.

### Inbound Commands (Host ➔ ESP32)
The ESP32 listens on `Serial` for string commands:
- `"OPEN_GATE\n"`: Requests the ESP32 to open the Entrance Gate (Ch 0) to begin a transaction.

### Outbound Events (ESP32 ➔ Host)
The ESP32 broadcasts asynchronous JSON payloads to notify the host of state changes.

**1. Valid Bottle Accepted**
Fired when an item passes all sensors and successfully drops past the Bottom IR.
```json
{"event": "CREDIT_ADD", "bottles": 1, "sessionTotal": 3}
```
*Host Action: Increment the connected client's unclaimed bottle pool by 1.*

**2. Invalid Object Rejected**
Fired when an item fails the metal or NIR scan and is dumped via the Reject Gate.
```json
{"event": "REJECTED"}
```
*Host Action: None required. Used for logging/analytics.*

**3. Storage Bin Full**
Fired when the JSN-SR04T Ultrasonic sensor detects the bottle pile has exceeded the configured `bin_full_threshold_cm`.
```json
{"event": "BIN_FULL"}
```
*Host Action: Disable the portal's "Insert Bottle" button and alert the admin.*

---

## 5. Hardware Pin Mapping

| Peripheral | Interface | ESP32 Pin(s) |
| :--- | :--- | :--- |
| **PCA9685 (Servos)** | I2C | SDA: 21, SCL: 22 |
| **AS7263 (NIR)** | I2C | SDA: 21, SCL: 22 |
| **20x4 LCD** | I2C | SDA: 21, SCL: 22 |
| **Top IR Sensor** | Digital In | 18 (Interrupt) |
| **Bottom IR Sensor**| Digital In | 19 (Interrupt) |
| **Metal Sensor** | Digital In | 23 |
| **Capacitive** | Digital In | 15 |
| **Ultrasonic Bin** | Dig In/Out | Trig: 14, Echo: 12 |
| **Finish Button** | Digital In | 34 |
| **Status Buzzer** | Digital Out| 33 |
| **LED Green/Red** | Digital Out| 25 / 26 |
