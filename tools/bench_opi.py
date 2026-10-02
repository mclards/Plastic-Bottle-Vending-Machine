import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.opi_access import connect

ssh = connect()
script = """
import urllib.request, time

urls = [
    'http://127.0.0.1/hotspot-detect.html',
    'http://127.0.0.1/generate_204',
    'http://127.0.0.1/',
    'http://127.0.0.1/static/banner-main.jpg',
    'http://127.0.0.1/static/time_controls.js'
]

for url in urls:
    times = []
    for _ in range(5):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (iPhone)'})
            res = urllib.request.urlopen(req, timeout=5)
            code = res.getcode()
            data = res.read()
        except urllib.error.HTTPError as e:
            code = e.code
        t1 = time.time()
        times.append((t1 - t0) * 1000)
    avg_t = sum(times) / len(times)
    min_t = min(times)
    print('{:40s} -> code={:d} min={:6.2f}ms avg={:6.2f}ms'.format(url, code, min_t, avg_t))
"""

sftp = ssh.open_sftp()
with sftp.file('/tmp/bench.py', 'w') as f:
    f.write(script.strip())
sftp.close()

_, stdout, stderr = ssh.exec_command("python3 /tmp/bench.py")
print(stdout.read().decode('utf-8'))
err = stderr.read().decode('utf-8')
if err:
    print('ERR:', err)
