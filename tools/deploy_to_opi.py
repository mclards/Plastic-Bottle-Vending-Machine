"""Deploy updated host files & firmware to live Orange Pi, flash ESP32, restart service, and verify health."""
import datetime
import hashlib
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.opi_access import connect, execute

ROOT = Path(__file__).resolve().parent.parent
HOST_DIR = ROOT / 'host'
HOST_TOOLS_DIR = HOST_DIR / 'tools'
RESOURCES_DIR = ROOT / 'resources'

FILES_TO_DEPLOY = [
    'portal.py',
    'time_portal.py',
    'esp32_simulator.py',
    'test_entitlement_regressions.py',
    'gateway_network.py',
    'license_manager.py',
    'migrate_legacy_sessions.py',
    'status_led.py',
    'test_network_regressions.py',
    'test_time_system.py',
    'time_policy.py',
    'time_schema.py',
    'transition_engine.py',
]

TOOLS_TO_DEPLOY = [
    'flash_esp32.py',
    'stealth_enroll.sh',
]

def main():
    print("==================================================")
    print("  VMC ECO-VENDO LIVE OPI & ESP32 SYSTEM UPGRADE   ")
    print("==================================================")
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = "/opt/ecofi_backups/backup_{}".format(ts)

    print("[1/7] Connecting to Live OPi via SSH...")
    client = connect()
    try:
        # Step 1: Create remote backup directory and ensure target directories
        print("[2/7] Creating remote backup directory at {}...".format(backup_dir))
        code, out, err = execute(client, "mkdir -p {} /opt/ecofi/tools /opt/ecofi/firmware".format(backup_dir))
        if code != 0:
            raise RuntimeError("Failed to create backup dir: {}".format(err))

        sftp = client.open_sftp()
        try:
            # Backup existing files
            print("  Backing up current remote files...")
            for fname in FILES_TO_DEPLOY:
                remote_path = "/opt/ecofi/{}".format(fname)
                backup_path = "{}/{}".format(backup_dir, fname)
                execute(client, "cp -p {} {} 2>/dev/null || true".format(remote_path, backup_path))

            # Step 2: SFTP upload host files
            print("[3/7] Uploading updated host modules to /opt/ecofi/...")
            for fname in FILES_TO_DEPLOY:
                local_path = HOST_DIR / fname
                remote_path = "/opt/ecofi/{}".format(fname)
                print("  -> Uploading {} ({:,} bytes)...".format(fname, local_path.stat().st_size))
                sftp.put(str(local_path), remote_path)

            # Upload tools
            print("  Uploading host tools to /opt/ecofi/tools/...")
            for tname in TOOLS_TO_DEPLOY:
                local_path = HOST_TOOLS_DIR / tname
                remote_path = "/opt/ecofi/tools/{}".format(tname)
                print("  -> Uploading tools/{} ({:,} bytes)...".format(tname, local_path.stat().st_size))
                sftp.put(str(local_path), remote_path)

            # Upload VERSION
            version_file = ROOT / 'VERSION'
            if version_file.exists():
                print("  -> Uploading VERSION ({})".format(version_file.read_text().strip()))
                sftp.put(str(version_file), "/opt/ecofi/VERSION")

            # Step 3: SFTP upload latest ESP32 firmware binary
            print("[4/7] Uploading latest ESP32 factory firmware binary...")
            fw_local = RESOURCES_DIR / 'esp32_firmware_factory.bin'
            if not fw_local.exists():
                raise FileNotFoundError("Firmware binary not found: {}".format(fw_local))
            fw_bytes = fw_local.read_bytes()
            fw_sha256 = hashlib.sha256(fw_bytes).hexdigest()
            print("  Firmware: {} ({:,} bytes)".format(fw_local.name, len(fw_bytes)))
            print("  SHA-256:  {}".format(fw_sha256))

            remote_fw = "/opt/ecofi/firmware/esp32_firmware.bin"
            remote_sha = "/opt/ecofi/firmware/esp32_firmware.sha256"
            sftp.put(str(fw_local), remote_fw)

            # Write sha256 file
            with sftp.open(remote_sha, 'w') as f_sha:
                f_sha.write("{}  esp32_firmware.bin\n".format(fw_sha256))

        finally:
            sftp.close()

        # Step 4: Fix permissions
        print("[5/7] Configuring permissions & cleaning cache...")
        execute(client, "chmod +x /opt/ecofi/portal.py /opt/ecofi/tools/flash_esp32.py /opt/ecofi/tools/stealth_enroll.sh")
        execute(client, "find /opt/ecofi/__pycache__ -name '*.pyc' -delete 2>/dev/null || true")

        # Step 5: Flash ESP32 via GPIO harness flasher
        print("[6/7] Flashing ESP32 via hardware GPIO flasher (/dev/ttyS3)...")
        flash_cmd = "python3 /opt/ecofi/tools/flash_esp32.py flash /opt/ecofi/firmware/esp32_firmware.bin /dev/ttyS3"
        code, flash_out, flash_err = execute(client, flash_cmd, timeout=120)
        print("--- ESP32 Flasher Output ---")
        for line in (flash_out + flash_err).splitlines():
            print("  |", line)
        print("----------------------------")
        if code != 0:
            print("WARNING: Flasher exited with code {}. Verifying portal service...".format(code))

        # Restart service
        print("[7/7] Restarting ecofi_portal.service and verifying health...")
        execute(client, "systemctl restart ecofi_portal.service")
        time.sleep(6)

        # Verify service state
        code, status_out, _ = execute(client, "systemctl is-active ecofi_portal.service")
        status_str = status_out.strip()
        print("  ecofi_portal.service: {}".format(status_str))
        if status_str != 'active':
            code, journal, _ = execute(client, "journalctl -u ecofi_portal.service -n 30 --no-pager")
            print("ERROR: Service not active! Journal:\n", journal)
            raise SystemExit(1)

        # Run unit tests on OPi
        print("  Running test suite on live OPi (Python 3.5.3 runtime)...")
        test_cmd = (
            "cd /opt/ecofi && python3 -B -m unittest "
            "test_entitlement_regressions.PortalRegression.test_manual_retrieval_bounds_and_zero_residue "
            "test_entitlement_regressions.PortalRegression.test_admin_servo_channel_bounds_reject_channel_2 "
            "test_entitlement_regressions.PortalRegression.test_manual_retrieval_events_lifecycle "
            "test_entitlement_regressions.PortalRegression.test_simulator_retrieve_endpoint "
            "test_entitlement_regressions.PortalRegression.test_admin_routes_restored_and_guarded "
            "test_entitlement_regressions.PortalRegression.test_status_counts_and_zero_balance_gate"
        )
        code, test_out, test_err = execute(client, test_cmd, timeout=120)
        print("  Live tests output:")
        for line in (test_out + test_err).splitlines():
            print("  |", line)

        # Check API status
        code, curl_out, _ = execute(client, "curl -s -m 5 http://127.0.0.1:5000/api/status")
        print("\n  Live /api/status response:")
        print("  ", curl_out[:300] if curl_out else "NO RESPONSE")

        # Check Tailscale status via ecofi-net sync socket
        code, ts_out, _ = execute(client, "/usr/local/bin/tailscale --socket=/run/ecofi-net/sync.sock status")
        print("\n  Live Tailscale sync status:")
        for line in ts_out.splitlines():
            print("  |", line)

        print("\n==================================================")
        print("  BOTH OPI AND ESP32 UPDATED & VERIFIED!          ")
        print("==================================================")
    finally:
        client.close()

if __name__ == '__main__':
    main()
