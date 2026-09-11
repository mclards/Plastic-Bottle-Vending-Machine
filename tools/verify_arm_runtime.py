"""Read-only release runtime smoke test with disposable application storage.
Run under the image's ARM Python 3.5 interpreter, passing the host source folder.
"""
import atexit
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch

source=sys.argv[1]
with tempfile.TemporaryDirectory(prefix='ecofi-arm-smoke-') as folder:
    for name in os.listdir(source):
        if name.endswith('.py') and not name.startswith('test_'):
            shutil.copy2(os.path.join(source,name),folder)
    version_path=os.path.join(source,'VERSION')
    if not os.path.isfile(version_path):version_path=os.path.join(source,'..','VERSION')
    shutil.copy2(version_path,os.path.join(folder,'VERSION'))
    os.symlink(os.path.join(source,'static'),os.path.join(folder,'static'))
    sys.path.insert(0,folder)
    spec=importlib.util.spec_from_file_location('arm_portal',os.path.join(folder,'portal.py'))
    portal=importlib.util.module_from_spec(spec)
    with patch.object(threading.Thread,'start'):spec.loader.exec_module(portal)
    atexit.unregister(portal.save_sessions_to_db)
    with open(version_path) as stream:assert portal.RELEASE_VERSION==stream.read().strip()
    portal.esp32.running=False;portal.app.testing=True
    if '--live-clock' in sys.argv:
        with patch.dict(os.environ,{'ECOFI_TRUST_CLOCK':'0'}):
            assert portal.time_service.clock_trusted(), 'Actual OPi synchronization check failed'
        print('PASS: actual OPi clock synchronization detected without trust override')
    portal.platform.system=lambda:'Windows'
    portal.time_service.clock_trusted=lambda:True
    portal.license_valid=lambda:True
    portal.get_arp_table=lambda:{'10.0.0.2':'02:00:00:00:00:01'}
    portal.update_firewall=lambda *args,**kwargs:True
    portal.transmit_to_esp32=lambda data:True
    with portal.db_connection() as conn:conn.execute("INSERT INTO walled_garden(domain,note) VALUES ('example.test','operator')")
    with patch.object(portal.platform,'system',return_value='Linux'),patch.object(portal.socket,'getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',0))]),patch.object(portal.gateway_network,'policies') as policies,patch.object(portal.time_service,'worker_pass'):
        portal.apply_walled_garden_and_macs()
        assert policies.call_args[0]==(set(),{'93.184.216.34'})
    client=portal.app.test_client()
    def request(path,data=None):
        return client.post(path,json=data or {},environ_overrides={'REMOTE_ADDR':'10.0.0.2'})
    assert client.get('/admin/api/clients').status_code==401
    for page in ('/','/admin/login'):
        response=client.get(page,environ_overrides={'REMOTE_ADDR':'10.0.0.2'})
        assert response.status_code==200,(page,response.status_code)
    with portal.db_connection() as conn:conn.execute("INSERT INTO vouchers(code,minutes) VALUES ('ARM-CHECK',5)")
    assert request('/api/voucher/redeem',{'code':'ARM-CHECK'}).get_json()['success']
    portal.time_service.worker_pass()
    assert portal.check_client_online('10.0.0.2')
    assert request('/api/client/pause',{'action':'pause'}).get_json()['success']
    assert not portal.check_client_online('10.0.0.2')
    opened=request('/api/open_gate').get_json();assert opened['success'],opened
    event={'event':'CREDIT_ADD','event_id':'arm:1','session_id':opened['deposit_session_id'],'bottles':1,'protocol':2}
    portal.on_esp32_uart_output(json.dumps(event));portal.on_esp32_uart_output(json.dumps(event))
    result=request('/api/vendo/done').get_json();assert result['success'] and result['bottles_credited']==1,result
    with client.session_transaction() as session:session['admin_logged_in']=True
    for path in ('/admin','/admin/api/time/diagnostics','/admin/api/clients','/admin/api/system/backup/download'):
        response=client.get(path,environ_overrides={'REMOTE_ADDR':'10.0.0.2'})
        assert response.status_code==200,(path,response.status_code)
    for path in ('/api/client/switch','/api/member/register','/api/member/login','/api/member/save_time','/api/member/use_wallet',
                 '/admin/api/members/add','/admin/api/members/topup','/admin/api/members/delete'):
        assert request(path).status_code==404,path
    assert client.get('/admin/api/members/list').status_code==404
    for action in ('add15','kick','resume'):
        result=request('/admin/api/client/action',{'ip':'10.0.0.2','action':action}).get_json()
        assert result['success'] and not result['network_pending'],result
        if action=='kick':
            assert request('/api/client/pause',{'action':'resume'}).get_json()['error']=='admin_suspended'
    with open(os.path.join(source,'static','time_controls.js')) as stream:controls=stream.read()
    for token in ('Use Credit','OTHER CREDITS','/api/client/switch'):
        assert token not in controls,token
    for path in ('/','/admin'):
        html=client.get(path).get_data(as_text=True)
        for token in ('tab-member','sec-members','Member Wallet','/api/member/','/admin/api/members/'):
            assert token not in html,(path,token)
    with portal.db_connection() as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='members'").fetchone()[0]==0
    assert client.get('/admin/api/export_xlsx').status_code in (200,302)
    print('PASS: ARM runtime, portal/admin pages, vouchers, pause, bottle receipts, network, diagnostics, backup, exports, and complete Member route/UI removal')
