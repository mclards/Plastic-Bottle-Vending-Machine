# -*- coding: utf-8 -*-
"""
Eco-Fi PisoFi-Style Transactional Transition Engine
Strictly compatible with Python 3.5.3 (NO f-strings, NO variable annotations).

Provides atomic state mutations, idempotency, ledger recording, and
precedence enforcement for time grants, connections, and pause budgets.
"""

import json
import uuid
import time
import math
import hashlib

import time_policy


def generate_uuid():
    return str(uuid.uuid4())


def get_or_create_owner(conn, owner_type, owner_key, now_utc):
    """
    Look up or insert a credit_owners record.
    Returns owner_id (str).
    """
    c = conn.cursor()
    c.execute("SELECT id FROM credit_owners WHERE owner_type = ? AND owner_key = ?", (owner_type, owner_key))
    row = c.fetchone()
    if row:
        return row[0]

    owner_id = generate_uuid()
    c.execute(
        "INSERT INTO credit_owners (id, owner_type, owner_key, created_at) VALUES (?, ?, ?, ?)",
        (owner_id, owner_type, owner_key, now_utc)
    )
    return owner_id


def get_or_create_device(conn, mac, owner_id, ip, now_utc):
    """
    Look up or insert/update a devices record.
    """
    c = conn.cursor()
    c.execute("SELECT owner_id FROM devices WHERE mac = ?", (mac,))
    row = c.fetchone()
    if row:
        c.execute("UPDATE devices SET last_ip = ?, last_seen_at = ? WHERE mac = ?", (ip, now_utc, mac))
    else:
        c.execute(
            "INSERT INTO devices (mac, owner_id, last_ip, last_seen_at) VALUES (?, ?, ?, ?)",
            (mac, owner_id, ip, now_utc)
        )


def get_or_create_connection(conn, ip, mac, owner_id, now_utc, mono_now):
    """
    Look up or insert a connections record.
    """
    if not mac or mac in ('00:00:00:00:00:00', 'mac:'):
        raise ValueError("invalid_mac")

    # Sync devices table
    get_or_create_device(conn, mac, owner_id, ip, now_utc)

    c = conn.cursor()
    c.execute("SELECT id, selected_grant_id, desired_state, applied_state, binding_version, last_settled_at_mono, last_settled_at_utc, ip, owner_id FROM connections WHERE mac = ?", (mac,))
    row = c.fetchone()
    if row:
        conn_id, grant_id, des_state, app_state, version, last_mono, last_utc, old_ip, old_owner = row
        # Update IP and timestamps
        if old_ip != ip or old_owner != owner_id:
            version += 1
            des_state = 'DISCONNECTED'
        c.execute(
            "UPDATE connections SET ip = ?, owner_id = ?, desired_state = ?, binding_version = ?, updated_at = ? WHERE id = ?",
            (ip, owner_id, des_state, version, now_utc, conn_id)
        )
        return {
            'id': conn_id,
            'ip': ip,
            'mac': mac,
            'owner_id': owner_id,
            'selected_grant_id': grant_id,
            'desired_state': des_state,
            'applied_state': app_state,
            'binding_version': version,
            'last_settled_at_mono': last_mono,
            'last_settled_at_utc': last_utc
        }

    conn_id = generate_uuid()
    c.execute(
        """
        INSERT INTO connections (
            id, ip, mac, owner_id, selected_grant_id, desired_state, applied_state,
            binding_version, last_settled_at_mono, last_settled_at_utc, updated_at
        ) VALUES (?, ?, ?, ?, NULL, 'DISCONNECTED', 'DISCONNECTED', 1, ?, ?, ?)
        """,
        (conn_id, ip, mac, owner_id, mono_now, now_utc, now_utc)
    )
    return {
        'id': conn_id,
        'ip': ip,
        'mac': mac,
        'owner_id': owner_id,
        'selected_grant_id': None,
        'desired_state': 'DISCONNECTED',
        'applied_state': 'DISCONNECTED',
        'binding_version': 1,
        'last_settled_at_mono': mono_now,
        'last_settled_at_utc': now_utc
    }


def _activate_next_grant_or_disconnect(conn, owner_id, connection_id, mono_now, now_utc):
    c = conn.cursor()
    c.execute(
        """
        SELECT id, validity_duration_sec 
        FROM time_grants 
        WHERE owner_id = ? AND state = 'UNUSED' 
        ORDER BY created_at ASC LIMIT 1
        """,
        (owner_id,)
    )
    nxt = c.fetchone()
    if nxt:
        new_gid, val_dur = nxt
        val_until = time_policy.calculate_activation_validity(now_utc, val_dur)
        c.execute(
            """
            UPDATE time_grants 
            SET state = 'ACTIVE', activated_at_utc = ?, valid_until_utc = ?, updated_at = ? 
            WHERE id = ?
            """,
            (now_utc, val_until, now_utc, new_gid)
        )
        if connection_id:
            c.execute(
                """
                UPDATE connections 
                SET selected_grant_id = ?, desired_state = 'ACTIVE', 
                    last_settled_at_mono = ?, last_settled_at_utc = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_gid, mono_now, now_utc, now_utc, connection_id)
            )
        return new_gid
    else:
        if connection_id:
            c.execute(
                "UPDATE connections SET desired_state = 'DISCONNECTED', updated_at = ? WHERE id = ?",
                (now_utc, connection_id)
            )
        return None

def settle_connection_balance(conn, connection_data, now_utc, mono_now):
    """
    Settle elapsed active seconds on the currently selected grant.
    Returns (settled_grant_dict, debited_seconds).
    """
    grant_id = connection_data.get('selected_grant_id')
    if not grant_id:
        return None, 0.0

    c = conn.cursor()
    c.execute(
        """
        SELECT id, owner_id, remaining_seconds, state, valid_until_utc, policy_version_id
        FROM time_grants WHERE id = ?
        """,
        (grant_id,)
    )
    grow = c.fetchone()
    if not grow:
        return None, 0.0

    gid, owner_id, rem_sec, state, valid_until, policy_id = grow
    if state != 'ACTIVE':
        # Nothing to debit if not active
        return {'id': gid, 'remaining_seconds': rem_sec, 'state': state, 'valid_until_utc': valid_until}, 0.0

    last_mono = connection_data.get('last_settled_at_mono')
    if last_mono is None or mono_now < last_mono:
        elapsed = 0.0
    else:
        elapsed = mono_now - last_mono
    debited = min(rem_sec, elapsed)
    new_rem = max(0.0, rem_sec - debited)

    new_state = state
    if new_rem <= 0:
        new_state = 'DEPLETED'
    elif valid_until is not None and now_utc >= valid_until:
        new_state = 'EXPIRED'

    c.execute(
        "UPDATE time_grants SET remaining_seconds = ?, state = ?, updated_at = ? WHERE id = ?",
        (new_rem, new_state, now_utc, gid)
    )
    c.execute(
        "UPDATE connections SET last_settled_at_mono = ?, last_settled_at_utc = ?, updated_at = ? WHERE id = ?",
        (mono_now, now_utc, now_utc, connection_data['id'])
    )
    if new_state in ('DEPLETED', 'EXPIRED'):
        c.execute("UPDATE grant_pauses SET status = 'EXPIRED', closed_at_utc = ? WHERE grant_id = ? AND status = 'OPEN'", (now_utc, gid))
        _activate_next_grant_or_disconnect(conn, owner_id, connection_data['id'], mono_now, now_utc)

    if debited > 0.001:
        # Record consumption event in time_ledger
        event_id = generate_uuid()
        c.execute(
            """
            INSERT INTO time_ledger (
                event_id, operation_id, grant_id, owner_id, reason,
                delta_seconds, balance_before, balance_after, created_at
            ) VALUES (?, NULL, ?, ?, 'time_consumed', ?, ?, ?, ?)
            """,
            (event_id, gid, owner_id, -debited, rem_sec, new_rem, now_utc)
        )

    return {'id': gid, 'remaining_seconds': new_rem, 'state': new_state, 'valid_until_utc': valid_until}, debited


def create_pause_budget(conn, owner_id, pause_count_max, now_utc):
    """
    Create a new open pause budget for an independent grant.
    """
    bid = generate_uuid()
    c = conn.cursor()
    c.execute(
        "INSERT INTO pause_budgets (id, owner_id, pause_count_max, used_count, created_at) VALUES (?, ?, ?, 0, ?)",
        (bid, owner_id, pause_count_max, now_utc)
    )
    return {'id': bid, 'pause_count_max': pause_count_max, 'used_count': 0}


def bulk_settle_active_connections(conn, now_utc, mono_now):
    c = conn.cursor()
    c.execute("""
        SELECT id, ip, mac, owner_id, selected_grant_id, desired_state, applied_state,
               binding_version, last_settled_at_mono, last_settled_at_utc
        FROM connections WHERE desired_state = 'ACTIVE'
    """)
    rows = c.fetchall()
    results = []
    for row in rows:
        cdata = {
            'id': row[0], 'ip': row[1], 'mac': row[2], 'owner_id': row[3],
            'selected_grant_id': row[4], 'desired_state': row[5], 'applied_state': row[6],
            'binding_version': row[7], 'last_settled_at_mono': row[8], 'last_settled_at_utc': row[9]
        }
        settle_connection_balance(conn, cdata, now_utc, mono_now)
        c.execute("SELECT desired_state FROM connections WHERE id = ?", (row[0],))
        results.append((row[1], c.fetchone()[0]))
    conn.commit()
    return results

def apply_operation(conn, owner_id, connection_data, action, payload, op_id, now_utc, mono_now):
    """
    Atomically apply an operation to the time architecture.
    """
    c = conn.cursor()

    # 1. Idempotency Check
    payload_str = json.dumps(payload, sort_keys=True)
    payload_hash = hashlib.sha256(payload_str.encode('utf-8')).hexdigest()

    if op_id:
        c.execute("SELECT owner_id, action, payload_hash, response_json FROM value_operations WHERE operation_id = ?", (op_id,))
        row = c.fetchone()
        if row:
            row_owner_id, row_action, row_payload_hash, response_json = row
            if row_owner_id != owner_id or row_action != action or row_payload_hash != payload_hash:
                return {'success': False, 'error': 'operation_id_conflict'}
            result = json.loads(response_json)
            result['replayed'] = True
            return result

    # 2. Settle current connection usage
    settled_grant, debited = settle_connection_balance(conn, connection_data, now_utc, mono_now)

    result = {'success': False, 'action': action, 'operation_id': op_id}

    if action == 'PAUSE':
        grant_id = connection_data.get('selected_grant_id')
        if not grant_id:
            result['error'] = 'no_active_grant'
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        # Fetch grant details & policy
        c.execute(
            """
            SELECT g.id, g.remaining_seconds, g.state, g.valid_until_utc, g.pause_budget_id,
                   p.pause_count_max, p.pause_duration_sec, p.min_balance_sec, p.max_balance_sec,
                   b.used_count, g.owner_id
            FROM time_grants g
            JOIN time_policy_versions p ON g.policy_version_id = p.id
            JOIN pause_budgets b ON g.pause_budget_id = b.id
            WHERE g.id = ?
            """,
            (grant_id,)
        )
        grow = c.fetchone()
        if not grow:
            result['error'] = 'grant_not_found'
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        gid, rem_sec, gstate, valid_until, bid, count_max, dur_sec, min_bal, max_bal, used_count, grant_owner_id = grow

        if grant_owner_id != owner_id:
            result['error'] = 'forbidden_grant_owner'
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        # If already paused, return idempotent success with current state
        if gstate == 'PAUSED':
            c.execute("SELECT pause_deadline_utc, effective_deadline_utc FROM grant_pauses WHERE grant_id = ? AND status = 'OPEN'", (gid,))
            prow = c.fetchone()
            result['success'] = True
            result['already_paused'] = True
            result['remaining_seconds'] = rem_sec
            result['pause_count_used'] = used_count
            result['effective_deadline_utc'] = prow[1] if prow else None
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        # Check eligibility via pure policy engine
        admin_suspended = payload.get('admin_suspended', False)
        ok, reason = time_policy.can_pause_grant(
            grant_state=gstate,
            remaining_seconds=rem_sec,
            pause_count_used=used_count,
            now_utc=now_utc,
            valid_until_utc=valid_until,
            pause_count_max=count_max,
            min_balance_sec=min_bal,
            max_balance_sec=max_bal,
            admin_suspended=admin_suspended
        )

        if not ok:
            result['error'] = reason
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        # Calculate deadlines
        deadlines = time_policy.calculate_pause_deadlines(now_utc, pause_duration_sec=dur_sec, valid_until_utc=valid_until)
        pause_id = generate_uuid()

        # Update budget & grant state
        c.execute("UPDATE pause_budgets SET used_count = used_count + 1 WHERE id = ?", (bid,))
        c.execute("UPDATE time_grants SET state = 'PAUSED', updated_at = ? WHERE id = ?", (now_utc, gid))
        c.execute(
            """
            INSERT INTO grant_pauses (
                id, grant_id, paused_at_utc, pause_deadline_utc, effective_deadline_utc,
                pause_reason, timeout_action, status, closed_at_utc, created_at
            ) VALUES (?, ?, ?, ?, ?, 'user', 'resume', 'OPEN', NULL, ?)
            """,
            (pause_id, gid, now_utc, deadlines['pause_deadline_utc'], deadlines['effective_next_deadline_utc'], now_utc)
        )
        c.execute("UPDATE connections SET desired_state = 'PAUSED', updated_at = ? WHERE id = ?", (now_utc, connection_data['id']))

        # Log to ledger
        event_id = generate_uuid()
        c.execute(
            """
            INSERT INTO time_ledger (
                event_id, operation_id, grant_id, owner_id, reason,
                delta_seconds, balance_before, balance_after, created_at
            ) VALUES (?, ?, ?, ?, 'user_paused', 0.0, ?, ?, ?)
            """,
            (event_id, op_id, gid, owner_id, rem_sec, rem_sec, now_utc)
        )

        result['success'] = True
        result['remaining_seconds'] = rem_sec
        result['pause_count_used'] = used_count + 1
        result['pause_deadline_utc'] = deadlines['pause_deadline_utc']
        result['effective_deadline_utc'] = deadlines['effective_next_deadline_utc']
        result['next_event_type'] = deadlines['next_event_type']

    elif action == 'RESUME':
        grant_id = connection_data.get('selected_grant_id')
        if not grant_id:
            result['error'] = 'no_active_grant'
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        c.execute("SELECT id, remaining_seconds, state, valid_until_utc, owner_id FROM time_grants WHERE id = ?", (grant_id,))
        grow = c.fetchone()
        if not grow:
            result['error'] = 'grant_not_found'
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        gid, rem_sec, gstate, valid_until, grant_owner_id = grow

        if grant_owner_id != owner_id:
            result['error'] = 'forbidden_grant_owner'
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        if gstate == 'ACTIVE':
            result['success'] = True
            result['already_active'] = True
            result['remaining_seconds'] = rem_sec
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        if gstate != 'PAUSED':
            result['error'] = 'grant_not_paused'
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)
            
        admin_suspended = payload.get('admin_suspended', False)
        if admin_suspended:
            result['error'] = 'admin_suspended'
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        # Check calendar expiry before resuming
        if valid_until is not None and now_utc >= valid_until:
            c.execute("UPDATE time_grants SET state = 'EXPIRED', updated_at = ? WHERE id = ?", (now_utc, gid))
            c.execute("UPDATE grant_pauses SET status = 'EXPIRED', closed_at_utc = ? WHERE grant_id = ? AND status = 'OPEN'", (now_utc, gid))
            c.execute("UPDATE connections SET desired_state = 'DISCONNECTED', updated_at = ? WHERE id = ?", (now_utc, connection_data['id']))
            result['error'] = 'calendar_expired'
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        # Close open pause
        c.execute("UPDATE grant_pauses SET status = 'CLOSED', closed_at_utc = ? WHERE grant_id = ? AND status = 'OPEN'", (now_utc, gid))
        c.execute("UPDATE time_grants SET state = 'ACTIVE', updated_at = ? WHERE id = ?", (now_utc, gid))
        c.execute(
            """
            UPDATE connections SET desired_state = 'ACTIVE', last_settled_at_mono = ?, last_settled_at_utc = ?, updated_at = ?
            WHERE id = ?
            """,
            (mono_now, now_utc, now_utc, connection_data['id'])
        )

        event_id = generate_uuid()
        c.execute(
            """
            INSERT INTO time_ledger (
                event_id, operation_id, grant_id, owner_id, reason,
                delta_seconds, balance_before, balance_after, created_at
            ) VALUES (?, ?, ?, ?, 'user_resumed', 0.0, ?, ?, ?)
            """,
            (event_id, op_id, gid, owner_id, rem_sec, rem_sec, now_utc)
        )

        result['success'] = True
        result['remaining_seconds'] = rem_sec
        result['state'] = 'ACTIVE'

    elif action == 'TOP_UP_GRANT':
        issued_seconds = payload.get('seconds', 0)
        origin = payload.get('origin', 'bottle')
        source_ref = payload.get('source_ref')
        policy_id = payload.get('policy_version_id', 'pisofi_time_v1')

        if issued_seconds <= 0:
            result['error'] = 'invalid_seconds'
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        # Load policy
        c.execute("SELECT pause_count_max, brackets_json, global_validity_min FROM time_policy_versions WHERE id = ?", (policy_id,))
        prow = c.fetchone()
        if not prow:
            result['error'] = 'policy_not_found'
            return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)

        count_max, brackets_json, global_val_min = prow
        brackets = json.loads(brackets_json)

        val_duration = time_policy.calculate_bracket_validity(issued_seconds, brackets, global_validity_min=global_val_min)
        budget = create_pause_budget(conn, owner_id, count_max, now_utc)

        grant_id = generate_uuid()

        # Decide activation: if connection has no selected grant or current is terminal, activate now
        curr_gid = connection_data.get('selected_grant_id')
        activate_immediately = False
        if not curr_gid:
            activate_immediately = True
        else:
            c.execute("SELECT state FROM time_grants WHERE id = ?", (curr_gid,))
            strow = c.fetchone()
            if not strow or strow[0] in ('DEPLETED', 'EXPIRED', 'DISCONNECTED'):
                activate_immediately = True

        if activate_immediately:
            initial_state = 'ACTIVE'
            activated_at = now_utc
            valid_until = time_policy.calculate_activation_validity(now_utc, val_duration)
            c.execute(
                """
                INSERT INTO time_grants (
                    id, owner_id, origin, source_ref, issued_seconds, remaining_seconds,
                    state, validity_mode, validity_duration_sec, activated_at_utc, valid_until_utc,
                    pause_budget_id, policy_version_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'activation_relative', ?, ?, ?, ?, ?, ?, ?)
                """,
                (grant_id, owner_id, origin, source_ref, issued_seconds, float(issued_seconds),
                 initial_state, val_duration, activated_at, valid_until, budget['id'], policy_id, now_utc, now_utc)
            )
            c.execute(
                """
                UPDATE connections SET selected_grant_id = ?, desired_state = 'ACTIVE',
                       last_settled_at_mono = ?, last_settled_at_utc = ?, updated_at = ?
                WHERE id = ?
                """,
                (grant_id, mono_now, now_utc, now_utc, connection_data['id'])
            )
        else:
            initial_state = 'UNUSED'
            activated_at = None
            valid_until = None
            c.execute(
                """
                INSERT INTO time_grants (
                    id, owner_id, origin, source_ref, issued_seconds, remaining_seconds,
                    state, validity_mode, validity_duration_sec, activated_at_utc, valid_until_utc,
                    pause_budget_id, policy_version_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'activation_relative', ?, ?, ?, ?, ?, ?, ?)
                """,
                (grant_id, owner_id, origin, source_ref, issued_seconds, float(issued_seconds),
                 initial_state, val_duration, activated_at, valid_until, budget['id'], policy_id, now_utc, now_utc)
            )

        event_id = generate_uuid()
        c.execute(
            """
            INSERT INTO time_ledger (
                event_id, operation_id, grant_id, owner_id, reason,
                delta_seconds, balance_before, balance_after, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0.0, ?, ?)
            """,
            (event_id, op_id, grant_id, owner_id, origin + '_reward', float(issued_seconds), float(issued_seconds), now_utc)
        )

        result['success'] = True
        result['grant_id'] = grant_id
        result['state'] = initial_state
        result['issued_seconds'] = issued_seconds
        result['remaining_seconds'] = issued_seconds
        result['valid_until_utc'] = valid_until

    return _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc)


def check_due_events(conn, now_utc, mono_now):
    """
    Background worker pass to process:
    1. Calendar expiries (time_grants where valid_until_utc <= now)
    2. Pause timeouts (grant_pauses where effective_deadline_utc <= now)
       - If calendar expiry: status -> EXPIRED, grant -> EXPIRED, connection -> DISCONNECTED
       - If pause timeout: auto-resume! status -> CLOSED, grant -> ACTIVE, connection -> ACTIVE
    Returns dict of processed event counts.
    """
    c = conn.cursor()
    events_processed = {'expired': 0, 'resumed': 0}

    # A. Active grants reaching calendar expiry
    c.execute(
        """
        SELECT g.id, g.owner_id, g.remaining_seconds, c.id
        FROM time_grants g
        LEFT JOIN connections c ON c.selected_grant_id = g.id
        WHERE g.state IN ('ACTIVE', 'PAUSED')
          AND g.valid_until_utc IS NOT NULL
          AND g.valid_until_utc <= ?
        """,
        (now_utc,)
    )
    exp_rows = c.fetchall()
    for gid, oid, rem, cid in exp_rows:
        c.execute("UPDATE time_grants SET state = 'EXPIRED', updated_at = ? WHERE id = ?", (now_utc, gid))
        c.execute("UPDATE grant_pauses SET status = 'EXPIRED', closed_at_utc = ? WHERE grant_id = ? AND status = 'OPEN'", (now_utc, gid))
        _activate_next_grant_or_disconnect(conn, oid, cid, mono_now, now_utc)
        event_id = generate_uuid()
        c.execute(
            """
            INSERT INTO time_ledger (
                event_id, operation_id, grant_id, owner_id, reason,
                delta_seconds, balance_before, balance_after, created_at
            ) VALUES (?, NULL, ?, ?, 'validity_expired', 0.0, ?, ?, ?)
            """,
            (event_id, gid, oid, rem, rem, now_utc)
        )
        events_processed['expired'] += 1

    # B. Open grant_pauses reaching effective deadline
    c.execute(
        """
        SELECT p.id, p.grant_id, p.effective_deadline_utc, p.timeout_action,
               g.owner_id, g.remaining_seconds, g.valid_until_utc, c.id
        FROM grant_pauses p
        JOIN time_grants g ON p.grant_id = g.id
        LEFT JOIN connections c ON c.selected_grant_id = g.id
        WHERE p.status = 'OPEN'
          AND p.effective_deadline_utc IS NOT NULL
          AND p.effective_deadline_utc <= ?
        """,
        (now_utc,)
    )
    due_pauses = c.fetchall()
    for pid, gid, eff_dl, timeout_act, oid, rem, valid_until, cid in due_pauses:
        # Check if calendar expiry applies
        is_calendar_expiry = (valid_until is not None and now_utc >= valid_until)
        if is_calendar_expiry or timeout_act == 'expire':
            # Forfeit / Expire
            c.execute("UPDATE grant_pauses SET status = 'EXPIRED', closed_at_utc = ? WHERE id = ?", (now_utc, pid))
            c.execute("UPDATE time_grants SET state = 'EXPIRED', updated_at = ? WHERE id = ?", (now_utc, gid))
            _activate_next_grant_or_disconnect(conn, oid, cid, mono_now, now_utc)
            event_id = generate_uuid()
            reason = 'validity_expired' if is_calendar_expiry else 'pause_timeout_expired'
            c.execute(
                """
                INSERT INTO time_ledger (
                    event_id, operation_id, grant_id, owner_id, reason,
                    delta_seconds, balance_before, balance_after, created_at
                ) VALUES (?, NULL, ?, ?, ?, 0.0, ?, ?, ?)
                """,
                (event_id, gid, oid, reason, rem, rem, now_utc)
            )
            events_processed['expired'] += 1
        else:
            # AUTO-RESUME! (PisoFi-style reconnect)
            c.execute("UPDATE grant_pauses SET status = 'CLOSED', closed_at_utc = ? WHERE id = ?", (now_utc, pid))
            c.execute("UPDATE time_grants SET state = 'ACTIVE', updated_at = ? WHERE id = ?", (now_utc, gid))
            if cid:
                c.execute(
                    """
                    UPDATE connections SET desired_state = 'ACTIVE', last_settled_at_mono = ?,
                           last_settled_at_utc = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (mono_now, now_utc, now_utc, cid)
                )
            event_id = generate_uuid()
            c.execute(
                """
                INSERT INTO time_ledger (
                    event_id, operation_id, grant_id, owner_id, reason,
                    delta_seconds, balance_before, balance_after, created_at
                ) VALUES (?, NULL, ?, ?, 'pause_timeout_resumed', 0.0, ?, ?, ?)
                """,
                (event_id, gid, oid, rem, rem, now_utc)
            )
            events_processed['resumed'] += 1

    conn.commit()
    return events_processed


def _save_op(conn, op_id, owner_id, action, payload_hash, result, now_utc):
    if op_id:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO value_operations (
                operation_id, owner_id, action, payload_hash, response_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (op_id, owner_id, action, payload_hash, json.dumps(result), now_utc)
        )
    conn.commit()
    return result
