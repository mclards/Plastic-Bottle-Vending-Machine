# Technical Architecture: ESP32 Hardware Config Portal & 3-Servo Vending System

This document outlines the implemented architecture for the ESP32 Sub-Controller. It details the dedicated Captive Portal for hardware configuration and the modernized 3-servo sorting system, replacing the legacy 12V rejection solenoid and load cell.

---

## 1. Components & Configurable Parameters

The Config Portal interfaces directly with the following physical hardware components outlined in the `BUILDER_MANUAL.md`. All variables below are stored in the ESP32's Non-Volatile Storage (NVS) and can be dynamically adjusted.

| Component | Connected Pin | Configurable Parameter (via Portal) |
| :--- | :--- | :--- |
| **Physical Finish Button** | `GPIO 34` | Triggers the Config Portal during boot if held `LOW`. |
| **AS7263 NIR Spectrometer** | `I2C (0x49)` | `pet_nir_w_min`, `pet_nir_w_max` (NIR W-Channel thresholds) |
| **PCA9685 I2C Servo Driver** | `I2C (0x40)` | **Entrance (Ch 0):** `ent_open_angle`, `ent_close_angle`<br>**Success (Ch 1):** `suc_open_angle`, `suc_close_angle`<br>**Reject (Ch 2):** `rej_open_angle`, `rej_close_angle` |
| **JSN-SR04T Ultrasonic** | `Trig (14), Echo (12)`| `bin_full_threshold_cm` (Distance to trigger BIN FULL) |
| **E18-D80NK Top IR Sensor** | `GPIO 18` | `entrance_gate_timeout` (Time allowed to drop bottle) |
| **20x4 I2C LCD Display** | `I2C (0x27)` | Displays "CONFIG MODE" and connection IP |

---

## 2. Operational Flow Diagram

```mermaid
flowchart TD
    Start([Power On / Reset]) --> Init(Initialize Serial, LCD & GPIOs)
    Init --> CheckBtn{Is GPIO 34<br/>(Finish Button)<br/>Held LOW?}
    
    CheckBtn -- YES (Held) --> ConfigMode[ENTER CONFIG MODE]
    CheckBtn -- NO (Not Held) --> NormalMode[ENTER NORMAL VENDING MODE]
    
    subgraph Config_Mode_Core_0 [CONFIG MODE (Core 0 only)]
        ConfigMode --> ShowLCD1[LCD: 'ENTERING CONFIG MODE']
        ShowLCD1 --> StartAP[Start SoftAP: 'ECO-Fi-Hardware-Config']
        StartAP --> StartDNS[Start DNSServer: Redirect * to 192.168.4.1]
        StartDNS --> StartWeb[Start WebServer on Port 80]
        
        StartWeb --> WaitClient((Wait for<br/>Client Connection))
        WaitClient -- "Client connects to WiFi" --> CaptivePortal[Serve Captive Portal HTML]
        CaptivePortal -- "User Submits POST /save" --> SaveNVS[Save to Preferences NVS]
        SaveNVS --> ShowSuccess[Display Success Message]
        ShowSuccess --> SnapServos[Snap Servos to Test Seal]
        SnapServos --> AwaitReboot([Wait for Manual Reboot])
    end

    subgraph Normal_Mode_Dual_Core [NORMAL VENDING MODE (Dual Core)]
        NormalMode --> LoadNVS[Load Parameters from NVS]
        LoadNVS --> Core0[Start sensorTaskCode on Core 0]
        LoadNVS --> Core1[Start commTaskCode on Core 1]
        
        Core0 --> VendingLoop1((Real-Time Hardware<br/>Sensing & Actuation))
        Core1 --> VendingLoop2((Real-Time UART<br/>Telemetry to Orange Pi))
    end
```

---

## 3. Implemented Code Changes

### [src/main.cpp](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/src/main.cpp)
**1. Legacy Cleanup:**
- Completely stripped out `HX711.h` and all load cell weight logic.
- Completely stripped out `PIN_SOLENOID_REJ` (GPIO 32) and its associated activation logic.

**2. Global Config Struct (`MachineConfig`):**
A global struct populated via `Preferences` on boot holds all limits for the mechanical system:
```cpp
struct MachineConfig {
    int bin_full_threshold_cm = 15;
    int pet_nir_w_min = 200;
    int pet_nir_w_max = 5000;
    int entrance_gate_timeout = 30;
    
    // Independent Servo Angles for Fine-Tuning
    int ent_open_angle = 90;
    int ent_close_angle = 0;
    int suc_open_angle = 90;
    int suc_close_angle = 0;
    int rej_open_angle = 90;
    int rej_close_angle = 0;
};
```

**3. Captive Portal Mode (GPIO 34 Trigger):**
In `setup()`, GPIO 34 (Finish Button) is checked immediately. If held `LOW`:
- **SoftAP Initialization:** Spawns `ECO-Fi-Hardware-Config` access point.
- **DNS Server:** A `DNSServer` resolves all DNS queries (`*`) to the ESP32's AP IP, forcing mobile devices to trigger a captive portal popup.
- **Web Server:** 
  - `GET /` -> Serves embedded HTML UI.
  - `POST /save` -> Updates all servo angles and sensor thresholds, saves to `preferences`. Instantly snaps servos to their new "Closed" positions so the builder can verify the mechanical seal visually.
  - Spawns `configPortalTask` pinned to Core 0, suspending normal vending entirely.

**4. 3-Servo Vending Logic Validation:**
Inside `sensorTaskCode`:
- **Default State:** Entrance closed, Success closed, Reject closed.
- **Entry:** Open Entrance Servo (`ent_open_angle`). Wait for Top IR. Close Entrance (`ent_close_angle`).
- **Validation:** 
  1. Metal Sensor must be HIGH (no metal).
  2. Capacitive Sensor must be LOW (item present).
  3. NIR Spectrometer W-Channel must fall between `pet_nir_w_min` and `pet_nir_w_max`.
- **Valid Route:** Open Success Servo (`suc_open_angle`). Wait for Bottom IR or 3s timeout. Close Success Servo (`suc_close_angle`). Send `CREDIT_ADD` event to Orange Pi via `eventQueue`.
- **Invalid Route:** Open Reject Servo (`rej_open_angle`). Delay 2 seconds for gravity drop. Close Reject Servo (`rej_close_angle`). Send `REJECTED` event.

---

## Verification Plan

### Manual Field Verification
1. **Flash Firmware:** `pio run --target upload`.
2. **Boot Test (Normal):** Power on normally. Verify the 3 servos snap to their defined "close" angles.
3. **Vending Flow:** Drop a valid item, verify Success Gate (Ch 1) opens. Drop a tin can, verify Reject Gate (Ch 2) opens downward.
4. **Boot Test (Config):** Power off. Hold GPIO 34. Power on. Release GPIO 34 when LCD says "CONFIG MODE".
5. **Portal Test:** Connect to `ECO-Fi-Hardware-Config`, adjust the `rej_open_angle` to 120 degrees, save, and verify the Reject Gate immediately physically updates its angle. Reboot normally to resume vending.
