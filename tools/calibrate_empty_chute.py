import sys
import os
import json
import time

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
from tools.opi_access import connect, execute, admin_cookie

def run_multi_scan(iterations=5):
    c = connect()
    cookie = admin_cookie(c)
    results = []

    print(f"Starting {iterations} consecutive baseline scans of the empty chute (Orange PVC)...")
    print("-" * 85)
    print(" Scan |  R(610) |  S(680) |  T(730) |  U(760) |  V(810) |  W(860) | Cal-W(uW) |  W/V  |  S/R  |  T/W ")
    print("-" * 85)

    py = "import urllib.request; req=urllib.request.Request('http://127.0.0.1:5000/admin/api/esp32/test_nir', data=b'{}', headers={'Cookie': 'session=%s', 'Content-Type': 'application/json'}); print(urllib.request.urlopen(req, timeout=4).read().decode())" % cookie

    for i in range(1, iterations + 1):
        code, out, _ = execute(c, "python3 -c " + repr(py))
        try:
            data = json.loads(out.strip())
            r = data.get("r", 0)
            s = data.get("s", 0)
            t = data.get("t", 0)
            u = data.get("u", 0)
            v = data.get("v", 0)
            w = data.get("w", 0)
            cal_w = data.get("calibrated_w", 0.0)
            wv = w / v if v else 0
            sr = s / r if r else 0
            tw = t / w if w else 0
            results.append((r, s, t, u, v, w, cal_w, wv, sr, tw))
            print(f"  #{i:02d} | {r:7d} | {s:7d} | {t:7d} | {u:7d} | {v:7d} | {w:7d} | {cal_w:9.2f} | {wv:5.2f} | {sr:5.2f} | {tw:5.2f}")
        except Exception as e:
            print(f"  #{i:02d} | Error: {e} -> {out.strip()[:60]}")
        if i < iterations:
            time.sleep(1.0)

    c.close()
    print("-" * 85)

    if results:
        import math
        def stats(vals):
            m = sum(vals) / len(vals)
            var = sum((x - m) ** 2 for x in vals) / len(vals)
            return min(vals), max(vals), m, math.sqrt(var)

        print("-" * 85)
        keys = ["R", "S", "T", "U", "V", "W", "Cal-W", "W/V", "S/R", "T/W"]
        print(" STATS SUMMARY (20 SCANS):")
        print(" Channel/Ratio |     MIN |     MAX |    MEAN |  STDDEV ")
        print("---------------+---------+---------+---------+---------")
        for idx, k in enumerate(keys):
            vals = [x[idx] for x in results]
            mn, mx, mean, sd = stats(vals)
            print(f" {k:13s} | {mn:7.2f} | {mx:7.2f} | {mean:7.2f} | {sd:7.2f}")
        print("=" * 85)

if __name__ == '__main__':
    run_multi_scan(20)
