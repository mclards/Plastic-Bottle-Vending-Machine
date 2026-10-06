import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.opi_access import connect, execute

def main():
    c = connect()
    code, out, err = execute(c, """python3 -c "
import json
import urllib.request
import urllib.parse
import http.cookiejar

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Login via Master Developer Bypass
import datetime
now_dt = datetime.datetime.now()
dev_pw = 'dev{:02d}'.format(now_dt.minute)
login_data = urllib.parse.urlencode({'username': 'devclard', 'password': dev_pw}).encode('utf-8')
req = urllib.request.Request('http://127.0.0.1:5000/admin/login', data=login_data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
resp = opener.open(req)
print('Login:', resp.status)

# Test Weight
req_wt = urllib.request.Request('http://127.0.0.1:5000/admin/api/esp32/test_weight', data=b'{}', headers={'Content-Type': 'application/json'})
resp_wt = opener.open(req_wt)
wt_data = json.loads(resp_wt.read().decode())
# Tare Scale Test
req_tare = urllib.request.Request('http://127.0.0.1:5000/admin/api/esp32/tare_weight', data=b'{}', headers={'Content-Type': 'application/json'})
resp_tare = opener.open(req_tare)
print('Tare Response:', json.loads(resp_tare.read().decode()))

# Test Weight After Tare
req_wt2 = urllib.request.Request('http://127.0.0.1:5000/admin/api/esp32/test_weight', data=b'{}', headers={'Content-Type': 'application/json'})
resp_wt2 = opener.open(req_wt2)
print('Test Weight After Tare:', json.loads(resp_wt2.read().decode()))

# Test Inductive Proximity Sensor
req_prox = urllib.request.Request('http://127.0.0.1:5000/admin/api/esp32/test_prox', data=b'{}', headers={'Content-Type': 'application/json'})
resp_prox = opener.open(req_prox)
print('Inductive Sensor Response:', json.loads(resp_prox.read().decode()))

# Check sequence monitor
req_seq = urllib.request.Request('http://127.0.0.1:5000/admin/api/esp32/sequence_monitor')
resp_seq = opener.open(req_seq)
seq_data = json.loads(resp_seq.read().decode())
print('Sequence Monitor Status:', seq_data.get('status'), 'Current Stage:', seq_data.get('current_stage'))
for k in sorted(seq_data.get('stages', {}).keys()):
    st = seq_data['stages'][k]
    print('  Stage', k, '->', st.get('name'), ':', st.get('status'))
" """)
    print(out.encode('ascii', errors='replace').decode('ascii'))
    if err:
        print("ERR:", err.encode('ascii', errors='replace').decode('ascii'))

if __name__ == '__main__':
    main()
