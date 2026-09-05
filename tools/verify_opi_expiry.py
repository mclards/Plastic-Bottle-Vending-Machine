"""Observe a real one-minute session ending on this PC's LAN connection."""
import json
import time
from pathlib import Path
from opi_access import connect, admin_cookie, api
from verify_opi_live import internet_connection, fetch, CLIENT

def main():
    c=connect();k=admin_cookie(c);results=[]
    original=next(x for x in api('/admin/api/clients',cookie=k) if x['ip']==CLIENT)
    if original['remaining_seconds']!=0: raise RuntimeError('Requires zero test balance.')
    conn=internet_connection()
    try:
        api('/admin/api/client/edit',{'ip':CLIENT,'minutes':1},cookie=k)
        start=time.monotonic()
        print('Observing one-minute session expiration...',flush=True)
        while time.monotonic()-start<75:
            status=api('/api/status')
            if status['remaining_seconds']==0: break
            fetch(conn)
            time.sleep(2)
        elapsed=time.monotonic()-start
        results.append({'test':'one-minute credit expires','pass':status['remaining_seconds']==0 and 58<=elapsed<=66,
                        'elapsed_seconds':round(elapsed,2),'remaining_seconds':status['remaining_seconds']})
        try:
            r=fetch(conn)
            results.append({'test':'expiry stops established HTTPS','pass':False,'observed':r})
        except OSError as e:
            results.append({'test':'expiry stops established HTTPS','pass':True,'observed':type(e).__name__})
        conn.close();conn=internet_connection()
        try:
            r=fetch(conn)
            results.append({'test':'expiry blocks new HTTPS','pass':False,'observed':r})
        except OSError as e:
            results.append({'test':'expiry blocks new HTTPS','pass':True,'observed':type(e).__name__})
    finally:
        conn.close()
        api('/admin/api/client/edit',{'ip':CLIENT,'minutes':0,'dl_kbps':original['dl_kbps'],'ul_kbps':original['ul_kbps']},cookie=k)
        c.close()
        Path('audits/2026-09-05-live/live-expiry-tests.json').write_text(json.dumps(results,indent=2))
        print(json.dumps(results,indent=2))

if __name__=='__main__':main()
