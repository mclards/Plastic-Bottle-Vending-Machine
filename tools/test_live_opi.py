import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from opi_access import connect

def main():
    print("Connecting to live Orange Pi...")
    client = connect()
    
    test_code = """
import urllib.request
import urllib.parse
import json
import http.cookiejar
import time
import datetime

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# 1. Login to admin using developer bypass
now_dt = datetime.datetime.now()
dev_pw = 'dev{:02d}'.format(now_dt.minute)
login_payload = urllib.parse.urlencode({'username': 'devclard', 'password': dev_pw}).encode('utf-8')
req = urllib.request.Request('http://127.0.0.1:5000/admin/login', data=login_payload)
res = opener.open(req)
print('ADMIN_LOGIN_STATUS:', res.getcode())
print('COOKIES:', [c.name for c in cj])

# 2. Test sequence monitor (Initial)
req2 = urllib.request.Request('http://127.0.0.1:5000/admin/api/esp32/sequence_monitor')
res2 = opener.open(req2)
d2 = json.loads(res2.read().decode('utf-8'))
print('SEQUENCE_MONITOR_STATUS:', d2.get('success'))
print('CURRENT_STAGE:', d2.get('data', {}).get('current_stage'))

# 3. Trigger simulation scan
sim_payload = json.dumps({'is_pet': True, 'weight': 34.2, 'cal_w': 48.6}).encode('utf-8')
req3 = urllib.request.Request('http://127.0.0.1:5000/admin/api/esp32/sequence/simulate', data=sim_payload, headers={'Content-Type': 'application/json'})
res3 = opener.open(req3)
print('SIMULATE_STATUS:', res3.getcode())

# Wait for sequence to complete (400ms * 6 + tolerances ~= 2.8s)
time.sleep(3.0)

# 4. Check sequence monitor after simulation
res2_after = opener.open(req2)
d2_after = json.loads(res2_after.read().decode('utf-8'))
data = d2_after.get('data', {})
print('SEQUENCE_STATUS_AFTER_SIM:', data.get('status'))
print('SEQUENCE_ELAPSED_MS:', data.get('total_elapsed_ms'))
stages = data.get('stages', {})
for sk in sorted(stages.keys()):
    st = stages[sk]
    print('  Stage', sk, '->', st.get('status'), '(', st.get('elapsed_ms'), 'ms ) detail:', st.get('detail'))

# 5. Fetch system event logs
req4 = urllib.request.Request('http://127.0.0.1:5000/admin/api/system/logs?limit=15')
res4 = opener.open(req4)
d4 = json.loads(res4.read().decode('utf-8'))
print('LOG_ENTRIES_COUNT:', d4.get('count'))
print('LATEST_LOGS_SAMPLE:')
for e in d4.get('entries', [])[-7:]:
    print(' ', e.get('timestamp'), '[{}]'.format(e.get('level')), '[{}]'.format(e.get('category')), e.get('message'))

# 6. Test download endpoint
req5 = urllib.request.Request('http://127.0.0.1:5000/admin/api/system/logs/download')
res5 = opener.open(req5)
print('DOWNLOAD_ENDPOINT_STATUS:', res5.getcode(), 'bytes:', len(res5.read()))
"""

    sftp = client.open_sftp()
    with sftp.open('/tmp/test_sequence.py', 'w') as f:
        f.write(test_code)
    sftp.close()

    stdin, stdout, stderr = client.exec_command('python3 /tmp/test_sequence.py')
    out = stdout.read().decode('utf-8')
    err = stderr.read().decode('utf-8')
    client.exec_command('rm -f /tmp/test_sequence.py')
    client.close()

    print("=== LIVE OPI TEST OUTPUT ===")
    print(out)
    if err:
        print("=== STDERR ===")
        print(err)

if __name__ == '__main__':
    main()
