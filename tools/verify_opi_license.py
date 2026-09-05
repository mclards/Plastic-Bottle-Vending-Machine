"""Brief live missing-license test with restoration owned by the remote process.

Uses only the empty test client's balance. Does not operate the physical gate.
"""
import json
from pathlib import Path
import shlex
from opi_access import connect, admin_cookie, api
from verify_opi_live import internet_connection, fetch, CLIENT

WINDOW='''import os,time,signal
path='/opt/ecofi/license.key'
backup=path+'.audit-20260905'
if os.path.exists(backup): raise RuntimeError('Backup already exists; refusing to overwrite')
def stop(*args): raise SystemExit(1)
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGHUP,stop)
os.rename(path,backup)
try:
    print('READY',flush=True)
    time.sleep(12)
finally:
    if not os.path.exists(path):
        os.rename(backup,path)
        print('RESTORED',flush=True)
    else:
        raise RuntimeError('A new license appeared; original preserved in backup')
'''


def main():
    c=connect(); k=admin_cookie(c); results=[]
    original=next(x for x in api('/admin/api/clients',cookie=k) if x['ip']==CLIENT)
    if original['remaining_seconds']!=0: raise RuntimeError('Requires empty test balance.')
    before=api('/admin/api/license',cookie=k)
    if not before['valid']: raise RuntimeError('Requires a valid license to restore.')
    _,stdout,stderr=c.exec_command('python3 -u -c '+shlex.quote(WINDOW),timeout=25)
    if stdout.readline().strip()!='READY': raise RuntimeError(stderr.read().decode())
    try:
        status=api('/admin/api/license',cookie=k)
        results.append({'test':'missing license is detected live','pass':status['status']=='UNLICENSED','status':status['status']})
        r=api('/admin/api/client/edit',{'ip':CLIENT,'minutes':1,'dl_kbps':3072,'ul_kbps':1536},cookie=k)
        conn=internet_connection()
        try:
            data=fetch(conn)
            results.append({'test':'unlicensed board denies newly granted client internet','pass':False,'observed':data})
        except OSError as e:
            results.append({'test':'unlicensed board denies newly granted client internet','pass':True,'observed':type(e).__name__})
        finally: conn.close()
    finally:
        remote_output=stdout.read().decode(); remote_error=stderr.read().decode()
        after=api('/admin/api/license',cookie=k)
        results.append({'test':'original license restored','pass':after==before,'remote':remote_output.strip(),'error':remote_error})
        api('/admin/api/client/edit',{'ip':CLIENT,'minutes':0,'dl_kbps':original['dl_kbps'],'ul_kbps':original['ul_kbps']},cookie=k)
        c.close()
        Path('audits/2026-09-05-live/live-license-tests.json').write_text(json.dumps(results,indent=2))
        print(json.dumps(results,indent=2))


if __name__=='__main__': main()
