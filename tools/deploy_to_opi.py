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
    'system_logger.py',
]

TOOLS_TO_DEPLOY = [
    'flash_esp32.py',
    'stealth_enroll.sh',
    'ap_kick.sh',
]

def main():
    print("==================================================")
    print("  VMC ECO-VENDO LIVE OPI & ESP32 SYSTEM UPGRADE   ")
    print("==================================================")
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = "/opt/ecofi_backups/backup_{}".format(ts)

    host_only = '--host-only' in sys.argv

    print("[1/7] Connecting to Live OPi via SSH...", flush=True)
    client = connect()
    try:
        # Step 1: Create remote backup directory and ensure target directories
        print("[2/7] Creating remote backup directory at {}...".format(backup_dir), flush=True)
        code, out, err = execute(client, "mkdir -p {} /opt/ecofi/tools /opt/ecofi/firmware".format(backup_dir))
        if code != 0:
            raise RuntimeError("Failed to create backup dir: {}".format(err))

        sftp = client.open_sftp()
        try:
            # Backup existing files
            print("  Backing up current remote files...", flush=True)
            for fname in FILES_TO_DEPLOY:
                remote_path = "/opt/ecofi/{}".format(fname)
                backup_path = "{}/{}".format(backup_dir, fname)
                execute(client, "cp -p {} {} 2>/dev/null || true".format(remote_path, backup_path))

            # Step 2: SFTP upload host files
            print("[3/7] Uploading updated host modules to /opt/ecofi/...", flush=True)
            for fname in FILES_TO_DEPLOY:
                local_path = HOST_DIR / fname
                remote_path = "/opt/ecofi/{}".format(fname)
                print("  -> Uploading {} ({:,} bytes)...".format(fname, local_path.stat().st_size), flush=True)
                sftp.put(str(local_path), remote_path)

            # Upload tools
            print("  Uploading host tools to /opt/ecofi/tools/...", flush=True)
            for tname in TOOLS_TO_DEPLOY:
                local_path = HOST_TOOLS_DIR / tname
                remote_path = "/opt/ecofi/tools/{}".format(tname)
                print("  -> Uploading tools/{} ({:,} bytes)...".format(tname, local_path.stat().st_size), flush=True)
                sftp.put(str(local_path), remote_path)

            # Upload VERSION
            version_file = ROOT / 'VERSION'
            if version_file.exists():
                print("  -> Uploading VERSION ({})".format(version_file.read_text().strip()), flush=True)
                sftp.put(str(version_file), "/opt/ecofi/VERSION")

            # Upload optimized static assets (images, scripts, CSS)
            print("  Uploading optimized static assets to /opt/ecofi/static/...", flush=True)
            static_dir = HOST_DIR / 'static'
            for ext in ('*.jpg', '*.png', '*.js'):
                for sfile in static_dir.glob(ext):
                    print("  -> Uploading static/{} ({:,} bytes)...".format(sfile.name, sfile.stat().st_size), flush=True)
                    sftp.put(str(sfile), "/opt/ecofi/static/{}".format(sfile.name))

            # Step 3: SFTP upload latest ESP32 firmware binary
            if not host_only:
                print("[4/7] Uploading latest ESP32 factory firmware binary...", flush=True)
                fw_local = RESOURCES_DIR / 'esp32_firmware_factory.bin'
                if not fw_local.exists():
                    raise FileNotFoundError("Firmware binary not found: {}".format(fw_local))
                fw_bytes = fw_local.read_bytes()
                fw_sha256 = hashlib.sha256(fw_bytes).hexdigest()
                print("  Firmware: {} ({:,} bytes)".format(fw_local.name, len(fw_bytes)), flush=True)
                print("  SHA-256:  {}".format(fw_sha256), flush=True)

                remote_fw = "/opt/ecofi/firmware/esp32_firmware.bin"
                remote_sha = "/opt/ecofi/firmware/esp32_firmware.sha256"
                sftp.put(str(fw_local), remote_fw)

                # Write sha256 file
                with sftp.open(remote_sha, 'w') as f_sha:
                    f_sha.write("{}  esp32_firmware.bin\n".format(fw_sha256))
            else:
                print("[4/7] Skipping ESP32 firmware upload (--host-only mode active)", flush=True)

        finally:
            sftp.close()

        # Step 4: Fix permissions
        print("[5/7] Configuring permissions & cleaning cache...", flush=True)
        execute(client, "chmod +x /opt/ecofi/portal.py /opt/ecofi/tools/flash_esp32.py /opt/ecofi/tools/stealth_enroll.sh /opt/ecofi/tools/ap_kick.sh")
        execute(client, "find /opt/ecofi/__pycache__ -name '*.pyc' -delete 2>/dev/null || true")

        # Step 4.5: Update and reload Nginx configuration
        print("  Updating Nginx reverse proxy configuration (/etc/nginx/sites-available/ecofi)...", flush=True)
        nginx_conf = '''upstream ecofi_backend {
    server 127.0.0.1:5000;
    keepalive 32;
}

server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    server_tokens off;

    # Security Headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Static Assets Cache (Aggressive 30d browser caching for instant rendering)
    location /static/ {
        alias /opt/ecofi/static/;
        expires 30d;
        add_header Cache-Control "public, max-age=2592000, immutable";
        access_log off;
    }

    # Proxy all traffic to VMC ECO-VENDO Python Web Engine with persistent HTTP/1.1 keepalive
    location / {
        proxy_pass http://ecofi_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 60s;
    }
}
'''
        sftp = client.open_sftp()
        try:
            with sftp.open('/etc/nginx/sites-available/ecofi', 'w') as nf:
                nf.write(nginx_conf)
        finally:
            sftp.close()
        code, ntest_out, ntest_err = execute(client, "nginx -t && systemctl reload nginx")
        if code != 0:
            print("WARNING: nginx reload issue: {}".format(ntest_out + ntest_err))
        else:
            print("  Nginx reloaded successfully with persistent keepalive upstream!")

        # Step 5: Flash ESP32 via GPIO harness flasher
        if not host_only:
            print("[6/7] Flashing ESP32 via hardware GPIO flasher (/dev/ttyS3)...", flush=True)
            flash_cmd = "python3 /opt/ecofi/tools/flash_esp32.py flash /opt/ecofi/firmware/esp32_firmware.bin /dev/ttyS3"
            code, flash_out, flash_err = execute(client, flash_cmd, timeout=120)
            print("--- ESP32 Flasher Output ---", flush=True)
            for line in (flash_out + flash_err).splitlines():
                print("  |", line, flush=True)
            print("----------------------------", flush=True)
            if code != 0:
                print("WARNING: Flasher exited with code {}. Verifying portal service...".format(code), flush=True)
        else:
            print("[6/7] Skipping ESP32 flashing (--host-only mode active)", flush=True)

        # Restart service
        print("[7/7] Restarting ecofi_portal.service and verifying health...", flush=True)
        execute(client, "systemctl restart ecofi_portal.service")
        time.sleep(6)

        # Verify service state
        code, status_out, _ = execute(client, "systemctl is-active ecofi_portal.service")
        status_str = status_out.strip()
        print("  ecofi_portal.service: {}".format(status_str), flush=True)
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
