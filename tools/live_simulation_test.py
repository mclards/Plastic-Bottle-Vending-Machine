"""Live Simulation & Hardware Reset Recovery Verification for VMC ECO-VENDO.
Tests both physical ESP32 on Orange Pi and simulated workflows.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.opi_access import connect, execute

def run_cmd(client, cmd):
    code, out, err = execute(client, cmd)
    if code != 0:
        print("ERROR running: {}\nOutput: {}\nError: {}".format(cmd, out, err))
    return code, out, err

def get_db_session(client, sid):
    py = "import sqlite3; conn=sqlite3.connect('/opt/ecofi/vendo_sessions.db'); row=conn.execute('SELECT id, status, error FROM deposit_sessions WHERE id=?', ('{}',)).fetchone(); print(row[0], row[1], row[2] or '')".format(sid)
    code, out, err = run_cmd(client, 'python3 -c "{}"'.format(py))
    return out.strip().split()

def get_live_admin_stats(client):
    cmd = 'curl -s -o /dev/null -c /tmp/cookies.txt "http://127.0.0.1:5000/admin/dev_auth?token=dev$(date +%M)" && curl -s -b /tmp/cookies.txt http://127.0.0.1:5000/admin/api/stats'
    code, out, err = run_cmd(client, cmd)
    return json.loads(out)

def toggle_simulator(client, enabled):
    cmd = 'curl -s -b /tmp/cookies.txt -X POST -H "Content-Type: application/json" -d \'{{"enabled": {}}}\' http://127.0.0.1:5000/admin/api/simulator/toggle'.format("true" if enabled else "false")
    code, out, err = run_cmd(client, cmd)
    return json.loads(out)

def main():
    print("=" * 60)
    print("  LIVE SIMULATION & HARDWARE RESET TEST SUITE  ")
    print("=" * 60)

    client = connect()
    print("[1] Connected to Live Orange Pi via Tailscale.")

    # ---------------------------------------------------------
    # PART A: PHYSICAL ESP32 INTAKE HEARTBEAT & RESET RECOVERY
    # ---------------------------------------------------------
    print("\n" + "=" * 50)
    print("  PART A: Physical ESP32 Tests (/dev/ttyS3)")
    print("=" * 50)

    # Ensure simulator is disabled so we talk to physical hardware
    get_live_admin_stats(client) # Authenticate
    toggle_simulator(client, False)
    time.sleep(1)

    print("\n--- Test A1: Checking Initial Physical ESP32 Hardware Status ---")
    st = get_live_admin_stats(client)
    print("  ESP32 Status:", st.get('esp32_status'))
    print("  Hardware Ready:", st.get('hardware'))
    print("  Gate Open:", st.get('esp32_gate_open'))
    print("  rx_delta:", st.get('esp32_rx_delta'), "s")
    assert st.get('esp32_status') == 'ONLINE', "ESP32 is not ONLINE!"

    # If gate was open from previous test, abort it first
    if st.get('deposit_session_active') or st.get('esp32_gate_open'):
        print("  Aborting previous active session...")
        run_cmd(client, 'curl -s -X POST -H "X-Forwarded-For: 10.0.5.148" -H "Content-Type: application/json" -d "{}" http://127.0.0.1:5000/api/vendo/abort')
        time.sleep(1)

    print("\n--- Test A2: Open Entrance Gate (Start Deposit Session) ---")
    code, out, _ = run_cmd(client, 'curl -s -X POST -H "X-Forwarded-For: 10.0.5.148" -H "Content-Type: application/json" -d "{}" http://127.0.0.1:5000/api/vendo/open_gate')
    open_res = json.loads(out)
    print("  Open Gate Response:", open_res)
    assert open_res.get('success') is True, "Failed to open gate! {}".format(open_res)
    session_id = open_res.get('deposit_session_id')
    print("  Active Session ID:", session_id)

    print("\n--- Test A3: Verifying Intake Heartbeat During Waiting Loop ---")
    # Wait 6 seconds while gate is open waiting for bottle
    print("  Waiting 6s while gate is open (testing telemetry heartbeat during intake wait)...")
    time.sleep(6)
    stats = get_live_admin_stats(client)
    rx_delta = stats.get('esp32_rx_delta')
    esp_status = stats.get('esp32_status')
    gate_state = stats.get('esp32_gate_open')
    print("  Live ESP32 Status:", esp_status)
    print("  Live ESP32 Gate Open:", gate_state)
    print("  Live ESP32 rx_delta:", rx_delta, "seconds")
    assert esp_status == 'ONLINE', "ESP32 watchdog timed out during gate wait! Status: {}".format(esp_status)
    assert rx_delta is not None and rx_delta <= 5, "Heartbeat not emitted during intake! rx_delta: {}".format(rx_delta)
    print("  -> SUCCESS: Periodic intake heartbeat kept serial connection active (rx_delta={}s)!".format(rx_delta))

    print("\n--- Test A4: Simulating Physical ESP32 Hardware Reset (RST pin low->high) ---")
    print("  Triggering hardware reset via /opt/ecofi/tools/flash_esp32.py reset...")
    code, out, _ = run_cmd(client, 'python3 /opt/ecofi/tools/flash_esp32.py reset')
    print("  Reset output:", out.strip())

    print("  Waiting 4s for ESP32 bootloader, setup, and BOOT UART emission...")
    time.sleep(4)

    print("\n--- Test A5: Verifying Host Auto-Recovery on ESP32 BOOT ---")
    parts = get_db_session(client, session_id)
    print("  Database row for previous session:", parts)
    assert len(parts) >= 2 and parts[1] == 'HOLD', "Session was not transitioned to HOLD on boot! Got: {}".format(parts)
    assert len(parts) >= 3 and parts[2] == 'boot', "Session error is not 'boot'! Got: {}".format(parts)
    print("  -> Session correctly transitioned to status='HOLD', error='boot'!")

    post_boot_stats = get_live_admin_stats(client)
    print("  Live post-boot ESP32 Status:", post_boot_stats.get('esp32_status'))
    print("  Live post-boot Gate Open:", post_boot_stats.get('esp32_gate_open'))
    print("  Live post-boot Session Active:", post_boot_stats.get('deposit_session_active'))
    assert post_boot_stats.get('esp32_gate_open') is False, "gate_open not reset to False!"
    assert post_boot_stats.get('deposit_session_active') is False, "deposit_session_active still True!"
    print("  -> Machine state fully reset and unlocked!")

    print("\n--- Test A6: Verifying Portal Works Immediately After ESP32 Reset ---")
    code, out, _ = run_cmd(client, 'curl -s -X POST -H "X-Forwarded-For: 10.0.5.148" -H "Content-Type: application/json" -d "{}" http://127.0.0.1:5000/api/vendo/open_gate')
    next_open = json.loads(out)
    print("  Second Open Gate Response:", next_open)
    assert next_open.get('success') is True, "Gate open failed after ESP32 reset! Lockout occurred: {}".format(next_open)
    print("  -> SUCCESS: Portal opened entrance gate immediately without any lockout or error!")

    # Close gate cleanly
    code, out, _ = run_cmd(client, 'curl -s -X POST -H "X-Forwarded-For: 10.0.5.148" -H "Content-Type: application/json" -d "{}" http://127.0.0.1:5000/api/vendo/abort')
    print("  Cleaned up second session:", out.strip())

    # ---------------------------------------------------------
    # PART B: SIMULATOR LIVE SIMULATION
    # ---------------------------------------------------------
    print("\n" + "=" * 50)
    print("  PART B: Software Simulator Live Simulation")
    print("=" * 50)

    print("\n--- Test B1: Enabling Simulator Mode via Admin API ---")
    t_res = toggle_simulator(client, True)
    print("  Toggle Response:", t_res)
    assert t_res.get('simulator_enabled') is True
    time.sleep(1)

    code, out, _ = run_cmd(client, 'curl -s -H "X-Forwarded-For: 10.0.5.148" http://127.0.0.1:5000/api/vendo/status')
    sim_st = json.loads(out)
    print("  Simulator Status:", sim_st.get('hardware_status'), "| Ready:", sim_st.get('hardware_ready'))
    assert sim_st.get('hardware_ready') is True

    print("\n--- Test B2: Starting Simulator Deposit Session ---")
    code, out, _ = run_cmd(client, 'curl -s -X POST -H "X-Forwarded-For: 10.0.5.148" -H "Content-Type: application/json" -d "{}" http://127.0.0.1:5000/api/vendo/open_gate')
    sim_open = json.loads(out)
    print("  Simulator Gate Opened:", sim_open)
    assert sim_open.get('success') is True
    sim_sid = sim_open.get('deposit_session_id')

    print("\n--- Test B3: Triggering Simulated PET Bottle Drop ---")
    cmd = 'curl -s -b /tmp/cookies.txt -X POST -H "Content-Type: application/json" -d \'{"item_type":"valid_pet"}\' http://127.0.0.1:5000/simulator/api/trigger'
    code, out, _ = run_cmd(client, cmd)
    print("  Trigger response:", out.strip())

    # Wait for transit, scan, drop
    print("  Simulating physical transit and spectroscopy (3s)...")
    time.sleep(3)

    code, out, _ = run_cmd(client, 'curl -s -H "X-Forwarded-For: 10.0.5.148" http://127.0.0.1:5000/api/vendo/status')
    after_drop = json.loads(out)
    bottles = after_drop.get('session_bottles', 0)
    added_mins = after_drop.get('session_added_minutes', 0)
    print("  Session Bottles Credited:", bottles)
    print("  Session Added Minutes:", added_mins)
    assert bottles >= 1, "Simulated bottle was not credited!"
    assert added_mins > 0, "No time added for credited bottle!"

    print("\n--- Test B4: Finalizing Session & Crediting Time ---")
    code, out, _ = run_cmd(client, 'curl -s -X POST -H "X-Forwarded-For: 10.0.5.148" -H "Content-Type: application/json" -d "{}" http://127.0.0.1:5000/api/vendo/done')
    done_res = json.loads(out)
    print("  Session Done Response:", done_res)
    assert done_res.get('success') is True

    print("\n--- Test B5: Testing Simulator Session Reset ---")
    cmd = 'curl -s -b /tmp/cookies.txt -X POST http://127.0.0.1:5000/simulator/api/reset'
    code, out, _ = run_cmd(client, cmd)
    print("  Simulator Reset Response:", out.strip())
    time.sleep(1)

    code, out, _ = run_cmd(client, 'curl -s -H "X-Forwarded-For: 10.0.5.148" http://127.0.0.1:5000/api/vendo/status')
    post_reset_st = json.loads(out)
    print("  Post-Reset Gate Open:", post_reset_st.get('gate_open'))
    print("  Post-Reset Bottles:", post_reset_st.get('session_bottles'))
    assert post_reset_st.get('gate_open') is False

    # ---------------------------------------------------------
    # PART C: RESTORING PRODUCTION HARDWARE STATE
    # ---------------------------------------------------------
    print("\n" + "=" * 50)
    print("  PART C: Restoring Production Configuration")
    print("=" * 50)
    toggle_simulator(client, False)
    time.sleep(1)

    code, out, _ = run_cmd(client, 'curl -s -H "X-Forwarded-For: 10.0.5.148" http://127.0.0.1:5000/api/vendo/status')
    prod_st = json.loads(out)
    print("  Production Hardware Ready:", prod_st.get('hardware_ready'))
    print("  Production Hardware Status:", prod_st.get('hardware_status'))
    assert prod_st.get('hardware_ready') is True, "Physical hardware not ready in production state!"

    print("\n" + "=" * 60)
    print("  ALL LIVE SIMULATION & RESET RECOVERY TESTS PASSED!  ")
    print("=" * 60)

if __name__ == '__main__':
    main()
