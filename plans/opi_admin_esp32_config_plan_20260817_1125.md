# Implementation Plan: Centralized ESP32 Hardware Configuration via Orange Pi

## Objective
To provide the "best approach" for managing the ESP32 hardware, we will build a centralized **ESP32 Hardware** tab directly into the Orange Pi Master Admin Panel. 

Instead of forcing the admin to disconnect from the Orange Pi, connect to the ESP32's captive portal, and re-connect back to the Orange Pi every time they want to tweak a servo angle, the Orange Pi will act as the master controller and beam the settings down to the ESP32 over the Serial UART connection.

## User Review Required
> [!IMPORTANT]
> **Dependency Addition:** To allow the ESP32 to safely parse complex configuration packets from the Orange Pi, I will need to add the `bblanchon/ArduinoJson` library to your `platformio.ini` file. This is the industry standard for parsing JSON in C++.

## Open Questions
> [!QUESTION]
> 1. **Config Portal Fallback:** Even with the Orange Pi handling configurations, do you still want me to add a "Reboot ESP32 into Wi-Fi Config Mode" button on the Orange Pi dashboard as a fallback (e.g., in case you want to use your phone directly on the ESP32 later)? 
> 2. Are you okay with adding the `ArduinoJson` library to the ESP32 firmware?

---

## Proposed Changes

### 1. Orange Pi Backend & Dashboard
#### [MODIFY] [host/portal.py](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/host/portal.py)
- **UI Update:** Add a new `<i class="fas fa-microchip"></i> ESP32 Hardware` tab to the AdminLTE sidebar.
- **Form Interface:** Build a form inside `ADMIN_HTML` that mirrors the ESP32 configuration (Settle Time, Drop Time, Bin Distance, Servo Angles, NIR Limits).
- **UART API:** Create `/admin/api/esp32/save` which takes the form inputs, wraps them in a JSON payload `{"cmd":"SET_CONFIG", "ent_open":90, ...}`, and sends them to the ESP32 via `transmit_to_esp32()`.

### 2. ESP32 Firmware
#### [MODIFY] [platformio.ini](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/platformio.ini)
- Add `bblanchon/ArduinoJson@^6.21.3` to the `lib_deps`.

#### [MODIFY] [src/main.cpp](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/src/main.cpp)
- **JSON Parsing:** Include `<ArduinoJson.h>`.
- **Serial Listener:** Update the `loop()` function to intercept the `SET_CONFIG` JSON packet. 
- **Live Updates:** When the packet is received, the ESP32 will instantly apply the variables to the `MachineConfig` struct, save them to NVS using `savePreferences()`, and snap the servos to their new "Closed" positions so you can immediately see the tuning results physically!
- **Remote Config Trigger:** Add logic to intercept a `TRIGGER_CONFIG` command. If received, the ESP32 will save a temporary `force_cfg=true` flag to NVS and reboot itself to spawn the Captive Portal.
