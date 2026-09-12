import os
import hashlib
import sys
from pathlib import Path

ROOT = Path('d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine')
IMG_PATH = ROOT / 'resources' / 'EcoFi_Opi_v2.3.4.img'
SHA_FILE = ROOT / 'resources' / 'EcoFi_Opi_v2.3.4.img.sha256'
MD5_FILE = ROOT / 'resources' / 'EcoFi_Opi_v2.3.4.img.md5'

print('=== 1. VERIFYING DISK IMAGE CHECKSUMS ===')
print('Image Path:', IMG_PATH)
print('File Size:', IMG_PATH.stat().st_size, 'bytes')

md5 = hashlib.md5()
sha256 = hashlib.sha256()

with open(IMG_PATH, 'rb') as f:
    while True:
        chunk = f.read(16 * 1024 * 1024)
        if not chunk:
            break
        md5.update(chunk)
        sha256.update(chunk)

calc_md5 = md5.hexdigest()
calc_sha256 = sha256.hexdigest()

print('\nCalculated MD5:   ', calc_md5)
with open(MD5_FILE, 'r') as f:
    rec_md5 = f.read().split()[0]
print('Recorded MD5:     ', rec_md5)
print('MD5 Match:        ', calc_md5 == rec_md5)

print('\nCalculated SHA256:', calc_sha256)
with open(SHA_FILE, 'r') as f:
    rec_sha256 = f.read().split()[0]
print('Recorded SHA256:  ', rec_sha256)
print('SHA256 Match:     ', calc_sha256 == rec_sha256)

print('\n=== 2. COMPARING LOCAL HOST WITH LIVE OPi ===')
sys.path.insert(0, str(ROOT / 'tools'))
from opi_access import connect

ssh = connect()
sftp = ssh.open_sftp()

modules = [
    'portal.py', 'license_manager.py', 'esp32_simulator.py',
    'gateway_network.py', 'time_schema.py', 'time_policy.py',
    'transition_engine.py', 'time_portal.py', 'migrate_legacy_sessions.py',
    'VERSION'
]

mismatches = 0
for m in modules:
    local_p = ROOT / 'host' / m if m != 'VERSION' else ROOT / 'VERSION'
    with open(local_p, 'rb') as lf:
        lh = hashlib.sha256(lf.read()).hexdigest()
    
    rf = sftp.open('/opt/ecofi/' + m)
    rh = hashlib.sha256(rf.read()).hexdigest()
    rf.close()

    status = 'OK' if lh == rh else 'MISMATCH'
    if lh != rh:
        mismatches += 1
    print('  [%s] %-28s Local: %s.. | Live: %s..' % (status, m, lh[:10], rh[:10]))

sftp.close()
ssh.close()

print('\nLive vs Local Host Mismatches:', mismatches)
if mismatches == 0 and calc_sha256 == rec_sha256 and calc_md5 == rec_md5:
    print('\n>>> ALL CRYPTOGRAPHIC CHECKS AND LIVE OPi COMPARISONS PASSED 100% <<<')
else:
    print('\n>>> VERIFICATION FAILED <<<')
    sys.exit(1)
