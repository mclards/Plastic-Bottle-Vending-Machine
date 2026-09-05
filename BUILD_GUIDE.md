# ECO-Fi OS Image Build Guide

This guide explains how to build, flash, and troubleshoot the custom ECO-Fi Orange Pi image.

## Overview
The ECO-Fi system operates on a custom, hardened version of the Orange Pi Linux distribution. We use a bash script to automatically mount a clean base image, strip out legacy dependencies, and inject our offline Python backend and captive portal software.

## How to Build the Image

If you make changes to the Python backend (`host/*.py`) or the web UI (`portal_template.html`), you must rebuild the image so those changes are bundled into a flashable `.img` file.

1. Open a WSL (Windows Subsystem for Linux) terminal or a native Linux terminal.
2. Navigate to the project root directory:
   ```bash
   cd /mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine
   ```
3. Run the automated build script as root:
   ```bash
   wsl --user root bash build_ecofi_img.sh
   ```
4. The script will output progress logs. It takes about 1-2 minutes to copy the base image and inject the new files.
5. Once successful, the new image will be output to the `resources/` folder (e.g., `resources/EcoFi_Opi_v1.7.img`).

---

### Step 3: Flash to MicroSD Card
1. Insert your MicroSD card (16GB or 32GB recommended) into your PC card reader.
2. Download and launch **BalenaEtcher** or **Raspberry Pi Imager**.
3. Open BalenaEtcher, select the newly built `EcoFi_Opi_v1.7.img` file.
4. Select your MicroSD card as the target.
5. Click **Flash!**

## Connecting the Hardware

Once the SD card is flashed and inserted into the Orange Pi:

1. **Power:** Provide a stable 5V / 3A power supply to the Orange Pi.
2. **WAN (Internet):** Plug an ethernet cable from your ISP router into the main, built-in Ethernet port (`eth0`). This provides internet access to the machine.
3. **LAN (Wi-Fi AP):** Plug your USB Wi-Fi adapter or USB-to-Ethernet adapter into the USB port. The system will automatically detect it and create the `10.0.0.1` Access Point network.
4. **ESP32 Connection:** Connect the ESP32 to the Orange Pi's GPIO Hardware UART pins (UART1):
   - ESP32 GND -> OPi Pin 6 (GND)
   - ESP32 TX  -> OPi Pin 10 (UART1 RX)
   - ESP32 RX  -> OPi Pin 8 (UART1 TX)

## Troubleshooting

- **502 Bad Gateway:** If you see this on `http://10.0.0.1`, the Nginx web server is running but the Python backend (`portal.py`) crashed. Ensure all `.py` dependencies were copied properly in the `build_ecofi_img.sh` script.
- **10.0.0.1 Not Loading / Routing Issues:** Make sure you wait for the USB Wi-Fi dongle to initialize on boot. If routing acts strange, verify that the USB adapter was plugged in *before* turning on the machine so the firewall configures `eth1` properly.
- **Unlicensed Vendo Message:** Ensure the active license Activation PIN in the admin dashboard matches the physical hardware (CPU Serial, SD CID, MAC Address). Licenses are strictly bound to the hardware and cannot be transferred by just copying the SD card.

