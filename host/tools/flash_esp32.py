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

def flash_firmware(firmware_path, port="/dev/ttyS1", baud=460800):
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
        dev = sys.argv[3] if len(sys.argv) > 3 else "/dev/ttyS1"
        ok = flash_firmware(fw, port=dev)
        sys.exit(0 if ok else 1)
    else:
        print("Usage: python3 flash_esp32.py [flash <path> [port]] | reset | bootloader")
        sys.exit(1)
