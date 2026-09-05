"""Isolated regressions for gateway behavior. Never imports the working DB.

Failing tests are audit findings, not a claim of successful live verification.
Run: python -m unittest discover -s tools -p test_gateway_audit.py -v
"""
import atexit
import gc
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


class GatewayAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='ecofi-audit-')
        cls.folder=Path(cls.tmp.name)
        for path in (ROOT/'host').glob('*.py'):
            shutil.copy2(path, cls.folder/path.name)
        sys.path.insert(0,str(cls.folder))
        spec=importlib.util.spec_from_file_location('gateway_under_audit',cls.folder/'portal.py')
        cls.p=importlib.util.module_from_spec(spec)
        with patch.object(threading.Thread,'start'):
            spec.loader.exec_module(cls.p)
        cls.p.esp32.running=False
        cls.p.app.testing=True
        atexit.unregister(cls.p.save_sessions_to_db)
        cls.p.license_manager.LICENSE_FILE=str(cls.folder/'license.key')

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(cls.folder))
        gc.collect()
        cls.tmp.cleanup()

    def setUp(self):
        p=self.p
        p.active_clients.clear(); p.active_depositor_ip=None; p.active_depositor_timeout=0
        p.esp32.reset_session(); p.esp32.is_bin_full=False
        self.license_patch=patch.object(p.license_manager,'verify_license',return_value={'valid':True,'status':'ACTIVATED'})
        self.license_patch.start(); self.addCleanup(self.license_patch.stop)
        self.fw=patch.object(p,'update_firewall').start(); self.addCleanup(patch.stopall)
        self.arp=patch.object(p,'get_arp_table',return_value={'10.0.7.117':'00:00:00:00:02:43',
            '10.0.7.118':'02:00:00:00:00:02'}).start()
        with sqlite3.connect(p.DB_PATH) as c:
            for table in ['active_sessions','vouchers','time_transfers','members','stats','mac_control']:
                c.execute('DELETE FROM '+table) if c.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone() else None
        p.set_config('hw_bin_full','0'); p.set_config('auto_pause_disconnect','0')
        self.a=p.app.test_client(); self.b=p.app.test_client()

    def post(self,client,path,data=None,ip='10.0.7.117',headers=None):
        return client.post(path,json=data or {},environ_overrides={'REMOTE_ADDR':ip},headers=headers or {})

    def sess(self,ip='10.0.7.117',seconds=600):
        s=self.p.ensure_client_session(ip); s['remaining_seconds']=seconds; return s

    def voucher(self,code='AUDIT',minutes=10):
        with sqlite3.connect(self.p.DB_PATH) as c:
            c.execute('INSERT INTO vouchers(code,minutes) VALUES (?,?)',(code,minutes))

    def login_admin(self):
        with self.a.session_transaction() as s: s['admin_logged_in']=True

    def test_admin_api_requires_authentication(self):
        self.assertEqual(self.a.get('/admin/api/clients').status_code,401)

    def test_simulator_requires_authentication(self):
        self.assertEqual(self.post(self.a,'/simulator/api/trigger',{'item_type':'valid_pet'}).status_code,401)

    def test_default_admin_requires_password_change(self):
        self.a.post('/admin/login',data={'username':'admin','password':'admin123'})
        self.assertEqual(self.a.get('/admin/api/clients').status_code,403)

    def test_per_client_credit_isolation(self):
        self.sess(seconds=600)
        r=self.b.get('/api/status',environ_overrides={'REMOTE_ADDR':'10.0.7.118'})
        self.assertEqual(r.json['remaining_seconds'],0)

    def test_forwarded_header_cannot_impersonate_another_client(self):
        self.sess(seconds=600)
        r=self.b.get('/api/status',environ_overrides={'REMOTE_ADDR':'10.0.7.118'},
                     headers={'X-Forwarded-For':'10.0.7.117'})
        self.assertEqual(r.json['remaining_seconds'],0)

    def test_missing_license_blocks_existing_client_grant(self):
        self.sess()
        with patch.object(self.p.license_manager,'verify_license',return_value={'valid':False,'status':'UNLICENSED'}):
            self.p.sync_client_firewall('10.0.7.117')
        self.assertFalse(any(x.args[1]=='add' for x in self.fw.call_args_list))

    def test_missing_license_blocks_gate_open(self):
        with patch.object(self.p.license_manager,'verify_license',return_value={'valid':False,'status':'UNLICENSED'}):
            r=self.post(self.a,'/api/open_gate')
        self.assertFalse(r.json.get('success',False))

    def test_missing_license_blocks_voucher_redemption(self):
        self.voucher()
        with patch.object(self.p.license_manager,'verify_license',return_value={'valid':False,'status':'UNLICENSED'}):
            r=self.post(self.a,'/api/voucher/redeem',{'code':'AUDIT'})
        self.assertFalse(r.json.get('success',False))

    def test_manual_pause_revokes_and_preserves_balance(self):
        s=self.sess()
        self.post(self.a,'/api/client/pause',{'action':'pause'})
        self.assertTrue(s['is_paused']); self.assertEqual(s['remaining_seconds'],600)
        self.fw.assert_called_with('10.0.7.117','del')

    def test_expired_paused_credit_cannot_be_resumed_before_next_tick(self):
        s=self.sess(); s.update(is_paused=True,expires_at=time.time()-1)
        self.post(self.a,'/api/client/pause',{'action':'resume'})
        self.assertFalse(any(x.args[1]=='add' for x in self.fw.call_args_list))

    def test_admin_pause_cannot_be_undone_by_client(self):
        self.sess(); self.login_admin()
        self.post(self.a,'/admin/api/client/action',{'ip':'10.0.7.117','action':'pause'})
        self.post(self.a,'/api/client/pause',{'action':'resume'})
        self.assertTrue(self.p.active_clients['10.0.7.117']['is_paused'])

    def test_mac_reassignment_does_not_inherit_credit(self):
        self.sess()
        self.arp.return_value={'10.0.7.117':'02:99:99:99:99:99'}
        self.assertEqual(self.p.ensure_client_session('10.0.7.117')['remaining_seconds'],0)

    def test_roaming_revokes_old_ip(self):
        self.sess()
        self.arp.return_value={'10.0.7.118':'00:00:00:00:02:43'}
        self.p.ensure_client_session('10.0.7.118')
        self.assertTrue(any(x.args[:2]==('10.0.7.117','del') for x in self.fw.call_args_list))

    def test_two_depositors_cannot_own_gate_simultaneously(self):
        self.assertTrue(self.post(self.a,'/api/open_gate').json['success'])
        self.assertFalse(self.post(self.b,'/api/open_gate',ip='10.0.7.118').json['success'])

    def test_real_uart_bottle_awards_time(self):
        self.post(self.a,'/api/open_gate')
        self.p.on_esp32_uart_output(json.dumps({'event':'CREDIT_ADD','bottles':1,'sessionTotal':1}))
        r=self.post(self.a,'/api/vendo/done')
        self.assertEqual(r.json['added_minutes'],10)

    def test_duplicate_uart_event_not_counted_twice(self):
        self.post(self.a,'/api/open_gate')
        event=json.dumps({'event':'CREDIT_ADD','bottles':1,'sessionTotal':1})
        self.p.on_esp32_uart_output(event); self.p.on_esp32_uart_output(event)
        with sqlite3.connect(self.p.DB_PATH) as c:
            self.assertEqual(c.execute('SELECT SUM(total_bottles) FROM stats').fetchone()[0],1)

    def test_bin_full_blocks_gate(self):
        self.p.set_config('hw_bin_full','1')
        self.assertFalse(self.post(self.a,'/api/open_gate').json['success'])

    def test_promo_rates(self):
        self.assertEqual([self.p.calculate_minutes_for_bottles(n) for n in [0,1,3,5,10]], [0,10,40,75,180])

    def test_voucher_is_single_use_sequentially(self):
        self.voucher()
        self.assertTrue(self.post(self.a,'/api/voucher/redeem',{'code':'AUDIT'}).json['success'])
        self.assertFalse(self.post(self.b,'/api/voucher/redeem',{'code':'AUDIT'},ip='10.0.7.118').json['success'])

    def test_redeemed_voucher_credit_is_durable_immediately(self):
        self.voucher()
        self.post(self.a,'/api/voucher/redeem',{'code':'AUDIT'})
        self.p.active_clients.clear(); self.p.restore_sessions_from_db()
        self.assertEqual(self.p.active_clients.get('10.0.7.117',{}).get('remaining_seconds'),600)

    def test_custom_bandwidth_survives_restart(self):
        s=self.sess(); s.update(dl_kbps=5120,ul_kbps=1024)
        self.p.save_sessions_to_db(); self.p.active_clients.clear(); self.p.restore_sessions_from_db()
        s=self.p.active_clients['10.0.7.117']
        self.assertEqual((s['dl_kbps'],s['ul_kbps']),(5120,1024))

    def test_restore_retains_durable_record(self):
        self.sess(); self.p.save_sessions_to_db(); self.p.active_clients.clear(); self.p.restore_sessions_from_db()
        self.p.active_clients.clear(); self.p.restore_sessions_from_db()
        self.assertIn('10.0.7.117',self.p.active_clients)

    def test_pause_reason_survives_restart(self):
        s=self.sess(); s.update(is_paused=True,user_paused=True)
        self.p.save_sessions_to_db(); self.p.active_clients.clear(); self.p.restore_sessions_from_db()
        self.assertTrue(self.p.active_clients['10.0.7.117'].get('user_paused',False))

    def test_timer_accounts_for_elapsed_time_when_loop_is_delayed(self):
        s=self.sess(seconds=100); now=[1000.0]
        class StopClock(Exception): pass
        def sleep(_):
            if now[0]>=1010: raise StopClock()
            now[0]+=5
        with patch.object(self.p.time,'time',side_effect=lambda:now[0]), \
             patch.object(self.p.time,'monotonic',side_effect=lambda:now[0]), \
             patch.object(self.p.time,'sleep',side_effect=sleep):
            with self.assertRaises(StopClock): self.p.time_daemon()
        self.assertEqual(s['remaining_seconds'],90)

    def test_auto_pause_when_last_client_disconnects(self):
        self.p.set_config('auto_pause_disconnect','1'); self.arp.return_value={}
        s=self.sess(); ticks=[0]
        class StopClock(Exception): pass
        def sleep(_):
            ticks[0]+=1
            if ticks[0]>=60: raise StopClock()
        with patch.object(self.p.platform,'system',return_value='Linux'), \
             patch.object(self.p.subprocess,'run'), \
             patch.object(self.p.time,'sleep',side_effect=sleep):
            with self.assertRaises(StopClock): self.p.time_daemon()
        self.assertTrue(s['is_paused'])

    def test_admin_bandwidth_edit_updates_enforcement(self):
        self.sess(); self.login_admin()
        r=self.post(self.a,'/admin/api/client/edit',{'ip':'10.0.7.117','dl_kbps':1024,'ul_kbps':512})
        self.assertTrue(r.json['success'])
        self.fw.assert_called_with('10.0.7.117','add',600,1024,512)

    def test_mac_block_change_applied_immediately(self):
        self.login_admin()
        with patch.object(self.p,'apply_walled_garden_and_macs') as apply:
            self.post(self.a,'/admin/api/mac_control/add',{'mac':'00:00:00:00:02:43','type':'block'})
            self.assertTrue(apply.called)

    def test_transfer_conserves_balance_and_is_single_use(self):
        self.sess(seconds=600)
        r=self.post(self.a,'/api/transfer/generate',{'minutes':3}).json
        claimed=self.post(self.b,'/api/transfer/claim',{'code':r['code']},ip='10.0.7.118').json
        self.assertTrue(claimed['success'])
        self.assertEqual(sum(x['remaining_seconds'] for x in self.p.active_clients.values()),600)
        self.assertFalse(self.post(self.b,'/api/transfer/claim',{'code':r['code']},ip='10.0.7.118').json['success'])

    def test_member_wallet_round_trip(self):
        self.sess(seconds=600)
        self.assertTrue(self.post(self.a,'/api/member/register',{'username':'auditmember','pin':'1234'}).json['success'])
        self.assertTrue(self.post(self.a,'/api/member/save_time',{'username':'auditmember','pin':'1234'}).json['success'])
        self.assertTrue(self.post(self.a,'/api/member/use_wallet',{'username':'auditmember','pin':'1234','minutes':10}).json['success'])
        self.assertEqual(self.p.active_clients['10.0.7.117']['remaining_seconds'],600)

    def concurrent_claims(self,query,path,data):
        barrier=threading.Barrier(2)
        class AuditCursor(sqlite3.Cursor):
            def execute(self, sql, parameters=()):
                self.watch=sql.startswith(query)
                return super().execute(sql,parameters)
            def fetchone(self):
                value=super().fetchone()
                if getattr(self,'watch',False): barrier.wait(timeout=5)
                return value
        class AuditConnection(sqlite3.Connection):
            def cursor(self,*args,**kwargs):
                return super().cursor(factory=AuditCursor)
        original_connect=sqlite3.connect
        def connect(*args,**kwargs):
            kwargs['factory']=AuditConnection
            return original_connect(*args,**kwargs)
        results=[]
        def worker(ip):
            try:
                results.append(self.post(self.p.app.test_client(),path,data,ip=ip).json)
            except Exception as e: results.append({'exception':str(e)})
        with patch.object(self.p.sqlite3,'connect',side_effect=connect):
            threads=[threading.Thread(target=worker,args=(ip,)) for ip in ['10.0.7.117','10.0.7.118']]
            for t in threads:t.start()
            for t in threads:t.join(10)
        return results

    def test_voucher_concurrent_redemption_only_one_wins(self):
        self.voucher()
        results=self.concurrent_claims('SELECT minutes, is_used FROM vouchers','/api/voucher/redeem',{'code':'AUDIT'})
        self.assertEqual(sum(x.get('success',False) for x in results),1,results)

    def test_transfer_concurrent_claim_only_one_wins(self):
        with sqlite3.connect(self.p.DB_PATH) as c:
            c.execute('INSERT INTO time_transfers(code,seconds,created_at) VALUES (?,?,?)',('123456',600,time.time()))
        results=self.concurrent_claims('SELECT seconds, is_claimed FROM time_transfers','/api/transfer/claim',{'code':'123456'})
        self.assertEqual(sum(x.get('success',False) for x in results),1,results)

    def test_wallet_concurrent_withdrawal_cannot_overspend(self):
        self.post(self.a,'/api/member/register',{'username':'auditmember','pin':'1234'})
        with sqlite3.connect(self.p.DB_PATH) as c:
            c.execute('UPDATE members SET wallet_minutes=10 WHERE username=?',('auditmember',))
        results=self.concurrent_claims('SELECT pin_hash, wallet_minutes FROM members','/api/member/use_wallet',
                                      {'username':'auditmember','pin':'1234','minutes':10})
        self.assertEqual(sum(x.get('success',False) for x in results),1,results)


class LicenseAudit(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0,str(ROOT/'host'))
        import license_manager
        self.l=license_manager
        self.tmp=tempfile.TemporaryDirectory(prefix='ecofi-license-audit-')
        self.addCleanup(self.tmp.cleanup);self.addCleanup(sys.path.pop,0)
        patch.object(self.l,'LICENSE_FILE',str(Path(self.tmp.name)/'license.key')).start()
        patch.object(self.l,'get_machine_hwid',return_value='ECOFI-1111-2222-3333-4444').start()
        self.addCleanup(patch.stopall)

    def status(self,**overrides):
        hwid=self.l.get_machine_hwid()
        data={'machine_hwid':hwid,'tier':'COMMERCIAL','activation_key':self.l.compute_activation_pin(hwid,'COMMERCIAL'),'expiry_date':'PERPETUAL'}
        data.update(overrides)
        Path(self.l.LICENSE_FILE).write_text(json.dumps(data))
        return self.l.verify_license()

    def test_missing_license_reports_unlicensed(self):
        self.assertEqual(self.l.verify_license()['status'],'UNLICENSED')
    def test_valid_perpetual_license_accepted(self):
        self.assertTrue(self.status()['valid'])
    def test_wrong_board_rejected(self):
        self.assertEqual(self.status(machine_hwid='OTHER')['status'],'CLONED_HARDWARE_MISMATCH')
    def test_wrong_signature_rejected(self):
        self.assertEqual(self.status(activation_key='BAD')['status'],'CORRUPTED_SIGNATURE')
    def test_expired_license_rejected(self):
        self.assertEqual(self.status(expiry_date='2000-01-01')['status'],'EXPIRED')
    def test_invalid_expiry_fails_closed(self):
        self.assertFalse(self.status(expiry_date='not-a-date')['valid'])


if __name__=='__main__': unittest.main(verbosity=2)
