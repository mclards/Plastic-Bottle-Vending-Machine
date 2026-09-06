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

    def test_expiry_wins_tie_and_advances_queue(self):
        gid=self.mint();other=self.mint(300)
        self.c.execute('UPDATE time_grants SET valid_until_utc=? WHERE id=?',(self.now+3600,gid))
        self.op('PAUSE');self.advance(3600);e.check_due_events(self.c,self.now,self.mono)
        self.assertEqual(e.grant(self.c,gid)['state'],'EXPIRED');self.assertEqual(e.grant(self.c,gid)['remaining_us'],0)
        self.assertEqual(e.connection(self.c,self.cd['id'])['selected_grant_id'],other)
        self.assertEqual(e.grant(self.c,other)['valid_until_utc'],self.now+86400);self.conserved()

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

    def test_wallet_preserves_fraction_policy_and_shared_budget(self):
        gid=self.mint(125.5);member=e.get_or_create_owner(self.c,'member','alice',self.now)
        self.assertTrue(self.op('WALLET_SAVE',{'wallet_owner_id':member})['success'])
        self.assertEqual(e.wallet_us(self.c,member,self.now),125500000)
        cd=e.bind_member(self.c,self.cd['id'],member,self.now,self.mono)
        self.assertTrue(self.op('WALLET_WITHDRAW',{'seconds':120},cd=cd,owner=member)['success'])
        self.assertEqual(e.wallet_us(self.c,member,self.now),5500000)
        new=e.grant(self.c,e.connection(self.c,cd['id'])['selected_grant_id'])
        self.assertEqual(new['pause_budget_id'],e.grant(self.c,gid)['pause_budget_id'])
        self.assertEqual(new['valid_until_utc'],e.grant(self.c,gid)['valid_until_utc']);self.conserved()

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
        p.time_service.login_attempts.clear();self.client=p.app.test_client()

    def request(self,path,data=None,ip='10.0.0.2',get=False):
        self.seq+=1;data=dict(data or {});data.setdefault('operation_id','request:'+str(self.seq))
        return self.client.get(path,environ_overrides={'REMOTE_ADDR':ip}) if get else self.client.post(path,json=data,environ_overrides={'REMOTE_ADDR':ip})

    def voucher(self,seconds=600,code='V'):
        with self.p.db_connection() as c:c.execute('INSERT INTO vouchers(code,minutes) VALUES (?,?)',(code,seconds/60))
        result=self.request('/api/voucher/redeem',{'code':code});self.assertEqual(result.status_code,200,result.get_json());return result.get_json()

    def scalar(self,sql):
        with self.p.db_connection() as c:return c.execute(sql).fetchone()[0]

    def test_admin_routes_restored_and_guarded(self):
        for path in ('/admin/api/clients','/admin/api/rates/list','/admin/api/time/diagnostics','/simulator/api/state'):
            self.assertEqual(self.request(path,get=True).status_code,401,path)
        self.assertEqual(self.request('/admin/login',get=True).status_code,200)

    def test_member_hash_compatibility_and_plaintext_upgrade(self):
        result=self.request('/api/member/register',{'username':'alice','pin':'1234'});self.assertTrue(result.get_json()['success'])
        self.assertNotEqual(self.scalar("SELECT pin_hash FROM members WHERE username='alice'"),'1234')
        self.assertTrue(self.request('/api/member/login',{'username':'alice','pin':'1234'}).get_json()['success'])
        with self.p.db_connection() as c:c.execute("UPDATE members SET pin_hash='1234' WHERE username='alice'")
        self.assertTrue(self.request('/api/member/login',{'username':'alice','pin':'1234'}).get_json()['success'])
        self.assertNotEqual(self.scalar("SELECT pin_hash FROM members WHERE username='alice'"),'1234')

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

    def test_wallet_exact_seconds_are_conserved_across_real_routes(self):
        self.voucher(125.5);self.request('/api/member/register',{'username':'alice','pin':'1234'})
        saved=self.request('/api/member/save_time',{'username':'alice','pin':'1234'}).get_json()
        self.assertTrue(saved['success'],saved);self.assertEqual(saved['wallet_seconds'],125.5)
        used=self.request('/api/member/use_wallet',{'username':'alice','pin':'1234','minutes':2}).get_json()
        self.assertTrue(used['success'],used);self.assertEqual(used['wallet_seconds'],5.5)
        self.assertEqual(self.scalar('SELECT SUM(remaining_us) FROM time_grants'),125500000)

    def test_worker_death_stops_renewal_during_status_polling(self):
        self.voucher();self.p.time_service.worker_pass()
        calls=[];self.p.update_firewall=lambda *args,**kwargs:calls.append(args) or True
        self.utc+=16;self.mono+=16
        for i in range(3):self.request('/api/vendo/status',get=True)
        self.assertFalse(any(args[1]=='add' for args in calls),calls)
        self.assertEqual(self.scalar('SELECT remaining_us FROM time_grants'),585000000)

    def test_member_rebind_revokes_old_device_and_cannot_cross_save(self):
        self.voucher();self.request('/api/member/register',{'username':'alice','pin':'1234'})
        self.request('/api/member/login',{'username':'alice','pin':'1234'})
        self.request('/api/member/register',{'username':'bobby','pin':'4321'})
        denied=self.request('/api/member/save_time',{'username':'bobby','pin':'4321'}).get_json()
        self.assertFalse(denied['success'])
        moved=self.request('/api/member/login',{'username':'alice','pin':'1234'},ip='10.0.0.3').get_json();self.assertTrue(moved['success'],moved)
        old=self.request('/api/vendo/status',get=True).get_json()
        new=self.request('/api/vendo/status',get=True,ip='10.0.0.3').get_json()
        self.assertEqual(old['remaining_seconds'],0);self.assertEqual(new['remaining_seconds'],600)
        self.voucher(60,'SECOND')
        old=self.request('/api/vendo/status',get=True).get_json()
        self.assertEqual(old['remaining_seconds'],60)
        self.assertEqual(self.request('/api/vendo/status',get=True,ip='10.0.0.3').get_json()['remaining_seconds'],600)

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


def uuid_token():
    import uuid
    return uuid.uuid4().hex


if __name__=='__main__':unittest.main()
