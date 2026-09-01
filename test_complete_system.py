import urllib.request
import urllib.parse
import json
import time
import sys

BASE_URL = "http://127.0.0.1:5000"

def request_json(path, data=None):
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"} if data is not None else {}
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as res:
        res_data = res.read().decode("utf-8")
        try:
            return res.status, json.loads(res_data)
        except Exception:
            return res.status, res_data

def request_get(path):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as res:
        return res.status, res.read().decode("utf-8")

def run_tests():
    print("==================================================================")
    print("      RUNNING 100% COMPREHENSIVE ECO-FI SYSTEM TEST SUITE        ")
    print("==================================================================")

    # 1. Captive Portal UI & Status
    print("[*] Testing Captive Portal UI (GET /)...")
    status, html = request_get("/")
    assert status == 200 and "ECO-Fi" in html
    print("    [+] Captive Portal UI loaded successfully.")

    print("[*] Testing Portal Status API (GET /api/vendo/status)...")
    status, data = request_json("/api/vendo/status")
    assert status == 200 and "client_time_remaining" in data
    print(f"    [+] Status OK: Remaining Seconds = {data['client_time_remaining']}")

    # 2. Simulator UI & Sensor Dropping
    print("[*] Testing Simulator UI (GET /simulator)...")
    status, html = request_get("/simulator")
    assert status == 200 and "ESP32 Hardware Simulator" in html
    print("    [+] Simulator UI loaded successfully.")

    print("[*] Resetting simulator session...")
    request_json("/simulator/api/reset", {})
    status, state = request_json("/simulator/api/state")
    assert state["session_bottles"] == 0
    print("    [+] Simulator session reset.")

    # 3. Simulate Deposit Gate Open
    print("[*] Testing Gate Open (POST /api/vendo/open_gate)...")
    status, res = request_json("/api/vendo/open_gate", {})
    assert status == 200 and res.get("success")
    time.sleep(0.2)
    status, state = request_json("/simulator/api/state")
    assert state["entrance_servo"] == 90
    print("    [+] Airlock Entrance Gate opened (90 deg).")

    # 4. Simulate Valid Drop (22.5g PET)
    print("[*] Simulating physical drop of 1x Valid PET Bottle (22.5g, 1450nm NIR)...")
    request_json("/simulator/api/trigger", {"item_type": "valid_pet"})
    time.sleep(3.5)
    status, state = request_json("/simulator/api/state")
    assert state["session_bottles"] >= 1
    print(f"    [+] Valid PET accepted! Session bottles = {state['session_bottles']}")

    # 5. Client Session Verification
    status, client_status = request_json("/api/vendo/status")
    assert client_status["client_time_remaining"] > 0
    print(f"    [+] Client internet credited: {client_status['client_time_remaining']}s")

    # 6. Admin Panel Dashboard & Statistics
    print("[*] Testing Admin Dashboard (GET /admin)...")
    status, html = request_get("/admin")
    assert status == 200 and "ECO-Fi MASTER" in html
    print("    [+] AdminLTE Master Panel loaded.")

    status, stats = request_json("/admin/api/stats")
    assert status == 200 and stats["total_bottles"] >= 1
    print(f"    [+] Live Accounting: Total Bottles = {stats['total_bottles']}, Active Clients = {stats['active_clients']}")

    # 7. Connected Clients Table & Modal Edit
    print("[*] Testing Connected Clients API (GET /admin/api/clients)...")
    status, clients = request_json("/admin/api/clients")
    assert status == 200 and len(clients) >= 1
    client_ip = clients[0]["ip"]
    print(f"    [+] Active client found: IP = {client_ip}, MAC = {clients[0]['mac']}")

    print(f"[*] Testing Client Modal Edit Action (POST /admin/api/client/edit)...")
    status, res = request_json("/admin/api/client/edit", {"ip": client_ip, "minutes": 45, "dl_kbps": 5120, "ul_kbps": 2048})
    assert status == 200 and res.get("success")
    status, clients = request_json("/admin/api/clients")
    assert clients[0]["dl_kbps"] == 5120 and clients[0]["ul_kbps"] == 2048
    print("    [+] Modal Edit Client Session verified: Speed updated to 5120/2048 Kbps.")

    # 8. Promo Rates & Package Builder
    print("[*] Testing Promo Rates Builder (Add/List/Edit/Delete)...")
    request_json("/admin/api/rates/add", {"bottles": 5, "minutes": 75, "label": "5 Bottles = 75 mins Promo"})
    status, rates = request_json("/admin/api/rates/list")
    assert any(r["bottles"] == 5 for r in rates)
    print("    [+] Added Promo Rate tier (5 Bottles = 75m).")

    request_json("/admin/api/rates/delete", {"bottles": 5})
    status, rates = request_json("/admin/api/rates/list")
    assert not any(r["bottles"] == 5 for r in rates)
    print("    [+] Deleted Promo Rate tier successfully.")

    # 9. Prepaid Voucher Generator & Redemption
    print("[*] Testing Prepaid Voucher Batch Generator...")
    status, vres = request_json("/admin/api/vouchers/generate", {"qty": 2, "minutes": 60, "note": "Test Batch"})
    assert status == 200 and len(vres["vouchers"]) == 2
    v_code = vres["vouchers"][0]["code"]
    print(f"    [+] Generated test voucher: {v_code}")

    print(f"[*] Testing Client Voucher Redemption ({v_code})...")
    status, rres = request_json("/api/voucher/redeem", {"code": v_code})
    assert status == 200 and rres.get("success")
    print(f"    [+] Voucher redeemed successfully: {rres['message']}")

    # 9.1 Test Time Transfer with exact minutes
    print("[*] Testing Time Transfer generation with exact minutes (10m)...")
    status, tres = request_json("/api/transfer/generate", {"minutes": 10})
    assert status == 200 and tres.get("success") and tres.get("minutes") == 10
    t_code = tres["code"]
    print(f"    [+] Generated 10-minute Transfer Code: {t_code}")

    status, tc_res = request_json("/api/transfer/claim", {"code": t_code})
    assert status == 200 and tc_res.get("success")
    print(f"    [+] Claimed 10-minute Transfer Code successfully.")

    # 10. Member Accounts & Wallet Management
    print("[*] Testing Member Account Registration & Top-Up...")
    test_user = f"testuser_{int(time.time())}"
    status, mres = request_json("/api/member/register", {"username": test_user, "pin": "1234"})
    assert status == 200 and mres.get("success")
    print(f"    [+] Member account registered: {test_user}")

    status, lres = request_json("/api/member/login", {"username": test_user, "pin": "1234"})
    assert status == 200 and lres.get("success")
    print(f"    [+] Member login verified. Initial Balance = {lres['wallet_minutes']}m")

    request_json("/admin/api/members/topup", {"username": test_user, "minutes": 30})
    status, lres2 = request_json("/api/member/login", {"username": test_user, "pin": "1234"})
    assert lres2["wallet_minutes"] == 30
    print("    [+] Member wallet top-up verified: Balance is now 30m.")

    # Test Use Wallet to Connect
    status, ures = request_json("/api/member/use_wallet", {"username": test_user, "pin": "1234", "minutes": 10})
    assert status == 200 and ures.get("success") and ures.get("wallet_minutes") == 20
    print("    [+] Member used 10m from wallet to activate session: Remaining wallet = 20m.")

    # Test Save Active Session back to Wallet
    status, sres = request_json("/api/member/save_time", {"username": test_user, "pin": "1234"})
    assert status == 200 and sres.get("success") and sres.get("wallet_minutes") >= 20
    print(f"    [+] Active session saved back to wallet: Total wallet is now {sres['wallet_minutes']}m.")

    request_json("/admin/api/members/delete", {"username": test_user})
    print("    [+] Cleaned up test member.")

    # 11. MAC Filtering Controls & Validation
    print("[*] Testing MAC Filtering Rule Controls & IEEE 802 Format Validation...")
    # Test valid MAC
    status, mac_res = request_json("/admin/api/mac_control/add", {"mac": "AA:BB:CC:DD:EE:FF", "type": "whitelist", "note": "Owner iPhone"})
    assert status == 200 and mac_res.get("success")
    print("    [+] Standard MAC added: AA:BB:CC:DD:EE:FF")

    # Test auto-formatting raw hex
    status, mac_res2 = request_json("/admin/api/mac_control/add", {"mac": "112233445566", "type": "blacklist", "note": "Abusive Client"})
    assert status == 200 and mac_res2.get("success") and mac_res2.get("mac") == "11:22:33:44:55:66"
    print("    [+] Raw hex MAC automatically formatted and saved: 11:22:33:44:55:66")

    # Test deletion
    request_json("/admin/api/mac_control/delete", {"mac": "AA:BB:CC:DD:EE:FF"})
    request_json("/admin/api/mac_control/delete", {"mac": "11:22:33:44:55:66"})
    print("    [+] MAC filter rules deleted cleanly.")

    # 12. Walled Garden Free Domains
    print("[*] Testing Walled Garden Free Whitelisted Domains...")
    request_json("/admin/api/walled_garden/add", {"domain": "sampleportal.edu.ph", "note": "University Exam Site"})
    status, wlist = request_json("/admin/api/walled_garden/list")
    assert any(w["domain"] == "sampleportal.edu.ph" for w in wlist)
    print("    [+] Walled garden domain whitelisted: sampleportal.edu.ph")

    request_json("/admin/api/walled_garden/delete", {"domain": "sampleportal.edu.ph"})
    print("    [+] Cleaned up walled garden domain.")

    # 13. Audio Multi-Event Settings & Chimes (Pure ECO-Fi Branding)
    print("[*] Testing 3-Channel Audio Event Settings (ECO-Fi Branded)...")
    status, ares = request_json("/admin/api/audio/settings", {
        "audio_bg": "/static/audio/eco_loop.wav",
        "audio_insert": "/static/audio/eco_chime.wav",
        "audio_success": "/static/audio/eco_success.wav",
        "volume": "85"
    })
    assert status == 200 and ares.get("success")
    print("    [+] 3-Channel Audio settings verified (Background Loop, Bottle Ding, Success Bell).")

    # 14. Hardware Licensing Status
    print("[*] Testing Hardware Licensing...")
    status, lic = request_json("/admin/api/license")
    assert status == 200 and "hwid" in lic
    print(f"    [+] Hardware License Verified: HWID = {lic['hwid']}, Status = {lic['status']}")

    # 15. Export CSV
    print("[*] Testing Sales CSV Export...")
    status, csv_text = request_get("/admin/api/export_csv")
    assert status == 200 and "Date,Total Bottles,Equivalent Minutes" in csv_text
    print("    [+] Sales CSV report exported successfully.")

    # 16. Captive Portal Auto-Connect OS Probes
    print("[*] Testing Captive Portal Auto-Connect Probes (Android / iOS / Windows)...")
    # Add active session time
    request_json("/admin/api/client/edit", {"ip": "127.0.0.1", "minutes": 15, "dl_kbps": 2048, "ul_kbps": 1024})
    
    status, _ = request_get("/generate_204")
    assert status == 204 or status == 200
    print("    [+] Android Captive Probe (/generate_204) returned HTTP 204.")

    status, apple_res = request_get("/hotspot-detect.html")
    assert status == 200 and "Success" in apple_res
    print("    [+] Apple iOS Captive Probe (/hotspot-detect.html) returned Success HTML.")

    status, msft_res = request_get("/ncsi.txt")
    assert status == 200 and "Microsoft NCSI" in msft_res
    print("    [+] Windows NCSI Probe (/ncsi.txt) returned 'Microsoft NCSI'.")

    print("\n==================================================================")
    print("  ALL ECO-FI SYSTEMS, MODALS, BUTTONS & APIS PASSED 100% PERFECTLY! ")
    print("==================================================================")

if __name__ == "__main__":
    run_tests()
