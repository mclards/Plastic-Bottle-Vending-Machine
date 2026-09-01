import urllib.request
import urllib.parse
import json
import time

BASE_URL = "http://127.0.0.1:5000"

def request_json(path, method="GET", data=None, headers=None):
    url = f"{BASE_URL}{path}"
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=10) as res:
        return res.status, json.loads(res.read().decode())

def request_html(path, method="GET", data=None):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=10) as res:
        return res.status, res.read().decode()

def run_suite():
    print("==================================================================")
    print("      RUNNING FULL PISOFI MOCK ARCHITECTURE VERIFICATION SUITE    ")
    print("==================================================================")

    # 1. Reset Simulator Session
    status, res = request_json("/simulator/api/reset", method="POST")
    print(f"[*] [Reset] Simulator session reset: {res}")

    # 2. Check Initial Portal & State
    status, html = request_html("/")
    assert status == 200 and "ECO-Fi" in html
    print("[+] [Portal] Captive Portal loaded with live HTML UI.")

    # 3. Client Clicks 'Insert Plastic Bottle' -> Triggers Gate Opening
    status, res = request_json("/api/open_gate", method="POST")
    print(f"[+] [Portal -> ESP32] OPEN_GATE command issued: {res}")
    time.sleep(0.3)
    
    status, state = request_json("/simulator/api/state")
    print(f"    Entrance Servo Angle: {state['entrance_servo']}° (OPEN), LCD: '{state['lcd_lines'][1].strip()}'")

    # 4. Deposit Valid PET Bottle (22.5g)
    print("[*] [Drop 1] Depositing 1x Valid PET Bottle (22.5g)...")
    request_json("/simulator/api/trigger", method="POST", data={"item_type": "valid_pet"})
    
    # Wait for sensor validation & hatch cycle
    time.sleep(4.0)
    
    status, state = request_json("/simulator/api/state")
    print(f"    Validation complete! LCD Line 1: '{state['lcd_lines'][1].strip()}', Session Bottles: {state['session_bottles']}")
    assert state['session_bottles'] >= 1

    status, client_status = request_json("/api/status")
    print(f"    Client Internet Balance: {client_status['remaining_seconds']} seconds ({client_status['remaining_seconds']//60} mins)")
    assert client_status['remaining_seconds'] > 0

    # 5. Deposit Metal Soda Can (Anti-Fraud: Inductive Metal Proximity Sensor)
    print("\n[*] [Drop 2 - Fraud Test] Depositing Metal Can (Tin Detection)...")
    request_json("/simulator/api/trigger", method="POST", data={"item_type": "metal_can"})
    time.sleep(1.0)
    status, state = request_json("/simulator/api/state")
    print(f"    Rejection Active! Solenoid={state['solenoid']}, Buzzer={state['buzzer']}, LCD: '{state['lcd_lines'][2].strip()}'")
    assert "Tin/Can" in state['lcd_lines'][2]

    time.sleep(2.5) # Wait for reject cycle to reset

    # 6. Deposit Liquid-Filled Bottle (Anti-Fraud: Load Cell Overweight Check)
    print("\n[*] [Drop 3 - Fraud Test] Depositing Overfilled Liquid Bottle (480g)...")
    request_json("/simulator/api/trigger", method="POST", data={"item_type": "overweight"})
    time.sleep(1.0)
    status, state = request_json("/simulator/api/state")
    print(f"    Rejection Active! Solenoid={state['solenoid']}, Weight={state['scale_weight']}g, LCD: '{state['lcd_lines'][2].strip()}'")
    assert "Liquid" in state['lcd_lines'][2] or "Overweight" in state['lcd_lines'][2]

    time.sleep(2.5)

    # 7. Deposit PVC / Invalid Polymer (Anti-Fraud: AS7263 NIR Spectrometer)
    print("\n[*] [Drop 4 - Fraud Test] Depositing PVC / Non-PET Material (NIR W=45nm)...")
    request_json("/simulator/api/trigger", method="POST", data={"item_type": "invalid_polymer"})
    time.sleep(1.0)
    status, state = request_json("/simulator/api/state")
    print(f"    Rejection Active! NIR Val={state['nir_val']}, LCD: '{state['lcd_lines'][2].strip()}'")
    assert "NIR" in state['lcd_lines'][2] or "Material" in state['lcd_lines'][2]

    time.sleep(2.5)

    # 8. Simulate Bin Storage Full (Ultrasonic < 15cm)
    print("\n[*] [Bin Full Sensor] Simulating storage bin filled to capacity (Distance=8cm)...")
    request_json("/simulator/api/bin", method="POST", data={"distance_cm": 8})
    time.sleep(1.5)
    status, state = request_json("/simulator/api/state")
    print(f"    Bin Alert State: Full={state['is_bin_full']}, Red LED={state['led_red']}, LCD: '{state['lcd_lines'][1].strip()}'")
    assert state['is_bin_full'] is True

    # Restore Bin to Normal
    request_json("/simulator/api/bin", method="POST", data={"distance_cm": 60})
    time.sleep(1.5)

    # 9. Verify AdminLTE Accounting & Statistics
    print("\n[*] [Admin Accounting] Fetching live system statistics...")
    status, stats = request_json("/admin/api/stats")
    print(f"    Today's Bottles: {stats['today_bottles']}")
    print(f"    Total Bottles:   {stats['total_bottles']}")
    print(f"    Active Clients:  {stats['active_clients']}")
    print(f"    Weekly History:  {stats['history']}")

    status, clients = request_json("/admin/api/clients")
    print(f"    Active Clients List: {clients}")

    print("\n==================================================================")
    print("      ALL PISOFI ARCHITECTURE MOCK TESTS PASSED PERFECTLY!        ")
    print("==================================================================")

if __name__ == "__main__":
    run_suite()
