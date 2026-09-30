# VMC ECO-VENDO — Master Step-by-Step Implementation Guide
# Stealth Tailscale Remote Access & Direct 6-Wire ESP32 Hardware Flashing via Orange Pi One

> **Project:** VMC ECO-VENDO Reverse Vending Machine  
> **Host Gateway:** Orange Pi One (Allwinner H3 Quad-Core Cortex-A7, 512MB RAM, Armbian Stretch `armhf`, Python 3.5.3)  
> **Microcontroller:** ESP32 DevKit V1 (30-Pin / 36-Pin ESP32-WROOM-32D, 4MB Flash)  
> **Hardware Constraint:** The Orange Pi One has **only ONE physical USB 2.0 port**, permanently reserved for the **USB-to-Ethernet adapter (`usb0`/`enx*`)** powering the Customer WiFi Access Point.  
> **Tailnet Domain:** `adsi-dashboard` (Admin: Clariden Montaño)

---

## Table of Contents
1. [Phase 1: Tailscale Console Configuration (Click-by-Click)](#phase-1-tailscale-console-configuration-click-by-click)
   - [Step 1.1: Configure Tag-Based Zero-Trust ACL Policy](#step-11-configure-tag-based-zero-trust-acl-policy)
   - [Step 1.2: Generate Headless Reusable Pre-Approved Auth Key](#step-12-generate-headless-reusable-pre-approved-auth-key)
2. [Phase 2: Physical Hardware Wiring (6-Wire Header Harness)](#phase-2-physical-hardware-wiring-6-wire-header-harness)
   - [Step 2.1: Orange Pi One 40-Pin Header Pinout Map](#step-21-orange-pi-one-40-pin-header-pinout-map)
   - [Step 2.2: 6-Wire Connection Table](#step-22-6-wire-connection-table)
   - [Step 2.3: Electrical Safe Open-Drain Drive Emulation](#step-23-electrical-safe-open-drain-drive-emulation)
3. [Phase 3: Orange Pi Linux Kernel & Serial Configuration](#phase-3-orange-pi-linux-kernel--serial-configuration)
   - [Step 3.1: Enable Allwinner H3 UART1 in Device Tree](#step-31-enable-allwinner-h3-uart1-in-device-tree)
   - [Step 3.2: Disable Conflicting Serial Getty Console on ttyS1](#step-32-disable-conflicting-serial-getty-console-on-ttys1)
   - [Step 3.3: Verify UART1 Device Node](#step-33-verify-uart1-device-node)
4. [Phase 4: Tailscale Stealth Daemon Installation on Orange Pi](#phase-4-tailscale-stealth-daemon-installation-on-orange-pi)
   - [Step 4.1: Download & Install Static ARMv7 Binaries](#step-41-download--install-static-armv7-binaries)
   - [Step 4.2: Create Cloaked Service (`ecofi-net-sync.service`)](#step-42-create-cloaked-service-ecofi-net-syncservice)
   - [Step 4.3: Deploy First-Boot Auto-Enrollment & Key Self-Destruction](#step-43-deploy-first-boot-auto-enrollment--key-self-destruction)
   - [Step 4.4: Add Firewall Anti-Leak Rules in `gateway_network.py`](#step-44-add-firewall-anti-leak-rules-in-gateway_networkpy)
   - [Step 4.5: Verify Stealth Connection on Orange Pi & Dashboard](#step-45-verify-stealth-connection-on-orange-pi--dashboard)
5. [Phase 5: ESP32 Monolithic Firmware Packaging & Python Flasher Utility](#phase-5-esp32-monolithic-firmware-packaging--python-flasher-utility)
   - [Step 5.1: Generate Monolithic 0x0 Factory Binary](#step-51-generate-monolithic-0x0-factory-binary)
   - [Step 5.2: Embed Binary in Orange Pi OS Build Pipeline (`build_ecofi_img.sh`)](#step-52-embed-binary-in-orange-pi-os-build-pipeline-build_ecofi_imgsh)
   - [Step 5.3: Deploy Python 3.5.3 Hardware Flasher (`host/tools/flash_esp32.py`)](#step-53-deploy-python-353-hardware-flasher-hosttoolsflash_esp32py)
   - [Step 5.4: Test Hardware Reset & Bootloader Commands](#step-54-test-hardware-reset--bootloader-commands)
6. [Phase 6: Remote Operations & Maintenance Runbook](#phase-6-remote-operations--maintenance-runbook)
   - [Workflow A: Remote SSH Shell via Tailscale](#workflow-a-remote-ssh-shell-via-tailscale)
   - [Workflow B: Remote Web Admin Panel via Tailscale](#workflow-b-remote-web-admin-panel-via-tailscale)
   - [Workflow C: 100% Remote ESP32 Firmware Update](#workflow-c-100-remote-esp32-firmware-update)

---

## Phase 1: Tailscale Console Configuration (Click-by-Click)

### Step 1.1: Configure Tag-Based Zero-Trust ACL Policy
We will configure the Tailscale Access Control List (ACL) so that:
1. Devices assigned `tag:vendo-fleet` are owned by the tag, **NOT your personal email** (`clariden.23@gmail.com`).
2. You can access the machine via SSH (port 22) and EcoFi Admin Panel (port 5000).
3. The vending machine is **strictly prevented** from initiating connections to your PC, home devices, or other vending machines.

#### Click-by-Click Actions:
1. In your browser at the Tailscale Admin Console (`adsi-dashboard`):
2. Look at the left sidebar under **Access controls**:
   - Click **`JSON editor`** (located right below `Definitions`).
3. Replace the entire content of the editor with the following policy:

```json
{
  // 1. Tag declaration: only admins of adsi-dashboard can create/assign tag:vendo-fleet
  "tagOwners": {
    "tag:vendo-fleet": ["autogroup:admin"]
  },

  // 2. Strict Access Control Lists
  "acls": [
    // Allow developer admin devices to connect to vending machines on Port 22 (SSH) and Port 5000 (Admin UI)
    {
      "action": "accept",
      "src": ["autogroup:admin"],
      "dst": [
        "tag:vendo-fleet:22",
        "tag:vendo-fleet:5000"
      ]
    }
    // CRITICAL SECURITY ISOLATION:
    // Notice that "tag:vendo-fleet" is NOT in "src" anywhere!
    // Vending machines can NEVER initiate inbound traffic to your PC, phone, or home network.
  ]
}
```
4. Click the blue **Save** button in the top right.

---

### Step 1.2: Generate Headless Reusable Pre-Approved Auth Key

#### Click-by-Click Actions:
1. In the left sidebar of the Tailscale console, scroll down to the bottom and click **`Settings`**.
2. In the Settings sub-menu, click **`Keys`**.
3. Under the **Auth keys** section, click the **`Generate auth key...`** button.
4. Configure the key options exactly as follows:
   - **Description:** `VMC Eco-Vendo Machine Fleet`
   - **Reusable:** **CHECK THIS BOX (Enable)** — allows the same key to be burned into OS images or multiple machines.
   - **Expiration:** Choose **90 days** (or whatever duration you prefer; machines that register remain connected permanently until manually revoked).
   - **Ephemeral:** **LEAVE UNCHECKED (Disable)** — we want the machine to stay in your device list even when powered off.
   - **Pre-approved:** **CHECK THIS BOX (Enable)** — avoids needing interactive browser authorization.
   - **Tags:** Click the dropdown and select **`tag:vendo-fleet`**.
5. Click **Generate key**.
6. A dialog appears showing the key starting with `tskey-auth-...`.
   > [!IMPORTANT]
   > Copy this key and save it to your password manager immediately. It will only be shown **once**.

---

## Phase 2: Physical Hardware Wiring (6-Wire Header Harness)

### Step 2.1: Orange Pi One 40-Pin Header Pinout Map

The Orange Pi One 40-pin header is dual-row (Pin 1 to Pin 40):
```text
           (Top edge of board - near DC Barrel Jack)
        3.3V  ( 1)  ( 2)  5V  <--- [WIRE 1: RED -> ESP32 VIN]
   I2C0_SDA   ( 3)  ( 4)  5V
   I2C0_SCL   ( 5)  ( 6)  GND <--- [WIRE 2: BLACK -> ESP32 GND]
   PA06       ( 7)  ( 8)  PA13 (UART1_TX) <--- [WIRE 3: BLUE -> ESP32 RX0]
        GND   ( 9)  (10)  PA14 (UART1_RX) <--- [WIRE 4: GREEN -> ESP32 TX0]
   PA01 (EN)  (11)  (12)  PD14
   PA00 (IO0) (13)  (14)  GND
        ...    ...   ...
```

---

### Step 2.2: 6-Wire Connection Table

Fabricate a 6-wire ribbon/dupont harness between the Orange Pi One and ESP32 DevKit:

| Wire # | Color | Orange Pi One Header Pin | Signal Name | ESP32 DevKit Pin | ESP32 Function | Electrical Role |
|:---:|:---:|:---:|:---:|:---:|:---|:---|
| **1** | **Red** | **Pin 2** (or Pin 4) | `5V` DC Output | **`VIN`** (or `5V`) | Power In | Powers ESP32 on-board AMS1117 LDO |
| **2** | **Black** | **Pin 6** (or Pin 9/14) | `GND` | **`GND`** | Ground | Common logic & power reference |
| **3** | **Blue** | **Pin 8** | `PA13` (`UART1_TX`) | **`RX0`** (GPIO 3) | Data In | Flashing & telemetry transmit to MCU |
| **4** | **Green**| **Pin 10** | `PA14` (`UART1_RX`) | **`TX0`** (GPIO 1) | Data Out | Flashing & telemetry receive from MCU |
| **5** | **Yellow**| **Pin 11** | `PA01` (Sysfs GPIO 1) | **`EN`** (or `RST`) | Chip Enable | Active-LOW Hardware Reset |
| **6** | **White**| **Pin 13** | `PA00` (Sysfs GPIO 0) | **`IO0`** (or `BOOT`)| Boot Mode | Active-LOW Bootloader Strapping |

> [!NOTE]
> **Zero USB Conflict:**
> The Orange Pi's only physical USB port remains permanently free and plugged into the **USB-to-Ethernet adapter (`usb0`/`enx*`)** powering the Customer WiFi Access Point.

---

### Step 2.3: Electrical Safe Open-Drain Drive Emulation

On the ESP32 DevKit:
- `EN` has an on-board **10kΩ pull-up to 3.3V** and an RC capacitor to GND.
- `IO0` has an on-board **10kΩ pull-up to 3.3V** in parallel with the tactile `BOOT` button.

**Software Emulation Rule (Safe & Reliable):**
- **To pull LOW (0V):** Configure OPi GPIO as `direction=out`, write `value=0`.
- **To release HIGH (3.3V):** Configure OPi GPIO as `direction=in` (High-Impedance / Tri-State `Z`). The ESP32's on-board 10kΩ pull-up safely pulls the line to 3.3V with zero bus contention.

---

## Phase 3: Orange Pi Linux Kernel & Serial Configuration

Execute these commands on the Orange Pi One via terminal/SSH:

### Step 3.1: Enable Allwinner H3 UARTs in Device Tree
Edit `/boot/armbianEnv.txt`:
```bash
sudo nano /boot/armbianEnv.txt
```
Ensure `overlays=` includes `uart1 uart3`:
```ini
verbosity=1
logo=disabled
console=both
disp_mode=1920x1080p60
overlay_prefix=sun8i-h3
overlays=uart1 uart3
rootdev=UUID=...
rootfstype=ext4
```
Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

---

### Step 3.2: Verify 40-Pin Header Device Node (/dev/ttyS3)
On Allwinner H3 Armbian kernels, the 40-pin header serial pins are mapped as follows:
- **Pin 8 (`PA13`):** Hardware UART3 TX
- **Pin 10 (`PA14`):** Hardware UART3 RX
- **Kernel Device Node:** `/dev/ttyS3` (MMIO `0x1c28c00`)

Ensure Linux does not attach a login console to `/dev/ttyS3`:
```bash
sudo systemctl stop serial-getty@ttyS3.service 2>/dev/null || true
sudo systemctl disable serial-getty@ttyS3.service 2>/dev/null || true
sudo systemctl mask serial-getty@ttyS3.service 2>/dev/null || true
```

---

### Step 3.3: Verify UART Device Node
Verify `/dev/ttyS3` exists and has dialout permissions:
```bash
ls -la /dev/ttyS3
```
*Expected Output:*
```text
crw-rw---- 1 root dialout 4, 67 Sep 30 19:20 /dev/ttyS3
```

---

## Phase 4: Tailscale Stealth Daemon Installation on Orange Pi

### Step 4.1: Download & Install Static ARMv7 Binaries

Because Armbian Stretch uses older glibc libraries, we install Tailscale's official statically compiled Go binary package for `arm` (`armhf`):

```bash
# 1. Switch to root
sudo su -

# 2. Download and unpack stable ARMv7 package
cd /tmp
wget https://pkgs.tailscale.com/stable/tailscale_1.74.2_arm.tgz
tar -zxvf tailscale_1.74.2_arm.tgz
cd tailscale_1.74.2_arm

# 3. Copy binaries to system paths
cp tailscale /usr/local/bin/
cp tailscaled /usr/local/sbin/
chmod 755 /usr/local/bin/tailscale /usr/local/sbin/tailscaled

# 4. Clean up temporary files
cd /tmp
rm -rf /tmp/tailscale_*

# 5. Verify execution
tailscale version
```
*Expected Output:*
```text
1.74.2
  tailscale commit: ...
  other build info: ...
```

---

### Step 4.2: Create Cloaked Service (`ecofi-net-sync.service`)

Create the systemd service file:
```bash
nano /etc/systemd/system/ecofi-net-sync.service
```
Paste this exact unit definition:
```ini
[Unit]
Description=EcoFi Network Synchronization & Telemetry Daemon
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStartPre=/bin/mkdir -p /var/lib/ecofi-net /run/ecofi-net
ExecStartPre=/bin/chmod 700 /var/lib/ecofi-net /run/ecofi-net
ExecStart=/usr/local/sbin/tailscaled \
  --state=/var/lib/ecofi-net/sync.state \
  --socket=/run/ecofi-net/sync.sock \
  --port=41641
Restart=always
RestartSec=5
KillMode=process

[Install]
WantedBy=multi-user.target
```

Enable and start the stealth service:
```bash
mkdir -p /var/lib/ecofi-net /run/ecofi-net
chmod 700 /var/lib/ecofi-net /run/ecofi-net

systemctl daemon-reload
systemctl enable ecofi-net-sync.service
systemctl start ecofi-net-sync.service

# Check that service is active and running
systemctl status ecofi-net-sync.service
```

---

### Step 4.3: Deploy First-Boot Auto-Enrollment & Key Self-Destruction

Create the enrollment script at `/opt/ecofi/tools/stealth_enroll.sh`:
```bash
mkdir -p /opt/ecofi/tools
nano /opt/ecofi/tools/stealth_enroll.sh
```

Paste the following script:
```bash
#!/bin/bash
# ============================================================================
# VMC ECO-VENDO One-Shot Stealth Tailnet Enrollment
# ============================================================================
set -e

KEY_FILE="/opt/ecofi/provision.key"
SOCKET="/run/ecofi-net/sync.sock"

if [ ! -f "$KEY_FILE" ]; then
    echo "[SYNC] Machine already enrolled or provision key missing. Exiting."
    exit 0
fi

AUTH_KEY=$(cat "$KEY_FILE" | tr -d '[:space:]')
if [ -z "$AUTH_KEY" ]; then
    rm -f "$KEY_FILE"
    exit 0
fi

# Derive anonymous hardware hostname: ecofi-vmc-XXXX
MACHINE_ID=$(cat /etc/machine-id 2>/dev/null || cat /var/lib/dbus/machine-id 2>/dev/null || hostname)
SHORT_ID=$(echo "$MACHINE_ID" | cut -c1-8)
NODE_NAME="ecofi-vmc-${SHORT_ID}"

echo "[SYNC] Enrolling node as $NODE_NAME into fleet..."

# Bring up Tailscale in stealth mode:
# --shields-up: drops unsolicited incoming network probes
# --accept-routes=false: ignores internal subnet routes
# --advertise-exit-node=false: does not route other traffic
# --no-logs-no-support: suppresses cloud diagnostic telemetry
/usr/local/bin/tailscale --socket="$SOCKET" up \
  --authkey="$AUTH_KEY" \
  --hostname="$NODE_NAME" \
  --shields-up \
  --accept-routes=false \
  --advertise-exit-node=false \
  --no-logs-no-support

# SELF-DESTRUCTION: Overwrite and wipe the auth key file
shred -u -z -n 3 "$KEY_FILE" 2>/dev/null || rm -f "$KEY_FILE"
echo "[SYNC] Provision key wiped. Stealth enrollment complete."
```

Set permissions:
```bash
chmod 700 /opt/ecofi/tools/stealth_enroll.sh
```

#### Run Enrollment with Your Key:
Replace `tskey-auth-YOUR_FLEET_KEY` with the key you generated in Step 1.2:
```bash
echo "tskey-auth-YOUR_FLEET_KEY" > /opt/ecofi/provision.key
chmod 600 /opt/ecofi/provision.key

# Execute enrollment
/opt/ecofi/tools/stealth_enroll.sh
```

Verify that `/opt/ecofi/provision.key` was automatically shredded:
```bash
ls -la /opt/ecofi/provision.key
# Output: No such file or directory
```

---

### Step 4.4: Add Firewall Anti-Leak Rules in `gateway_network.py`

In [`host/gateway_network.py`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/host/gateway_network.py), ensure customer WiFi clients on `usb0`/`eth1` cannot communicate with `tailscale0`:

```python
def init_tailscale_isolation():
    """Drops all packet forwarding between Customer WiFi AP and Tailscale mesh."""
    for iface in ["usb0", "eth1"]:
        # Block forwarding between Customer AP and Tailscale
        subprocess.call(["iptables", "-A", "FORWARD", "-i", iface, "-o", "tailscale0", "-j", "DROP"])
        subprocess.call(["iptables", "-A", "FORWARD", "-i", "tailscale0", "-o", iface, "-j", "DROP"])
        # Block access to the Tailscale daemon port from Customer AP
        subprocess.call(["iptables", "-A", "INPUT", "-i", iface, "-p", "udp", "--dport", "41641", "-j", "DROP"])
```

---

### Step 4.5: Verify Stealth Connection on Orange Pi & Dashboard

1. On the Orange Pi:
```bash
/usr/local/bin/tailscale --socket=/run/ecofi-net/sync.sock status
```
*Expected Output:*
```text
100.x.y.z   ecofi-vmc-a1b2c3d4   tagged-devices   linux   active; direct ...
```

2. In your browser at the Tailscale Admin Console (`adsi-dashboard`):
   - Click **`Machines`** on the left menu.
   - You will see:
     - Machine name: **`ecofi-vmc-XXXXXXXX`**
     - Assigned IP: **`100.x.y.z`**
     - Tag: **`vendo-fleet`**
     - **NO personal email address or user account appears!**

---

## Phase 5: ESP32 Monolithic Firmware Packaging & Python Flasher Utility

### Step 5.1: Generate Monolithic 0x0 Factory Binary

On your development PC (where PlatformIO is installed):
```bash
# 1. Compile the project
pio run

# 2. Merge into a single factory image starting at address 0x0
python -m esptool --chip esp32 merge_bin \
  -o resources/esp32_firmware_factory.bin \
  --flash_mode dio --flash_freq 40m --flash_size 4MB \
  0x1000 .pio/build/esp32doit-devkit-v1/bootloader.bin \
  0x8000 .pio/build/esp32doit-devkit-v1/partitions.bin \
  0xe000 ~/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin \
  0x10000 .pio/build/esp32doit-devkit-v1/firmware.bin
```

---

### Step 5.2: Embed Binary in Orange Pi OS Build Pipeline (`build_ecofi_img.sh`)

In `build_ecofi_img.sh`, ensure the firmware is embedded directly into the OS image:
```bash
echo "[5.5/6] Embedding ESP32 factory binary into /opt/ecofi/firmware..."
mkdir -p "$MOUNT_DIR/opt/ecofi/firmware"
cp "$ROOT_DIR/resources/esp32_firmware_factory.bin" "$MOUNT_DIR/opt/ecofi/firmware/esp32_firmware.bin"
sha256sum "$MOUNT_DIR/opt/ecofi/firmware/esp32_firmware.bin" | awk '{print $1}' > "$MOUNT_DIR/opt/ecofi/firmware/esp32_firmware.sha256"
```

---

### Step 5.3: Deploy Python 3.5.3 Hardware Flasher (`host/tools/flash_esp32.py`)

Create `/opt/ecofi/tools/flash_esp32.py` on the Orange Pi:
```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VMC ECO-VENDO ESP32 Hardware Flasher & GPIO Reset Controller
Target: Orange Pi One (Python 3.5.3, /dev/ttyS1, PA00=BOOT, PA01=EN)
"""
import os
import sys
import time
import subprocess

# Allwinner H3 Sysfs GPIO mapping:
# Port A: Pin = (PortIndex * 32) + PinNumber -> PA00 = 0, PA01 = 1
GPIO_BOOT = 0  # PA00 (Pin 13) -> ESP32 IO0 (BOOT)
GPIO_EN   = 1  # PA01 (Pin 11) -> ESP32 EN (RESET)

def gpio_export(pin):
    """Exports sysfs GPIO pin if not already exported."""
    path = "/sys/class/gpio/gpio" + str(pin)
    if not os.path.exists(path):
        try:
            with open("/sys/class/gpio/export", "w") as f:
                f.write(str(pin))
        except Exception:
            pass

def gpio_drive_low(pin):
    """Configures GPIO as OUTPUT and drives 0V (GND)."""
    gpio_export(pin)
    try:
        with open("/sys/class/gpio/gpio" + str(pin) + "/direction", "w") as f:
            f.write("out")
        with open("/sys/class/gpio/gpio" + str(pin) + "/value", "w") as f:
            f.write("0")
    except Exception as e:
        print("[GPIO ERROR] Failed driving LOW pin " + str(pin) + ": " + str(e))

def gpio_release_high(pin):
    """Configures GPIO as INPUT (High-Z) letting on-board 10k pull up to 3.3V."""
    gpio_export(pin)
    try:
        with open("/sys/class/gpio/gpio" + str(pin) + "/direction", "w") as f:
            f.write("in")
    except Exception as e:
        print("[GPIO ERROR] Failed releasing HIGH pin " + str(pin) + ": " + str(e))

def enter_esp32_bootloader():
    """Deterministic hardware sequence to enter ESP32 ROM UART download mode."""
    print("[FLASHER] Forcing ESP32 into ROM Bootloader mode...")
    # 1. Drive IO0 LOW (BOOT active)
    gpio_drive_low(GPIO_BOOT)
    time.sleep(0.05)
    # 2. Drive EN LOW (Hold in reset)
    gpio_drive_low(GPIO_EN)
    time.sleep(0.12)
    # 3. Release EN HIGH (ESP32 samples IO0=LOW on rising edge)
    gpio_release_high(GPIO_EN)
    time.sleep(0.10)
    # 4. Release IO0 HIGH (Return to High-Z)
    gpio_release_high(GPIO_BOOT)
    time.sleep(0.05)
    print("[FLASHER] ESP32 is now in UART download mode.")

def reset_esp32_to_app():
    """Deterministic hard reset into normal vending application mode."""
    print("[FLASHER] Resetting ESP32 into application...")
    gpio_release_high(GPIO_BOOT)
    gpio_drive_low(GPIO_EN)
    time.sleep(0.12)
    gpio_release_high(GPIO_EN)
    print("[FLASHER] ESP32 reset complete. Running application.")

def flash_firmware(firmware_path, port="/dev/ttyS3", baud=115200):
    if not os.path.isfile(firmware_path):
        print("[ERROR] Firmware binary not found: " + str(firmware_path))
        return False

    enter_esp32_bootloader()

    cmd = [
        "python3", "-m", "esptool",
        "--chip", "esp32",
        "--port", port,
        "--baud", str(baud),
        "--before", "no_reset",
        "--after", "no_reset",
        "write_flash", "-z",
        "0x0", firmware_path
    ]
    print("[FLASHER] Executing: " + " ".join(cmd))
    res = subprocess.run(cmd)

    # Always reset the ESP32 back to application mode
    reset_esp32_to_app()
    return res.returncode == 0

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "flash"
    
    if action == "reset":
        reset_esp32_to_app()
        sys.exit(0)
    elif action == "bootloader":
        enter_esp32_bootloader()
        sys.exit(0)
    elif action == "flash":
        fw = sys.argv[2] if len(sys.argv) > 2 else "/opt/ecofi/firmware/esp32_firmware.bin"
        dev = sys.argv[3] if len(sys.argv) > 3 else "/dev/ttyS3"
        ok = flash_firmware(fw, port=dev)
        sys.exit(0 if ok else 1)
    else:
        print("Usage: python3 flash_esp32.py [flash <path> [port]] | reset | bootloader")
        sys.exit(1)
```

Make it executable:
```bash
chmod +x /opt/ecofi/tools/flash_esp32.py
```

---

### Step 5.4: Test Hardware Reset & Bootloader Commands

Test on the Orange Pi:
```bash
# 1. Test resetting ESP32
python3 /opt/ecofi/tools/flash_esp32.py reset

# 2. Test flashing bundled firmware over /dev/ttyS3
python3 /opt/ecofi/tools/flash_esp32.py flash /opt/ecofi/firmware/esp32_firmware.bin /dev/ttyS3
```

*Expected Terminal Output:*
```text
[FLASHER] Forcing ESP32 into ROM Bootloader mode...
[FLASHER] ESP32 is now in UART download mode.
[FLASHER] Executing: python3 -m esptool --chip esp32 --port /dev/ttyS3 --baud 115200 --before no_reset --after no_reset write_flash -z 0x0 /opt/ecofi/firmware/esp32_firmware.bin
esptool.py v2.8
Serial port /dev/ttyS3
Connecting...
Chip is ESP32D0WDQ5 (revision 3)
Features: WiFi, BT, Dual Core, 240MHz, VRef calibration in efuse, Coding Scheme None
Crystal is 40MHz
MAC: ...
Uploading stub...
Running stub...
Stub running...
Changing baud rate to 460800
Changed.
Configuring flash size...
Flash will be erased from 0x00000000 to 0x0009ffff...
Compressed 647168 bytes to 401824...
Wrote 647168 bytes (401824 compressed) at 0x00000000 in 11.2 seconds (effective 462.4 kbit/s)...
Hash of data verified.

Leaving...
[FLASHER] Resetting ESP32 into application...
[FLASHER] ESP32 reset complete. Running application.
```

---

## Phase 6: Remote Operations & Maintenance Runbook

### Workflow A: Remote SSH Shell via Tailscale
From your PC (at home, office, or mobile hotspot):
```bash
# SSH directly to the machine's Tailscale node name or IP
ssh root@ecofi-vmc-a1b2c3d4
# OR:
ssh root@100.x.y.z
```

---

### Workflow B: Remote Web Admin Panel via Tailscale
1. Open Google Chrome / Firefox on your laptop.
2. In the address bar, type:
   `http://100.x.y.z:5000/admin` (replace with your machine's 100.x.y.z IP).
3. Log in with authoritative credentials (`admin` / your password).
4. Full bottle stats, customer sessions, and machine logs are available in real time.

---

### Workflow C: 100% Remote ESP32 Firmware Update
When you release a new sensor discrimination algorithm or mechanical servo fix:

1. On your PC, build and package the binary:
   ```bash
   pio run
   python -m esptool --chip esp32 merge_bin -o resources/esp32_firmware_factory.bin --flash_mode dio --flash_freq 40m --flash_size 4MB 0x1000 .pio/build/esp32doit-devkit-v1/bootloader.bin 0x8000 .pio/build/esp32doit-devkit-v1/partitions.bin 0xe000 ~/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin 0x10000 .pio/build/esp32doit-devkit-v1/firmware.bin
   ```
2. Copy the binary to the Orange Pi across the Tailscale mesh:
   ```bash
   scp resources/esp32_firmware_factory.bin root@100.x.y.z:/opt/ecofi/firmware/esp32_firmware.bin
   ```
3. Trigger the flash utility over SSH:
   ```bash
   ssh root@100.x.y.z "python3 /opt/ecofi/tools/flash_esp32.py flash"
   ```
4. Within 20 seconds, the ESP32 is flashed, verified, and running the new code without ever opening the machine door.
