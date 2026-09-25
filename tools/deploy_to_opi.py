"""Deploy updated host files to live Orange Pi, restart service, and verify health."""
import datetime
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.opi_access import connect, execute, api

ROOT = Path(__file__).resolve().parent.parent
HOST_DIR = ROOT / 'host'

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

def main():
    print("=== VMC ECO-VENDO LIVE OPI DEPLOYMENT ===")
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = "/opt/ecofi_backups/backup_{}".format(ts)

    print("[1/6] Connecting to Live OPi via SSH...")
    client = connect()
    try:
        # Step 1: Create remote backup directory
        print("[2/6] Creating backup directory at {}...".format(backup_dir))
        code, out, err = execute(client, "mkdir -p {}".format(backup_dir))
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

            # Step 2: SFTP upload files
            print("[3/6] Uploading updated host files via SFTP to /opt/ecofi/...")
            for fname in FILES_TO_DEPLOY:
                local_path = HOST_DIR / fname
                remote_path = "/opt/ecofi/{}".format(fname)
                print("  Uploading {} ({} bytes)...".format(fname, local_path.stat().st_size))
                sftp.put(str(local_path), remote_path)

            # Upload VERSION
            version_file = ROOT / 'VERSION'
            if version_file.exists():
                print("  Uploading VERSION ({})...".format(version_file.read_text().strip()))
                sftp.put(str(version_file), "/opt/ecofi/VERSION")

        finally:
            sftp.close()

        # Step 3: Fix permissions
        print("[4/6] Setting executable permissions and cleaning pycache...")
        execute(client, "chmod +x /opt/ecofi/portal.py")
        execute(client, "find /opt/ecofi/__pycache__ -name '*.pyc' -delete 2>/dev/null || true")

        # Step 4: Restart service
        print("[5/6] Restarting ecofi_portal.service on live OPi...")
        code, out, err = execute(client, "systemctl restart ecofi_portal.service")
        if code != 0:
            print("WARNING: Restart command returned {}: {}".format(code, err))

        time.sleep(8)

        # Verify service status
        code, status_out, err = execute(client, "systemctl is-active ecofi_portal.service")
        status_str = status_out.strip()
        print("  ecofi_portal.service state: {}".format(status_str))
        if status_str != 'active':
            code, journal, _ = execute(client, "journalctl -u ecofi_portal.service -n 25 --no-pager")
            print("ERROR: Service is not active! Journal:\n", journal)
            raise SystemExit(1)

        # Step 5: Run unit tests on live OPi
        print("[6/6] Running unit tests directly on live OPi (Python 3.5.3)...")
        test_cmd = (
            "cd /opt/ecofi && python3 -B -m unittest "
            "test_entitlement_regressions.PortalRegression.test_manual_retrieval_bounds_and_zero_residue "
            "test_entitlement_regressions.PortalRegression.test_admin_servo_channel_bounds_reject_channel_2 "
            "test_entitlement_regressions.PortalRegression.test_manual_retrieval_events_lifecycle "
            "test_entitlement_regressions.PortalRegression.test_simulator_retrieve_endpoint"
        )
        code, test_out, test_err = execute(client, test_cmd, timeout=120)
        print("  Manual retrieval regression test output:")
        print(test_out + test_err)

        # Query local status endpoint via curl on OPi
        print("  Checking local OPi web server...")
        code, curl_out, _ = execute(client, "curl -s -m 5 http://127.0.0.1:5000/api/vendo/status")
        print("  Status endpoint response (first 200 chars):", curl_out[:200] if curl_out else "NO OUTPUT")

        print("\n=== DEPLOYMENT COMPLETED SUCCESSFULLY ===")
    finally:
        client.close()

if __name__ == '__main__':
    main()
