#!/usr/bin/env python3
"""
ECO-Fi Master Vendor License Generator (Builder Tool)
Run this script on your PC to generate machine-locked activation PINs for your clients.
"""

import sys
import os

# Import license engine logic
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
from license_manager import compute_activation_pin

def main():
    print("================================================================")
    print("   ECO-Fi VENDO - MASTER VENDOR LICENSE GENERATOR               ")
    print("   (Use this tool to issue activation keys for client machines) ")
    print("================================================================")
    print()

    if len(sys.argv) >= 2:
        hwid = sys.argv[1].strip().upper()
        tier = sys.argv[2].strip().upper() if len(sys.argv) >= 3 else "COMMERCIAL"
    else:
        hwid = input("Enter Client Machine Hardware ID (e.g. ECOFI-XXXX-XXXX-XXXX-XXXX): ").strip().upper()
        if not hwid:
            print("Error: Hardware ID is required.")
            return

        print("\nSelect License Tier:")
        print(" [1] COMMERCIAL (Standard 50-User Production Vendo)")
        print(" [2] THESIS_STUDENT (Educational / Research Edition)")
        print(" [3] ENTERPRISE_LGU (Barangay / Municipal High-Capacity)")
        choice = input("Choice [1-3] (Default 1): ").strip()
        
        tier_map = {
            "1": "COMMERCIAL",
            "2": "THESIS_STUDENT",
            "3": "ENTERPRISE_LGU"
        }
        tier = tier_map.get(choice, "COMMERCIAL")

    pin = compute_activation_pin(hwid, tier)

    print()
    print("----------------------------------------------------------------")
    print(f" TARGET HWID:       {hwid}")
    print(f" LICENSE TIER:      {tier}")
    print(f" ACTIVATION PIN:    {pin}")
    print("----------------------------------------------------------------")
    print(f" Send this 16-character PIN to your client to activate their machine.")
    print("================================================================")

if __name__ == "__main__":
    main()
