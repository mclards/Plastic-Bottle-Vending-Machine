# -*- coding: utf-8 -*-
"""Quiescent legacy import with exact reconciliation; Python 3.5 compatible.

CLI writes require --stopped. Always rehearse --dry-run on a consistent copy.
Unknown pending deposits and unresolved identities are held, never guessed.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import time

import time_schema as storage
import transition_engine as engine


def consistent_copy(source,destination):
    """SQLite 3.5/Python 3.5 compatible snapshot, including committed WAL data."""
    origin=sqlite3.connect('file:'+os.path.abspath(source).replace('\\','/')+'?mode=ro',uri=True)
    target=sqlite3.connect(destination)
    try:
        origin.execute('BEGIN')
        target.executescript('\n'.join(origin.iterdump()))
    finally:
        origin.close();target.close()


def rows(conn,table):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():return []
    return engine.all_rows(conn,'SELECT rowid AS source_rowid,* FROM '+table)


def run_migration(db_path,dry_run=False,now_utc=None,memory_snapshot=None,resolutions=None):
    if not os.path.isfile(db_path):return {'success':False,'error':'database_not_found'}
    if dry_run:
        with tempfile.TemporaryDirectory(prefix='ecofi-migration-dry-') as folder:
            copy=os.path.join(folder,'snapshot.db');consistent_copy(db_path,copy)
            report=run_migration(copy,False,now_utc,memory_snapshot,resolutions)
            report['dry_run']=True
            return report
    now=time.time() if now_utc is None else now_utc;mono=time.monotonic()
    resolutions=resolutions or {}
    conn=sqlite3.connect(db_path,timeout=15,isolation_level=None);conn.execute('PRAGMA foreign_keys=ON')
    report={'success':False,'dry_run':False,'migrated_sessions':0,'skipped_sessions':0,
            'total_legacy_seconds':0,'imported_seconds':0,'expired_seconds':0,'held_seconds':0,
            'held_pending_bottles':0,'unresolved':[]}
    try:
        with storage.transaction(conn):
            legacy=rows(conn,'active_sessions');members=rows(conn,'members');transfers=rows(conn,'time_transfers')
            if memory_snapshot:
                by_ip={r['ip']:r for r in legacy}
                for ip,value in memory_snapshot.items():
                    row=dict(by_ip.get(ip,{}));row.update(value);row['ip']=ip
                    row['state_json']=json.dumps(value);by_ip[ip]=row
                legacy=list(by_ip.values())
            storage.init_time_schema(conn)
            storage.set_metadata(conn,'ready',0)
            # Establish a labelled opening journal for the old partial-engine balances.
            # Differences against the live snapshot need an explicit reviewed resolution.
            for g in engine.all_rows(conn,'SELECT * FROM time_grants WHERE remaining_us IS NULL'):
                cd=engine.one(conn,'SELECT * FROM connections WHERE selected_grant_id=?',(g['id'],))
                matching=[r for r in legacy if cd and str(r.get('mac','')).lower()==cd['mac']]
                amount=storage.to_us(g['remaining_seconds']) if g['state'] in engine.LIVE else 0
                resolution=resolutions.get('grant:'+g['id'])
                if matching and storage.to_us(matching[0].get('remaining_seconds',0))!=amount and not resolution:
                    report['unresolved'].append({'source':'grant:'+g['id'],'grant_seconds':g['remaining_seconds'],
                                                 'snapshot_seconds':matching[0].get('remaining_seconds'),'reason':'conflicting_balance_authorities'})
                    continue
                if resolution:
                    if not resolution.get('reason'):raise ValueError('resolution_reason_required')
                    amount=storage.to_us(resolution['seconds'])
                conn.execute('UPDATE time_grants SET issued_us=?,remaining_us=0,remaining_seconds=0 WHERE id=?',
                             (storage.to_us(g['issued_seconds']),g['id']))
                engine._account(conn,'grant:'+g['id'],g['owner_id'],g['id'])
                engine._move(conn,'external:legacy','grant:'+g['id'],amount,'legacy_engine_opening',now,'import:'+g['id'])
                if g['origin']=='legacy' and matching:
                    original=matching[0]
                    # Undo invented 24-hour contracts from the old partial migrator.
                    conn.execute("UPDATE time_grants SET policy_version_id='legacy_ecofi_pause_v1',validity_duration_sec=NULL,valid_until_utc=NULL,validity_mode='legacy_pause_expiry' WHERE id=?",(g['id'],))
                    conn.execute('UPDATE pause_budgets SET pause_count_max=NULL WHERE id=?',(g['pause_budget_id'],))
                    deadline=original.get('expires_at') or None
                    if original.get('is_paused'):
                        pause=engine.one(conn,"SELECT id FROM grant_pauses WHERE grant_id=? AND status='OPEN'",(g['id'],))
                        if pause:
                            conn.execute("UPDATE grant_pauses SET pause_deadline_utc=?,effective_deadline_utc=?,timeout_action='expire' WHERE id=?",(deadline,deadline,pause['id']))
                        if deadline is not None and deadline<=now:
                            engine._terminal(conn,engine.grant(conn,g['id']),now,'legacy_pause_expired','import:'+g['id'])
                            report['expired_seconds']+=amount/float(storage.SCALE)
                    # Restoring already-forfeited value requires an explicit opening resolution.
                    elif g['state'] in engine.TERMINAL and amount:
                        conn.execute("UPDATE time_grants SET state='UNUSED' WHERE id=?",(g['id'],))

            if report['unresolved']:raise ValueError('reviewed_resolution_required')

            def mapped(key,gid,amount,disposition,details):
                conn.execute('INSERT INTO legacy_imports VALUES (?,?,?,?,?,?)',
                    (key,gid,amount,disposition,json.dumps(details,sort_keys=True),now))

            def seen(key):
                return conn.execute('SELECT 1 FROM legacy_imports WHERE source_key=?',(key,)).fetchone() is not None

            for row in sorted(legacy,key=lambda r:str(r['ip'])):
                key='active_sessions:'+str(row['ip'])
                if seen(key):report['skipped_sessions']+=1;continue
                amount=storage.to_us(row.get('remaining_seconds') or 0)
                if amount<0:raise ValueError('negative_legacy_credit')
                pending=int(row.get('pending_bottles') or 0)
                state=json.loads(row.get('state_json') or '{}')
                member=str(row.get('member_username') or state.get('member_username') or '').strip()
                try:mac=engine.normalize_mac(row.get('mac'));identified=True
                except ValueError:mac=None;identified=False
                if member:owner=engine.get_or_create_owner(conn,'member',member,now)
                elif identified:owner=engine.get_or_create_owner(conn,'device','mac:'+mac,now)
                else:owner=engine.get_or_create_owner(conn,'legacy','unresolved:'+key,now)
                if pending:
                    conn.execute('INSERT OR IGNORE INTO deposit_recovery(event_id,payload_json,reason,received_at) VALUES (?,?,?,?)',
                        ('legacy-pending:'+key,json.dumps(dict(row,owner_id=owner)),
                         'Unfinalized or possibly already-finalized bottles; verify before crediting.',now))
                    report['held_pending_bottles']+=pending
                report['total_legacy_seconds']+=amount/float(storage.SCALE)
                existing=engine.one(conn,'SELECT * FROM connections WHERE mac=?',(mac,)) if identified else None
                represented=engine.grant(conn,existing['selected_grant_id']) if existing else None
                if represented:
                    if represented['owner_id']!=owner:raise ValueError('legacy_owner_conflict')
                    if represented['remaining_us']!=amount and represented['state']!='EXPIRED' and not resolutions.get('grant:'+represented['id']):
                        raise ValueError('legacy_balance_conflict')
                    mapped(key,represented['id'],amount,'represented_by_existing_grant',row)
                    if represented['state']!='EXPIRED':report['imported_seconds']+=represented['remaining_us']/float(storage.SCALE)
                    report['skipped_sessions']+=1;continue
                if not amount:
                    mapped(key,None,0,'empty',row);report['skipped_sessions']+=1;continue
                paused=bool(row.get('is_paused'));deadline=row.get('expires_at') or None
                expired=paused and deadline is not None and deadline<=now
                target='HELD' if not identified and not member else ('PAUSED' if paused else 'UNUSED')
                g=engine._create_grant(conn,owner,amount,'legacy_session',now,'legacy_ecofi_pause_v1',source_ref=key,state=target,op='import:'+key)
                conn.execute('UPDATE time_grants SET activated_at_utc=?,valid_until_utc=NULL,speed_override=1,dl_kbps=?,ul_kbps=? WHERE id=?',
                    (now,row.get('dl_kbps') or 3072,row.get('ul_kbps') or 1536,g['id']))
                if paused:
                    reason='admin' if state.get('admin_paused') else ('disconnect' if state.get('auto_paused') else 'user')
                    conn.execute('''INSERT INTO grant_pauses(id,grant_id,paused_at_utc,pause_deadline_utc,effective_deadline_utc,
                        pause_reason,timeout_action,status,created_at) VALUES (?,?,?,?,?,?,'expire','OPEN',?)''',
                        (engine.generate_uuid(),g['id'],row.get('paused_at') or now,deadline,deadline,reason,now))
                disposition='held' if target=='HELD' else 'imported'
                if expired:
                    engine._terminal(conn,engine.grant(conn,g['id']),now,'legacy_pause_expired','import:'+key)
                    report['expired_seconds']+=amount/float(storage.SCALE);disposition='expired'
                elif target=='HELD':
                    report['held_seconds']+=amount/float(storage.SCALE)
                    conn.execute('INSERT OR IGNORE INTO deposit_recovery VALUES (?,?,?,?,NULL)',
                        ('legacy-owner:'+key,json.dumps({'grant_id':g['id'],'source':row}),
                         'Known credit held until its owner is identified.',now))
                else:report['imported_seconds']+=amount/float(storage.SCALE)
                if identified and not str(row['ip']).startswith('saved:'):
                    cd=engine.get_or_create_connection(conn,row['ip'],mac,owner,now,mono)
                    if member and not conn.execute('SELECT 1 FROM owner_bindings WHERE owner_id=?',(owner,)).fetchone():
                        conn.execute('INSERT INTO owner_bindings VALUES (?,?)',(owner,cd['id']))
                    binding=engine.one(conn,'SELECT connection_id FROM owner_bindings WHERE owner_id=?',(owner,))
                    if not expired and cd['selected_grant_id'] is None and (not binding or binding['connection_id']==cd['id']):
                        engine._select(conn,cd['id'],engine.grant(conn,g['id']),now,mono)
                    conn.execute('UPDATE connections SET admin_suspended=? WHERE id=?',(int(bool(state.get('admin_paused'))),cd['id']))
                    engine.refresh_desired(conn,cd['id'],now,mono)
                mapped(key,g['id'],amount,disposition,row);report['migrated_sessions']+=1

            for member in members:
                key='members:'+member['username']
                if seen(key):continue
                amount=storage.to_us(float(member.get('wallet_minutes') or 0)*60)
                owner=engine.get_or_create_owner(conn,'member',member['username'],now)
                if amount<0:raise ValueError('negative_legacy_wallet')
                g=engine._create_grant(conn,owner,amount,'legacy_wallet',now,'legacy_ecofi_pause_v1',source_ref=key,state='WALLET',op='import:'+key) if amount else None
                mapped(key,g['id'] if g else None,amount,'wallet',member)
                report['total_legacy_seconds']+=amount/float(storage.SCALE);report['imported_seconds']+=amount/float(storage.SCALE)
            for transfer in transfers:
                key='time_transfers:'+transfer['code']
                if seen(key) or transfer.get('is_claimed'):continue
                amount=storage.to_us(transfer['seconds'])
                if amount<=0:raise ValueError('invalid_legacy_transfer')
                escrow=engine.get_or_create_owner(conn,'escrow','legacy:'+transfer['code'],now)
                g=engine._create_grant(conn,escrow,amount,'legacy_transfer',now,'legacy_ecofi_pause_v1',source_ref=key,state='ESCROW',op='import:'+key)
                conn.execute('''INSERT INTO transfer_claims(code,source_grant_id,escrow_owner_id,status,created_at,operation_id)
                    VALUES (?,?,?,'OPEN',?,?)''',(transfer['code'],g['id'],escrow,transfer.get('created_at') or now,'import:'+key))
                mapped(key,g['id'],amount,'escrow',transfer)
                report['total_legacy_seconds']+=amount/float(storage.SCALE);report['imported_seconds']+=amount/float(storage.SCALE)
            if rows(conn,'vouchers'):
                cols={r[1] for r in conn.execute('PRAGMA table_info(vouchers)')}
                if 'policy_version_id' not in cols:conn.execute('ALTER TABLE vouchers ADD COLUMN policy_version_id TEXT')
                conn.execute("UPDATE vouchers SET policy_version_id='legacy_ecofi_pause_v1' WHERE is_used=0 AND policy_version_id IS NULL")
            bad=conn.execute('''SELECT a.id FROM ledger_accounts a JOIN time_grants g ON a.grant_id=g.id
                WHERE a.balance_us<>g.remaining_us OR g.remaining_us IS NULL''').fetchall()
            unbalanced=conn.execute('SELECT journal_id FROM time_ledger WHERE journal_id IS NOT NULL GROUP BY journal_id HAVING SUM(delta_us)<>0').fetchall()
            if bad or unbalanced:raise ValueError('reconciliation_failed')
            conn.execute("UPDATE connections SET applied_state='DISCONNECTED',authorized_until_us=NULL,last_mono_us=?,last_settled_at_mono=?,last_settled_at_utc=?,boot_id=?",(engine.mono_us(mono),mono,now,engine.BOOT_ID))
            storage.set_metadata(conn,'ready',1);storage.set_metadata(conn,'migration_completed_at',now)
            report['custody_us']=conn.execute('SELECT COALESCE(SUM(balance_us),0) FROM ledger_accounts WHERE grant_id IS NOT NULL').fetchone()[0]
            report['by_owner']=engine.all_rows(conn,'''SELECT o.id,o.owner_type,o.owner_key,COALESCE(SUM(a.balance_us),0) AS custody_us
                FROM credit_owners o LEFT JOIN ledger_accounts a ON a.owner_id=o.id AND a.grant_id IS NOT NULL GROUP BY o.id''')
            report['source_inventory']=engine.all_rows(conn,'SELECT disposition,SUM(amount_us) AS source_us,COUNT(*) AS sources FROM legacy_imports GROUP BY disposition')
            report['success']=True
    except (ValueError,sqlite3.Error) as error:
        report['error']=str(error)
        report['success']=False
    finally:conn.close()
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database');parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--stopped',action='store_true',help='Accounting, deposits and value writers are stopped.')
    parser.add_argument('--memory-snapshot');parser.add_argument('--resolutions')
    args=parser.parse_args()
    if not args.dry_run and not args.stopped:parser.error('Stop the service and use --stopped; rehearse --dry-run first.')
    def read_json(path):
        if not path:return None
        with open(path) as handle:return json.load(handle)
    result=run_migration(args.database,args.dry_run,memory_snapshot=read_json(args.memory_snapshot),resolutions=read_json(args.resolutions))
    print(json.dumps(result,indent=2,sort_keys=True))
    raise SystemExit(0 if result['success'] else 1)
