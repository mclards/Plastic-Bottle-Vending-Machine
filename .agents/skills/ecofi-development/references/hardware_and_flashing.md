# VMC ECO-VENDO Hardware Architecture & ESP32 Flashing Guide
> **Hardware Stack:** Orange Pi One (Allwinner H3 Quad-Core Cortex-A7) + ESP32-WROOM-32 (Dual-Core 240MHz).

---

## 1. Pinout & Inter-Processor Communication

- **Physical UART Channel:** `/dev/ttyS3` (Orange Pi UART3).
  - Baud Rate: `115200` baud, 8-N-1.
  - Signal Levels: 3.3V TTL (TX/RX direct connection).
- **Hardware Reset & Bootloader GPIO Lines:**
  - **GPIO 1 (PA1):** Connected to ESP32 **EN** (Chip Enable / Hardware Reset).
  - **GPIO 0 (PA0):** Connected to ESP32 **IO0** (Boot Mode Strap).
- **Communication Protocol:** Line-delimited JSON packets transmitted every 100ms:
  - Telemetry: `{"event": "TELEMETRY", "cal_w": 42.5, "weight": 0.0, "ind": 0, "ir_ent": 1, "ir_drop": 0, ...}`
  - Commands: `{"cmd": "TARE"}`, `{"cmd": "RESET_COUNT"}`, `{"cmd": "SERVO_TEST", "ch": 0, "angle": 90}`, `{"cmd": "REBOOT"}`

---

## 2. In-System Hardware Firmware Flasher (`tools/flash_esp32.py`)

The Orange Pi can reflash the ESP32 in-situ without requiring external programmers or manual button pressing:

### Flashing Sequence
1. **Service Suspension:** `systemctl stop ecofi_portal.service` to release the exclusive file lock on `/dev/ttyS3`.
2. **Bootloader Sequencing:**
   - Drive `IO0` (GPIO 0) **LOW**.
   - Drive `EN` (GPIO 1) **LOW** for 100ms, then pull `EN` **HIGH**.
   - ESP32 enters ROM Serial Download Mode (`UART download mode`).
3. **Firmware Upload:**
   - Executes `esptool.py` via Python 3 on the Orange Pi:
     ```bash
     python3 -m esptool --chip esp32 --port /dev/ttyS3 --baud 115200 \
       --before no_reset --after no_reset write_flash -z \
       --flash_mode dio --flash_freq 40m --flash_size detect \
       0x0 /opt/ecofi/firmware/esp32_firmware.bin
     ```
4. **Application Reboot:**
   - Drive `IO0` **HIGH**.
   - Pulse `EN` **LOW** for 100ms, then pull `EN` **HIGH**.
   - ESP32 boots the freshly flashed application firmware.
5. **Service Restoration:** `systemctl start ecofi_portal.service`.

### Flasher CLI Usage
```bash
# Reflash firmware from host or OPi:
python3 /opt/ecofi/tools/flash_esp32.py flash /opt/ecofi/firmware/esp32_firmware.bin /dev/ttyS3

# Hard reset ESP32 via GPIO EN pin:
python3 /opt/ecofi/tools/flash_esp32.py reset
```

---

## 3. Firmware Binary Management
- **Repository Factory Image:** [`resources/esp32_firmware_factory.bin`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/resources/esp32_firmware_factory.bin).
- **Target Location on OPi:** `/opt/ecofi/firmware/esp32_firmware.bin`.
- When updating firmware, always verify the SHA-256 hash using `deploy_to_opi.py` step 4.
