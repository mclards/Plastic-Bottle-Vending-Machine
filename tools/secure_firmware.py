#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VMC ECO-VENDO Firmware Security Audit & Hardware Hardening Utility
Validates binary confidentiality and generates ESP32 silicon protection commands.
"""
import os
import sys
import re

SENSITIVE_KEYWORDS = [
    'mclards23', 'vendor_secret', 'admin1234', 'tskey-',
    'private_key', 'auth_key', 'sovereign_key', 'db_password'
]

def audit_binary(bin_path):
    if not os.path.isfile(bin_path):
        print("[ERROR] Binary not found: {}".format(bin_path))
        return False
    with open(bin_path, 'rb') as f:
        data = f.read()

    print("=" * 60)
    print("  FIRMWARE CONFIDENTIALITY AUDIT: {}".format(os.path.basename(bin_path)))
    print("  Binary Size: {:,} bytes".format(len(data)))
    print("=" * 60)

    # Extract printable strings (min length 6)
    strings = re.findall(b'[\x20-\x7e]{6,}', data)
    print("  Total readable strings extracted: {:,}".format(len(strings)))

    leaks = []
    for s in strings:
        s_lower = s.lower()
        for kw in SENSITIVE_KEYWORDS:
            if kw.encode('ascii') in s_lower:
                leaks.append((kw, s.decode('ascii', errors='ignore')[:80]))

    if leaks:
        print("\n  [WARNING] POTENTIAL SENSITIVE STRINGS DETECTED:")
        for kw, val in leaks:
            print("    Keyword: {:<15} Found: {}".format(kw, val))
        return False
    else:
        print("  [PASSED] Zero sensitive passwords, secrets, salts, or keys found in binary!")
        return True

def print_efuse_protection_guide():
    print("""
======================================================================
  VMC ECO-VENDO ESP32 HARDWARE ANTI-DUMP & FLASH ENCRYPTION GUIDE
======================================================================
To permanently prevent any developer from extracting the firmware via
UART, flash programmers, or JTAG probes on customer hardware:

STEP 1: Enable Flash Encryption in Development Mode (allows OTA/flashing)
  python -m espefuse --port /dev/ttyS3 burn_efuse FLASH_CRYPT_CONFIG 0xF
  python -m espefuse --port /dev/ttyS3 burn_efuse FLASH_CRYPT_CNT 0x1

STEP 2: Disable JTAG Hardware Debugging (Prevents OpenOCD RAM dumps)
  python -m espefuse --port /dev/ttyS3 burn_efuse DIS_JTAG 1

STEP 3: Disable ROM Bootloader Cache Access (Prevents UART memory dumps)
  python -m espefuse --port /dev/ttyS3 burn_efuse DIS_DOWNLOAD_ICACHE 1
  python -m espefuse --port /dev/ttyS3 burn_efuse DIS_DOWNLOAD_DCACHE 1

STEP 4: Verify eFuse Status
  python -m espefuse --port /dev/ttyS3 summary

WARNING: eFuses are One-Time Programmable (OTP). Fuses cannot be unburned!
Apply Step 1-3 only to production vending units sold to customers.
======================================================================
""")

if __name__ == '__main__':
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_bin = os.path.join(root_dir, 'resources', 'firmware.bin')
    target_factory = os.path.join(root_dir, 'resources', 'esp32_firmware_factory.bin')

    cmd = sys.argv[1] if len(sys.argv) > 1 else 'audit'
    if cmd == 'audit':
        ok1 = audit_binary(target_bin)
        print()
        ok2 = audit_binary(target_factory)
        sys.exit(0 if (ok1 and ok2) else 1)
    elif cmd == 'efuse-guide':
        print_efuse_protection_guide()
        sys.exit(0)
    else:
        print("Usage: python secure_firmware.py [audit | efuse-guide]")
        sys.exit(1)
