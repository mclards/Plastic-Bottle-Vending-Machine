# -*- coding: utf-8 -*-
"""Isolated credit, migration and actual Flask regressions. Python 3.5 compatible.

Run: python -B -m unittest discover -s host -p 'test_*.py' -v
Never imports a portal against the repository's customer database.
"""
from contextlib import contextmanager
import atexit
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import time_schema as s
import transition_engine as e
import migrate_legacy_sessions as migration


@contextmanager
def database(path):
    c=sqlite3.connect(path)
    try:
        with c:yield c
    finally:c.close()


class EngineRegression(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:',isolation_level=None);self.c.execute('PRAGMA foreign_keys=ON');s.init_time_schema(self.c)
        self.now=100000.;self.mono=100.;self.seq=0
        self.owner=e.get_or_create_owner(self.c,'device','mac:02:00:00:00:00:01',self.now)
        self.cd=e.get_or_create_connection(self.c,'10.0.0.2','02:00:00:00:00:01',self.owner,self.now,self.mono)
        self.addCleanup(self.c.close)

    def op(self,action,payload=None,op=None,cd=None,owner=None):
        self.seq+=1
        return e.apply_operation(self.c,owner or self.owner,cd or self.cd,action,payload or {},op or 'op:'+str(self.seq),self.now,self.mono)

    def mint(self,seconds=600):
        r=self.op('TOP_UP_GRANT',{'seconds':seconds});self.assertTrue(r['success'],r);return r['grant_id']

    def allow(self):
        intents=e.request_network_intents(self.c,self.now,self.mono,True)
        for intent in intents:e.acknowledge_network(self.c,intent,self.now,self.mono,True)

    def advance(self,seconds):
        self.now+=seconds;self.mono+=seconds

    def conserved(self):
        self.assertEqual(self.c.execute('SELECT COALESCE(SUM(delta_us),0) FROM time_ledger').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM time_ledger WHERE after_us<>before_us+delta_us').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM ledger_accounts a JOIN time_grants g ON a.grant_id=g.id WHERE a.balance_us<>g.remaining_us').fetchone()[0],0)

    def test_zero_and_fractional_elapsed_exact(self):
        gid=self.mint();self.allow();e.check_due_events(self.c,self.now,self.mono)
        self.assertEqual(e.grant(self.c,gid)['remaining_us'],600000000)
        for step in range(20):
            self.advance(.0005);e.check_due_events(self.c,self.now,self.mono)
        self.assertEqual(e.grant(self.c,gid)['remaining_us'],599990000);self.conserved()

    def test_stale_dictionary_cannot_double_bill(self):
        gid=self.mint();self.allow();self.advance(1);e.settle_connection_balance(self.c,self.cd,self.now,self.mono)
        self.advance(1);e.settle_connection_balance(self.c,self.cd,self.now,self.mono)
        self.assertEqual(e.grant(self.c,gid)['remaining_us'],598000000)

    def test_no_billing_without_network_ack_and_bounded_lease(self):
        gid=self.mint();self.advance(30);e.check_due_events(self.c,self.now,self.mono)
        self.assertEqual(e.grant(self.c,gid)['remaining_us'],600000000)
        self.allow();self.advance(30);e.check_due_events(self.c,self.now,self.mono)
        self.assertEqual(e.grant(self.c,gid)['remaining_us'],585000000)

    def test_commit_failure_rolls_back_grant_and_retry(self):
        self.c.execute("CREATE TRIGGER fail_result BEFORE INSERT ON value_operations BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.op('TOP_UP_GRANT',{'seconds':600},'same')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM time_grants').fetchone()[0],0)
        self.c.execute('DROP TRIGGER fail_result');self.op('TOP_UP_GRANT',{'seconds':600},'same');self.op('TOP_UP_GRANT',{'seconds':600},'same')
        self.assertEqual(self.c.execute('SELECT SUM(issued_us) FROM time_grants').fetchone()[0],600000000);self.conserved()

    def test_outer_transaction_can_rollback_successful_operation(self):
        with self.assertRaises(ValueError):
            with s.transaction(self.c):self.mint();raise ValueError('source write failed')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM time_grants').fetchone()[0],0)

    def test_replay_conflict_and_foreign_owner_do_not_mutate(self):
        self.op('TOP_UP_GRANT',{'seconds':600},'same')
        result=self.op('PAUSE',{},'same');self.assertEqual(result['error'],'operation_id_conflict')
        foreign=e.get_or_create_owner(self.c,'member','other',self.now)
        result=self.op('PAUSE',owner=foreign);self.assertFalse(result['success'])
        self.assertEqual(self.c.execute('SELECT SUM(used_count) FROM pause_budgets').fetchone()[0],0)

    def test_three_pauses_duplicate_timeout_and_new_purchase_budget(self):
        gid=self.mint()
        for count in range(3):
            self.assertTrue(self.op('PAUSE')['success']);self.assertTrue(self.op('PAUSE')['already_paused']);self.assertTrue(self.op('RESUME')['success'])
        self.assertEqual(self.op('PAUSE')['error'],'pause_limit_reached')
        self.op('ADMIN_SET_BALANCE',{'seconds':0});self.mint()
        self.assertTrue(self.op('PAUSE')['success']);self.advance(3600)
        result=self.op('RESUME');self.assertTrue(result['success']);self.assertGreater(result['remaining_seconds'],0)

    def test_zero_cap_and_budget_cap_are_authoritative(self):
        self.c.execute('UPDATE time_policy_versions SET pause_count_max=0 WHERE id=?',('pisofi_time_v1',))
        self.mint();self.assertEqual(self.op('PAUSE')['error'],'pause_limit_reached')

    def test_policy_snapshot_is_immutable(self):
        self.mint()
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute('UPDATE time_policy_versions SET pause_count_max=9 WHERE id=?',('pisofi_time_v1',))
        pid=e.create_policy(self.c,{'pause_count_max':1},self.now)
        self.assertNotEqual(pid,'pisofi_time_v1')

    def test_admin_pause_survives_user_timeout(self):
        self.mint();self.op('PAUSE');self.op('ADMIN_PAUSE');self.advance(3600);e.check_due_events(self.c,self.now,self.mono)
        self.assertEqual(e.connection(self.c,self.cd['id'])['desired_state'],'DISCONNECTED')
        self.assertEqual(self.op('RESUME')['error'],'admin_suspended')

    def test_cross_boot_checkpoint_does_not_consume_uptime(self):
        gid=self.mint();self.allow();self.c.execute("UPDATE connections SET boot_id='previous',last_mono_us=0")
        self.advance(5000);e.check_due_events(self.c,self.now,self.mono)
        self.assertEqual(e.grant(self.c,gid)['remaining_us'],600000000)

    def test_rebind_versions_and_stale_ack(self):
        self.mint();intent=e.request_network_intents(self.c,self.now,self.mono,True)[-1]
        moved=e.get_or_create_connection(self.c,'10.0.0.3',self.cd['mac'],self.owner,self.now,self.mono)
        self.assertGreater(moved['binding_version'],intent['version'])
        self.assertFalse(e.acknowledge_network(self.c,intent,self.now,self.mono,True))


    def test_transfer_conserves_and_claims_once(self):
        self.mint(125.5);transfer=self.op('TRANSFER_CREATE',{'seconds':120},'send')
        self.assertTrue(transfer['success'],transfer)
        other=e.get_or_create_owner(self.c,'device','mac:02:00:00:00:00:02',self.now)
        cd=e.get_or_create_connection(self.c,'10.0.0.3','02:00:00:00:00:02',other,self.now,self.mono)
        result=self.op('TRANSFER_CLAIM',{'code':transfer['code']},'claim',cd,other);self.assertTrue(result['success'],result)
        self.assertTrue(self.op('TRANSFER_CLAIM',{'code':transfer['code']},'claim',cd,other)['replayed'])
        self.assertEqual(self.c.execute('SELECT SUM(remaining_us) FROM time_grants').fetchone()[0],125500000);self.conserved()


class MigrationRegression(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='ecofi-legacy-test-');self.addCleanup(self.folder.cleanup)
        self.path=os.path.join(self.folder.name,'copy.db')
        with database(self.path) as c:
            c.execute('CREATE TABLE active_sessions(ip TEXT PRIMARY KEY,mac TEXT,remaining_seconds REAL,is_paused INTEGER,paused_at REAL,expires_at REAL,member_username TEXT)')
            c.execute('CREATE TABLE members(username TEXT PRIMARY KEY,wallet_minutes INTEGER)')

    def put(self,ip,mac,seconds,paused=0,deadline=0,member=''):
        with database(self.path) as c:c.execute('INSERT INTO active_sessions VALUES (?,?,?,?,?,?,?)',(ip,mac,seconds,paused,90000,deadline,member))

    def test_active_legacy_has_no_new_expiry_and_all_member_rows_import(self):
        self.put('10.0.0.2','02:00:00:00:00:01',108000,member='alice');self.put('10.0.0.3','02:00:00:00:00:02',600,member='alice')
        with database(self.path) as c:c.execute("INSERT INTO members VALUES ('alice',10)")
        result=migration.run_migration(self.path,now_utc=100000);self.assertTrue(result['success'],result)
        with database(self.path) as c:
            self.assertEqual(c.execute('SELECT SUM(remaining_us) FROM time_grants').fetchone()[0],109200000000)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM time_grants WHERE valid_until_utc IS NOT NULL').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM connections WHERE selected_grant_id IS NOT NULL').fetchone()[0],1)

    def test_legacy_wallet_import_is_archival_only(self):
        with database(self.path) as c:c.execute("INSERT INTO members VALUES ('retired',12.5)")
        for attempt in range(2):
            result=migration.run_migration(self.path,now_utc=100000)
            self.assertTrue(result['success'],result)
        with database(self.path) as c:
            self.assertEqual(c.execute("SELECT state,remaining_us FROM time_grants WHERE origin='legacy_wallet'").fetchall(),[('ARCHIVED',750000000)])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM connections WHERE selected_grant_id IS NOT NULL').fetchone()[0],0)

    def test_overdue_legacy_expires_and_rerun_does_not_reimport(self):
        self.put('10.0.0.2','02:00:00:00:00:01',1800,1,99999)
        result=migration.run_migration(self.path,now_utc=100000);self.assertTrue(result['success'],result)
        result=migration.run_migration(self.path,now_utc=100001);self.assertTrue(result['success'],result)
        with database(self.path) as c:
            self.assertEqual(c.execute('SELECT state,remaining_us FROM time_grants').fetchone(),('EXPIRED',0))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM time_grants').fetchone()[0],1)

    def test_dry_run_is_byte_identical_and_migration_checkpoint_is_current(self):
        self.put('10.0.0.2','02:00:00:00:00:01',1800)
        with open(self.path,'rb') as f:before=hashlib.sha256(f.read()).hexdigest()
        self.assertTrue(migration.run_migration(self.path,True,100000)['success'])
        with open(self.path,'rb') as f:self.assertEqual(before,hashlib.sha256(f.read()).hexdigest())
        self.assertTrue(migration.run_migration(self.path,now_utc=100000)['success'])
        with database(self.path) as c:self.assertGreater(c.execute('SELECT last_mono_us FROM connections').fetchone()[0],0)

    def test_partial_engine_import_removes_invented_legacy_expiry(self):
        self.put('10.0.0.2','02:00:00:00:00:01',600)
        with database(self.path) as c:
            s.init_time_schema(c)
            with s.transaction(c):
                owner=e.get_or_create_owner(c,'device','mac:02:00:00:00:00:01',100000)
                cd=e.get_or_create_connection(c,'10.0.0.2','02:00:00:00:00:01',owner,100000,100)
                g=e._create_grant(c,owner,600000000,'legacy',100000,source_ref='active_sessions')
                e._select(c,cd['id'],g,100000,100)
                c.execute('DELETE FROM time_ledger');c.execute('DELETE FROM ledger_accounts')
                c.execute('UPDATE time_grants SET remaining_us=NULL,issued_us=NULL')
        result=migration.run_migration(self.path,now_utc=100001);self.assertTrue(result['success'],result)
        with database(self.path) as c:
            self.assertEqual(c.execute('SELECT remaining_us,valid_until_utc,policy_version_id FROM time_grants').fetchone(),(600000000,None,'legacy_ecofi_pause_v1'))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM time_grants').fetchone()[0],1)

    def test_concurrent_duplicate_operation_has_one_issuance(self):
        with database(self.path) as c:
            s.init_time_schema(c)
            with s.transaction(c):
                owner=e.get_or_create_owner(c,'device','mac:02:00:00:00:00:01',100000)
                cd=e.get_or_create_connection(c,'10.0.0.2','02:00:00:00:00:01',owner,100000,100)
                s.set_metadata(c,'ready',1)
        barrier=threading.Barrier(2);results=[];errors=[]
        def issue():
            c=sqlite3.connect(self.path,timeout=15,isolation_level=None);c.execute('PRAGMA foreign_keys=ON')
            try:
                barrier.wait(10)
                results.append(e.apply_operation(c,owner,cd,'TOP_UP_GRANT',{'seconds':600},'simultaneous',100000,100))
            except Exception as error:errors.append(str(error))
            finally:c.close()
        workers=[threading.Thread(target=issue) for _ in range(2)]
        for worker in workers:worker.start()
        for worker in workers:worker.join(20)
        self.assertFalse(errors,errors);self.assertEqual(len(results),2)
        self.assertTrue(all(r['success'] for r in results),results)
        with database(self.path) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM time_grants').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT SUM(issued_us) FROM time_grants').fetchone()[0],600000000)


class PortalRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix='ecofi-portal-tests-');root=os.path.dirname(__file__)
        for name in os.listdir(root):
            if name.endswith('.py'):shutil.copy2(os.path.join(root,name),os.path.join(cls.folder.name,name))
        sys.path.insert(0,cls.folder.name)
        spec=importlib.util.spec_from_file_location('isolated_ecofi_portal',os.path.join(cls.folder.name,'portal.py'))
        cls.p=importlib.util.module_from_spec(spec)
        with patch.object(threading.Thread,'start'):spec.loader.exec_module(cls.p)
        cls.p.esp32.running=False;atexit.unregister(cls.p.save_sessions_to_db)
        cls.p.app.testing=True
        cls.original_transmit=staticmethod(cls.p.transmit_to_esp32)

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(cls.folder.name);cls.folder.cleanup()

    def setUp(self):
        p=self.p;p.DB_PATH=os.path.join(self.folder.name,str(uuid_token())+'.db');p.init_db()
        p.active_clients.clear();p.active_depositor_ip=None;p.esp32.reset_session()
        self.utc=100000.;self.mono=100.;self.seq=0
        clock=type('Clock',(),{})();clock.time=lambda:self.utc;clock.monotonic=lambda:self.mono
        self.clock_patch=patch.object(p,'time',clock);self.clock_patch.start();self.addCleanup(self.clock_patch.stop)
        p.license_valid=lambda:True;p.get_arp_table=lambda:{'10.0.0.2':'02:00:00:00:00:01','10.0.0.3':'02:00:00:00:00:02'}
        p.update_firewall=lambda *args,**kwargs:True;p.transmit_to_esp32=lambda data:None
        for name in ('set_license','grant','revoke','policies'):
            network_patch=patch.object(p.gateway_network,name,return_value=True);network_patch.start();self.addCleanup(network_patch.stop)
        p.time_service.clock_trusted=lambda:True;p.time_service.last_success_mono=None;p.time_service.last_utc=None
        self.client=p.app.test_client()

    def request(self,path,data=None,ip='10.0.0.2',get=False):
        self.seq+=1;data=dict(data or {});data.setdefault('operation_id','request:'+str(self.seq))
        return self.client.get(path,environ_overrides={'REMOTE_ADDR':ip}) if get else self.client.post(path,json=data,environ_overrides={'REMOTE_ADDR':ip})

    def voucher(self,seconds=600,code='V'):
        with self.p.db_connection() as c:c.execute('INSERT INTO vouchers(code,minutes) VALUES (?,?)',(code,seconds/60))
        result=self.request('/api/voucher/redeem',{'code':code});self.assertEqual(result.status_code,200,result.get_json());return result.get_json()

    def scalar(self,sql):
        with self.p.db_connection() as c:return c.execute(sql).fetchone()[0]

    def test_manual_credit_switch_route_is_removed(self):
        initial=self.voucher(600)
        response=self.request('/api/client/switch',{'grant_id':initial['grant_id']})
        self.assertEqual(response.status_code,404)
        self.assertNotIn('/api/client/switch',[r.rule for r in self.p.app.url_map.iter_rules()])
        self.assertEqual(self.request('/api/vendo/status',get=True).get_json()['remaining_seconds'],600)

    def test_kick_persists_through_worker_and_restore_until_admin_resume(self):
        self.voucher(125.5)
        self.request('/api/client/pause',{'action':'pause'})
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        kicked=self.request('/admin/api/client/action',{'ip':'10.0.0.2','action':'kick'}).get_json()
        self.assertTrue(kicked['success']);self.assertFalse(kicked['network_pending'])
        self.p.time_service.restore()
        self.utc+=10;self.mono+=10;self.p.time_service.worker_pass()
        blocked=self.request('/api/client/pause',{'action':'resume'}).get_json()
        self.assertEqual(blocked['error'],'admin_suspended')
        current=self.request('/api/vendo/status',get=True).get_json()
        self.assertEqual(current['state'],'DISCONNECTED');self.assertEqual(current['remaining_seconds'],0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM grant_pauses WHERE status='OPEN'"),0)

    def test_failed_kick_revoke_is_pending_and_retried(self):
        self.voucher()
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        with patch.object(self.p,'update_firewall',return_value=False):
            result=self.request('/admin/api/client/action',{'ip':'10.0.0.2','action':'kick'}).get_json()
        self.assertTrue(result['success']);self.assertTrue(result['network_pending'])
        self.assertGreater(self.scalar("SELECT COUNT(*) FROM network_intents WHERE status='PENDING'"),0)
        with patch.object(self.p,'update_firewall',return_value=True) as firewall:self.p.time_service.reconcile()
        self.assertTrue(any(call[0][1]=='del' for call in firewall.call_args_list))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM network_intents WHERE status='PENDING'"),0)

    def test_whitelist_does_not_bypass_kick(self):
        self.voucher()
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        self.request('/admin/api/client/action',{'ip':'10.0.0.2','action':'kick'})
        with self.p.db_connection() as c:c.execute("INSERT INTO mac_control(mac,type) VALUES ('02:00:00:00:00:01','whitelist')")
        with patch.object(self.p.platform,'system',return_value='Linux'),patch.object(self.p.gateway_network,'grant') as grant,patch.object(self.p.gateway_network,'revoke') as revoke:
            self.p.time_service.worker_pass()
        grant.assert_not_called();revoke.assert_called_with('10.0.0.2')
        self.assertEqual(self.scalar('SELECT remaining_us FROM time_grants'),0)

    def test_admin_targets_required_and_missing_disconnect_rejected(self):
        self.voucher()
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        for path,data in [('/admin/api/client/action',{'action':'kick'}),('/admin/api/client/edit',{'minutes':0}),('/admin/api/client/action',{'ip':'10.0.0.99','action':'kick'})]:
            response=self.request(path,data);self.assertEqual(response.status_code,400,response.get_json())
        self.assertEqual(self.request('/admin/api/clients/disconnect',{'ip':'10.0.0.99'}).status_code,404)
        self.assertEqual(self.scalar('SELECT remaining_us FROM time_grants'),600000000)

    def test_speed_only_edit_preserves_fractional_time_and_invalid_edit_rolls_back(self):
        self.voucher(125.567)
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        result=self.request('/admin/api/client/edit',{'ip':'10.0.0.2','dl_kbps':4096,'ul_kbps':1024})
        self.assertEqual(result.status_code,200,result.get_json())
        self.assertEqual(self.scalar('SELECT remaining_us FROM time_grants'),125567000)
        result=self.request('/admin/api/client/edit',{'ip':'10.0.0.2','minutes':1,'dl_kbps':5000,'ul_kbps':0})
        self.assertEqual(result.status_code,400,result.get_json())
        self.assertEqual(self.scalar('SELECT remaining_us FROM time_grants'),125567000)
        self.assertEqual(self.scalar('SELECT dl_kbps FROM time_grants'),4096)

    def test_mac_rename_conflict_preserves_original_and_success_keeps_limits(self):
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        with self.p.db_connection() as c:
            c.execute("INSERT INTO mac_control(mac,type,dl_kbps,ul_kbps) VALUES ('02:00:00:00:00:11','whitelist',4096,1024)")
            c.execute("INSERT INTO mac_control(mac,type) VALUES ('02:00:00:00:00:12','block')")
        with patch.object(self.p,'apply_walled_garden_and_macs'):
            conflict=self.request('/admin/api/mac_control/add',{'original_mac':'02:00:00:00:00:11','mac':'02:00:00:00:00:12','type':'whitelist'})
            self.assertEqual(conflict.status_code,409)
            self.assertEqual(self.scalar('SELECT COUNT(*) FROM mac_control'),2)
            moved=self.request('/admin/api/mac_control/add',{'original_mac':'02:00:00:00:00:11','mac':'02:00:00:00:00:13','type':'whitelist'})
            self.assertEqual(moved.status_code,200,moved.get_json())
        self.assertEqual(self.scalar("SELECT dl_kbps FROM mac_control WHERE mac='02:00:00:00:00:13'"),4096)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM mac_control WHERE mac='02:00:00:00:00:11'"),0)

    def test_bandwidth_rejects_unsupported_or_invalid_settings_before_writes(self):
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        before=self.p.get_config('default_dl_kbps')
        for data,code in [({'default_dl_kbps':0},400),({'default_ul_kbps':0},400)]:
            result=self.request('/admin/api/bandwidth/qos/save',data)
            self.assertEqual(result.status_code,code,result.get_json())
            self.assertEqual(self.p.get_config('default_dl_kbps'),before)
        result=self.request('/admin/api/bandwidth/qos/save',{'qos_gaming_enabled':'1','qos_gaming_percent':25})
        self.assertEqual(result.status_code,200,result.get_json())
        self.assertEqual(self.p.get_config('qos_gaming_enabled'),'1')
        self.assertEqual(self.p.get_config('qos_gaming_percent'),'25')
        result=self.request('/admin/api/bandwidth/qos/save',{'default_dl_kbps':4096,'default_ul_kbps':1024})
        self.assertEqual(result.status_code,200,result.get_json())
        self.assertEqual(self.p.get_config('default_dl_kbps'),'4096')

    def test_admin_add15_extends_selected_credit_once_without_queue(self):
        initial=self.voucher(125.5)
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        for attempt in range(2):
            result=self.request('/admin/api/client/action',{'ip':'10.0.0.2','action':'add15','operation_id':'same-add'}).get_json()
            self.assertTrue(result['success'],result)
        current=self.request('/api/vendo/status',get=True).get_json()
        self.assertEqual(current['grant_id'],initial['grant_id'])
        self.assertEqual(current['remaining_seconds'],1025.5)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM time_grants'),1)
        self.assertEqual(current['pauses_left'],3)

    def test_admin_resume_releases_customer_pause_without_resetting_budget(self):
        self.voucher()
        paused=self.request('/api/client/pause',{'action':'pause'}).get_json()
        self.assertTrue(paused['success']);self.assertFalse(paused['network_pending'])
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        result=self.request('/admin/api/client/action',{'ip':'10.0.0.2','action':'resume'}).get_json()
        self.assertTrue(result['success'],result)
        current=self.request('/api/vendo/status',get=True).get_json()
        self.assertEqual(current['state'],'ACTIVE');self.assertFalse(current['is_paused'])
        self.assertEqual(current['pauses_left'],2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM grant_pauses WHERE status='OPEN'"),0)

    def test_admin_client_status_includes_actual_network_state(self):
        self.voucher()
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        with patch.object(self.p,'update_firewall',return_value=False):
            self.request('/admin/api/client/action',{'ip':'10.0.0.2','action':'pause'})
        rows=self.request('/admin/api/clients',get=True).get_json()
        self.assertIn('applied_state',rows[0]);self.assertIn('admin_paused',rows[0])
        self.assertEqual(rows[0]['desired_state'],'DISCONNECTED')

    def test_admin_routes_restored_and_guarded(self):
        for path in ('/admin/api/clients','/admin/api/rates/list','/admin/api/time/diagnostics','/simulator/api/state'):
            self.assertEqual(self.request(path,get=True).status_code,401,path)
        self.assertEqual(self.request('/admin/login',get=True).status_code,200)

    def test_admin_button_api_targets_resolve(self):
        adapter=self.p.app.url_map.bind('localhost')
        calls=re.findall(r"(?:adminFetch|fetch)\(\s*['\"](/admin/api/[^'\"]+)['\"]\s*(,\s*\{\s*method\s*:\s*['\"]POST['\"])?",self.p.ADMIN_HTML)
        self.assertGreater(len(calls),40)
        for path,post in calls:
            endpoint,args=adapter.match(path.split('?')[0],method='POST' if post else 'GET')
            self.assertIn(endpoint,self.p.app.view_functions,path)


    def test_member_routes_are_removed_and_do_not_change_credit(self):
        initial=self.voucher(600)
        public=['/api/member/register','/api/member/login','/api/member/save_time','/api/member/use_wallet']
        admin=['/admin/api/members/add','/admin/api/members/topup','/admin/api/members/delete']
        for path in public:
            self.assertEqual(self.request(path,{'username':'retired','pin':'1234','minutes':50}).status_code,404,path)
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        for path in public+admin:
            self.assertEqual(self.request(path,{'username':'retired','pin':'1234','minutes':50}).status_code,404,path)
        self.assertEqual(self.request('/admin/api/members/list',get=True).status_code,404)
        self.assertFalse(any('/member' in rule.rule for rule in self.p.app.url_map.iter_rules()))
        current=self.request('/api/vendo/status',get=True).get_json()
        self.assertEqual(current['grant_id'],initial['grant_id'])
        self.assertEqual(current['remaining_seconds'],600)
        self.assertEqual(current['pauses_left'],3)
        self.assertEqual(self.scalar("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='members'"),0)

    def test_member_ui_and_wallet_export_are_removed(self):
        public=self.request('/',get=True).get_data(as_text=True)
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        admin=self.request('/admin',get=True).get_data(as_text=True)
        for text in [public,admin]:
            for token in ['tab-member','sec-members','Member Wallet','/api/member/','/admin/api/members/','saveMemberWallet']:
                self.assertNotIn(token,text)
        for token in ['tab-rates','tab-voucher','tab-transfer']:
            self.assertIn(token,public)
        self.assertEqual(self.request('/admin/api/export_csv',get=True).status_code,200)
        if self.p.openpyxl:
            wb=self.p.generate_ecofi_excel_report(self.p.DB_PATH)
            self.assertNotIn('Member Wallets',wb.sheetnames)
            self.assertEqual(len(wb.sheetnames),3)
            wb.close()

    def test_legacy_member_credit_survives_without_wallet_operations(self):
        initial=self.voucher(600)
        with self.p.db_connection() as c:
            # Represent a previously linked account without restoring its login feature.
            owner=c.execute('SELECT owner_id FROM time_grants WHERE id=?',(initial['grant_id'],)).fetchone()[0]
            c.execute("UPDATE credit_owners SET owner_type='member',owner_key='member:retired' WHERE id=?",(owner,))
            archived=e._create_grant(c,owner,125500000,'legacy_wallet',self.utc,state='WALLET')
            cd=e.one(c,'SELECT * FROM connections WHERE selected_grant_id=?',(initial['grant_id'],))
            for action in ['WALLET_SAVE','WALLET_WITHDRAW']:
                result=e.apply_operation(c,owner,cd,action,{'seconds':60,'wallet_owner_id':owner},'retired:'+action,self.utc,self.mono)
                self.assertEqual(result['error'],'unknown_action')
            self.assertEqual(e.grant(c,archived['id'])['remaining_us'],125500000)
        self.p.time_service.restore()
        current=self.request('/api/vendo/status',get=True).get_json()
        self.assertEqual(current['grant_id'],initial['grant_id'])
        self.assertEqual(current['remaining_seconds'],600)
        self.assertEqual(current['pauses_left'],3)
        self.assertTrue(self.request('/api/client/pause',{'action':'pause'}).get_json()['success'])
        self.assertTrue(self.request('/api/client/pause',{'action':'resume'}).get_json()['success'])

    def test_removed_member_setting_cannot_be_reenabled(self):
        self.assertEqual(self.request('/admin/api/settings/save',{'member_wallet_enabled':'0'}).status_code,401)
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        for value in [True,False,'1','0',2,None,[],{},1.0]:
            self.assertEqual(self.request('/admin/api/settings/save',{'member_wallet_enabled':value}).status_code,400)
        self.assertNotIn('member_wallet_enabled',self.request('/api/vendo/status',get=True).get_json())


    def test_finalized_bottle_restart_is_not_credited_twice(self):
        opened=self.request('/api/vendo/open_gate').get_json();self.assertTrue(opened['success'],opened)
        event={'event':'CREDIT_ADD','event_id':'device:1','session_id':opened['deposit_session_id'],'bottles':1,'protocol':2}
        self.p.on_esp32_uart_output(json.dumps(event));self.p.on_esp32_uart_output(json.dumps(event))
        result=self.request('/api/vendo/done').get_json();self.assertTrue(result['success'],result)
        self.assertEqual(result['bottles_credited'],1)
        self.p.restore_sessions_from_db()
        self.assertEqual(self.request('/api/vendo/status',get=True).get_json()['remaining_seconds'],600)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM deposit_events'),1)
        self.assertTrue(self.request('/api/vendo/done').get_json()['success'])
        self.assertEqual(self.scalar('SELECT SUM(issued_us) FROM time_grants'),600000000)

    def test_unidentified_device_event_is_held_not_minted(self):
        self.p.on_esp32_uart_output(json.dumps({'event':'CREDIT_ADD','bottles':1,'sessionTotal':1}))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM deposit_recovery'),1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM time_grants'),0)

    def test_voucher_failure_rolls_back_source(self):
        with self.p.db_connection() as c:c.execute("INSERT INTO vouchers(code,minutes) VALUES ('FAIL',10)")
        with patch.object(e,'apply_operation',side_effect=sqlite3.OperationalError('injected')):
            response=self.request('/api/voucher/redeem',{'code':'FAIL'})
        self.assertEqual(response.status_code,503);self.assertEqual(self.scalar("SELECT is_used FROM vouchers WHERE code='FAIL'"),0)

    def test_pause_timeout_and_old_replay_do_not_erase_or_repause(self):
        self.voucher();self.request('/api/client/pause',{'action':'pause','operation_id':'P'})
        self.utc+=3600;self.mono+=3600
        self.request('/api/client/pause',{'action':'resume','operation_id':'R'})
        self.request('/api/client/pause',{'action':'pause','operation_id':'P'})
        state=self.request('/api/vendo/status',get=True).get_json()
        self.assertFalse(state['is_paused']);self.assertEqual(state['remaining_seconds'],600)

    def test_status_counts_and_zero_balance_gate(self):
        self.assertFalse(self.request('/api/vendo/status',get=True).get_json()['can_pause'])
        self.voucher()
        for i in range(3):self.request('/api/client/pause',{'action':'pause'});self.request('/api/client/pause',{'action':'resume'})
        state=self.request('/api/vendo/status',get=True).get_json()
        self.assertEqual(state['pause_count_used'],3);self.assertEqual(state['pauses_left'],0);self.assertFalse(state['can_pause'])

    def test_dashboard_counts_only_confirmed_live_access(self):
        self.voucher();self.p.time_service.worker_pass()
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        def count():return self.request('/admin/api/stats',get=True).get_json()['active_clients']
        self.assertEqual(count(),1)
        self.request('/admin/api/client/action',{'ip':'10.0.0.2','action':'pause'})
        self.assertEqual(count(),0)
        self.assertGreater(self.scalar('SELECT remaining_us FROM time_grants'),0)
        self.request('/admin/api/client/action',{'ip':'10.0.0.2','action':'resume'})
        self.assertEqual(count(),1)
        self.request('/admin/api/client/action',{'ip':'10.0.0.2','action':'kick'})
        self.assertEqual(count(),0)
        self.assertEqual(self.scalar('SELECT remaining_us FROM time_grants'),0)
        self.utc+=16;self.mono+=16
        self.assertEqual(count(),0)


    def test_worker_death_stops_renewal_during_status_polling(self):
        self.voucher();self.p.time_service.worker_pass()
        calls=[];self.p.update_firewall=lambda *args,**kwargs:calls.append(args) or True
        self.utc+=16;self.mono+=16
        for i in range(3):self.request('/api/vendo/status',get=True)
        self.assertFalse(any(args[1]=='add' for args in calls),calls)
        self.assertEqual(self.scalar('SELECT remaining_us FROM time_grants'),585000000)


    def test_voucher_retry_retains_policy_after_admin_change(self):
        first=self.voucher()
        with self.p.db_connection() as c:e.create_policy(c,{'pause_count_max':9},self.utc)
        retry=self.request('/api/voucher/redeem',{'code':'V'}).get_json()
        self.assertTrue(retry['success'],retry);self.assertEqual(retry['grant_id'],first['grant_id'])
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM time_grants'),1)

    def test_simulator_receipt_ack_restart_and_completion(self):
        from esp32_simulator import ESP32Simulator
        journal=os.path.join(self.folder.name,uuid_token()+'.json')
        sim=ESP32Simulator(self.p.on_esp32_uart_output,journal,False)
        self.p.transmit_to_esp32=lambda data:sim.receive_uart(json.dumps(data))
        opened=self.request('/api/vendo/open_gate').get_json();self.assertTrue(opened['success'],opened)
        # Drop the first acknowledgement; the device must retain the receipt.
        self.p.transmit_to_esp32=lambda data:None
        sim.record_bottle();self.assertIsNotNone(sim.journal['pending'])
        restarted=ESP32Simulator(self.p.on_esp32_uart_output,journal,False)
        self.p.transmit_to_esp32=lambda data:restarted.receive_uart(json.dumps(data))
        restarted.replay_receipt();self.assertIsNone(restarted.journal['pending'])
        result=self.request('/api/vendo/done').get_json();self.assertEqual(result['bottles_credited'],1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM deposit_events'),1)
        self.assertEqual(self.scalar('SELECT SUM(issued_us) FROM time_grants'),600000000)

    def test_uncertain_drop_is_held_and_acknowledged(self):
        opened=self.request('/api/vendo/open_gate').get_json();sent=[];self.p.transmit_to_esp32=sent.append
        self.p.on_esp32_uart_output(json.dumps({'event':'DEPOSIT_RECOVERY','event_id':'uncertain:1','session_id':opened['deposit_session_id'],'phase':1}))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM deposit_recovery'),1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM time_grants'),0)
        self.assertEqual(sent[-1]['cmd'],'CREDIT_ACK')

    def test_admin_held_credit_resolution_moves_existing_value(self):
        with self.p.db_connection() as c:
            unknown=e.get_or_create_owner(c,'legacy','unknown',self.utc)
            g=e._create_grant(c,unknown,125500000,'legacy_session',self.utc,'legacy_ecofi_pause_v1',state='HELD')
            target=e.get_or_create_owner(c,'device','mac:02:00:00:00:00:01',self.utc)
            c.execute('INSERT INTO deposit_recovery VALUES (?,?,?,?,NULL)',('held:1',json.dumps({'grant_id':g['id']}),'Unknown owner',self.utc))
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        result=self.request('/admin/api/time/recovery',{'event_id':'held:1','owner_id':target,'seconds':125.5,'reason':'Verified receipt owner'}).get_json()
        self.assertTrue(result['success'],result)
        self.assertEqual(self.scalar('SELECT SUM(issued_us) FROM time_grants'),125500000)
        self.assertEqual(self.scalar('SELECT SUM(remaining_us) FROM time_grants'),125500000)

    def test_test_environment_does_not_bypass_admin_login(self):
        with patch.dict(os.environ,{'TESTING':'1'}):
            self.assertEqual(self.request('/admin/api/clients',get=True).status_code,401)

    def test_disabled_simulator_requires_login_then_returns_404(self):
        self.assertEqual(self.request('/simulator/api/state',get=True).status_code,401)
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        self.assertEqual(self.request('/simulator/api/state',get=True).status_code,404)
        self.p.set_config('simulator_enabled','1')
        self.assertEqual(self.request('/simulator/api/state',get=True).status_code,200)


    def test_physical_finish_retries_finalize_once(self):
        opened=self.request('/api/vendo/open_gate').get_json();sent=[];self.p.transmit_to_esp32=sent.append
        sid=opened['deposit_session_id']
        self.p.on_esp32_uart_output(json.dumps({'event':'CREDIT_ADD','event_id':'finish:1','session_id':sid,'bottles':1,'protocol':2}))
        for _ in range(2):self.p.on_esp32_uart_output(json.dumps({'event':'FINISH','session_id':sid,'protocol':2}))
        self.assertEqual(self.scalar('SELECT SUM(issued_us) FROM time_grants'),600000000)
        self.assertEqual(sum(item['cmd']=='FINISH_ACK' for item in sent),2)

    def test_unsynchronized_modern_date_does_not_become_trusted(self):
        service=self.p.time_service;service.clock_checked=None;service.clock_ok=False
        result=type('Result',(),{'returncode':0,'stdout':b'NTP synchronized: no'})()
        with patch.dict(os.environ,{'ECOFI_TRUST_CLOCK':'0'}),patch.object(self.p.platform,'system',return_value='Linux'),patch.object(self.p.subprocess,'run',return_value=result) as run:
            self.utc=1800000000.
            self.assertFalse(type(service).clock_trusted(service))
            self.assertFalse(type(service).clock_trusted(service))
            self.assertEqual(run.call_count,1)
            self.mono+=2;result.stdout=b'NTP synchronized: yes'
            self.assertTrue(type(service).clock_trusted(service))
            self.assertTrue(all(c[0][0][0]=='timedatectl' for c in run.call_args_list))

    def test_clock_status_compatibility_and_failed_probe(self):
        service=self.p.time_service
        with patch.dict(os.environ,{'ECOFI_TRUST_CLOCK':'0'}),patch.object(self.p.platform,'system',return_value='Linux'):
            for output,code,expected in [(b' NTP synchronized: yes\n',0,True),
                    (b'System clock synchronized: yes\n',0,True),
                    (b'NTP service: active\nSystem clock synchronized: no\n',0,False),
                    (b'NTP synchronized: yes\n',1,False)]:
                service.clock_checked=None;service.clock_ok=False
                result=type('Result',(),{'returncode':code,'stdout':output})()
                with patch.object(self.p.subprocess,'run',return_value=result) as run:
                    self.assertEqual(type(service).clock_trusted(service),expected)
                    self.assertEqual(run.call_args[0][0],['timedatectl','status'])
                    self.assertEqual(run.call_args[1]['env']['LC_ALL'],'C')
                    self.assertEqual(run.call_args[1]['timeout'],2)
            service.clock_checked=None;service.clock_ok=False
            with patch.object(self.p.subprocess,'run',side_effect=OSError('not available')) as run:
                self.assertFalse(type(service).clock_trusted(service))
                self.assertEqual(run.call_count,1)

    def test_default_garden_does_not_bypass_captive_probes(self):
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM walled_garden'),0)

    def test_disconnect_routes_revoke_authoritative_grants(self):
        self.voucher();self.p.time_service.worker_pass()
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        with patch.object(self.p,'update_firewall',return_value=True) as network:
            result=self.request('/admin/api/clients/disconnect',{'ip':'10.0.0.2'})
        self.assertTrue(result.get_json()['success'])
        self.assertEqual(self.scalar('SELECT desired_state FROM connections'),'DISCONNECTED')
        self.assertTrue(any(c[0][:2]==('10.0.0.2','del') for c in network.call_args_list))
        self.assertTrue(self.request('/admin/api/system/flush_sessions').get_json()['success'])
        self.assertEqual(self.scalar('SELECT SUM(delta_us) FROM time_ledger'),0)

    def test_backup_zip_round_trip_includes_wal_and_restores_ledger(self):
        import io,zipfile
        # Keep a reader open so committed pages remain in WAL during export.
        keeper=sqlite3.connect(self.p.DB_PATH);self.addCleanup(keeper.close)
        keeper.execute('PRAGMA journal_mode=WAL')
        self.voucher()
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        backup=self.request('/admin/api/system/backup/download',get=True)
        self.assertEqual(backup.status_code,200)
        with zipfile.ZipFile(io.BytesIO(backup.data)) as archive:self.assertTrue('ecovendo.db' in archive.namelist() or 'ecofi.db' in archive.namelist())
        self.voucher(60,'AFTER_BACKUP')
        restored=self.client.post('/admin/api/system/backup/restore',data={'backup_file':(io.BytesIO(backup.data),'backup.zip')})
        self.assertEqual(restored.status_code,200,restored.get_json())
        self.assertEqual(self.scalar('SELECT SUM(remaining_us) FROM time_grants'),600000000)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM ledger_accounts a JOIN time_grants g ON a.grant_id=g.id WHERE a.balance_us<>g.remaining_us'),0)
        self.assertEqual(self.request('/admin/api/clients',get=True).status_code,401)

    def test_invalid_backup_cannot_replace_existing_credit(self):
        import io
        self.voucher()
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        result=self.client.post('/admin/api/system/backup/restore',data={'backup_file':(io.BytesIO(b'invalid'),'backup.db')})
        self.assertEqual(result.status_code,400)
        self.assertEqual(self.scalar('SELECT SUM(remaining_us) FROM time_grants'),600000000)


    def test_hardware_settings_validate_before_persistence_or_transmission(self):
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        sent=[];self.p.transmit_to_esp32=sent.append
        for data in ({'settle_time_ms':-1},{'ent_open_angle':181},{'pet_nir_w_min':6000},{'entrance_gate_timeout':0},{'min_bottle_weight_g':-1},{'min_bottle_weight_g':100,'max_bottle_weight_g':50},['invalid']):
            response=self.client.post('/admin/api/esp32/save',json=data)
            self.assertEqual(response.status_code,400,response.get_json())
        self.assertFalse(sent)
        self.assertEqual(self.p.get_config('esp_settle_time_ms','unset'),'unset')
        response=self.client.post('/admin/api/esp32/save',json={'settle_time_ms':750,'min_bottle_weight_g':12,'max_bottle_weight_g':60,'weight_cal_factor':430})
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(self.p.get_config('esp_settle_time_ms'),'750')
        self.assertEqual(self.p.get_config('esp_min_bottle_weight_g'),'12')
        self.assertEqual(self.p.get_config('esp_max_bottle_weight_g'),'60')
        self.assertEqual(self.p.get_config('esp_weight_cal_factor'),'430')
        self.assertEqual(sent[-1]['settle_time_ms'],750)
        self.assertEqual(sent[-1]['min_bottle_weight_g'],12)

    def test_admin_esp32_weight_routes(self):
        self.assertEqual(self.client.post('/admin/api/esp32/test_weight').status_code,401)
        self.assertEqual(self.client.post('/admin/api/esp32/tare_weight').status_code,401)
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        self.p.set_config('simulator_enabled','1')
        self.p.transmit_to_esp32=self.original_transmit
        resp_test=self.client.post('/admin/api/esp32/test_weight')
        self.assertEqual(resp_test.status_code,200,resp_test.get_json())
        self.assertTrue(resp_test.get_json()['success'])
        resp_tare=self.client.post('/admin/api/esp32/tare_weight')
        self.assertEqual(resp_tare.status_code,200,resp_tare.get_json())
        self.assertTrue(resp_tare.get_json()['success'])

    def test_portal_admin_simulator_render_and_inline_javascript_parse(self):
        import re,subprocess
        node=shutil.which('node')
        if not node:self.skipTest('Node is required for inline JavaScript syntax verification')
        with self.client.session_transaction() as cookie:cookie['admin_logged_in']=True
        self.p.set_config('simulator_enabled','1')
        count=0
        for path in ('/','/admin','/simulator','/admin/login'):
            response=self.request(path,get=True)
            self.assertEqual(response.status_code,200,path)
            for script in re.findall(r'<script[^>]*>(.*?)</script>',response.get_data(as_text=True),re.S|re.I):
                if not script.strip():continue
                count+=1
                filename=os.path.join(self.folder.name,'script-'+str(count)+'.js')
                with open(filename,'w',encoding='utf-8') as stream:stream.write(script)
                check=subprocess.run([node,'--check',filename],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                self.assertEqual(check.returncode,0,path+': '+check.stderr.decode('utf-8','replace'))
        self.assertGreater(count,0)


    def test_captive_probe_requires_confirmed_licensed_access(self):
        self.voucher()
        self.assertFalse(self.p.check_client_online('10.0.0.2'))
        self.p.time_service.worker_pass()
        self.assertTrue(self.p.check_client_online('10.0.0.2'))
        self.p.license_valid=lambda:False
        self.assertFalse(self.p.check_client_online('10.0.0.2'))

    def test_hardware_unavailable_does_not_report_open_gate_success(self):
        self.p.transmit_to_esp32=lambda data:False
        response=self.request('/api/vendo/open_gate')
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.get_json()['error'],'hardware_unavailable')
        self.assertEqual(self.scalar('SELECT status FROM deposit_sessions'),'HOLD')

    def test_simulator_transport_routes_only_to_selected_backend(self):
        self.p.set_config('simulator_enabled','1')
        with patch.object(self.p.esp32,'receive_uart') as simulator,patch.object(self.p,'ser') as physical:
            self.assertTrue(self.original_transmit({'cmd':'OPEN_GATE'}))
            self.assertEqual(simulator.call_count,1);self.assertEqual(physical.write.call_count,0)
        self.p.set_config('simulator_enabled','0')
        with patch.object(self.p,'ser',None):self.assertFalse(self.original_transmit({'cmd':'OPEN_GATE'}))

    def test_disabled_backend_cannot_inject_receipt(self):
        opened=self.request('/api/vendo/open_gate').get_json()
        event=json.dumps({'event':'CREDIT_ADD','event_id':'source:1','session_id':opened['deposit_session_id'],'bottles':1,'protocol':2})
        self.p.time_service.on_event(event,source='simulator')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM deposit_events'),0)
        self.p.set_config('simulator_enabled','1')
        self.p.time_service.on_event(event,source='physical')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM deposit_events'),0)
        self.p.time_service.on_event(event,source='simulator')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM deposit_events'),1)

    def test_empty_walled_garden_installs_no_builtin_bypasses(self):
        with patch.object(self.p.platform,'system',return_value='Linux'),patch.object(self.p.gateway_network,'policies') as policies,patch.object(self.p.time_service,'worker_pass'):
            self.p.apply_walled_garden_and_macs()
        self.assertEqual(policies.call_args[0],(set(),set()))


    def test_usb_lan_name_matches_gateway_and_neighbor_lookup(self):
        with patch.dict(os.environ,{},clear=True),patch.object(self.p.os,'listdir',return_value=['lo','eth0','enx001122334455']):
            self.assertEqual(self.p.get_lan_interface(),'enx001122334455')
        with patch.dict(os.environ,{'ECOFI_LAN_IFACE':'usb0'}):
            self.assertEqual(self.p.get_lan_interface(),'usb0')


    def test_license_loss_preserves_receipt_without_reopening_intake(self):
        opened=self.request('/api/vendo/open_gate').get_json();sent=[]
        self.p.transmit_to_esp32=sent.append;self.p.license_valid=lambda:False
        self.p.on_esp32_uart_output(json.dumps({'event':'CREDIT_ADD','event_id':'license-loss:1','session_id':opened['deposit_session_id'],'bottles':1,'protocol':2}))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM deposit_events'),1)
        self.assertEqual([packet['cmd'] for packet in sent],['CREDIT_ACK'])


    def test_configured_walled_garden_resolves_public_addresses(self):
        with self.p.db_connection() as conn:conn.execute("INSERT INTO walled_garden(domain,note) VALUES ('example.test','operator')")
        answers=[(2,1,6,'',('93.184.216.34',0)),(2,1,6,'',('10.0.0.1',0))]
        with patch.object(self.p.platform,'system',return_value='Linux'),patch.object(self.p.socket,'getaddrinfo',return_value=answers),patch.object(self.p.gateway_network,'policies') as policies,patch.object(self.p.time_service,'worker_pass'):
            self.p.apply_walled_garden_and_macs()
    def test_admin_set_balance_recalculates_bracket_validity(self):
        self.voucher(600)
        with self.client.session_transaction() as cookie: cookie['admin_logged_in'] = True
        grant_row = self.scalar("SELECT id FROM time_grants WHERE state='ACTIVE'")
        with self.p.db_connection() as c:
            g = c.execute("SELECT validity_duration_sec FROM time_grants WHERE id=?", (grant_row,)).fetchone()
            self.assertEqual(g[0], 86400)
        # Admin sets balance to 7 days (10080 minutes = 604800s)
        self.request('/admin/api/client/edit', {'ip': '10.0.0.2', 'minutes': 10080})
        with self.p.db_connection() as c:
            updated = c.execute("SELECT remaining_seconds, validity_duration_sec, valid_until_utc FROM time_grants WHERE id=?", (grant_row,)).fetchone()
            self.assertEqual(updated[0], 604800.0)
            self.assertGreaterEqual(updated[1], 7776000)
            now = self.p.time.time()
            self.assertGreaterEqual(updated[2], now + 604800)
            self.assertGreaterEqual(updated[2], now + 7776000)

    def test_client_time_sync_endpoint(self):
        with patch.object(self.p.time_service, 'clock_trusted', return_value=False):
            # Missing parameter
            r = self.request('/api/vendo/client_time', {})
            self.assertEqual(r.status_code, 400)
            # Timestamp behind system
            with self.p.db_connection() as conn:
                s.set_metadata(conn, 'last_known_utc', '1789338000')
            r = self.request('/api/vendo/client_time', {'client_time_utc': 1789330000})
            self.assertEqual(r.status_code, 400)
            self.assertIn('behind_system', r.get_json()['error'])
            # Valid timestamp
            r = self.request('/api/vendo/client_time', {'client_time_utc': 1789340000})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.get_json()['success'])
            self.assertTrue(r.get_json()['synced'])

    def test_hardware_readiness_blocks_open_gate_when_actuators_offline(self):
        with patch.object(self.p, 'is_hardware_ready', return_value=(False, 'actuators_offline', 'Servo driver (PCA9685) offline')):
            r = self.request('/api/vendo/open_gate')
            self.assertEqual(r.status_code, 400)
            data = r.get_json()
            self.assertFalse(data['success'])
            self.assertEqual(data['error'], 'actuators_offline')
            self.assertIn('Machine servo actuators', data['message'])

    def test_hardware_readiness_blocks_open_gate_when_sensors_offline(self):
        with patch.object(self.p, 'is_hardware_ready', return_value=(False, 'sensors_offline', 'Optical spectrometer (AS7263) offline')):
            r = self.request('/api/vendo/open_gate')
            self.assertEqual(r.status_code, 400)
            data = r.get_json()
            self.assertFalse(data['success'])
            self.assertEqual(data['error'], 'sensors_offline')
            self.assertIn('Machine optical sensors', data['message'])

    def test_status_endpoint_reports_hardware_readiness(self):
        with patch.object(self.p, 'is_hardware_ready', return_value=(True, 'ready', 'Hardware ready')):
            r = self.request('/api/vendo/status', get=True)
            self.assertEqual(r.status_code, 200)
            data = r.get_json()
            self.assertTrue(data['hardware_ready'])
            self.assertEqual(data['hardware_status'], 'ready')
            self.assertEqual(data['hardware_msg'], 'Hardware ready')

    def test_admin_test_servo_endpoint(self):
        sent = []
        self.p.transmit_to_esp32 = sent.append
        # Unauthorized without admin session
        r = self.client.post('/admin/api/esp32/test_servo', json={'channel': 0, 'angle': 90})
        self.assertEqual(r.status_code, 401)
        # Authorized
        with self.client.session_transaction() as sess:
            sess['admin_logged_in'] = True
        # Invalid channel
        r = self.client.post('/admin/api/esp32/test_servo', json={'channel': 5, 'angle': 90})
        self.assertEqual(r.status_code, 400)
        # Hardware unavailable
        self.p.transmit_to_esp32 = lambda data: False
        r = self.client.post('/admin/api/esp32/test_servo', json={'channel': 0, 'angle': 90})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()['error'], 'hardware_unavailable')
        # Valid test servo
        self.p.transmit_to_esp32 = lambda data: sent.append(data) or True
        r = self.client.post('/admin/api/esp32/test_servo', json={'channel': 0, 'angle': 90, 'hold_ms': 1500})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['success'])
        self.assertEqual(sent[-1]['cmd'], 'TEST_SERVO')
        self.assertEqual(sent[-1]['channel'], 0)
        self.assertEqual(sent[-1]['angle'], 90)

    def test_admin_test_nir_endpoint(self):
        sent = []
        # Unauthorized without admin session
        r = self.client.post('/admin/api/esp32/test_nir')
        self.assertEqual(r.status_code, 401)
        # Authorized
        with self.client.session_transaction() as sess:
            sess['admin_logged_in'] = True
        # Spectrometer offline
        self.p.physical_esp32_state['spectrometer_ready'] = False
        r = self.client.post('/admin/api/esp32/test_nir')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()['error'], 'spectrometer_offline')
        # Spectrometer ready, hardware unavailable
        self.p.physical_esp32_state['spectrometer_ready'] = True
        self.p.transmit_to_esp32 = lambda data: False
        r = self.client.post('/admin/api/esp32/test_nir')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()['error'], 'hardware_unavailable')
        # Valid test NIR
        self.p.transmit_to_esp32 = lambda data: sent.append(data) or True
        r = self.client.post('/admin/api/esp32/test_nir')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['success'])
        self.assertEqual(sent[-1]['cmd'], 'TEST_NIR')

    def test_hardware_bounds_includes_require_nir_sensor(self):
        from esp32_simulator import HARDWARE_BOUNDS, validate_hardware_config
        self.assertIn('require_nir_sensor', HARDWARE_BOUNDS)
        low, high, default = HARDWARE_BOUNDS['require_nir_sensor']
        self.assertEqual((low, high, default), (0, 1, 1))
        # Valid values
        cfg0 = validate_hardware_config({'require_nir_sensor': 0})
        self.assertEqual(cfg0['require_nir_sensor'], 0)
        cfg1 = validate_hardware_config({'require_nir_sensor': 1})
        self.assertEqual(cfg1['require_nir_sensor'], 1)
        # Out of bounds
        with self.assertRaises(ValueError):
            validate_hardware_config({'require_nir_sensor': 2})


def uuid_token():
    import uuid
    return uuid.uuid4().hex


if __name__=='__main__':unittest.main()
