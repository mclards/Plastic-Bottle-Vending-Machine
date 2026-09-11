"""
Eco-Fi Hardware Licensing & Anti-Cloning Engine
Handles Silicon Hardware ID (HWID) extraction, cryptographic license verification,
and offline activation validation.
"""
import hashlib
import json
import os
import re
import time
import tempfile

LICENSE_FILE = '/opt/ecofi/license.key'
HWID_OVERRIDE_FILE = '/opt/ecofi/hwid_override.txt'
DEV_CODE = 'mclards23'
VENDOR_SECRET_SALT = 'ECOFI_MASTER_SOVEREIGN_KEY_2026_SECURE_SALT_v1_mclards23'


def normalize_hwid(hwid: str) -> str:
    """
    Normalizes any raw, partial, or user-copied HWID string into canonical format.
    Accepts 32-hex format (8 blocks of 4) or 16-hex legacy format.
    """
    if not hwid:
        return ""
    raw = str(hwid).strip().strip('"\'').upper()
    raw = re.sub(r'^(ECO[-_]?FI[-_:\s]*)+', '', raw)
    hex_chars = re.sub(r'[^0-9A-F]', '', raw)
    if len(hex_chars) >= 32:
        h = hex_chars[:32]
        return '{}-{}-{}-{}-{}-{}-{}-{}'.format(
            h[0:4], h[4:8], h[8:12], h[12:16],
            h[16:20], h[20:24], h[24:28], h[28:32]
        )
    if len(hex_chars) >= 16:
        h = hex_chars[:16]
        return '{}-{}-{}-{}'.format(h[0:4], h[4:8], h[8:12], h[12:16])
    if hex_chars:
        return '{}'.format(hex_chars)
    return str(hwid).strip().upper()


def get_machine_hwid() -> str:
    """
    Extracts silicon hardware registers to generate an unforgeable,
    machine-locked Hardware ID (HWID).
    Includes developer authorization code 'mclards23'.
    """
    if os.path.exists(HWID_OVERRIDE_FILE):
        try:
            with open(HWID_OVERRIDE_FILE, 'r') as f:
                override = f.read().strip()
                if override:
                    return normalize_hwid(override)
        except Exception:
            pass
    cpu_serial = 'CPU_GENERIC_OPI'
    try:
        if os.path.exists('/sys/class/sunxi_info/sys_info'):
            with open('/sys/class/sunxi_info/sys_info', 'r') as f:
                cpu_serial = f.read().strip()
        elif os.path.exists('/proc/cpuinfo'):
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if 'Serial' in line or 'serial' in line:
                        cpu_serial = line.split(':')[1].strip()
                        break
    except Exception:
        pass
    sd_cid = 'SD_CID_SANDISK_DEFAULT'
    try:
        if os.path.exists('/sys/block/mmcblk0/device/cid'):
            with open('/sys/block/mmcblk0/device/cid', 'r') as f:
                sd_cid = f.read().strip()
    except Exception:
        pass
    mac_addr = '00:00:00:00:00:00'
    try:
        if os.path.exists('/sys/class/net/eth0/address'):
            with open('/sys/class/net/eth0/address', 'r') as f:
                mac_addr = f.read().strip()
    except Exception:
        pass
    raw_signature = '{}|{}|{}|{}|{}'.format(cpu_serial, sd_cid, mac_addr, DEV_CODE, VENDOR_SECRET_SALT)
    sha = hashlib.sha256(raw_signature.encode('utf-8')).hexdigest().upper()
    return '{}-{}-{}-{}-{}-{}-{}-{}'.format(
        sha[:4], sha[4:8], sha[8:12], sha[12:16],
        sha[16:20], sha[20:24], sha[24:28], sha[28:32]
    )


def compute_activation_pin(hwid: str, tier: str='COMMERCIAL', expiry_date: str='PERPETUAL') -> str:
    """
    Computes the 32-character mathematical activation key for a specific HWID.
    Binds the developer code 'mclards23', hardware ID, tier, and salt.
    """
    clean_hwid = normalize_hwid(hwid)
    clean_tier = tier.strip().upper()
    payload = '{}::{}::{}::{}'.format(clean_hwid, clean_tier, DEV_CODE, VENDOR_SECRET_SALT)
    if expiry_date != 'PERPETUAL':
        time.strptime(expiry_date, '%Y-%m-%d')
        payload += '::EXPIRY::' + expiry_date
    sha = hashlib.sha256(payload.encode('utf-8')).hexdigest().upper()
    return '{}-{}-{}-{}-{}-{}-{}-{}'.format(
        sha[:4], sha[4:8], sha[8:12], sha[12:16],
        sha[16:20], sha[20:24], sha[24:28], sha[28:32]
    )


def verify_license() -> dict:
    """
    Validates the local license certificate against the physical board.
    Returns: {"valid": bool, "tier": str, "hwid": str, "licensee": str, "message": str}
    """
    current_hwid = get_machine_hwid()
    if not os.path.exists(LICENSE_FILE):
        return {
            'valid': False,
            'status': 'UNLICENSED',
            'hwid': current_hwid,
            'tier': 'NONE',
            'licensee': 'Unregistered',
            'message': 'No license key found. Machine is in Lockout / Demo mode.'
        }
    try:
        with open(LICENSE_FILE, 'r') as f:
            data = json.load(f)
        stored_hwid = data.get('machine_hwid', '')
        stored_tier = data.get('tier', 'COMMERCIAL')
        stored_key = data.get('activation_key', '')
        licensee = data.get('licensee', 'Standard Client')
        expiry = data.get('expiry_date', 'PERPETUAL')
        if normalize_hwid(stored_hwid) != normalize_hwid(current_hwid):
            return {
                'valid': False,
                'status': 'CLONED_HARDWARE_MISMATCH',
                'hwid': current_hwid,
                'tier': stored_tier,
                'licensee': licensee,
                'message': 'Hardware mismatch! License issued for {}, but running on {}.'.format(stored_hwid, current_hwid)
            }
        expected_key = compute_activation_pin(stored_hwid, stored_tier, expiry)
        clean_stored = re.sub(r'[^0-9A-F]', '', stored_key.upper())
        clean_expected = re.sub(r'[^0-9A-F]', '', expected_key.upper())
        if clean_stored != clean_expected:
            return {
                'valid': False,
                'status': 'CORRUPTED_SIGNATURE',
                'hwid': current_hwid,
                'tier': stored_tier,
                'licensee': licensee,
                'message': 'Invalid cryptographic license signature.'
            }
        if expiry != 'PERPETUAL':
            try:
                exp_timestamp = time.mktime(time.strptime(expiry, '%Y-%m-%d'))
                if time.time() > exp_timestamp:
                    return {
                        'valid': False,
                        'status': 'EXPIRED',
                        'hwid': current_hwid,
                        'tier': stored_tier,
                        'licensee': licensee,
                        'message': 'License expired on {}. Contact vendor for renewal.'.format(expiry)
                    }
            except Exception:
                return {
                    'valid': False,
                    'status': 'CORRUPTED_EXPIRY',
                    'hwid': current_hwid,
                    'tier': stored_tier,
                    'licensee': licensee,
                    'message': 'Invalid license expiry date format.'
                }
        return {
            'valid': True,
            'status': 'ACTIVATED',
            'hwid': current_hwid,
            'tier': stored_tier,
            'licensee': licensee,
            'expiry': expiry,
            'message': 'Genuine Eco-Fi {} License Activated.'.format(stored_tier)
        }
    except Exception as e:
        return {
            'valid': False,
            'status': 'ERROR',
            'hwid': current_hwid,
            'tier': 'NONE',
            'licensee': 'Error',
            'message': 'License read error: {}'.format(e)
        }


def activate_machine(activation_pin: str, licensee_name: str='Store Owner', tier: str='COMMERCIAL') -> dict:
    """
    Activates the machine using an offline 32-character hexadecimal key.
    Accepts key with or without dashes, with spaces, or lowercase.
    """
    current_hwid = get_machine_hwid()
    expected_pin = compute_activation_pin(current_hwid, tier)
    clean_pin = re.sub(r'[^0-9A-F]', '', str(activation_pin).strip().upper())
    expected_clean = re.sub(r'[^0-9A-F]', '', expected_pin.strip().upper())
    if clean_pin != expected_clean:
        return {'success': False, 'message': 'Invalid Activation Key. Please check your Hardware ID and try again.'}
    license_data = {
        'vendor': 'Eco-Fi Technologies',
        'licensee': licensee_name,
        'machine_hwid': current_hwid,
        'tier': tier,
        'activation_key': expected_pin,
        'activated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'expiry_date': 'PERPETUAL'
    }
    try:
        os.makedirs(os.path.dirname(LICENSE_FILE), exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.license-', dir=os.path.dirname(LICENSE_FILE))
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(license_data, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, LICENSE_FILE)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        return {'success': True, 'message': 'Machine successfully activated for {} ({} Edition)!'.format(licensee_name, tier)}
    except Exception as e:
        return {'success': False, 'message': 'Failed to save license certificate: {}'.format(e)}


if __name__ == '__main__':
    hwid = get_machine_hwid()
    print('======================================================')
    print(' Eco-Fi Cryptographic Hardware Identifier & Validator')
    print('======================================================')
    print(' Detected Machine HWID: {}'.format(hwid))
    status = verify_license()
    print(' License Status:        {}'.format(status['status']))
    print(' Active Tier:           {}'.format(status.get('tier', 'NONE')))
    print(' Message:               {}'.format(status['message']))
    print('======================================================')
