"""Measure LAN shaping with a temporary, bounded traffic server on the OPi.

This exercises the physical USB-LAN and IFB paths, not ISP throughput.
"""
import http.client
import json
from pathlib import Path
import shlex
import time
from opi_access import connect, execute, admin_cookie, api

CLIENT = '10.0.7.117'
SERVER = '''from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        size=2*1024*1024
        self.send_response(200); self.send_header('Content-Length',str(size)); self.end_headers()
        block=b'x'*16384
        for i in range(size//len(block)): self.wfile.write(block)
    def do_POST(self):
        remaining=min(int(self.headers.get('Content-Length','0')),2*1024*1024)
        while remaining:
            data=self.rfile.read(min(remaining,16384))
            if not data: break
            remaining-=len(data)
        self.send_response(200); self.send_header('Content-Length','2'); self.end_headers(); self.wfile.write(b'OK')
class Server(ThreadingMixIn,HTTPServer):
    daemon_threads=True
Server(('10.0.0.1',5210),Handler).serve_forever()
'''


def measure(direction):
    conn=http.client.HTTPConnection('10.0.0.1',5210,timeout=40,source_address=(CLIENT,0))
    start=time.monotonic()
    if direction=='download':
        conn.request('GET','/')
        response=conn.getresponse(); n=len(response.read())
    else:
        data=b'x'*(512*1024)
        conn.request('POST','/',body=data)
        response=conn.getresponse(); response.read(); n=len(data)
    elapsed=time.monotonic()-start
    conn.close()
    return {'bytes':n,'seconds':round(elapsed,3),'kbps':round(n*8/elapsed/1000,1)}


def main():
    c=connect(); k=admin_cookie(c); results=[]
    original=next(x for x in api('/admin/api/clients',cookie=k) if x['ip']==CLIENT)
    if original['remaining_seconds']!=0: raise RuntimeError('Test requires zero original credit.')
    server_path='/tmp/ecofi-audit-throughput.py'
    with c.open_sftp() as sftp:
        with sftp.file(server_path,'w') as f: f.write(SERVER)
    code,out,err=execute(c,'systemd-run --unit=ecofi-audit-throughput /usr/bin/timeout 180 /usr/bin/python3 '+shlex.quote(server_path))
    if code: raise RuntimeError(err)
    try:
        for dl,ul in [(3072,1536),(1024,512)]:
            api('/admin/api/client/edit',{'ip':CLIENT,'minutes':3,'dl_kbps':dl,'ul_kbps':ul},cookie=k)
            for direction,limit in [('download',dl),('upload',ul)]:
                r=measure(direction)
                results.append(dict(test=direction,configured_kbps=limit,observed=r,
                                    passed=0.65*limit<=r['kbps']<=1.15*limit))
            print(json.dumps(results[-2:]),flush=True)
    finally:
        api('/admin/api/client/edit',{'ip':CLIENT,'minutes':0,
            'dl_kbps':original['dl_kbps'],'ul_kbps':original['ul_kbps']},cookie=k)
        execute(c,'systemctl stop ecofi-audit-throughput; rm -f /tmp/ecofi-audit-throughput.py')
        c.close()
        Path('audits/2026-09-05-live/live-bandwidth-tests.json').write_text(json.dumps(results,indent=2))


if __name__=='__main__': main()
