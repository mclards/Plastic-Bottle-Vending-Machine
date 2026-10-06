import json
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.opi_access import connect, execute, admin_cookie

def main():
    print("Connecting to live OPi...")
    client = connect()
    cookie = admin_cookie(client)

    # 1. Test sequence_monitor API on live OPi
    code, out, err = execute(client, 'curl -s -b "session={}" http://127.0.0.1:5000/admin/api/esp32/sequence_monitor'.format(cookie))
    data = json.loads(out)
    stages = list(data.get('data', {}).get('stages', {}).keys())
    print("Live Stages registered on OPi ({} stages):".format(len(stages)), stages)
    for k, v in data.get('data', {}).get('stages', {}).items():
        print("  - Stage {}: {} [{}]".format(k, v.get('title'), v.get('status')))

    # 2. Test sequence simulation with Metal rejection
    print("\n--- Testing Metal Rejection Simulation ---")
    sim_payload = json.dumps({"is_pet": False, "is_metal": True}).replace('"', '\\"')
    cmd = 'curl -s -b "session={}" -H "Content-Type: application/json" -d "{}" http://127.0.0.1:5000/admin/api/esp32/sequence/simulate'.format(cookie, sim_payload)
    code, out, err = execute(client, cmd)
    print("Simulate Metal response:", out)

    # Poll live sequence state
    code, out, err = execute(client, 'curl -s -b "session={}" http://127.0.0.1:5000/admin/api/esp32/sequence_monitor'.format(cookie))
    data = json.loads(out).get('data', {})
    print("Overall Status:", data.get('status'))
    s3 = data.get('stages', {}).get('3_prox', {})
    print("S3 Inductive Stage Status:", s3.get('status'))
    print("S3 Detail:", s3.get('detail'))
    print("S3 Latency:", s3.get('latency_ms'), "ms")

    # Wait for metal simulation thread to complete
    time.sleep(2.5)

    # 3. Test sequence simulation with PET acceptance
    print("\n--- Testing Valid PET Deposit Simulation ---")
    sim_pet_payload = json.dumps({"is_pet": True, "is_metal": False, "weight": 36.2, "cal_w": 52.4}).replace('"', '\\"')
    cmd_pet = 'curl -s -b "session={}" -H "Content-Type: application/json" -d "{}" http://127.0.0.1:5000/admin/api/esp32/sequence/simulate'.format(cookie, sim_pet_payload)
    code, out, err = execute(client, cmd_pet)
    print("Simulate PET response:", out)

    # Wait for PET simulation thread to complete all 7 stages
    time.sleep(3.0)

    code, out, err = execute(client, 'curl -s -b "session={}" http://127.0.0.1:5000/admin/api/esp32/sequence_monitor'.format(cookie))
    data = json.loads(out).get('data', {})
    print("Overall Status:", data.get('status'))
    for k in ['1_gate', '2_intake', '3_prox', '4_scale', '5_nir', '6_exit', '7_drop']:
        st = data.get('stages', {}).get(k, {})
        print("  - Stage {}: status={} detail='{}' latency={}ms".format(k, st.get('status'), st.get('detail'), st.get('latency_ms')))

    # Reset
    execute(client, 'curl -s -b "session={}" -X POST http://127.0.0.1:5000/admin/api/esp32/sequence/reset'.format(cookie))
    print("\nChute sequence successfully reset to IDLE.")

if __name__ == '__main__':
    main()
