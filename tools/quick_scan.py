import sys
import os
import urllib.request
import json
import time

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
from tools.opi_access import connect, execute, admin_cookie

def main():
    t0 = time.time()
    c = connect()
    cookie = admin_cookie(c)
    py = "import urllib.request; req=urllib.request.Request('http://127.0.0.1:5000/admin/api/esp32/test_nir', data=b'{}', headers={'Cookie': 'session=%s', 'Content-Type': 'application/json'}); print(urllib.request.urlopen(req, timeout=3).read().decode())" % cookie
    code, out, _ = execute(c, "python3 -c " + repr(py))
    c.close()
    elapsed = time.time() - t0
    data = json.loads(out.strip())
    print("ELAPSED: %.2fs" % elapsed)
    print("IS_PET: %s (%s)" % (data.get("is_pet"), data.get("reason", "unknown")))
    print("CAL_W: %.2f uW/cm2" % data.get("calibrated_w", 0))
    print("RATIOS: W/V=%.2f, S/R=%.2f, T/W=%.2f" % (data.get("wv_ratio", 0), data.get("sr_ratio", 0), data.get("tw_ratio", 0)))
    print("RAW: R=%d, S=%d, T=%d, U=%d, V=%d, W=%d" % (data.get("r",0), data.get("s",0), data.get("t",0), data.get("u",0), data.get("v",0), data.get("w",0)))

if __name__ == '__main__':
    main()
