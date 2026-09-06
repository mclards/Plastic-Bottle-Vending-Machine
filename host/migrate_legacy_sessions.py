# -*- coding: utf-8 -*-
"""
Eco-Fi PisoFi-Style Data Migration Tool
Strictly compatible with Python 3.5.3 (NO f-strings, NO variable annotations).

Performs idempotent migration of legacy active_sessions and members to the
new separate entitlement architecture, preserving legacy forfeiture deadlines
under legacy_ecofi_pause_v1 while assigning pisofi_time_v1 to active grants.
"""

import sys
import os
import sqlite3
import time
import json

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

import time_schema
import transition_engine


def run_migration(db_path, dry_run=False):
    """
    Run migration from legacy active_sessions to time_grants/connections.
    """
    if not os.path.exists(db_path):
        return {'success': False, 'error': 'Database file not found: ' + str(db_path)}

    conn = sqlite3.connect(db_path, timeout=15)
    try:
        conn.execute('PRAGMA foreign_keys = ON;')
        time_schema.init_time_schema(conn)

        c = conn.cursor()
        now_utc = int(time.time())
        mono_now = 0.0

        # Check if active_sessions exists
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='active_sessions'")
        if not c.fetchone():
            return {'success': True, 'message': 'No legacy active_sessions table found. Schema initialized.'}

        # Check existing migrated count
        c.execute("SELECT COUNT(*) FROM time_grants")
        existing_grants = c.fetchone()[0]

        c.execute("SELECT ip, mac, remaining_seconds, is_paused, paused_at, expires_at, member_username FROM active_sessions")
        legacy_rows = c.fetchall()

        total_legacy_seconds = 0.0
        migrated_sessions = 0
        skipped_sessions = 0

        for row in legacy_rows:
            ip, mac, rem_sec, is_paused, paused_at, expires_at, member_user = row
            rem_sec = float(rem_sec or 0)
            if rem_sec <= 0:
                skipped_sessions += 1
                continue

            total_legacy_seconds += rem_sec

            if dry_run:
                migrated_sessions += 1
                continue

            # Determine owner
            if member_user and member_user.strip():
                owner_id = transition_engine.get_or_create_owner(conn, 'member', 'member:' + member_user.strip(), now_utc)
            else:
                mac_key = mac if mac else 'ip:' + ip
                owner_id = transition_engine.get_or_create_owner(conn, 'device', 'mac:' + mac_key.lower(), now_utc)

            conn_data = transition_engine.get_or_create_connection(conn, ip, mac or '00:00:00:00:00:00', owner_id, now_utc, mono_now)

            # Check if this owner already has an active grant
            c.execute("SELECT id FROM time_grants WHERE owner_id = ? AND state IN ('ACTIVE', 'PAUSED')", (owner_id,))
            if c.fetchone():
                # Already migrated!
                skipped_sessions += 1
                continue

            grant_id = transition_engine.generate_uuid()
            budget = transition_engine.get_or_create_pause_budget(conn, owner_id, 3, now_utc)

            if is_paused:
                # Paused legacy session: preserve forfeiture policy under legacy_ecofi_pause_v1
                policy_id = 'legacy_ecofi_pause_v1'
                state = 'PAUSED'
                valid_until = int(expires_at) if expires_at and expires_at > now_utc else None
                c.execute(
                    """
                    INSERT INTO time_grants (
                        id, owner_id, origin, source_ref, issued_seconds, remaining_seconds,
                        state, validity_mode, validity_duration_sec, activated_at_utc, valid_until_utc,
                        pause_budget_id, policy_version_id, created_at, updated_at
                    ) VALUES (?, ?, 'legacy', 'active_sessions', ?, ?, ?, 'legacy_pause_expiry', NULL, ?, ?, ?, ?, ?, ?)
                    """,
                    (grant_id, owner_id, int(rem_sec), rem_sec, state, now_utc, valid_until, budget['id'], policy_id, now_utc, now_utc)
                )

                pause_id = transition_engine.generate_uuid()
                c.execute(
                    """
                    INSERT INTO grant_pauses (
                        id, grant_id, paused_at_utc, pause_deadline_utc, effective_deadline_utc,
                        pause_reason, timeout_action, status, closed_at_utc, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'legacy', 'expire', 'OPEN', NULL, ?)
                    """,
                    (pause_id, grant_id, int(paused_at or now_utc), valid_until, valid_until, now_utc)
                )

                c.execute(
                    "UPDATE connections SET selected_grant_id = ?, desired_state = 'PAUSED', updated_at = ? WHERE id = ?",
                    (grant_id, now_utc, conn_data['id'])
                )
            else:
                # Active legacy session: assign pisofi_time_v1 with 24h fallback validity
                policy_id = 'pisofi_time_v1'
                state = 'ACTIVE'
                valid_until = now_utc + 86400  # 24 hours
                c.execute(
                    """
                    INSERT INTO time_grants (
                        id, owner_id, origin, source_ref, issued_seconds, remaining_seconds,
                        state, validity_mode, validity_duration_sec, activated_at_utc, valid_until_utc,
                        pause_budget_id, policy_version_id, created_at, updated_at
                    ) VALUES (?, ?, 'legacy', 'active_sessions', ?, ?, ?, 'activation_relative', 86400, ?, ?, ?, ?, ?, ?)
                    """,
                    (grant_id, owner_id, int(rem_sec), rem_sec, state, now_utc, valid_until, budget['id'], policy_id, now_utc, now_utc)
                )
                c.execute(
                    """
                    UPDATE connections SET selected_grant_id = ?, desired_state = 'ACTIVE',
                           last_settled_at_mono = ?, last_settled_at_utc = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (grant_id, mono_now, now_utc, now_utc, conn_data['id'])
                )

            # Record in ledger
            event_id = transition_engine.generate_uuid()
            c.execute(
                """
                INSERT INTO time_ledger (
                    event_id, operation_id, grant_id, owner_id, reason,
                    delta_seconds, balance_before, balance_after, created_at
                ) VALUES (?, NULL, ?, ?, 'legacy_import', ?, 0.0, ?, ?)
                """,
                (event_id, grant_id, owner_id, rem_sec, rem_sec, now_utc)
            )
            migrated_sessions += 1

        if not dry_run:
            conn.commit()

        return {
            'success': True,
            'dry_run': dry_run,
            'total_legacy_seconds': total_legacy_seconds,
            'migrated_sessions': migrated_sessions,
            'skipped_sessions': skipped_sessions
        }

    finally:
        conn.close()


if __name__ == '__main__':
    default_db = os.path.join(CURRENT_DIR, 'vendo_sessions.db')
    target_db = sys.argv[1] if len(sys.argv) > 1 else default_db
    is_dry = '--dry-run' in sys.argv
    res = run_migration(target_db, dry_run=is_dry)
    print("Migration result: " + json.dumps(res, indent=2))
