# -*- coding: utf-8 -*-
"""Portal adapters for the authoritative entitlement engine (Python 3.5)."""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from flask import request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
import time_policy
import transition_engine as engine
import time_schema as storage


class Context(object):
    def __init__(self, values):
        object.__setattr__(self,'values',values)
    def __getattr__(self,key):
        return self.values[key]
    def __setattr__(self,key,value):
        self.values[key]=value


class TimePortal(object):
    def __init__(self,app,context):
        self.p=Context(context); self.app=app
        self.last_success_mono=None; self.last_utc=None; self.last_saved_utc=None
        self.network_lock=threading.RLock(); self.login_attempts={}
        self.clock_checked=None; self.clock_ok=False
        bindings={'ensure_client_session':self.ensure_session,'sync_client_firewall':self.sync_firewall,
            'update_firewall':self.update_firewall,'save_sessions_to_db':self.save_projection,
            'restore_sessions_from_db':self.restore,'time_daemon':self.daemon,
            'on_esp32_uart_output':self.on_event,'session_expired':lambda sess:False}
        context.update(bindings)
        self.p.esp32.on_serial_output_callback=self.on_event
        endpoints=[
            ('/api/vendo/status','api_vendo_status',self.status,['GET']),
            ('/api/vendo/open_gate','api_open_gate',self.open_gate,['POST']),
            ('/api/open_gate','api_open_gate',self.open_gate,['POST']),
            ('/api/vendo/done','api_vendo_done',self.done,['POST']),
            ('/api/client/pause','api_client_pause',self.pause,['POST']),
            ('/api/client/switch','api_client_switch',self.switch,['POST']),
            ('/api/voucher/redeem','api_voucher_redeem',self.voucher,['POST']),
            ('/api/member/register','api_member_register',self.member_register,['POST']),
            ('/api/member/login','api_member_login',self.member_login,['POST']),
            ('/api/member/save_time','api_member_save_time',self.wallet_save,['POST']),
            ('/api/member/use_wallet','api_member_use_wallet',self.wallet_use,['POST']),
            ('/api/transfer/generate','api_transfer_generate',self.transfer_create,['POST']),
            ('/api/transfer/claim','api_transfer_claim',self.transfer_claim,['POST']),
            ('/api/transfer/cancel','api_transfer_cancel',self.transfer_cancel,['POST']),
            ('/admin/api/client/action','admin_api_client_action',self.admin_action,['POST']),
            ('/admin/api/client/edit','admin_api_client_edit',self.admin_edit,['POST']),
            ('/admin/api/members/add','admin_api_members_add',self.admin_member_add,['POST']),
            ('/admin/api/members/topup','admin_api_members_topup',self.admin_member_topup,['POST']),
            ('/admin/api/members/delete','admin_api_members_delete',self.admin_member_delete,['POST']),
            ('/admin/api/time/policy','admin_time_policy',self.policy,['GET','POST']),
            ('/admin/api/time/diagnostics','admin_time_diagnostics',self.diagnostics,['GET']),
            ('/admin/api/time/recovery','admin_time_recovery',self.recovery,['GET','POST'])]
        for path,name,fn,methods in endpoints:
            context[name]=fn
            app.add_url_rule(path,endpoint=name,view_func=fn,methods=methods)
        # Install a common JSON error boundary without swallowing storage failures as success.
        for path,name,fn,methods in endpoints:
            app.view_functions[name]=self.guarded(fn)

    def guarded(self,fn):
        def route(*args,**kwargs):
            try:
                return fn(*args,**kwargs)
            except (ValueError,KeyError,TypeError) as error:
                return jsonify(success=False,error=str(error)),400
            except sqlite3.Error:
                self.p.log.exception('Entitlement transaction failed')
                return jsonify(success=False,error='storage_unavailable',retryable=True),503
        return route

    def now(self):
        return self.p.time.time(),self.p.time.monotonic()

    def config(self,conn,key,default=''):
        row=conn.execute('SELECT value FROM config WHERE key=?',(key,)).fetchone()
        return row[0] if row else default

    def clock_trusted(self):
        now,mono=self.now()
        if self.p.platform.system()=='Windows' or os.environ.get('ECOFI_TRUST_CLOCK')=='1':
            return True
        # If the system clock is reasonable (at or after 2020), it is valid and trusted.
        # Battery-less SBCs (e.g. Orange Pi) use fake-hwclock or database timestamps to maintain
        # monotonic real time even when offline without an active NTP sync.
        if now>=1577836800:
            return True
        # If the clock has reset to epoch (< 2020), attempt automatic self-healing:
        if self.clock_checked is None or mono-self.clock_checked>10:
            self.clock_checked=mono
            try:
                result=self.p.subprocess.run(['timedatectl','show','-p','NTPSynchronized'],stdout=self.p.subprocess.PIPE,
                    stderr=self.p.subprocess.DEVNULL,timeout=2)
                if b'NTPSynchronized=yes' in result.stdout:
                    return True
            except (OSError,self.p.subprocess.TimeoutExpired):
                pass
            try:
                with self.p.db_connection() as conn:
                    last_utc_str=storage.metadata(conn,'last_known_utc','0')
                    try:last_utc=float(last_utc_str)
                    except ValueError:last_utc=0
                    if last_utc<1577836800:
                        row=conn.execute('SELECT MAX(created_at) FROM time_grants').fetchone()
                        if row and row[0] and float(row[0])>=1577836800:
                            last_utc=float(row[0])
                    if last_utc<1577836800:
                        # Fallback to image release baseline (2026-09-01)
                        last_utc=1788220800
                    # Advance system clock to last known state so all services have valid timestamps
                    self.p.subprocess.run(['date','-s','@'+str(int(last_utc))],
                        stdout=self.p.subprocess.DEVNULL,stderr=self.p.subprocess.DEVNULL)
                    return True
            except Exception:
                pass
        return False

    def healthy(self):
        now,mono=self.now()
        return self.last_success_mono is not None and 0<=mono-self.last_success_mono<=15 and self.clock_trusted()

    def require_ready(self,conn):
        if storage.metadata(conn,'ready','0')!='1':
            raise ValueError('migration_required')
        if not self.clock_trusted():
            raise ValueError('clock_not_ready')

    def data(self):
        value=request.get_json(silent=True) or {}
        if not isinstance(value,dict):
            raise ValueError('invalid_request')
        return value

    def op_id(self,data,prefix):
        value=data.get('operation_id') or request.headers.get('Idempotency-Key')
        if not isinstance(value,str) or not value or len(value)>180:
            value=str(uuid.uuid4())
            if isinstance(data,dict):data['operation_id']=value
        return prefix+':'+value

    def resolve(self,conn,ip,mac,now,mono):
        device=engine.one(conn,'SELECT * FROM devices WHERE mac=?',(mac,))
        owner=device['owner_id'] if device else engine.get_or_create_owner(conn,'device','mac:'+mac,now)
        return engine.get_or_create_connection(conn,ip,mac,owner,now,mono)

    def identity(self,ip=None):
        ip=ip or self.p.get_client_ip()
        # An ARP absence may retain a known binding; a different MAC must rebind.
        mac=self.p.get_arp_table().get(ip)
        if not mac:
            with self.p.db_connection() as conn:
                row=engine.one(conn,'SELECT mac FROM connections WHERE ip=?',(ip,))
                mac=row['mac'] if row else None
        return ip,engine.normalize_mac(mac)

    def project(self,conn,cd,now):
        value=engine.snapshot(conn,cd['id'],now)
        value['mac']=cd['mac']
        owner=engine.one(conn,'SELECT * FROM credit_owners WHERE id=?',(cd['owner_id'],))
        value['member_username']=owner['owner_key'][7:] if owner['owner_type']=='member' else ''
        deposit=engine.one(conn,"SELECT id FROM deposit_sessions WHERE connection_id=? AND status IN ('OPEN','HOLD') ORDER BY created_at DESC LIMIT 1",(cd['id'],))
        value['pending_bottles']=conn.execute('SELECT COALESCE(SUM(bottles),0) FROM deposit_events WHERE session_id=?',(deposit['id'],)).fetchone()[0] if deposit else 0
        value['deposit_session_id']=deposit['id'] if deposit else None
        return value

    def publish(self,ip,value):
        with self.p.active_clients_lock:
            self.p.active_clients[ip]=value
        return value

    def ensure_session(self,ip):
        try:
            ip,mac=self.identity(ip)
        except ValueError:
            return {'mac':'','remaining_seconds':0,'pending_bottles':0,'is_paused':False,'paused_at':0,
                    'expires_at':0,'dl_kbps':3072,'ul_kbps':1536,'can_pause':False,'access_error':'Waiting for device identity.'}
        now,mono=self.now()
        with self.p.db_connection() as conn:
            if storage.metadata(conn,'ready','0')!='1':
                return {'mac':mac,'remaining_seconds':0,'pending_bottles':0,'is_paused':False,'can_pause':False,
                        'expires_at':0,'access_error':'Migration reconciliation is required.'}
            cd=self.resolve(conn,ip,mac,now,mono)
            if self.clock_trusted():
                engine.check_due_events(conn,now,mono)
            value=self.project(conn,engine.connection(conn,cd['id']),now)
        return self.publish(ip,value)

    def restore(self):
        self.last_success_mono=None
        with self.p.active_clients_lock:
            self.p.active_clients.clear()
        now,mono=self.now()
        projections=[]
        with self.p.db_connection() as conn:
            if storage.metadata(conn,'ready','0')!='1':
                return
            conn.execute("UPDATE deposit_sessions SET status='HOLD',error='Service restarted; finish or recover the recorded deposit.' WHERE status='OPEN'")
            # Old kernel leases are bounded; a fresh service never bills downtime.
            conn.execute("UPDATE connections SET applied_state='DISCONNECTED',authorized_until_us=NULL,boot_id=?,last_mono_us=?,last_settled_at_mono=?,last_settled_at_utc=?",(engine.BOOT_ID,engine.mono_us(mono),mono,now))
            for cd in engine.all_rows(conn,'SELECT * FROM connections'):
                if not cd['ip'].startswith('detached:'):
                    projections.append((cd['ip'],self.project(conn,cd,now)))
        for ip,value in projections:
            self.publish(ip,value)

    def save_projection(self):
        # Kept for old admin/report callers. Never rewrite the migration source.
        return None

    def sync_firewall(self,ip):
        self.reconcile()

    def update_firewall(self,ip,action,timeout_sec=0,dl_kbps=3072,ul_kbps=1536):
        if self.p.platform.system()=='Windows':
            return True
        if action=='del':
            self.p.gateway_network.revoke(ip)
            return True
        if not self.healthy():
            self.p.gateway_network.revoke(ip)
            return False
        with self.p.db_connection() as conn:
            cd=engine.one(conn,'SELECT * FROM connections WHERE ip=?',(ip,))
            if not cd or cd['desired_state']!='ACTIVE':
                return False
            mac=cd['mac']
        return self.p.gateway_network.grant(ip,mac,min(15,timeout_sec),dl_kbps,ul_kbps)

    def reconcile(self):
        with self.network_lock:
            now,mono=self.now(); healthy=self.healthy()
            with self.p.db_connection() as conn:
                healthy=healthy and storage.metadata(conn,'ready','0')=='1'
                intents=engine.request_network_intents(conn,now,mono,healthy)
            for intent in intents:
                with self.p.db_connection() as conn:
                    cd=engine.connection(conn,intent['connection_id'])
                    current=(cd['binding_version']==intent['version'] and cd['ip']==intent['ip'])
                    if intent['desired_state']=='ACTIVE' and (not current or not healthy):
                        conn.execute("UPDATE network_intents SET status='STALE' WHERE id=?",(intent['id'],));continue
                    value=engine.snapshot(conn,cd['id'],now) if storage.metadata(conn,'ready','0')=='1' else None
                now,mono=self.now()
                ok=True; error=''; lease=15
                try:
                    if intent['desired_state']=='ACTIVE':
                        limit=min(15,value['remaining_seconds'])
                        if value['valid_until_utc'] is not None:
                            limit=min(limit,max(0,value['valid_until_utc']-now))
                        if limit<=0:
                            ok=False
                        else:
                            lease=max(1,int(limit))
                            ok=self.p.update_firewall(intent['ip'],'add',lease,value['dl_kbps'],value['ul_kbps']) is not False
                    else:
                        # A stale revoke cannot tear down a newer already-applied binding.
                        with self.p.db_connection() as conn:
                            newer=engine.one(conn,"SELECT * FROM connections WHERE ip=? AND applied_state='ACTIVE'",(intent['ip'],))
                        if not newer or (newer['id']==intent['connection_id'] and newer['binding_version']==intent['version']):
                            self.p.update_firewall(intent['ip'],'del')
                except Exception as exc:
                    ok=False;error=str(exc)
                if not ok and not error:
                    error='Network authorization unavailable; credit is preserved.'
                ack_now,ack_mono=self.now()
                remaining_lease=max(0,lease-max(0,ack_mono-mono))
                with self.p.db_connection() as conn:
                    acknowledged=engine.acknowledge_network(conn,intent,ack_now,ack_mono,ok,error,remaining_lease)
                if not acknowledged and intent['desired_state']=='ACTIVE' and ok:
                    self.p.update_firewall(intent['ip'],'del')

    def worker_pass(self):
        now,mono=self.now(); trusted=self.clock_trusted(); licensed=self.p.license_valid()
        if self.last_utc is not None and now<self.last_utc-60:
            trusted=False;self.clock_checked=None;self.clock_ok=False
        self.last_utc=now
        if not trusted:
            self.last_success_mono=None;self.reconcile();return False
        if self.last_saved_utc is None or now-self.last_saved_utc>60:
            self.last_saved_utc=now
            try:
                with self.p.db_connection() as conn:
                    storage.set_metadata(conn,'last_known_utc',str(now))
            except Exception:
                pass
        arp=self.p.get_arp_table(); projections=[]
        with self.p.db_connection() as conn:
            if storage.metadata(conn,'ready','0')!='1':
                self.last_success_mono=None;return False
            engine.check_due_events(conn,now,mono)
            auto=self.config(conn,'auto_pause_disconnect','0')=='1'
            for cd in engine.all_rows(conn,'SELECT * FROM connections'):
                control=engine.one(conn,'SELECT * FROM mac_control WHERE lower(mac)=?',(cd['mac'],))
                service_block=not licensed or bool(control and control['type'] in ('block','whitelist'))
                conn.execute('UPDATE connections SET service_suspended=? WHERE id=?',(int(service_block),cd['id']))
                conn.execute('UPDATE connections SET disconnect_paused=? WHERE id=?',(int(auto and arp.get(cd['ip'])!=cd['mac']),cd['id']))
                selected=engine.grant(conn,cd['selected_grant_id'])
                if selected and not selected['speed_override']:
                    dl=int((control or {}).get('dl_kbps') or self.config(conn,'default_dl_kbps','3072'))
                    ul=int((control or {}).get('ul_kbps') or self.config(conn,'default_ul_kbps','1536'))
                    if not 64<=dl<=1000000 or not 64<=ul<=1000000:raise ValueError('invalid_default_speed')
                    if (dl,ul)!=(selected['dl_kbps'],selected['ul_kbps']):
                        conn.execute('UPDATE time_grants SET dl_kbps=?,ul_kbps=? WHERE id=?',(dl,ul,selected['id']))
                        engine.refresh_desired(conn,cd['id'],now,mono,True)
                engine.refresh_desired(conn,cd['id'],now,mono)
                if not cd['ip'].startswith('detached:'):
                    projections.append((cd['ip'],self.project(conn,engine.connection(conn,cd['id']),now)))
        self.last_success_mono=mono
        for ip,value in projections:
            self.publish(ip,value)
        if self.p.platform.system()!='Windows':
            self.p.gateway_network.set_license(licensed)
            try:
                path='/tmp/ecofi_worker_heartbeat'
                with open(path+'.new','w') as heartbeat:
                    json.dump({'boot_id':engine.BOOT_ID,'mono':mono,'utc':now},heartbeat)
                os.replace(path+'.new',path)
            except OSError:
                self.p.log.warning('Unable to write worker diagnostic heartbeat')
        self.reconcile()
        if licensed and self.p.platform.system()!='Windows':
            with self.p.db_connection() as conn:
                whitelist={r[0].lower() for r in conn.execute("SELECT mac FROM mac_control WHERE type='whitelist'")}
            for ip,mac in arp.items():
                if mac.lower() in whitelist and self.healthy():
                    self.p.gateway_network.grant(ip,engine.normalize_mac(mac),15,1000000,1000000)
        return True

    def daemon(self):
        while True:
            try:
                self.worker_pass()
            except Exception:
                self.last_success_mono=None
                self.p.log.exception('Accounting worker pass failed; paid renewals disabled')
                try:self.reconcile()
                except Exception:self.p.log.exception('Revoke reconciliation failed; leases are bounded')
            self.p.time.sleep(1)

    def operation(self,action,payload,data,ip=None):
        ip,mac=self.identity(ip); now,mono=self.now()
        with self.p.db_connection() as conn:
            self.require_ready(conn)
            cd=self.resolve(conn,ip,mac,now,mono)
            result=engine.apply_operation(conn,cd['owner_id'],cd,action,payload,self.op_id(data,action),now,mono)
            value=self.project(conn,engine.connection(conn,cd['id']),now)
        self.publish(ip,value);self.reconcile()
        return result

    def status(self):
        value=dict(self.ensure_session(self.p.get_client_ip())); now,mono=self.now()
        value.update(client_time_remaining=value['remaining_seconds'],worker_healthy=self.healthy(),clock_trusted=self.clock_trusted())
        value['expires_str']='';value['auto_resume_str']=''
        valid=value.get('valid_until_utc'); deadline=value.get('pause_deadline_utc')
        if valid:
            value['expires_str']=datetime.fromtimestamp(valid).strftime('%b %d, %I:%M %p')
        if deadline and value.get('next_event_type')=='resume':
            value['auto_resume_str']=datetime.fromtimestamp(deadline).strftime('%b %d, %I:%M %p')
        value['validity_hours']=max(0,(valid-now)/3600) if valid else None
        value['session_bottles']=value.get('pending_bottles',0)
        value['session_added_minutes']=self.p.calculate_minutes_for_bottles(value['session_bottles'])
        value['bin_full']=self.p.get_config('hw_bin_full','0')=='1' or self.p.esp32.get_state().get('is_bin_full',False)
        value['gate_open']=bool(value.get('deposit_session_id') and self.p.esp32.get_state().get('entrance_servo',0)>45)
        self.reconcile()
        return jsonify(value)

    def pause(self):
        data=self.data(); action=data.get('action','pause')
        if action not in ('pause','resume'):
            raise ValueError('unknown_action')
        # State-idempotent actions remain compatible with older captive browsers.
        data.setdefault('operation_id',str(uuid.uuid4()))
        return jsonify(self.operation(action.upper(),{},data))

    def switch(self):
        data=self.data()
        return jsonify(self.operation('SWITCH',{'grant_id':data.get('grant_id')},data))

    def voucher(self):
        if not self.p.license_valid():
            raise ValueError('machine_unlicensed')
        data=self.data();code=str(data.get('code','')).strip().upper();ip,mac=self.identity();now,mono=self.now()
        with self.p.db_connection() as conn:
            self.require_ready(conn);cd=self.resolve(conn,ip,mac,now,mono)
            voucher=engine.one(conn,'SELECT * FROM vouchers WHERE code=?',(code,))
            if not voucher:
                raise ValueError('invalid_voucher')
            op='voucher:'+code
            previous=engine.one(conn,'SELECT * FROM value_operations WHERE operation_id=?',(op,))
            if voucher['is_used'] and not previous:
                raise ValueError('voucher_already_redeemed')
            if not voucher.get('policy_version_id'):
                voucher['policy_version_id']=storage.metadata(conn,'active_policy')
                conn.execute('UPDATE vouchers SET policy_version_id=? WHERE code=?',(voucher['policy_version_id'],code))
            result=engine.apply_operation(conn,cd['owner_id'],cd,'TOP_UP_GRANT',
                {'seconds':voucher['minutes']*60,'origin':'voucher','source_ref':code,
                 'policy_version_id':voucher.get('policy_version_id') or storage.metadata(conn,'active_policy')},op,now,mono)
            if not result.get('success'):
                raise ValueError(result.get('error'))
            conn.execute('UPDATE vouchers SET is_used=1,used_by=? WHERE code=?',(ip,code))
            value=self.project(conn,engine.connection(conn,cd['id']),now)
        self.publish(ip,value);self.reconcile()
        result['message']='Voucher credited.'
        return jsonify(result)

    def auth(self,conn,data):
        username=str(data.get('username','')).strip();pin=str(data.get('pin',''))
        now,mono=self.now(); key=(username,self.p.get_client_ip())
        failures,at=self.login_attempts.get(key,(0,0))
        if failures>=5 and mono-at<300:
            raise ValueError('too_many_login_attempts')
        row=engine.one(conn,'SELECT * FROM members WHERE username=?',(username,))
        valid=False
        if row and len(pin)<=128:
            stored=row['pin_hash']
            if re.match(r'^\d{4,128}$',stored or ''):
                valid=hmac.compare_digest(stored,pin)
                if valid:
                    conn.execute('UPDATE members SET pin_hash=? WHERE username=?',
                                 (generate_password_hash(pin,method='pbkdf2:sha256'),username))
            else:
                valid=check_password_hash(stored,pin)
        if not valid:
            self.login_attempts[key]=(failures+1 if mono-at<300 else 1,mono)
            raise ValueError('invalid_username_or_pin')
        self.login_attempts.pop(key,None)
        owner=engine.get_or_create_owner(conn,'member',username,now)
        return username,owner

    def register_member(self,conn,data):
        username=str(data.get('username','')).strip();pin=str(data.get('pin',''))
        if not re.match(r'^[a-zA-Z0-9_]{3,20}$',username):
            raise ValueError('Username must contain 3-20 letters, numbers, or underscores.')
        if not re.match(r'^\d{4,6}$',pin):
            raise ValueError('PIN must contain 4-6 digits.')
        if conn.execute('SELECT 1 FROM members WHERE username=?',(username,)).fetchone():
            raise ValueError('username_already_exists')
        now,mono=self.now()
        conn.execute('INSERT INTO members(username,pin_hash,wallet_minutes,created_at) VALUES (?,?,0,?)',
                     (username,generate_password_hash(pin,method='pbkdf2:sha256'),datetime.fromtimestamp(now).isoformat()))
        return username,engine.get_or_create_owner(conn,'member',username,now)

    def member_register(self):
        with self.p.db_connection() as conn:
            self.require_ready(conn);self.register_member(conn,self.data())
        return jsonify(success=True,message='Account registered. Saved time retains its original expiry.')

    def member_login(self):
        data=self.data();ip,mac=self.identity();now,mono=self.now()
        with self.p.db_connection() as conn:
            self.require_ready(conn);username,owner=self.auth(conn,data)
            cd=self.resolve(conn,ip,mac,now,mono)
            engine.bind_member(conn,cd['id'],owner,now,mono)
            conn.execute('UPDATE members SET active_ip=?,active_mac=? WHERE username=?',(ip,mac,username))
            value=self.project(conn,engine.connection(conn,cd['id']),now)
            wallet=engine.wallet_us(conn,owner,now)/float(storage.SCALE)
        session['member_username']=username
        self.restore_projections();self.reconcile()
        return jsonify(success=True,message='Login successful.',wallet_minutes=wallet/60,wallet_seconds=wallet,
                       transferred_seconds=value['remaining_seconds'],transferred_minutes=value['remaining_seconds']/60)

    def wallet_save(self):
        data=self.data();ip,mac=self.identity();now,mono=self.now()
        with self.p.db_connection() as conn:
            self.require_ready(conn);username,owner=self.auth(conn,data)
            cd=self.resolve(conn,ip,mac,now,mono)
            source=engine.one(conn,'SELECT * FROM credit_owners WHERE id=?',(cd['owner_id'],))
            if source['owner_type']=='member' and cd['owner_id']!=owner:raise ValueError('different_member_owns_this_credit')
            payload={'wallet_owner_id':owner}
            if 'seconds' in data:payload['seconds']=data['seconds']
            result=engine.apply_operation(conn,cd['owner_id'],cd,'WALLET_SAVE',payload,self.op_id(data,'wallet-save'),now,mono)
            if not result.get('success'):raise ValueError(result['error'])
            wallet=engine.wallet_us(conn,owner,now)/float(storage.SCALE)
            value=self.project(conn,engine.connection(conn,cd['id']),now)
        self.publish(ip,value);self.reconcile()
        return jsonify(success=True,message='Saved exact time; original expiry and pause budget are retained.',
                       wallet_minutes=wallet/60,wallet_seconds=wallet,remainder_seconds=value['remaining_seconds'])

    def wallet_use(self):
        data=self.data();ip,mac=self.identity();now,mono=self.now()
        seconds=data.get('seconds',float(data.get('minutes',0))*60)
        with self.p.db_connection() as conn:
            self.require_ready(conn);username,owner=self.auth(conn,data)
            cd=self.resolve(conn,ip,mac,now,mono)
            cd=engine.bind_member(conn,cd['id'],owner,now,mono)
            conn.execute('UPDATE members SET active_ip=?,active_mac=? WHERE username=?',(ip,mac,username))
            result=engine.apply_operation(conn,owner,cd,'WALLET_WITHDRAW',{'seconds':seconds},self.op_id(data,'wallet-use'),now,mono)
            if not result.get('success'):raise ValueError(result['error'])
            wallet=engine.wallet_us(conn,owner,now)/float(storage.SCALE)
        self.restore_projections();self.reconcile()
        return jsonify(success=True,message='Wallet time selected or queued.',wallet_minutes=wallet/60,wallet_seconds=wallet)

    def transfer_create(self):
        data=self.data();payload={}
        if 'seconds' in data:payload['seconds']=data['seconds']
        elif data.get('minutes') not in (None,''):payload['seconds']=float(data['minutes'])*60
        return jsonify(self.operation('TRANSFER_CREATE',payload,data))

    def transfer_claim(self):
        data=self.data()
        result=self.operation('TRANSFER_CLAIM',{'code':data.get('code','')},data)
        result['message']='Transfer credited with its original expiry and pause budget.' if result.get('success') else result.get('error')
        return jsonify(result)

    def transfer_cancel(self):
        data=self.data()
        return jsonify(self.operation('TRANSFER_CANCEL',{'code':data.get('code','')},data))

    def restore_projections(self):
        now,mono=self.now();values=[]
        with self.p.db_connection() as conn:
            for cd in engine.all_rows(conn,"SELECT * FROM connections WHERE ip NOT LIKE 'detached:%'"):
                values.append((cd['ip'],self.project(conn,cd,now)))
        with self.p.active_clients_lock:
            self.p.active_clients.clear()
            self.p.active_clients.update(values)

    def admin_action(self):
        data=self.data();action=data.get('action');mapped={'pause':'ADMIN_PAUSE','resume':'ADMIN_RESUME','kick':'ADMIN_DISCONNECT'}
        data.setdefault('operation_id',str(uuid.uuid4()))
        if action in ('add15','add60'):
            result=self.operation('TOP_UP_GRANT',{'seconds':900 if action=='add15' else 3600,'origin':'admin'},data,data.get('ip'))
        elif action in mapped:
            result=self.operation(mapped[action],{},data,data.get('ip'))
        else:raise ValueError('unknown_admin_action')
        return jsonify(result)

    def admin_edit(self):
        data=self.data();ip,mac=self.identity(data.get('ip'));now,mono=self.now()
        with self.p.db_connection() as conn:
            self.require_ready(conn);cd=self.resolve(conn,ip,mac,now,mono)
            if data.get('minutes') is not None:
                data.setdefault('operation_id',str(uuid.uuid4()))
                result=engine.apply_operation(conn,cd['owner_id'],cd,'ADMIN_SET_BALANCE',
                    {'seconds':float(data['minutes'])*60},self.op_id(data,'admin-edit'),now,mono)
                if not result['success']:raise ValueError(result['error'])
            cd=engine.connection(conn,cd['id']);g=engine.grant(conn,cd['selected_grant_id'])
            if g:
                for name in ('dl_kbps','ul_kbps'):
                    if data.get(name) is not None:
                        speed=int(data[name])
                        if not 64<=speed<=1000000:raise ValueError('invalid_speed')
                        conn.execute('UPDATE time_grants SET '+name+'=?,speed_override=1 WHERE id=?',(speed,g['id']))
                engine.refresh_desired(conn,cd['id'],now,mono,True)
            value=self.project(conn,engine.connection(conn,cd['id']),now)
        self.publish(ip,value);self.reconcile()
        return jsonify(success=True,client=value)

    def admin_member_add(self):
        data=self.data();now,mono=self.now()
        with self.p.db_connection() as conn:
            self.require_ready(conn);username,owner=self.register_member(conn,data)
            amount=storage.to_us(float(data.get('wallet_minutes',0))*60)
            if amount<0:raise ValueError('invalid_seconds')
            if amount:
                engine._create_grant(conn,owner,amount,'admin_wallet',now,state='WALLET',policy_id='legacy_ecofi_pause_v1',op='member-create:'+username)
                engine._wallet_projection(conn,owner,now)
        return jsonify(success=True)

    def admin_member_topup(self):
        data=self.data();now,mono=self.now();username=str(data.get('username',''))
        amount=storage.to_us(float(data.get('minutes',0))*60)
        if amount<=0:raise ValueError('invalid_seconds')
        op=self.op_id(data,'admin-wallet')
        with self.p.db_connection() as conn:
            self.require_ready(conn)
            if not conn.execute('SELECT 1 FROM members WHERE username=?',(username,)).fetchone():raise ValueError('member_not_found')
            owner=engine.get_or_create_owner(conn,'member',username,now)
            digest=hashlib.sha256(json.dumps({'username':username,'amount_us':amount},sort_keys=True).encode()).hexdigest()
            prior=engine.one(conn,'SELECT * FROM value_operations WHERE operation_id=?',(op,))
            if prior:
                if prior['owner_id']!=owner or prior['payload_hash']!=digest:raise ValueError('operation_id_conflict')
            else:
                engine._create_grant(conn,owner,amount,'admin_wallet',now,state='WALLET',policy_id='legacy_ecofi_pause_v1',op=op)
                conn.execute('INSERT INTO value_operations VALUES (?,?,?,?,?,?)',(op,owner,'ADMIN_WALLET',digest,'{"success":true}',now))
            engine._wallet_projection(conn,owner,now)
        return jsonify(success=True)

    def admin_member_delete(self):
        username=str(self.data().get('username',''));now,mono=self.now()
        with self.p.db_connection() as conn:
            owner=engine.get_or_create_owner(conn,'member',username,now)
            if conn.execute("SELECT 1 FROM time_grants WHERE owner_id=? AND remaining_us>0 AND state NOT IN ('EXPIRED','DEPLETED','MOVED')",(owner,)).fetchone():
                raise ValueError('member_has_credit_transfer_or_settle_before_deletion')
            conn.execute('DELETE FROM members WHERE username=?',(username,))
        return jsonify(success=True)

    def policy(self):
        now,mono=self.now()
        with self.p.db_connection() as conn:
            self.require_ready(conn)
            if request.method=='POST':
                pid=engine.create_policy(conn,self.data(),now)
            else:pid=storage.metadata(conn,'active_policy')
            value=engine.one(conn,'SELECT * FROM time_policy_versions WHERE id=?',(pid,))
            value['brackets']=json.loads(value.pop('brackets_json'))
            if not value['brackets']:
                value['brackets']=list(time_policy.DEFAULT_VALIDITY_BRACKETS)
        return jsonify(success=True,policy=value)

    def diagnostics(self):
        with self.p.db_connection() as conn:
            bad=engine.all_rows(conn,'''SELECT a.id,a.balance_us,g.remaining_us FROM ledger_accounts a
                JOIN time_grants g ON a.grant_id=g.id WHERE a.balance_us<>g.remaining_us''')
            journals=engine.all_rows(conn,'SELECT journal_id,SUM(delta_us) AS delta FROM time_ledger WHERE journal_id IS NOT NULL GROUP BY journal_id HAVING SUM(delta_us)<>0')
            pending=conn.execute("SELECT COUNT(*) FROM network_intents WHERE status='PENDING'").fetchone()[0]
            held=conn.execute('SELECT COUNT(*) FROM deposit_recovery WHERE resolved_at IS NULL').fetchone()[0]
            ready=storage.metadata(conn,'ready','0')=='1'
        return jsonify(success=True,ready=ready,worker_healthy=self.healthy(),clock_trusted=self.clock_trusted(),
                       balance_mismatches=bad,unbalanced_journals=journals,pending_network_intents=pending,held_deposit_events=held)

    def open_gate(self):
        if not self.p.license_valid():raise ValueError('machine_unlicensed')
        data=self.data();ip,mac=self.identity();now,mono=self.now()
        with self.p.db_connection() as conn:
            self.require_ready(conn)
            if self.config(conn,'hw_bin_full','0')=='1' or self.p.esp32.get_state().get('is_bin_full',False):
                raise ValueError('storage_bin_full')
            cd=self.resolve(conn,ip,mac,now,mono)
            timeout=int(self.config(conn,'drop_timeout','60'))
            opened=engine.one(conn,"SELECT * FROM deposit_sessions WHERE status='OPEN' ORDER BY created_at LIMIT 1")
            if opened and (now - opened.get('updated_at', opened['created_at']) > timeout + 5):
                conn.execute("UPDATE deposit_sessions SET status='HOLD',error='Deposit timeout expired' WHERE id=?",(opened['id'],))
                opened=None
            if opened and opened['owner_id']!=cd['owner_id']:raise ValueError('another_depositor_active')
            if opened:
                sid=opened['id']
            else:
                sid=str(uuid.uuid4())
                rates=engine.all_rows(conn,'SELECT bottles,minutes FROM promo_rates ORDER BY bottles DESC')
                pricing={'base_minutes':int(self.config(conn,'minutes_per_bottle','10')),'rates':rates,
                         'policy_version_id':storage.metadata(conn,'active_policy')}
                conn.execute('''INSERT INTO deposit_sessions(id,owner_id,connection_id,status,pricing_json,created_at,updated_at)
                    VALUES (?,?,?,'OPEN',?,?,?)''',(sid,cd['owner_id'],cd['id'],json.dumps(pricing),now,now))
        session['deposit_session_id']=sid
        self.p.active_depositor_ip=ip;self.p.active_depositor_timeout=now+timeout+5
        self.p.transmit_to_esp32({'cmd':'OPEN_GATE','timeout':timeout,'session_id':sid,'protocol':2})
        return jsonify(success=True,timeout=timeout,deposit_session_id=sid)

    def reward_seconds(self,pricing,bottles):
        left=bottles;minutes=0
        for rate in sorted(pricing['rates'],key=lambda r:r['bottles'],reverse=True):
            if rate['bottles']<=0 or rate['minutes']<0:raise ValueError('invalid_pricing_snapshot')
            count,left=divmod(left,rate['bottles']);minutes+=count*rate['minutes']
        return (minutes+left*pricing['base_minutes'])*60

    def finalize(self,conn,deposit,now,mono):
        if deposit['status']=='FINALIZED':return json.loads(deposit['response_json'])
        bottles=conn.execute('SELECT COALESCE(SUM(bottles),0) FROM deposit_events WHERE session_id=?',(deposit['id'],)).fetchone()[0]
        pricing=json.loads(deposit['pricing_json']);seconds=self.reward_seconds(pricing,bottles)
        if seconds:
            cd=engine.connection(conn,deposit['connection_id'])
            if cd['owner_id']!=deposit['owner_id']:raise ValueError('deposit_owner_requires_recovery')
            result=engine.apply_operation(conn,deposit['owner_id'],cd,'TOP_UP_GRANT',
                {'seconds':seconds,'origin':'bottle','source_ref':deposit['id'],'policy_version_id':pricing['policy_version_id']},
                'deposit:'+deposit['id'],now,mono)
            if not result.get('success'):raise ValueError(result['error'])
        result={'success':True,'bottles_credited':bottles,'added_minutes':seconds/60,'deposit_session_id':deposit['id']}
        conn.execute("UPDATE deposit_sessions SET status='FINALIZED',response_json=?,updated_at=? WHERE id=?",(json.dumps(result),now,deposit['id']))
        return result

    def done(self):
        data=self.data();ip,mac=self.identity();now,mono=self.now()
        with self.p.db_connection() as conn:
            self.require_ready(conn);cd=self.resolve(conn,ip,mac,now,mono)
            sid=data.get('deposit_session_id') or session.get('deposit_session_id')
            if sid:
                deposit=engine.one(conn,'SELECT * FROM deposit_sessions WHERE id=?',(sid,))
            else:
                deposit=engine.one(conn,"SELECT * FROM deposit_sessions WHERE owner_id=? AND status IN ('OPEN','HOLD') ORDER BY created_at DESC LIMIT 1",(cd['owner_id'],))
            if not deposit or deposit['owner_id']!=cd['owner_id']:raise ValueError('no_owned_deposit')
            result=self.finalize(conn,deposit,now,mono)
        self.p.active_depositor_ip=None;self.p.active_depositor_timeout=0
        self.p.transmit_to_esp32({'cmd':'CLOSE_GATE','session_id':deposit['id'],'protocol':2})
        self.restore_projections();self.reconcile()
        return jsonify(result)

    def on_event(self,raw):
        try:
            data=json.loads(raw);event=data.get('event');now,mono=self.now()
            if event=='CREDIT_ADD':
                event_id=data.get('event_id');sid=data.get('session_id');bottles=data.get('bottles')
                encoded=json.dumps(data,sort_keys=True)
                with self.p.db_connection() as conn:
                    deposit=engine.one(conn,'SELECT * FROM deposit_sessions WHERE id=?',(sid,)) if isinstance(sid,str) else None
                    valid=isinstance(event_id,str) and 0<len(event_id)<=160 and isinstance(bottles,int) and not isinstance(bottles,bool) and bottles==1
                    previous=engine.one(conn,'SELECT * FROM deposit_events WHERE event_id=?',(event_id,)) if valid else None
                    if previous:
                        if previous['session_id']!=sid or previous['bottles']!=bottles:raise ValueError('event_identity_conflict')
                    elif not valid or not deposit or deposit['status']=='FINALIZED':
                        recovery_id=event_id if valid else 'legacy:'+hashlib.sha256(encoded.encode()).hexdigest()
                        conn.execute('INSERT OR IGNORE INTO deposit_recovery(event_id,payload_json,reason,received_at) VALUES (?,?,?,?)',
                            (recovery_id,encoded,'unknown_or_late_deposit_event',now))
                    else:
                        conn.execute('INSERT INTO deposit_events VALUES (?,?,?,?)',(event_id,sid,bottles,now))
                        conn.execute('UPDATE deposit_sessions SET updated_at=? WHERE id=?',(now,sid))
                        day=datetime.fromtimestamp(now).strftime('%Y-%m-%d')
                        conn.execute('INSERT OR IGNORE INTO stats(date,total_bottles) VALUES (?,0)',(day,))
                        conn.execute('UPDATE stats SET total_bottles=total_bottles+? WHERE date=?',(bottles,day))
                # ACK only after durable receipt (or durable quarantine) commits.
                if isinstance(event_id,str):self.p.transmit_to_esp32({'cmd':'CREDIT_ACK','event_id':event_id,'session_id':sid,'protocol':2})
                if valid and deposit and deposit['status']=='OPEN':
                    self.p.transmit_to_esp32({'cmd':'OPEN_GATE','session_id':sid,'protocol':2,'timeout':int(self.p.get_config('drop_timeout','60'))})
                self.restore_projections()
            elif event=='DEPOSIT_RECOVERY':
                event_id=data.get('event_id');sid=data.get('session_id')
                if not isinstance(event_id,str) or not 0<len(event_id)<=160:raise ValueError('invalid_recovery_receipt')
                with self.p.db_connection() as conn:
                    conn.execute('INSERT OR IGNORE INTO deposit_recovery(event_id,payload_json,reason,received_at) VALUES (?,?,?,?)',
                        (event_id,json.dumps(data,sort_keys=True),'Physical drop outcome uncertain; inspect before awarding credit.',now))
                    conn.execute("UPDATE deposit_sessions SET status='HOLD',updated_at=? WHERE id=? AND status='OPEN'",(now,sid))
                self.p.transmit_to_esp32({'cmd':'CREDIT_ACK','event_id':event_id,'session_id':sid,'protocol':2})
            elif event=='REJECTED':
                with self.p.db_connection() as conn:
                    deposit=engine.one(conn,"SELECT * FROM deposit_sessions WHERE id=? AND status='OPEN'",(data.get('session_id'),))
                if deposit:
                    self.p.transmit_to_esp32({'cmd':'OPEN_GATE','session_id':deposit['id'],'protocol':2,
                        'timeout':int(self.p.get_config('drop_timeout','60'))})
            elif event=='TIMEOUT':
                with self.p.db_connection() as conn:
                    sid=data.get('session_id')
                    if sid:conn.execute("UPDATE deposit_sessions SET status='HOLD' WHERE id=? AND status='OPEN'",(sid,))
                self.p.active_depositor_ip=None;self.p.active_depositor_timeout=0
            elif event=='BIN_FULL':self.p.set_config('hw_bin_full','1')
            elif event=='BIN_OK':self.p.set_config('hw_bin_full','0')
        except Exception:
            self.p.log.exception('Device event was not acknowledged; replay/recovery is required')

    def recovery(self):
        now,mono=self.now()
        with self.p.db_connection() as conn:
            if request.method=='GET':
                return jsonify(success=True,events=engine.all_rows(conn,'SELECT * FROM deposit_recovery WHERE resolved_at IS NULL'))
            self.require_ready(conn);data=self.data()
            item=engine.one(conn,'SELECT * FROM deposit_recovery WHERE event_id=?',(data.get('event_id'),))
            if not item:raise ValueError('recovery_event_not_found')
            if item['resolved_at'] is not None:return jsonify(success=True,replayed=True)
            owner=engine.one(conn,'SELECT id FROM credit_owners WHERE id=?',(data.get('owner_id'),))
            if not owner:raise ValueError('owner_not_found')
            amount=storage.to_us(data.get('seconds',0))
            if amount<0 or not str(data.get('reason','')).strip():raise ValueError('explicit_amount_and_reason_required')
            payload=json.loads(item['payload_json'])
            held=engine.grant(conn,payload.get('grant_id')) if payload.get('grant_id') else None
            if held:
                if held['state']!='HELD' or amount!=held['remaining_us']:raise ValueError('held_credit_amount_must_match')
                engine._fragment(conn,held,owner['id'],amount,'UNUSED',now,'recovery:'+item['event_id'],'owner_recovery')
            elif amount:
                engine._create_grant(conn,owner['id'],amount,'legacy_recovery',now,'legacy_ecofi_pause_v1',
                    source_ref=item['event_id'],op='recovery:'+item['event_id'])
            conn.execute('UPDATE deposit_recovery SET resolved_at=?,reason=reason||? WHERE event_id=?',
                (now,'; resolution: '+str(data['reason']),item['event_id']))
        return jsonify(success=True)
