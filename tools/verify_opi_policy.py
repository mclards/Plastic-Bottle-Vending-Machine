"""Live DNS, captive response, identity, and MAC policy probes for the test PC."""
import http.client
import json
from pathlib import Path
import socket
import struct
from opi_access import connect, admin_cookie, api, execute
from verify_opi_live import internet_connection, fetch, CLIENT


def request(path,port=80,headers=None):
    conn=http.client.HTTPConnection('10.0.0.1',port,timeout=5,source_address=(CLIENT,0))
    try:
        conn.request('GET',path,headers=headers or {})
        r=conn.getresponse()
        return r.status,dict(r.getheaders()),r.read().decode(errors='replace')
    finally:conn.close()


def dns_a(name):
    labels=b''.join(bytes([len(x)])+x.encode() for x in name.split('.'))+b'\0'
    payload=struct.pack('!6H',0xEC0F,0x100,1,0,0,0)+labels+struct.pack('!HH',1,1)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
        s.bind((CLIENT,0));s.settimeout(4);s.sendto(payload,('10.0.0.1',53));raw=s.recv(4096)
    return {'answers':struct.unpack('!H',raw[6:8])[0],
            'ends_with_gateway_A':raw.endswith(socket.inet_aton('10.0.0.1'))}


def main():
    c=connect();k=admin_cookie(c);results=[]
    original=next(x for x in api('/admin/api/clients',cookie=k) if x['ip']==CLIENT)
    if original['remaining_seconds']!=0: raise RuntimeError('Requires zero test-client balance.')
    controls=api('/admin/api/mac_control/list',cookie=k)
    mac=original['mac']
    if any(x['mac'].lower()==mac.lower() for x in controls): raise RuntimeError('Test MAC already has a policy; refusing to overwrite it.')
    block_added=False
    try:
        for path in ['/admin/api/clients','/simulator/api/state']:
            status,_,_=request(path)
            results.append({'test':path+' requires authentication','pass':status==401,'status':status})
        api('/admin/api/client/edit',{'ip':CLIENT,'minutes':1},cookie=k)
        r=dns_a('example.com')
        results.append({'test':'paid-client DNS resolves public website','pass':not r['ends_with_gateway_A'],'observed':r})
        status,headers,_=request('/generate_204')
        results.append({'test':'paid-client Android probe reports online through nginx','pass':status==204,'status':status,'location':headers.get('Location')})
        status,_,_=request('/generate_204',port=5000)
        results.append({'test':'backend paid probe','pass':status==204,'status':status,
                        'note':'Backend is directly reachable from LAN; this bypasses nginx.'})
        normal=api('/api/status')['remaining_seconds']
        _,_,body=request('/api/status',headers={'X-Forwarded-For':'10.0.7.118'})
        other=json.loads(body)['remaining_seconds']
        results.append({'test':'supplied X-Forwarded-For cannot change client identity','pass':abs(normal-other)<=2,
                        'actual_pc_seconds':normal,'with_forged_header_seconds':other})
        api('/admin/api/mac_control/add',{'mac':mac,'type':'block','note':'Temporary audit test'},cookie=k)
        block_added=True
        conn=internet_connection()
        try:
            r=fetch(conn)
            results.append({'test':'admin MAC block stops internet immediately','pass':False,'observed':r})
        except OSError as e:
            results.append({'test':'admin MAC block stops internet immediately','pass':True,'observed':type(e).__name__})
        finally:conn.close()
    finally:
        if block_added:
            api('/admin/api/mac_control/delete',{'mac':mac},cookie=k)
            # Hourly refresh might have run during this test. Remove only our temporary MAC rule.
            execute(c,'iptables -D ECOFI_MAC_BLOCK -m mac --mac-source '+mac+' -j DROP 2>/dev/null || true')
        api('/admin/api/client/edit',{'ip':CLIENT,'minutes':0,'dl_kbps':original['dl_kbps'],'ul_kbps':original['ul_kbps']},cookie=k)
        c.close()
        Path('audits/2026-09-05-live/live-policy-tests.json').write_text(json.dumps(results,indent=2))
        print(json.dumps(results,indent=2))


if __name__=='__main__':main()
