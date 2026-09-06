# -*- coding: utf-8 -*-
"""
Eco-Fi PisoFi-Style Time & Pause Architecture Unit Tests
Strictly compatible with Python 3.5.3 (NO f-strings, NO variable annotations).
"""

import unittest
import sys
import os

# Ensure host directory is in path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

import time_policy


class TestTimePolicy(unittest.TestCase):

    def setUp(self):
        # Standard PisoFi test brackets (minutes ceiling -> validity minutes)
        self.sample_brackets = [
            {'value': 60, 'expiration': 1440, 'enabled': True},    # 1 hr  -> 24 hrs
            {'value': 180, 'expiration': 4320, 'enabled': True},   # 3 hrs -> 3 days
            {'value': 1440, 'expiration': 10080, 'enabled': True}, # 1 day -> 7 days
            {'value': 2880, 'expiration': 20160, 'enabled': False} # Disabled bracket
        ]

    def test_bracket_validity_selection(self):
        # 40 mins (2400s) matches 60 min bracket -> 1440 mins = 86400s
        self.assertEqual(time_policy.calculate_bracket_validity(2400, self.sample_brackets), 86400)
        
        # 60 mins (3600s) exact match -> 1440 mins = 86400s
        self.assertEqual(time_policy.calculate_bracket_validity(3600, self.sample_brackets), 86400)
        
        # 75 mins (4500s) matches 180 min bracket -> 4320 mins = 259200s
        self.assertEqual(time_policy.calculate_bracket_validity(4500, self.sample_brackets), 259200)

        # Disabled bracket should be skipped: 2000 mins skips 2880 (disabled) and falls back to global (1440 min)
        # But global is max(global, purchased) -> max(1440 * 60, 2000 * 60) = 120000s
        res = time_policy.calculate_bracket_validity(2000 * 60, self.sample_brackets, global_validity_min=1440)
        self.assertEqual(res, 2000 * 60)

    def test_global_fallback_floor(self):
        # 30 hours (108,000s) browsing, global 24 hours (86,400s)
        # Should get at least 30 hours validity, not truncated to 24 hours
        res = time_policy.calculate_bracket_validity(108000, [], global_validity_min=1440)
        self.assertEqual(res, 108000)

        # 10 minutes (600s) browsing, global 24 hours (86,400s)
        # Should get 24 hours validity
        res = time_policy.calculate_bracket_validity(600, [], global_validity_min=1440)
        self.assertEqual(res, 86400)

    def test_can_pause_grant_count_gate(self):
        # N = 3
        now = 100000
        # C = 0: allowed
        ok, reason = time_policy.can_pause_grant('ACTIVE', 3600, 0, now, pause_count_max=3)
        self.assertTrue(ok)
        self.assertIsNone(reason)

        # C = 2: allowed (this will be the 3rd pause)
        ok, reason = time_policy.can_pause_grant('ACTIVE', 3600, 2, now, pause_count_max=3)
        self.assertTrue(ok)
        self.assertIsNone(reason)

        # C = 3: denied (reached limit)
        ok, reason = time_policy.can_pause_grant('ACTIVE', 3600, 3, now, pause_count_max=3)
        self.assertFalse(ok)
        self.assertEqual(reason, 'pause_limit_reached')

        # Unlimited pauses (pause_count_max=None)
        ok, reason = time_policy.can_pause_grant('ACTIVE', 3600, 10, now, pause_count_max=None)
        self.assertTrue(ok)

    def test_can_pause_grant_balance_window(self):
        now = 100000
        # Bounds: L = 300 (5 min), U = 5400 (90 min)
        min_b = 300
        max_b = 5400

        # Exact maximum equality (5400s): allowed
        ok, reason = time_policy.can_pause_grant('ACTIVE', 5400, 0, now, min_balance_sec=min_b, max_balance_sec=max_b)
        self.assertTrue(ok)

        # One second above maximum (5401s): denied
        ok, reason = time_policy.can_pause_grant('ACTIVE', 5401, 0, now, min_balance_sec=min_b, max_balance_sec=max_b)
        self.assertFalse(ok)
        self.assertEqual(reason, 'above_max_balance')

        # Exact minimum equality (300s): allowed
        ok, reason = time_policy.can_pause_grant('ACTIVE', 300, 0, now, min_balance_sec=min_b, max_balance_sec=max_b)
        self.assertTrue(ok)

        # One second below minimum (299s): denied
        ok, reason = time_policy.can_pause_grant('ACTIVE', 299, 0, now, min_balance_sec=min_b, max_balance_sec=max_b)
        self.assertFalse(ok)
        self.assertEqual(reason, 'below_min_balance')

        # Maximum disabled (max_balance_sec=None): 10,800s allowed
        ok, reason = time_policy.can_pause_grant('ACTIVE', 10800, 0, now, min_balance_sec=min_b, max_balance_sec=None)
        self.assertTrue(ok)

    def test_can_pause_grant_state_and_calendar_checks(self):
        now = 100000
        # Not active
        ok, reason = time_policy.can_pause_grant('PAUSED', 3600, 0, now)
        self.assertFalse(ok)
        self.assertEqual(reason, 'not_active')

        # Admin suspended
        ok, reason = time_policy.can_pause_grant('ACTIVE', 3600, 0, now, admin_suspended=True)
        self.assertFalse(ok)
        self.assertEqual(reason, 'admin_suspended')

        # Zero balance (depleted)
        ok, reason = time_policy.can_pause_grant('ACTIVE', 0, 0, now)
        self.assertFalse(ok)
        self.assertEqual(reason, 'depleted')

        # Past calendar expiry
        ok, reason = time_policy.can_pause_grant('ACTIVE', 3600, 0, now, valid_until_utc=now - 1)
        self.assertFalse(ok)
        self.assertEqual(reason, 'calendar_expired')

        # Exact calendar expiry boundary (now == valid_until)
        ok, reason = time_policy.can_pause_grant('ACTIVE', 3600, 0, now, valid_until_utc=now)
        self.assertFalse(ok)
        self.assertEqual(reason, 'calendar_expired')

    def test_pause_deadlines_and_precedence(self):
        now = 10000
        duration = 3600 # 60 min -> 13600

        # Case 1: Timeout is earlier than validity (validity 20000)
        # Next event must be resume at 13600
        res = time_policy.calculate_pause_deadlines(now, pause_duration_sec=duration, valid_until_utc=20000)
        self.assertEqual(res['pause_deadline_utc'], 13600)
        self.assertEqual(res['effective_next_deadline_utc'], 13600)
        self.assertEqual(res['next_event_type'], 'resume')

        # Case 2: Calendar validity is earlier than timeout (validity 12000, timeout 13600)
        # Next event must be expiry at 12000
        res = time_policy.calculate_pause_deadlines(now, pause_duration_sec=duration, valid_until_utc=12000)
        self.assertEqual(res['pause_deadline_utc'], 13600)
        self.assertEqual(res['effective_next_deadline_utc'], 12000)
        self.assertEqual(res['next_event_type'], 'expire')

        # Case 3: Exact tie between timeout and validity (both 13600)
        # Expiry MUST win at the tie
        res = time_policy.calculate_pause_deadlines(now, pause_duration_sec=duration, valid_until_utc=13600)
        self.assertEqual(res['effective_next_deadline_utc'], 13600)
        self.assertEqual(res['next_event_type'], 'expire')

    def test_full_use_slack(self):
        now = 1000
        # Remaining credit: 7200s (2 hrs)
        # Expiry in 14400s (4 hrs): valid_until = 15400
        slack, can_full = time_policy.calculate_full_use_slack(7200, 15400, now)
        self.assertEqual(slack, 7200)
        self.assertTrue(can_full)

        # Expiry in 3600s (1 hr): valid_until = 4600
        # Slack is negative (-3600): cannot use all credit before expiry
        slack, can_full = time_policy.calculate_full_use_slack(7200, 4600, now)
        self.assertEqual(slack, -3600)
        self.assertFalse(can_full)

    def test_nominal_future_pause_allowance(self):
        # N = 3, C = 0, P = 3600 -> 10,800s (180 min)
        self.assertEqual(time_policy.calculate_max_nominal_pause_allowance(0, 3, 3600), 10800)
        # N = 3, C = 2, P = 3600 -> 3,600s (60 min)
        self.assertEqual(time_policy.calculate_max_nominal_pause_allowance(2, 3, 3600), 3600)
        # N = 3, C = 3, P = 3600 -> 0s
        self.assertEqual(time_policy.calculate_max_nominal_pause_allowance(3, 3, 3600), 0)

    def test_seconds_until_pausable_by_max(self):
        # Max balance = 5400 (90 min)
        # If remaining is 5401 -> must consume 1 second
        self.assertEqual(time_policy.seconds_until_pausable_by_max(5401, 5400), 1)
        # If remaining is 7200 -> must consume 1800 seconds (30 min)
        self.assertEqual(time_policy.seconds_until_pausable_by_max(7200, 5400), 1800)
        # If remaining is 5400 or lower -> 0
        self.assertEqual(time_policy.seconds_until_pausable_by_max(5400, 5400), 0)
        self.assertEqual(time_policy.seconds_until_pausable_by_max(3000, 5400), 0)


import sqlite3
import time_schema


class TestTimeSchema(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute('PRAGMA foreign_keys = ON;')
        time_schema.init_time_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_schema_tables_exist(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = set([r[0] for r in cursor.fetchall()])
        expected = {
            'credit_owners', 'devices', 'time_policy_versions', 'pause_budgets',
            'time_grants', 'grant_pauses', 'connections', 'value_operations',
            'time_ledger', 'transfer_claims'
        }
        self.assertTrue(expected.issubset(tables))

    def test_default_policies_seeded(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, pause_timeout_action, pause_count_max FROM time_policy_versions;")
        rows = dict([(r[0], (r[1], r[2])) for r in cursor.fetchall()])
        self.assertIn('pisofi_time_v1', rows)
        self.assertEqual(rows['pisofi_time_v1'][0], 'resume')
        self.assertEqual(rows['pisofi_time_v1'][1], 3)

        self.assertIn('legacy_ecofi_pause_v1', rows)
        self.assertEqual(rows['legacy_ecofi_pause_v1'][0], 'expire')


import transition_engine


class TestTransitionEngine(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute('PRAGMA foreign_keys = ON;')
        time_schema.init_time_schema(self.conn)
        self.now_utc = 100000
        self.mono_now = 500.0
        self.owner_id = transition_engine.get_or_create_owner(self.conn, 'device', 'mac:11:22:33:44:55:66', self.now_utc)
        self.conn_data = transition_engine.get_or_create_connection(
            self.conn, '10.0.0.100', '11:22:33:44:55:66', self.owner_id, self.now_utc, self.mono_now
        )

    def tearDown(self):
        self.conn.close()

    def test_top_up_and_immediate_activation(self):
        res = transition_engine.apply_operation(
            self.conn, self.owner_id, self.conn_data, 'TOP_UP_GRANT',
            {'seconds': 3600, 'origin': 'bottle'}, 'op-topup-1', self.now_utc, self.mono_now
        )
        self.assertTrue(res['success'])
        self.assertEqual(res['state'], 'ACTIVE')
        self.assertEqual(res['remaining_seconds'], 3600)
        # 3600s matches 60 min ceiling -> 1440 min (86400s)
        self.assertEqual(res['valid_until_utc'], self.now_utc + 86400)

        # Check ledger
        c = self.conn.cursor()
        c.execute("SELECT reason, delta_seconds, balance_after FROM time_ledger WHERE grant_id = ?", (res['grant_id'],))
        lrow = c.fetchone()
        self.assertEqual(lrow[0], 'bottle_reward')
        self.assertEqual(lrow[1], 3600.0)

    def test_pause_and_resume_flow(self):
        # 1. Top up
        res_topup = transition_engine.apply_operation(
            self.conn, self.owner_id, self.conn_data, 'TOP_UP_GRANT',
            {'seconds': 3600, 'origin': 'bottle'}, 'op-topup-2', self.now_utc, self.mono_now
        )
        self.conn_data['selected_grant_id'] = res_topup['grant_id']

        # 2. Pause (1st pause)
        res_pause1 = transition_engine.apply_operation(
            self.conn, self.owner_id, self.conn_data, 'PAUSE',
            {}, 'op-pause-1', self.now_utc + 10, self.mono_now + 10.0
        )
        self.assertTrue(res_pause1['success'])
        self.assertEqual(res_pause1['pause_count_used'], 1)
        self.assertEqual(res_pause1['pause_deadline_utc'], self.now_utc + 10 + 3600)

        # 3. Duplicate Pause request (Idempotent: should NOT increment count)
        res_pause_dup = transition_engine.apply_operation(
            self.conn, self.owner_id, self.conn_data, 'PAUSE',
            {}, 'op-pause-dup', self.now_utc + 20, self.mono_now + 20.0
        )
        self.assertTrue(res_pause_dup['success'])
        self.assertTrue(res_pause_dup.get('already_paused', False))
        self.assertEqual(res_pause_dup['pause_count_used'], 1)

        # 4. Resume
        res_resume = transition_engine.apply_operation(
            self.conn, self.owner_id, self.conn_data, 'RESUME',
            {}, 'op-resume-1', self.now_utc + 30, self.mono_now + 30.0
        )
        self.assertTrue(res_resume['success'])
        self.assertEqual(res_resume['state'], 'ACTIVE')

    def test_pause_count_limit_rejection(self):
        res_topup = transition_engine.apply_operation(
            self.conn, self.owner_id, self.conn_data, 'TOP_UP_GRANT',
            {'seconds': 3600, 'origin': 'bottle'}, 'op-topup-3', self.now_utc, self.mono_now
        )
        self.conn_data['selected_grant_id'] = res_topup['grant_id']

        # Consume 3 pauses
        for i in range(1, 4):
            # Pause i
            p = transition_engine.apply_operation(
                self.conn, self.owner_id, self.conn_data, 'PAUSE',
                {}, 'op-p-%d' % i, self.now_utc + i * 10, self.mono_now + i * 10.0
            )
            self.assertTrue(p['success'])
            self.assertEqual(p['pause_count_used'], i)
            # Resume i
            r = transition_engine.apply_operation(
                self.conn, self.owner_id, self.conn_data, 'RESUME',
                {}, 'op-r-%d' % i, self.now_utc + i * 10 + 5, self.mono_now + i * 10.0 + 5.0
            )
            self.assertTrue(r['success'])

        # 4th Pause attempt MUST BE DENIED
        p4 = transition_engine.apply_operation(
            self.conn, self.owner_id, self.conn_data, 'PAUSE',
            {}, 'op-p-4', self.now_utc + 50, self.mono_now + 50.0
        )
        self.assertFalse(p4['success'])
        self.assertEqual(p4['error'], 'pause_limit_reached')

    def test_pause_timeout_triggers_auto_resume(self):
        res_topup = transition_engine.apply_operation(
            self.conn, self.owner_id, self.conn_data, 'TOP_UP_GRANT',
            {'seconds': 3600, 'origin': 'bottle'}, 'op-topup-4', self.now_utc, self.mono_now
        )
        self.conn_data['selected_grant_id'] = res_topup['grant_id']

        # Pause at now
        p = transition_engine.apply_operation(
            self.conn, self.owner_id, self.conn_data, 'PAUSE',
            {}, 'op-p-auto', self.now_utc, self.mono_now
        )
        self.assertTrue(p['success'])
        eff_deadline = p['effective_deadline_utc']  # now + 3600

        # Run check_due_events before timeout -> nothing happens
        counts = transition_engine.check_due_events(self.conn, self.now_utc + 1800, self.mono_now + 1800.0)
        self.assertEqual(counts['resumed'], 0)

        # Run check_due_events AT timeout (now + 3600) -> AUTO-RESUMED!
        counts = transition_engine.check_due_events(self.conn, eff_deadline, self.mono_now + 3600.0)
        self.assertEqual(counts['resumed'], 1)

        # Verify grant state is ACTIVE again!
        c = self.conn.cursor()
        c.execute("SELECT state FROM time_grants WHERE id = ?", (res_topup['grant_id'],))
        self.assertEqual(c.fetchone()[0], 'ACTIVE')

    def test_calendar_expiry_overrides_pause(self):
        # Create grant with very short calendar validity (100 seconds)
        res_topup = transition_engine.apply_operation(
            self.conn, self.owner_id, self.conn_data, 'TOP_UP_GRANT',
            {'seconds': 3600, 'origin': 'bottle'}, 'op-topup-5', self.now_utc, self.mono_now
        )
        gid = res_topup['grant_id']
        self.conn_data['selected_grant_id'] = gid

        # Manually force validity to now + 50s
        self.conn.execute("UPDATE time_grants SET valid_until_utc = ? WHERE id = ?", (self.now_utc + 50, gid))
        self.conn.commit()

        # Pause at now -> pause deadline is now + 3600, but effective deadline is now + 50
        p = transition_engine.apply_operation(
            self.conn, self.owner_id, self.conn_data, 'PAUSE',
            {}, 'op-p-cal', self.now_utc, self.mono_now
        )
        self.assertTrue(p['success'])
        self.assertEqual(p['effective_deadline_utc'], self.now_utc + 50)
        self.assertEqual(p['next_event_type'], 'expire')

        # Advance to now + 55s -> check_due_events MUST expire the grant
        counts = transition_engine.check_due_events(self.conn, self.now_utc + 55, self.mono_now + 55.0)
        self.assertEqual(counts['expired'], 1)

        c = self.conn.cursor()
        c.execute("SELECT state FROM time_grants WHERE id = ?", (gid,))
        self.assertEqual(c.fetchone()[0], 'EXPIRED')


import tempfile
import migrate_legacy_sessions


class TestMigration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_legacy.db')
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE active_sessions (
                ip TEXT PRIMARY KEY, mac TEXT, remaining_seconds INTEGER, is_paused INTEGER,
                dl_kbps INTEGER, ul_kbps INTEGER, pending_bottles INTEGER, paused_at REAL,
                expires_at REAL, saved_at REAL, state_json TEXT, member_username TEXT
            )
        """)
        # Insert 1 active session (3600s)
        conn.execute("""
            INSERT INTO active_sessions (
                ip, mac, remaining_seconds, is_paused, dl_kbps, ul_kbps, pending_bottles,
                paused_at, expires_at, saved_at, state_json, member_username
            ) VALUES (
                '10.0.0.50', 'aa:bb:cc:dd:ee:01', 3600, 0, 3072, 1536, 0,
                0, 0, 0, '{}', ''
            )
        """)
        # Insert 1 paused session (1800s with expiration in 80000s)
        conn.execute("""
            INSERT INTO active_sessions (
                ip, mac, remaining_seconds, is_paused, dl_kbps, ul_kbps, pending_bottles,
                paused_at, expires_at, saved_at, state_json, member_username
            ) VALUES (
                '10.0.0.51', 'aa:bb:cc:dd:ee:02', 1800, 1, 3072, 1536, 0,
                100000, 180000, 0, '{}', 'john_doe'
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
            os.rmdir(self.temp_dir)
        except Exception:
            pass

    def test_run_migration(self):
        res = migrate_legacy_sessions.run_migration(self.db_path, now_utc=100000)
        self.assertTrue(res['success'])
        self.assertEqual(res['migrated_sessions'], 2)
        self.assertEqual(res['total_legacy_seconds'], 5400.0)

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        # Verify grants
        c.execute("SELECT remaining_seconds, state, policy_version_id FROM time_grants ORDER BY remaining_seconds DESC")
        grants = c.fetchall()
        self.assertEqual(len(grants), 2)
        # Existing active credit retains its legacy validity contract.
        self.assertEqual(grants[0][0], 3600.0)
        self.assertEqual(grants[0][1], 'ACTIVE')
        self.assertEqual(grants[0][2], 'legacy_ecofi_pause_v1')

        # 1800s was paused -> legacy_ecofi_pause_v1
        self.assertEqual(grants[1][0], 1800.0)
        self.assertEqual(grants[1][1], 'PAUSED')
        self.assertEqual(grants[1][2], 'legacy_ecofi_pause_v1')

        # Balanced custody and external issuance entries sum to zero.
        c.execute("SELECT SUM(delta_seconds) FROM time_ledger")
        total_ledger = c.fetchone()[0]
        self.assertEqual(total_ledger, 0.0)

        conn.close()


if __name__ == '__main__':
    unittest.main()
