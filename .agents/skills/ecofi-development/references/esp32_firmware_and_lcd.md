# ESP32 Firmware & 20x4 LCD Display Engineering Reference
> **Domain:** Dual-Core FreeRTOS Firmware, HD44780 20x4 I2C LCD Pipeline, Buffer Invalidation, Dynamic Multi-Rate Cycling, Scale & Sensor Calibrations.

---

## 1. 20x4 I2C LCD Display Pipeline & Buffer Management

The machine uses an HD44780-compatible 20-column by 4-row LCD connected via PCF8574 I2C backpack (`0x27`) running at 100 kHz.

### A. The Buffer Match Pitfall & Resolution
- **Internal Buffer:** `char currentLcdLines[4][21];` caches the currently displayed text to eliminate I2C bus traffic when characters haven't changed.
- **The Bug:** If `currentLcdLines` is pre-populated with default strings in global memory:
  ```cpp
  // INCORRECT (Caused missing header on boot):
  char currentLcdLines[4][21] = {
      "  SMART ECO-VENDO   ",
      " BOTTLE FOR WIFI    ",
      "                    ",
      "                    "
  };
  ```
  On cold boot, the physical LCD screen is blank. When `updateLcdRowInternal(0, "  SMART ECO-VENDO   ")` ran, `strncmp(currentLcdLines[0], newText, 20)` evaluated to `0` (match), causing the driver to **skip sending I2C commands**. Rows 1 and 2 were never drawn, leaving the top half of the screen blank!
- **Authoritative Fix:**
  1. Initialize `currentLcdLines` empty: `char currentLcdLines[4][21] = { "", "", "", "" };`
  2. In `setup()`: `memset(currentLcdLines, 0, sizeof(currentLcdLines));`
  3. Provide `invalidateLcdBuffer()`:
     ```cpp
     void invalidateLcdBuffer() {
         memset(currentLcdLines, 0, sizeof(currentLcdLines));
     }
     ```
     Call `invalidateLcdBuffer()` whenever `lcd.clear()` or state transitions occur.

---

## 2. Dynamic Multi-Rate LCD Cycling Architecture

### Display Layout (4 Rows x 20 Columns)
```
┌────────────────────┐
│  SMART ECO-VENDO   │ Row 1 (Fixed Header)
│ BOTTLE FOR WIFI    │ Row 2 (Fixed Subtitle)
│ 1 BOTTLE = 10 MINS │ Row 3 (Cycling Dynamic Tier A)
│ 2 BOTTLE = 25 MINS │ Row 4 (Cycling Dynamic Tier B)
└────────────────────┘
```

### Rate Synchronization Protocol
1. **Host Backend (`host/portal.py`):**
   - Reads active rates from the database.
   - Formats a compact serial command: `{"cmd": "SET_RATES", "rates": [{"b": 1, "m": 10}, {"b": 2, "m": 25}, ...]}`.
   - Synchronizes on boot, upon admin rate changes, and periodically every 60 seconds.
2. **ESP32 Firmware (`src/main.cpp`):**
   - Parses the JSON payload into `LcdPromoRate dynamicRates[MAX_LCD_RATES]`.
   - Organizes rates into 2-tier pages:
     - Page 0: Tier 0 (Row 3), Tier 1 (Row 4)
     - Page 1: Tier 2 (Row 3), Tier 3 (Row 4)
   - Rotates pages every **3500ms** (`RATE_CYCLE_INTERVAL_MS = 3500`).
   - If only 1 rate exists, Page 0 displays it on Row 3 and clears Row 4 without cycling.

---

## 3. Sensor Fusion & Calibration Philosophy

### A. HX711 Load Cell & Weight Sensor
- **Calibration Factor:** Default `260.0`.
- **Target Weight Window:** `[20.0g - 90.0g]`.
- **Engineering Reality & Rationale:**
  - The mechanical intake chute does not suspend the full body of the bottle freely during weight measurement.
  - Therefore, the HX711 calibration factor is tuned empirically for **operational reliability across bottle forms** (water bottles, soft drink bottles, tea bottles) rather than strict laboratory gravimetric weight.
  - Scale and NIR together make the final determination:
    - **Scale/Weight:** Detects the presence of an object and distinguishes empty plastic bottles from heavy glass bottles (>150g) or massive foreign objects.
    - **NIR (AS7263):** Confirms polymer wall optical signature and rejects colored glass (<22 uW/cm²) or paper (>230 uW/cm²).
    - **Inductive Sensor (LJ12A3):** Rejects metallic cans and metal-capped bottles.

### B. Golden Baseline Calibration Defaults (v2.3.18)
| Parameter | Authoritative Value | Description |
| :--- | :--- | :--- |
| `NIR Cal-W Window` | `[10.0 - 120.0]` uW/cm² | Polymeric reflection band |
| `Weight Window` | `[20.0 - 90.0]` g | Reliable bottle intake weight |
| `HX711 Cal Factor` | `260.0` | Empirical scale factor |
| `Gate Open Timeout` | `65.0` s | Intake chute active window |
| `Weight Settle Delay` | `1000` ms | Scale stabilization time |
| `Drop Detection Window`| `3100` ms | Transit time through internal chute |
| `Retrieval Timeout` | `50.0` s | User collection window for invalid items |
| `Bin Full Distance` | `<= 18` cm for 3s | Ultrasonic waste bin saturation |
| `Servo Angles` | `0°` (Closed), `90°` (Open) | Entrance & exit chute servos |

---

## 4. Build & Flashing Workflow

1. **Compilation via PlatformIO:**
   ```bash
   pio run
   ```
2. **Artifact Synchronization:**
   - Copy `.pio/build/esp32dev/firmware.bin` to:
     - `resources/firmware.bin`
     - `resources/esp32_firmware_factory.bin`
3. **Flashing to Live Orange Pi:**
   ```bash
   python tools/deploy_to_opi.py
   ```
   Uses GPIO 0 (`PA0`) and GPIO 1 (`PA1`) for zero-touch hardware bootloader entry over `/dev/ttyS3`.
4. **Smoke Testing:**
   ```bash
   python tools/smoke_test.py
   ```
   Runs 4 automated hardware tests (serial ping, telemetry health, tare command, servo movement).
