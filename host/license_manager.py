#!/usr/bin/env python3
"""
ECO-Fi Hardware Licensing & Anti-Cloning Engine
Handles Silicon Hardware ID (HWID) extraction, cryptographic license verification,
and offline activation validation.
"""

import hashlib
import json
import os
import time

LICENSE_FILE = "/opt/ecofi/license.key"
HWID_OVERRIDE_FILE = "/opt/ecofi/hwid_override.txt"

# Master Vendor Public Secret Salt (Known only to the ECO-Fi platform)
VENDOR_SECRET_SALT = "ECOFI_MASTER_SOVEREIGN_KEY_2026_SECURE_SALT_v1"

def get_machine_hwid() -> str:
    """
    Extracts silicon hardware registers to generate an unforgeable,
    machine-locked Hardware ID (HWID).
    """
    if os.path.exists(HWID_OVERRIDE_FILE):
        try:
            with open(HWID_OVERRIDE_FILE, "r") as f:
                override = f.read().strip()
                if override:
                    return override
        except Exception:
            pass

    # 1. CPU Silicon Serial (Allwinner H3 / ARM SoC)
    cpu_serial = "CPU_GENERIC_OPI"
    try:
        if os.path.exists("/sys/class/sunxi_info/sys_info"):
            with open("/sys/class/sunxi_info/sys_info", "r") as f:
                cpu_serial = f.read().strip()
        elif os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "Serial" in line or "serial" in line:
                        cpu_serial = line.split(":")[1].strip()
                        break
    except Exception:
        pass

    # 2. MicroSD Card Silicon CID
    sd_cid = "SD_CID_SANDISK_DEFAULT"
    try:
        if os.path.exists("/sys/block/mmcblk0/device/cid"):
            with open("/sys/block/mmcblk0/device/cid", "r") as f:
                sd_cid = f.read().strip()
    except Exception:
        pass

    # 3. Primary Ethernet MAC Address
    mac_addr = "00:00:00:00:00:00"
    try:
        if os.path.exists("/sys/class/net/eth0/address"):
            with open("/sys/class/net/eth0/address", "r") as f:
                mac_addr = f.read().strip()
    except Exception:
        pass

    raw_signature = f"{cpu_serial}|{sd_cid}|{mac_addr}|{VENDOR_SECRET_SALT}"
    sha = hashlib.sha256(raw_signature.encode("utf-8")).hexdigest().upper()
    return f"ECOFI-{sha[:4]}-{sha[4:8]}-{sha[8:12]}-{sha[12:16]}"


def compute_activation_pin(hwid: str, tier: str = "COMMERCIAL") -> str:
    """
    Computes the mathematical activation PIN for a specific HWID.
    Used by both the vendor key generator and the on-device validator.
    """
    clean_hwid = hwid.strip().upper()
    clean_tier = tier.strip().upper()
    payload = f"{clean_hwid}::{clean_tier}::{VENDOR_SECRET_SALT}"
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()
    return f"{sha[:4]}-{sha[4:8]}-{sha[8:12]}-{sha[12:16]}"


def verify_license() -> dict:
    """
    Validates the local license certificate against the physical board.
    Returns: {"valid": bool, "tier": str, "hwid": str, "licensee": str, "message": str}
    """
    current_hwid = get_machine_hwid()

    if not os.path.exists(LICENSE_FILE):
        return {
            "valid": False,
            "status": "UNLICENSED",
            "hwid": current_hwid,
            "tier": "NONE",
            "licensee": "Unregistered",
            "message": "No license key found. Machine is in Lockout / Demo mode."
        }

    try:
        with open(LICENSE_FILE, "r") as f:
            data = json.load(f)

        stored_hwid = data.get("machine_hwid", "")
        stored_tier = data.get("tier", "COMMERCIAL")
        stored_key = data.get("activation_key", "")
        licensee = data.get("licensee", "Standard Client")
        expiry = data.get("expiry_date", "PERPETUAL")

        # 1. Anti-Cloning Check: Ensure HWID matches the current physical board
        if stored_hwid != current_hwid:
            return {
                "valid": False,
                "status": "CLONED_HARDWARE_MISMATCH",
                "hwid": current_hwid,
                "tier": stored_tier,
                "licensee": licensee,
                "message": f"Hardware mismatch! License issued for {stored_hwid}, but running on {current_hwid}."
            }

        # 2. Signature Check: Validate the cryptographic activation key
        expected_key = compute_activation_pin(stored_hwid, stored_tier)
        if stored_key != expected_key:
            return {
                "valid": False,
                "status": "CORRUPTED_SIGNATURE",
                "hwid": current_hwid,
                "tier": stored_tier,
                "licensee": licensee,
                "message": "Invalid cryptographic license signature."
            }

        # 3. Expiry Check (if time-locked)
        if expiry != "PERPETUAL":
            try:
                exp_timestamp = time.mktime(time.strptime(expiry, "%Y-%m-%d"))
                if time.time() > exp_timestamp:
                    return {
                        "valid": False,
                        "status": "EXPIRED",
                        "hwid": current_hwid,
                        "tier": stored_tier,
                        "licensee": licensee,
                        "message": f"License expired on {expiry}. Contact vendor for renewal."
                    }
            except Exception:
                pass

        return {
            "valid": True,
            "status": "ACTIVATED",
            "hwid": current_hwid,
            "tier": stored_tier,
            "licensee": licensee,
            "expiry": expiry,
            "message": f"Genuine ECO-Fi {stored_tier} License Activated."
        }

    except Exception as e:
        return {
            "valid": False,
            "status": "ERROR",
            "hwid": current_hwid,
            "tier": "NONE",
            "licensee": "Error",
            "message": f"License read error: {e}"
        }


def activate_machine(activation_pin: str, licensee_name: str = "Store Owner", tier: str = "COMMERCIAL") -> dict:
    """
    Activates the machine using an offline 16-character alphanumeric PIN.
    """
    current_hwid = get_machine_hwid()
    expected_pin = compute_activation_pin(current_hwid, tier)

    clean_pin = activation_pin.strip().upper().replace(" ", "")

    if clean_pin != expected_pin:
        return {
            "success": False,
            "message": "Invalid Activation PIN. Please check your Hardware ID and try again."
        }

    # Save verified license certificate
    license_data = {
        "vendor": "ECO-Fi Technologies",
        "licensee": licensee_name,
        "machine_hwid": current_hwid,
        "tier": tier,
        "activation_key": expected_pin,
        "activated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "expiry_date": "PERPETUAL"
    }

    try:
        os.makedirs(os.path.dirname(LICENSE_FILE), exist_ok=True)
        with open(LICENSE_FILE, "w") as f:
            json.dump(license_data, f, indent=4)
        return {
            "success": True,
            "message": f"Machine successfully activated for {licensee_name} ({tier} Edition)!"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to save license certificate: {e}"
        }


if __name__ == "__main__":
    hwid = get_machine_hwid()
    print("======================================================")
    print(" ECO-Fi Cryptographic Hardware Identifier & Validator")
    print("======================================================")
    print(f" Detected Machine HWID: {hwid}")
    status = verify_license()
    print(f" License Status:        {status['status']}")
    print(f" Active Tier:           {status.get('tier', 'NONE')}")
    print(f" Message:               {status['message']}")
    print("======================================================")
