# -*- coding: utf-8 -*-
"""Transactional, exact entitlement accounting (Python 3.5 compatible).

Only acknowledged, bounded network leases are billed. All helpers join the
caller's write transaction; no value helper commits caller-owned work.
"""
import hashlib
import ipaddress
import json
import math
import os
import re
import time
import uuid

import time_policy
from time_schema import SCALE, to_us, transaction, metadata, set_metadata

try:
    with open('/proc/sys/kernel/random/boot_id') as boot_file:
        BOOT_ID = boot_file.read().strip()
except OSError:
    BOOT_ID = str(uuid.uuid4())

LIVE = ('ACTIVE','PAUSED','UNUSED','WALLET','ESCROW','HELD')
TERMINAL = ('DEPLETED','EXPIRED','MOVED')
LEASE_SECONDS = 15


def generate_uuid():
    return str(uuid.uuid4())


def one(conn, sql, args=()):
    cursor = conn.execute(sql,args)
    row = cursor.fetchone()
    return dict(zip([d[0] for d in cursor.description],row)) if row else None


def all_rows(conn, sql, args=()):
    cursor = conn.execute(sql,args)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names,row)) for row in cursor.fetchall()]


def connection(conn, cid):
    return one(conn,'SELECT * FROM connections WHERE id=?',(cid,))


def grant(conn, gid):
    return one(conn,'SELECT * FROM time_grants WHERE id=?',(gid,)) if gid else None


def mono_us(value):
    # A monotonic timestamp may exceed the per-credit duration limit.
    return int(round(float(value)*SCALE))


def normalize_mac(mac):
    value = str(mac or '').lower().strip().replace('-',':')
    if not re.match(r'^[0-9a-f]{2}(:[0-9a-f]{2}){5}$',value) or value in ('00:00:00:00:00:00','ff:ff:ff:ff:ff:ff'):
        raise ValueError('invalid_mac')
    return value


def get_or_create_owner(conn, owner_type, owner_key, now_utc):
    key = str(owner_key).strip()
    if owner_type == 'member':
        key = 'member:' + (key[7:] if key.startswith('member:') else key)
    elif owner_type == 'device':
        key = 'mac:' + normalize_mac(key[4:] if key.startswith('mac:') else key)
    row = one(conn,'SELECT * FROM credit_owners WHERE owner_type=? AND owner_key=?',(owner_type,key))
    if row:
        return row['id']
    if owner_type=='member':
        old = one(conn,'SELECT * FROM credit_owners WHERE owner_type=? AND owner_key=?',('member',key[7:]))
        if old:
            conn.execute('UPDATE credit_owners SET owner_key=? WHERE id=?',(key,old['id']))
            return old['id']
    oid = generate_uuid()
    conn.execute('INSERT INTO credit_owners(id,owner_type,owner_key,created_at) VALUES (?,?,?,?)',(oid,owner_type,key,now_utc))
    return oid


def get_or_create_device(conn, mac, owner_id, ip, now_utc):
    mac = normalize_mac(mac)
    row = one(conn,'SELECT * FROM devices WHERE mac=?',(mac,))
    if row:
        if row['owner_id'] != owner_id:
            raise ValueError('explicit_member_binding_required')
        conn.execute('UPDATE devices SET last_ip=?,last_seen_at=? WHERE mac=?',(ip,now_utc,mac))
    else:
        conn.execute('INSERT INTO devices VALUES (?,?,?,?)',(mac,owner_id,ip,now_utc))


def _intent(conn, cd, desired, now):
    try:
        ipaddress.ip_address(cd['ip'])
    except (ValueError,TypeError):
        return
    conn.execute('''INSERT INTO network_intents(connection_id,version,ip,mac,desired_state,created_at)
        VALUES (?,?,?,?,?,?)''',(cd['id'],cd['binding_version'],cd['ip'],cd['mac'],desired,now))


def desired_state(conn, cd):
    g = grant(conn,cd['selected_grant_id'])
    if cd['admin_suspended'] or cd['service_suspended'] or cd['disconnect_paused']:
        return 'DISCONNECTED'
    if not g or g['owner_id']!=cd['owner_id'] or not g['remaining_us']:
        return 'DISCONNECTED'
    return g['state'] if g['state'] in ('ACTIVE','PAUSED') else 'DISCONNECTED'


def refresh_desired(conn, cid, now, mono, force=False):
    cd = connection(conn,cid)
    state = desired_state(conn,cd)
    if state != cd['desired_state'] or force:
        conn.execute('''UPDATE connections SET desired_state=?,applied_state='DISCONNECTED',
            binding_version=binding_version+1,authorized_until_us=NULL,last_mono_us=?,
            last_settled_at_mono=?,last_settled_at_utc=?,boot_id=?,updated_at=? WHERE id=?''',
            (state,mono_us(mono),mono,now,BOOT_ID,now,cid))
        _intent(conn,connection(conn,cid),state,now)


def get_or_create_connection(conn, ip, mac, owner_id, now_utc, mono_now):
    mac = normalize_mac(mac)
    ip = str(ipaddress.ip_address(ip))
    cd = one(conn,'SELECT * FROM connections WHERE mac=?',(mac,))
    if cd and cd['owner_id']!=owner_id:
        raise ValueError('explicit_member_binding_required')
    occupied = one(conn,'SELECT * FROM connections WHERE ip=? AND mac<>?',(ip,mac))
    if occupied:
        _settle(conn,occupied,now_utc,mono_now)
        _intent(conn,occupied,'DISCONNECTED',now_utc)
        conn.execute('''UPDATE connections SET ip=?,desired_state='DISCONNECTED',applied_state='DISCONNECTED',
            authorized_until_us=NULL,binding_version=binding_version+1 WHERE id=?''',
            ('detached:'+occupied['id'],occupied['id']))
    get_or_create_device(conn,mac,owner_id,ip,now_utc)
    if cd:
        if cd['ip']!=ip:
            _settle(conn,cd,now_utc,mono_now)
            _intent(conn,cd,'DISCONNECTED',now_utc)
            conn.execute('UPDATE connections SET ip=?,updated_at=? WHERE id=?',(ip,now_utc,cd['id']))
            refresh_desired(conn,cd['id'],now_utc,mono_now,True)
        return connection(conn,cd['id'])
    cid = generate_uuid()
    conn.execute('''INSERT INTO connections(id,ip,mac,owner_id,selected_grant_id,desired_state,applied_state,
        binding_version,last_settled_at_mono,last_settled_at_utc,updated_at,boot_id,last_mono_us)
        VALUES (?,?,?,?,NULL,'DISCONNECTED','DISCONNECTED',1,?,?,?,?,?)''',
        (cid,ip,mac,owner_id,mono_now,now_utc,now_utc,BOOT_ID,mono_us(mono_now)))
    _activate_next(conn,cid,now_utc,mono_now)
    return connection(conn,cid)


def create_pause_budget(conn, owner_id, pause_count_max, now_utc):
    if pause_count_max is not None and (isinstance(pause_count_max,bool) or int(pause_count_max)!=pause_count_max or pause_count_max<0):
        raise ValueError('invalid_pause_count')
    bid = generate_uuid()
    conn.execute('INSERT INTO pause_budgets VALUES (?,?,?,0,?)',(bid,owner_id,pause_count_max,now_utc))
    return {'id':bid,'pause_count_max':pause_count_max,'used_count':0}


def _account(conn, aid, owner=None, gid=None):
    conn.execute('INSERT OR IGNORE INTO ledger_accounts(id,owner_id,grant_id,balance_us) VALUES (?,?,?,0)',(aid,owner,gid))


def _move(conn, source, destination, amount, reason, now, op=None):
    """A balanced two-entry journal; custody accounts can never go negative."""
    if amount<0:
        raise ValueError('negative_movement')
    journal = generate_uuid()
    for aid, delta in ((source,-amount),(destination,amount)):
        _account(conn,aid)
        acct = one(conn,'SELECT * FROM ledger_accounts WHERE id=?',(aid,))
        before = acct['balance_us']; after = before+delta
        if acct['grant_id'] and after<0:
            raise ValueError('insufficient_balance')
        conn.execute('UPDATE ledger_accounts SET balance_us=? WHERE id=?',(after,aid))
        if acct['grant_id']:
            conn.execute('UPDATE time_grants SET remaining_us=?,remaining_seconds=?,updated_at=? WHERE id=?',
                         (after,after/float(SCALE),now,acct['grant_id']))
        conn.execute('''INSERT INTO time_ledger(event_id,operation_id,grant_id,owner_id,reason,
            delta_seconds,balance_before,balance_after,created_at,journal_id,account_id,delta_us,before_us,after_us)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (generate_uuid(),op,acct['grant_id'],acct['owner_id'] or 'system',reason,delta/float(SCALE),
             before/float(SCALE),after/float(SCALE),now,journal,aid,delta,before,after))


def _create_grant(conn, owner, amount, origin, now, policy_id=None, source_ref=None,
                  state='UNUSED', template=None, external=True, op=None):
    policy_id = policy_id or metadata(conn,'active_policy','pisofi_time_v1')
    policy = one(conn,'SELECT * FROM time_policy_versions WHERE id=?',(policy_id,))
    if not policy:
        raise ValueError('policy_not_found')
    if source_ref and conn.execute('SELECT 1 FROM time_grants WHERE origin=? AND source_ref=?',(origin,source_ref)).fetchone():
        raise ValueError('source_already_issued')
    if template:
        bid = template['pause_budget_id']; policy_id = template['policy_version_id']
        duration = template['validity_duration_sec']; activated = template['activated_at_utc']; until = template['valid_until_utc']
        mode = template['validity_mode']
    else:
        bid = create_pause_budget(conn,owner,policy['pause_count_max'],now)['id']
        duration = time_policy.calculate_bracket_validity(amount/float(SCALE),json.loads(policy['brackets_json']),policy['global_validity_min'])
        activated = None; until = None; mode = 'legacy_pause_expiry' if policy_id=='legacy_ecofi_pause_v1' else 'activation_relative'
    gid = generate_uuid()
    issued = amount if external else 0
    conn.execute('''INSERT INTO time_grants(id,owner_id,origin,source_ref,issued_seconds,remaining_seconds,state,
        validity_mode,validity_duration_sec,activated_at_utc,valid_until_utc,pause_budget_id,policy_version_id,
        created_at,updated_at,issued_us,remaining_us,dl_kbps,ul_kbps,pause_allowed)
        VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,0,?,?,?)''',
        (gid,owner,origin,source_ref,issued/float(SCALE),state,mode,duration,activated,until,bid,policy_id,now,now,issued,
         (template or {}).get('dl_kbps',3072),(template or {}).get('ul_kbps',1536),(template or {}).get('pause_allowed',1)))
    if template:
        conn.execute('UPDATE time_grants SET speed_override=? WHERE id=?',(template.get('speed_override',0),gid))
    _account(conn,'grant:'+gid,owner,gid)
    if external:
        _move(conn,'external:legacy' if origin.startswith('legacy') else 'external:issuance','grant:'+gid,amount,origin+'_reward',now,op)
    return grant(conn,gid)


def _close_pauses(conn,gid,now,status='CLOSED'):
    conn.execute("UPDATE grant_pauses SET status=?,closed_at_utc=? WHERE grant_id=? AND status='OPEN'",(status,now,gid))


def _terminal(conn,g,now,reason,op=None):
    if g['state'] in TERMINAL:
        return
    if g['remaining_us']:
        _move(conn,'grant:'+g['id'],'sink:expired',g['remaining_us'],reason,now,op)
    conn.execute("UPDATE time_grants SET state='EXPIRED',updated_at=? WHERE id=?",(now,g['id']))
    _close_pauses(conn,g['id'],now,'EXPIRED')


def _select(conn,cid,g,now,mono):
    if conn.execute('SELECT 1 FROM connections WHERE selected_grant_id=? AND id<>?',(g['id'],cid)).fetchone():
        raise ValueError('grant_bound_elsewhere')
    if g['state']=='UNUSED':
        activated = g['activated_at_utc'] if g['activated_at_utc'] is not None else now
        until = g['valid_until_utc']
        if g['activated_at_utc'] is None:
            until = time_policy.calculate_activation_validity(now,g['validity_duration_sec'])
        conn.execute("UPDATE time_grants SET state='ACTIVE',activated_at_utc=?,valid_until_utc=?,updated_at=? WHERE id=?",(activated,until,now,g['id']))
    conn.execute('UPDATE connections SET selected_grant_id=? WHERE id=?',(g['id'],cid))
    refresh_desired(conn,cid,now,mono,True)


def _activate_next(conn,cid,now,mono):
    cd = connection(conn,cid)
    if not cd:
        return
    binding = one(conn,'SELECT connection_id FROM owner_bindings WHERE owner_id=?',(cd['owner_id'],))
    if binding and binding['connection_id']!=cid:
        refresh_desired(conn,cid,now,mono)
        return
    current = grant(conn,cd['selected_grant_id'])
    if current and current['state'] not in TERMINAL and current['remaining_us']:
        refresh_desired(conn,cid,now,mono)
        return
    conn.execute('UPDATE connections SET selected_grant_id=NULL WHERE id=?',(cid,))
    if not cd['admin_suspended']:
        candidate = one(conn,'''SELECT g.* FROM time_grants g WHERE g.owner_id=? AND g.state='UNUSED'
            AND g.remaining_us>0 AND (g.valid_until_utc IS NULL OR g.valid_until_utc>?)
            AND NOT EXISTS(SELECT 1 FROM connections c WHERE c.selected_grant_id=g.id)
            ORDER BY g.created_at,g.rowid LIMIT 1''',(cd['owner_id'],now))
        if candidate:
            _select(conn,cid,candidate,now,mono)
            return
    refresh_desired(conn,cid,now,mono)


def _settle(conn,cd,now,mono):
    cd = connection(conn,cd['id'])  # Never trust a caller's old checkpoint or selection.
    g = grant(conn,cd['selected_grant_id'])
    debit = 0; tick = mono_us(mono)
    if g and g['owner_id']!=cd['owner_id']:
        raise ValueError('forbidden_grant_owner')
    if g and g['remaining_us'] is None:
        raise ValueError('migration_required')
    if (g and g['state']=='ACTIVE' and cd['boot_id']==BOOT_ID and cd['applied_state']=='ACTIVE'
            and not cd['admin_suspended'] and not cd['service_suspended'] and not cd['disconnect_paused']):
        start = cd['last_mono_us'] if cd['last_mono_us'] is not None else tick
        end = min(tick,cd['authorized_until_us'] or start)
        if g['valid_until_utc'] is not None:
            end = min(end,start+max(0,to_us(g['valid_until_utc']-cd['last_settled_at_utc'])))
        debit = min(g['remaining_us'],max(0,end-start))
        if debit:
            _move(conn,'grant:'+g['id'],'sink:consumed',debit,'time_consumed',now)
    if cd['boot_id']!=BOOT_ID or (cd['last_mono_us'] is not None and tick<cd['last_mono_us']):
        conn.execute("UPDATE connections SET applied_state='DISCONNECTED',authorized_until_us=NULL WHERE id=?",(cd['id'],))
    conn.execute('''UPDATE connections SET last_mono_us=?,last_settled_at_mono=?,last_settled_at_utc=?,boot_id=?,updated_at=? WHERE id=?''',
                 (tick,mono,now,BOOT_ID,now,cd['id']))
    if g:
        g = grant(conn,g['id'])
        if g['valid_until_utc'] is not None and now>=g['valid_until_utc']:
            _terminal(conn,g,now,'validity_expired')
        elif g['remaining_us']==0 and g['state'] not in TERMINAL:
            conn.execute("UPDATE time_grants SET state='DEPLETED',updated_at=? WHERE id=?",(now,g['id']))
            _close_pauses(conn,g['id'],now)
    return grant(conn,g['id']) if g else None,debit/float(SCALE)


def settle_connection_balance(conn,connection_data,now_utc,mono_now):
    with transaction(conn):
        result = _settle(conn,connection_data,now_utc,mono_now)
        _activate_next(conn,connection_data['id'],now_utc,mono_now)
        return result


def _due(conn,now,mono):
    events = {'expired':0,'resumed':0}
    before_expired = conn.execute("SELECT COUNT(*) FROM time_grants WHERE state='EXPIRED'").fetchone()[0]
    for cd in all_rows(conn,'SELECT * FROM connections'):
        _settle(conn,cd,now,mono)
    for g in all_rows(conn,"SELECT * FROM time_grants WHERE state NOT IN ('EXPIRED','DEPLETED','MOVED') AND valid_until_utc IS NOT NULL AND valid_until_utc<=?",(now,)):
        _terminal(conn,g,now,'validity_expired'); events['expired']+=1
    for p in all_rows(conn,"SELECT * FROM grant_pauses WHERE status='OPEN' AND effective_deadline_utc IS NOT NULL AND effective_deadline_utc<=?",(now,)):
        g = grant(conn,p['grant_id'])
        if g['state']!='PAUSED':
            _close_pauses(conn,g['id'],now)
        elif (g['valid_until_utc'] is not None and now>=g['valid_until_utc']) or p['timeout_action']=='expire':
            _terminal(conn,g,now,'pause_timeout_expired');events['expired']+=1
        else:
            _close_pauses(conn,g['id'],now)
            bound = conn.execute('SELECT 1 FROM connections WHERE selected_grant_id=?',(g['id'],)).fetchone()
            conn.execute('UPDATE time_grants SET state=?,updated_at=? WHERE id=?',('ACTIVE' if bound else 'UNUSED',now,g['id']))
            for cd in all_rows(conn,'SELECT * FROM connections WHERE selected_grant_id=?',(g['id'],)):
                refresh_desired(conn,cd['id'],now,mono,True)
            events['resumed']+=1
    for cd in all_rows(conn,'SELECT * FROM connections'):
        _activate_next(conn,cd['id'],now,mono)
    events['expired'] = conn.execute("SELECT COUNT(*) FROM time_grants WHERE state='EXPIRED'").fetchone()[0]-before_expired
    return events


def check_due_events(conn,now_utc,mono_now):
    with transaction(conn):
        return _due(conn,now_utc,mono_now)


def bulk_settle_active_connections(conn,now_utc,mono_now):
    check_due_events(conn,now_utc,mono_now)
    return [(r['ip'],r['desired_state']) for r in all_rows(conn,'SELECT ip,desired_state FROM connections')]


def pause_eligibility(conn,cd,g,now):
    if not g:
        return False,'no_active_grant'
    policy = one(conn,'SELECT * FROM time_policy_versions WHERE id=?',(g['policy_version_id'],))
    budget = one(conn,'SELECT * FROM pause_budgets WHERE id=?',(g['pause_budget_id'],))
    return time_policy.can_pause_grant(g['state'],g['remaining_us']/float(SCALE),budget['used_count'],now,
        g['valid_until_utc'],budget['pause_count_max'],policy['min_balance_sec'],policy['max_balance_sec'],
        metadata(conn,'pause_allowed','1')=='1',bool(g['pause_allowed'] and policy['pause_allowed']),bool(cd['admin_suspended']))


def _pause(conn,cd,g,now):
    if cd['admin_suspended']:
        raise ValueError('admin_suspended')
    if g and g['state']=='PAUSED':
        return
    ok,reason = pause_eligibility(conn,cd,g,now)
    if not ok:
        raise ValueError(reason)
    policy = one(conn,'SELECT * FROM time_policy_versions WHERE id=?',(g['policy_version_id'],))
    duration = policy['pause_duration_sec']
    if g['validity_mode']=='legacy_pause_expiry':
        minutes = g['remaining_us']/float(SCALE*60)
        duration = int(max(24,min(720,12+1.2*math.sqrt(minutes)+0.025*minutes))*3600)
    deadlines = time_policy.calculate_pause_deadlines(now,duration,g['valid_until_utc'])
    conn.execute('UPDATE pause_budgets SET used_count=used_count+1 WHERE id=?',(g['pause_budget_id'],))
    conn.execute("UPDATE time_grants SET state='PAUSED',updated_at=? WHERE id=?",(now,g['id']))
    conn.execute('''INSERT INTO grant_pauses(id,grant_id,paused_at_utc,pause_deadline_utc,effective_deadline_utc,
        pause_reason,timeout_action,status,created_at) VALUES (?,?,?,?,?,'user',?,'OPEN',?)''',
        (generate_uuid(),g['id'],now,deadlines['pause_deadline_utc'],deadlines['effective_next_deadline_utc'],policy['pause_timeout_action'],now))


def _fragment(conn,g,owner,amount,state,now,op,reason):
    if not 0<amount<=g['remaining_us']:
        raise ValueError('insufficient_balance')
    child = _create_grant(conn,owner,amount,'fragment',now,template=g,state=state,external=False,op=op)
    _move(conn,'grant:'+g['id'],'grant:'+child['id'],amount,reason,now,op)
    if amount==g['remaining_us']:
        conn.execute("UPDATE time_grants SET state='MOVED' WHERE id=?",(g['id'],))
        _close_pauses(conn,g['id'],now)
    return grant(conn,child['id'])


def snapshot(conn,cid,now):
    cd = connection(conn,cid); g = grant(conn,cd['selected_grant_id'])
    p = one(conn,"SELECT * FROM grant_pauses WHERE grant_id=? AND status='OPEN'",(g['id'],)) if g else None
    b = one(conn,'SELECT * FROM pause_budgets WHERE id=?',(g['pause_budget_id'],)) if g else None
    policy = one(conn,'SELECT * FROM time_policy_versions WHERE id=?',(g['policy_version_id'],)) if g else None
    ok,reason = pause_eligibility(conn,cd,g,now)
    remaining = (g['remaining_us'] or 0)/float(SCALE) if g else 0
    cap = b['pause_count_max'] if b else 0; count = b['used_count'] if b else 0
    valid = g['valid_until_utc'] if g else None; deadline = p['pause_deadline_utc'] if p else None
    effective = min(x for x in (valid,deadline) if x is not None) if valid is not None or deadline is not None else None
    event = ('expire' if valid is not None and (deadline is None or valid<=deadline) else p['timeout_action']) if effective is not None else 'none'
    return {'connection_id':cid,'binding_version':cd['binding_version'],'owner_id':cd['owner_id'],
        'grant_id':g['id'] if g else None,'state':g['state'] if g else 'DISCONNECTED',
        'remaining_seconds':remaining,'is_paused':bool(g and (g['state']=='PAUSED' or cd['admin_suspended'] or cd['disconnect_paused'])),
        'admin_paused':bool(cd['admin_suspended']),'user_paused':bool(g and g['state']=='PAUSED'),
        'paused_at':p['paused_at_utc'] if p else 0,'pause_deadline_utc':deadline,'valid_until_utc':valid,
        'effective_deadline_utc':effective,'expires_at':effective or 0,'next_event_type':event,
        'pause_count_used':count,'pause_count_max':cap,'pauses_left':max(0,cap-count) if cap is not None else None,
        'can_pause':ok,'pause_denial_reason':reason,'policy_version_id':g['policy_version_id'] if g else None,
        'seconds_until_pausable':time_policy.seconds_until_pausable_by_max(remaining,policy['max_balance_sec']) if policy else 0,
        'desired_state':cd['desired_state'],'applied_state':cd['applied_state'],'access_error':cd['access_error'],
        'dl_kbps':g['dl_kbps'] if g else 3072,'ul_kbps':g['ul_kbps'] if g else 1536,
        'grants':[{'id':v['id'],'state':v['state'],'remaining_seconds':(v['remaining_us'] or 0)/float(SCALE),
                   'valid_until_utc':v['valid_until_utc'],'policy_version_id':v['policy_version_id']}
                  for v in all_rows(conn,"SELECT * FROM time_grants WHERE owner_id=? AND state IN ('ACTIVE','PAUSED','UNUSED','HELD') ORDER BY created_at,rowid",(cd['owner_id'],))]}


def wallet_us(conn,owner,now):
    return conn.execute("SELECT COALESCE(SUM(remaining_us),0) FROM time_grants WHERE owner_id=? AND state='WALLET' AND (valid_until_utc IS NULL OR valid_until_utc>?)",(owner,now)).fetchone()[0]


def _wallet_projection(conn,owner,now):
    row = one(conn,"SELECT owner_key FROM credit_owners WHERE id=? AND owner_type='member'",(owner,))
    if row and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='members'").fetchone():
        conn.execute('UPDATE members SET wallet_minutes=? WHERE username=?',(wallet_us(conn,owner,now)//(SCALE*60),row['owner_key'][7:]))


def bind_member(conn,cid,member_owner,now,mono):
    """Called only after credential verification, inside the adapter transaction."""
    with transaction(conn):
        cd = connection(conn,cid)
        owner = one(conn,"SELECT * FROM credit_owners WHERE id=? AND owner_type='member'",(member_owner,))
        if not owner:
            raise ValueError('member_not_found')
        _due(conn,now,mono)
        choices = []
        for old in all_rows(conn,'SELECT * FROM connections WHERE owner_id=? OR id=?',(member_owner,cid)):
            if old['selected_grant_id']:
                choices.append(old['selected_grant_id'])
                conn.execute("UPDATE time_grants SET state='UNUSED' WHERE id=? AND state='ACTIVE'",(old['selected_grant_id'],))
            _intent(conn,old,'DISCONNECTED',now)
            conn.execute("UPDATE connections SET selected_grant_id=NULL,desired_state='DISCONNECTED',applied_state='DISCONNECTED',authorized_until_us=NULL,binding_version=binding_version+1 WHERE id=?",(old['id'],))
            if old['id']!=cid:
                detached_owner=get_or_create_owner(conn,'device','mac:'+old['mac'],now)
                conn.execute('UPDATE devices SET owner_id=? WHERE mac=?',(detached_owner,old['mac']))
                conn.execute('UPDATE connections SET owner_id=? WHERE id=?',(detached_owner,old['id']))
        # Anonymous device credit becomes member credit once, preserving each grant.
        old_owner = one(conn,'SELECT * FROM credit_owners WHERE id=?',(cd['owner_id'],))
        if old_owner['owner_type']=='device':
            conn.execute('UPDATE time_grants SET owner_id=? WHERE owner_id=?',(member_owner,cd['owner_id']))
            conn.execute('UPDATE ledger_accounts SET owner_id=? WHERE owner_id=?',(member_owner,cd['owner_id']))
            conn.execute("UPDATE deposit_sessions SET owner_id=? WHERE owner_id=? AND status<>'FINALIZED'",(member_owner,cd['owner_id']))
        elif cd['owner_id']!=member_owner:
            # Logging another member in never transfers the previous member's property.
            choices = [gid for gid in choices if grant(conn,gid)['owner_id']==member_owner]
        conn.execute('UPDATE devices SET owner_id=? WHERE mac=?',(member_owner,cd['mac']))
        conn.execute('UPDATE connections SET owner_id=? WHERE id=?',(member_owner,cid))
        conn.execute('DELETE FROM owner_bindings WHERE connection_id=?',(cid,))
        conn.execute('INSERT OR REPLACE INTO owner_bindings VALUES (?,?)',(member_owner,cid))
        for gid in choices:
            g = grant(conn,gid)
            if g['state']=='ACTIVE':
                conn.execute("UPDATE time_grants SET state='UNUSED' WHERE id=?",(gid,))
        eligible = [grant(conn,gid) for gid in choices if grant(conn,gid)['owner_id']==member_owner and grant(conn,gid)['state'] not in TERMINAL]
        if eligible:
            _select(conn,cid,eligible[0],now,mono)
        else:
            _activate_next(conn,cid,now,mono)
        return connection(conn,cid)


def _action(conn,cd,action,payload,op,now,mono):
    g = grant(conn,cd['selected_grant_id'])
    owner = cd['owner_id']
    if action=='TOP_UP_GRANT':
        amount = to_us(payload.get('seconds',0))
        if amount<=0:
            raise ValueError('invalid_seconds')
        new = _create_grant(conn,owner,amount,payload.get('origin','bottle'),now,payload.get('policy_version_id'),payload.get('source_ref'),op=op)
        conn.execute('UPDATE time_grants SET dl_kbps=?,ul_kbps=? WHERE id=?',
                     (int(payload.get('dl_kbps',3072)),int(payload.get('ul_kbps',1536)),new['id']))
        _activate_next(conn,cd['id'],now,mono)
        new = grant(conn,new['id'])
        return {'grant_id':new['id'],'issued_seconds':amount/float(SCALE),'state':new['state'],'valid_until_utc':new['valid_until_utc']}
    if action=='PAUSE':
        already = bool(g and g['state']=='PAUSED')
        _pause(conn,cd,g,now)
        return {'already_paused':already}
    elif action=='RESUME':
        if cd['admin_suspended']:
            raise ValueError('admin_suspended')
        if not g or g['state'] not in ('ACTIVE','PAUSED','HELD'):
            raise ValueError('no_active_grant')
        _close_pauses(conn,g['id'],now)
        conn.execute("UPDATE time_grants SET state='ACTIVE',updated_at=? WHERE id=?",(now,g['id']))
        conn.execute('UPDATE connections SET disconnect_paused=0 WHERE id=?',(cd['id'],))
    elif action=='SWITCH':
        target = grant(conn,payload.get('grant_id'))
        if not target or target['owner_id']!=owner or target['state'] not in ('UNUSED','PAUSED','HELD','ACTIVE') or not target['remaining_us']:
            raise ValueError('invalid_switch_target')
        if conn.execute('SELECT 1 FROM connections WHERE selected_grant_id=? AND id<>?',(target['id'],cd['id'])).fetchone():
            raise ValueError('grant_bound_elsewhere')
        if g and g['id']!=target['id'] and g['state']=='ACTIVE':
            _pause(conn,cd,g,now)
        if cd['admin_suspended']:
            raise ValueError('admin_suspended')
        if target['state'] in ('PAUSED','HELD'):
            _close_pauses(conn,target['id'],now)
            conn.execute("UPDATE time_grants SET state='UNUSED' WHERE id=?",(target['id'],))
            target = grant(conn,target['id'])
        _select(conn,cd['id'],target,now,mono)
    elif action in ('ADMIN_PAUSE','ADMIN_RESUME','DISCONNECT_PAUSE','DISCONNECT_RESUME'):
        column = 'admin_suspended' if action.startswith('ADMIN') else 'disconnect_paused'
        conn.execute('UPDATE connections SET '+column+'=? WHERE id=?',(int(action.endswith('PAUSE')),cd['id']))
    elif action=='ADMIN_DISCONNECT':
        if g and g['state']=='ACTIVE':
            conn.execute("UPDATE time_grants SET state='HELD' WHERE id=?",(g['id'],))
        conn.execute('UPDATE connections SET disconnect_paused=1 WHERE id=?',(cd['id'],))
    elif action=='ADMIN_SET_BALANCE':
        amount = to_us(payload.get('seconds',0))
        if amount<0:
            raise ValueError('invalid_seconds')
        if not g:
            if amount:
                return _action(conn,cd,'TOP_UP_GRANT',{'seconds':amount/float(SCALE),'origin':'admin'},op,now,mono)
        else:
            difference = amount-g['remaining_us']
            if difference>0:
                _move(conn,'external:correction','grant:'+g['id'],difference,'admin_adjustment',now,op)
            elif difference<0:
                _move(conn,'grant:'+g['id'],'external:correction',-difference,'admin_adjustment',now,op)
            if amount==0:
                conn.execute("UPDATE time_grants SET state='DEPLETED' WHERE id=?",(g['id'],));_close_pauses(conn,g['id'],now)
    elif action=='WALLET_SAVE':
        if not g or not g['remaining_us']:
            raise ValueError('no_active_grant')
        wallet_owner = payload['wallet_owner_id']
        if not conn.execute("SELECT 1 FROM credit_owners WHERE id=? AND owner_type='member'",(wallet_owner,)).fetchone():
            raise ValueError('member_not_found')
        if g['state']=='ACTIVE':
            _pause(conn,cd,g,now)
        amount = to_us(payload['seconds']) if 'seconds' in payload else g['remaining_us']
        _fragment(conn,grant(conn,g['id']),wallet_owner,amount,'WALLET',now,op,'wallet_save')
        _wallet_projection(conn,wallet_owner,now)
    elif action=='WALLET_WITHDRAW':
        amount = to_us(payload.get('seconds',0))
        if amount<=0 or amount>wallet_us(conn,owner,now):
            raise ValueError('insufficient_wallet_balance')
        left = amount
        for source in all_rows(conn,"SELECT * FROM time_grants WHERE owner_id=? AND state='WALLET' AND remaining_us>0 ORDER BY (valid_until_utc IS NULL),valid_until_utc,created_at,rowid",(owner,)):
            part = min(left,source['remaining_us'])
            _fragment(conn,source,owner,part,'UNUSED',now,op,'wallet_withdraw')
            left-=part
            if left==0:
                break
        _wallet_projection(conn,owner,now)
    elif action=='TRANSFER_CREATE':
        if not g:
            raise ValueError('no_active_grant')
        amount = to_us(payload['seconds']) if 'seconds' in payload else g['remaining_us']
        if amount<=0 or amount>g['remaining_us']:
            raise ValueError('insufficient_balance')
        if g['state']=='ACTIVE':
            _pause(conn,cd,g,now)
        code = uuid.uuid4().hex[:12].upper()
        escrow = get_or_create_owner(conn,'escrow','transfer:'+code,now)
        _fragment(conn,grant(conn,g['id']),escrow,amount,'ESCROW',now,op,'transfer_reserve')
        conn.execute('''INSERT INTO transfer_claims(code,source_grant_id,escrow_owner_id,status,created_at,expires_at,operation_id)
            VALUES (?,?,?,'OPEN',?,?,?)''',(code,g['id'],escrow,now,now+86400,op))
        return {'code':code,'seconds':amount/float(SCALE),'minutes':amount/float(SCALE*60)}
    elif action in ('TRANSFER_CLAIM','TRANSFER_CANCEL'):
        claim = one(conn,'SELECT * FROM transfer_claims WHERE code=?',(str(payload.get('code','')).upper(),))
        if not claim or claim['status']!='OPEN':
            raise ValueError('transfer_unavailable')
        source = grant(conn,claim['source_grant_id'])
        if action=='TRANSFER_CANCEL' and source['owner_id']!=owner:
            raise ValueError('forbidden_grant_owner')
        if action=='TRANSFER_CLAIM' and claim['expires_at'] is not None and now>=claim['expires_at']:
            raise ValueError('transfer_claim_expired')
        funds = all_rows(conn,"SELECT * FROM time_grants WHERE owner_id=? AND state='ESCROW' AND remaining_us>0",(claim['escrow_owner_id'],))
        if not funds:
            raise ValueError('transfer_credit_expired')
        for source in funds:
            _fragment(conn,source,owner,source['remaining_us'],'UNUSED',now,op,action.lower())
        conn.execute('UPDATE transfer_claims SET status=?,claimed_by_owner_id=?,claimed_at=? WHERE code=?',
                     ('CLAIMED' if action=='TRANSFER_CLAIM' else 'CANCELLED',owner,now,claim['code']))
    else:
        raise ValueError('unknown_action')
    if g:
        _move(conn,'grant:'+g['id'],'grant:'+g['id'],0,action.lower(),now,op)
    return {}


def apply_operation(conn,owner_id,connection_data,action,payload,op_id,now_utc,mono_now):
    if not op_id or not isinstance(op_id,str) or len(op_id)>200:
        return {'success':False,'error':'operation_id_required'}
    with transaction(conn):
        cd = connection(conn,connection_data['id'])
        if not cd or cd['owner_id']!=owner_id:
            return {'success':False,'error':'forbidden_connection_owner'}
        fingerprint = json.dumps({'payload':payload,'connection_id':cd['id']},sort_keys=True,separators=(',',':'),allow_nan=False)
        digest = hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()
        prior = one(conn,'SELECT * FROM value_operations WHERE operation_id=?',(op_id,))
        if prior:
            if (prior['owner_id'],prior['action'],prior['payload_hash'])!=(owner_id,action,digest):
                return {'success':False,'error':'operation_id_conflict'}
            result = json.loads(prior['response_json']); result['replayed']=True
            return result
        if metadata(conn,'ready','0')!='1':
            return {'success':False,'error':'migration_required'}
        _due(conn,now_utc,mono_now)
        cd = connection(conn,cd['id'])
        result = {'success':True,'action':action,'operation_id':op_id}
        try:
            with transaction(conn):
                extra = _action(conn,cd,action,payload,op_id,now_utc,mono_now)
                for row in all_rows(conn,'SELECT id FROM connections'):
                    _activate_next(conn,row['id'],now_utc,mono_now)
                refresh_desired(conn,cd['id'],now_utc,mono_now)
                result.update(snapshot(conn,cd['id'],now_utc)); result.update(extra)
        except ValueError as error:
            result = {'success':False,'action':action,'operation_id':op_id,'error':str(error)}
        conn.execute('INSERT INTO value_operations VALUES (?,?,?,?,?,?)',
                     (op_id,owner_id,action,digest,json.dumps(result,allow_nan=False),now_utc))
        return result


def request_network_intents(conn,now,mono,healthy):
    with transaction(conn):
        for cd in all_rows(conn,'SELECT * FROM connections'):
            state = cd['desired_state'] if healthy else 'DISCONNECTED'
            if state!='ACTIVE' and cd['applied_state']!='ACTIVE':
                continue
            pending = conn.execute("SELECT 1 FROM network_intents WHERE connection_id=? AND version=? AND status='PENDING' AND desired_state=?",(cd['id'],cd['binding_version'],state)).fetchone()
            if not pending:
                _intent(conn,cd,state,now)
    return all_rows(conn,"SELECT * FROM network_intents WHERE status='PENDING' ORDER BY CASE WHEN desired_state='ACTIVE' THEN 1 ELSE 0 END,id")


def acknowledge_network(conn,intent,now,mono,success,error='',lease_seconds=LEASE_SECONDS):
    with transaction(conn):
        cd = connection(conn,intent['connection_id'])
        current = (cd['binding_version']==intent['version'] and cd['ip']==intent['ip'] and cd['mac']==intent['mac'])
        if not current:
            conn.execute("UPDATE network_intents SET status='STALE' WHERE id=?",(intent['id'],))
            return False
        _settle(conn,cd,now,mono)
        active = success and lease_seconds>0 and intent['desired_state']=='ACTIVE' and desired_state(conn,connection(conn,cd['id']))=='ACTIVE'
        conn.execute('''UPDATE connections SET applied_state=?,authorized_until_us=?,boot_id=?,
            last_mono_us=?,last_settled_at_mono=?,last_settled_at_utc=?,access_error=? WHERE id=?''',
            ('ACTIVE' if active else 'DISCONNECTED',mono_us(mono+lease_seconds) if active else None,BOOT_ID,
             mono_us(mono),mono,now,error,cd['id']))
        conn.execute('UPDATE network_intents SET status=?,attempts=attempts+1,error=? WHERE id=?',
                     ('APPLIED' if success else 'PENDING',error,intent['id']))
        return current and (intent['desired_state']!='ACTIVE' or not success or active)


def create_policy(conn,settings,now):
    """Admin adapter only; immutable version instead of editing purchased terms."""
    cap = settings.get('pause_count_max',3)
    if cap is not None and (isinstance(cap,bool) or not isinstance(cap,int) or cap<0):
        raise ValueError('invalid_pause_count')
    def duration(key,default,nullable=False):
        value=settings.get(key,default)
        if value is None and nullable:
            return None
        if isinstance(value,bool) or not isinstance(value,int) or value<0 or value>315360000:
            raise ValueError('invalid_'+key)
        return value
    pause=duration('pause_duration_sec',3600); low=duration('min_balance_sec',0); high=duration('max_balance_sec',None,True)
    validity=duration('global_validity_min',1440,True)
    if high is not None and (high==0 or low>high):
        raise ValueError('invalid_balance_window')
    if validity is not None and (validity==0 or validity*60>315360000):
        raise ValueError('invalid_validity')
    brackets=settings.get('brackets',[])
    ok,reason=time_policy.validate_brackets(brackets)
    if not ok:
        raise ValueError(reason)
    allowed=settings.get('pause_allowed',True)
    if not isinstance(allowed,bool):
        raise ValueError('invalid_pause_permission')
    action=settings.get('pause_timeout_action','resume')
    if action not in ('resume','expire'):
        raise ValueError('invalid_timeout_action')
    pid='policy:'+generate_uuid()
    with transaction(conn):
        conn.execute('''INSERT INTO time_policy_versions(id,pause_count_max,pause_duration_sec,pause_timeout_action,
            min_balance_sec,max_balance_sec,global_validity_min,brackets_json,is_active,created_at,pause_allowed)
            VALUES (?,?,?,?,?,?,?,?,1,?,?)''',(pid,cap,pause,action,low,high,validity,json.dumps(brackets),now,int(allowed)))
        conn.execute('UPDATE time_policy_versions SET is_active=0 WHERE id<>?',(pid,))
        set_metadata(conn,'active_policy',pid)
    return pid
