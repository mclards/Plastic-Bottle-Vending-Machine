# -*- coding: utf-8 -*-
"""
Eco-Fi PisoFi-Style Time & Pause Schema Management
Strictly compatible with Python 3.5.3 (NO f-strings, NO variable annotations).

Provides additive table definitions and schema initialization without
disrupting existing legacy tables.
"""

import json
import time

SCHEMA_VERSION = 1

TIME_SCHEMA_STATEMENTS = [
    # 1. Credit Owners (Durable identity, independent of ephemeral IP)
    """
    CREATE TABLE IF NOT EXISTS credit_owners (
        id TEXT PRIMARY KEY,
        owner_type TEXT NOT NULL,
        owner_key TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        UNIQUE(owner_type, owner_key)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_credit_owners_key ON credit_owners (owner_type, owner_key);",

    # 2. Devices (Mapping MAC to persistent owner)
    """
    CREATE TABLE IF NOT EXISTS devices (
        mac TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL REFERENCES credit_owners(id),
        last_ip TEXT,
        last_seen_at INTEGER NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_devices_owner ON devices (owner_id);",

    # 3. Policy Versions
    """
    CREATE TABLE IF NOT EXISTS time_policy_versions (
        id TEXT PRIMARY KEY,
        pause_count_max INTEGER,
        pause_duration_sec INTEGER NOT NULL,
        pause_timeout_action TEXT NOT NULL,
        min_balance_sec INTEGER NOT NULL,
        max_balance_sec INTEGER,
        global_validity_min INTEGER,
        brackets_json TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL
    );
    """,

    # 4. Pause Budgets (Shared allowance across split/transferred fragments)
    """
    CREATE TABLE IF NOT EXISTS pause_budgets (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL REFERENCES credit_owners(id),
        pause_count_max INTEGER,
        used_count INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_pause_budgets_owner ON pause_budgets (owner_id);",

    # 5. Time Grants (Individual earned/purchased packages)
    """
    CREATE TABLE IF NOT EXISTS time_grants (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL REFERENCES credit_owners(id),
        origin TEXT NOT NULL,
        source_ref TEXT,
        issued_seconds INTEGER NOT NULL,
        remaining_seconds REAL NOT NULL,
        state TEXT NOT NULL,
        validity_mode TEXT NOT NULL,
        validity_duration_sec INTEGER,
        activated_at_utc INTEGER,
        valid_until_utc INTEGER,
        pause_budget_id TEXT NOT NULL REFERENCES pause_budgets(id),
        policy_version_id TEXT NOT NULL REFERENCES time_policy_versions(id),
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_time_grants_owner_state ON time_grants (owner_id, state);",
    "CREATE INDEX IF NOT EXISTS idx_time_grants_valid_until ON time_grants (valid_until_utc);",

    # 6. Grant Pauses (Active or historical pause intervals)
    """
    CREATE TABLE IF NOT EXISTS grant_pauses (
        id TEXT PRIMARY KEY,
        grant_id TEXT NOT NULL REFERENCES time_grants(id),
        paused_at_utc INTEGER NOT NULL,
        pause_deadline_utc INTEGER,
        effective_deadline_utc INTEGER,
        pause_reason TEXT NOT NULL,
        timeout_action TEXT NOT NULL,
        status TEXT NOT NULL,
        closed_at_utc INTEGER,
        created_at INTEGER NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_grant_pauses_grant ON grant_pauses (grant_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_grant_pauses_deadline ON grant_pauses (effective_deadline_utc);",

    # 7. Physical Network Connections (Device to Selected Grant)
    """
    CREATE TABLE IF NOT EXISTS connections (
        id TEXT PRIMARY KEY,
        ip TEXT NOT NULL,
        mac TEXT NOT NULL,
        owner_id TEXT NOT NULL REFERENCES credit_owners(id),
        selected_grant_id TEXT REFERENCES time_grants(id),
        desired_state TEXT NOT NULL,
        applied_state TEXT NOT NULL,
        binding_version INTEGER NOT NULL DEFAULT 1,
        last_settled_at_mono REAL,
        last_settled_at_utc INTEGER,
        updated_at INTEGER NOT NULL,
        UNIQUE(mac),
        UNIQUE(ip)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_connections_grant ON connections (selected_grant_id);",

    # 8. Value Operations (Idempotency and replay cache)
    """
    CREATE TABLE IF NOT EXISTS value_operations (
        operation_id TEXT PRIMARY KEY,
        owner_id TEXT,
        action TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        response_json TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    """,

    # 9. Time Ledger (Audit trail of every value change)
    """
    CREATE TABLE IF NOT EXISTS time_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT UNIQUE NOT NULL,
        operation_id TEXT,
        grant_id TEXT,
        owner_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        delta_seconds REAL NOT NULL,
        balance_before REAL NOT NULL,
        balance_after REAL NOT NULL,
        created_at INTEGER NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_time_ledger_grant ON time_ledger (grant_id);",
    "CREATE INDEX IF NOT EXISTS idx_time_ledger_owner ON time_ledger (owner_id);",

    # 10. Transfer Claims (Escrow for time transfers)
    """
    CREATE TABLE IF NOT EXISTS transfer_claims (
        code TEXT PRIMARY KEY,
        source_grant_id TEXT NOT NULL REFERENCES time_grants(id),
        escrow_owner_id TEXT NOT NULL REFERENCES credit_owners(id),
        claimed_by_owner_id TEXT,
        claimed_at INTEGER,
        expires_at INTEGER,
        status TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    """
]


def seed_default_policies(conn):
    """Seed the default time_policy_versions if not already present."""
    c = conn.cursor()
    now = int(time.time())

    # 1. pisofi_time_v1 (Standard PisoFi behavior: 3 pauses, 60 min duration, auto-resume)
    default_brackets = [
        {'value': 60, 'expiration': 1440, 'enabled': True},    # 1 hr  -> 24 hrs
        {'value': 180, 'expiration': 4320, 'enabled': True},   # 3 hrs -> 3 days
        {'value': 1440, 'expiration': 10080, 'enabled': True}  # 1 day -> 7 days
    ]

    c.execute("""
        INSERT OR IGNORE INTO time_policy_versions (
            id, pause_count_max, pause_duration_sec, pause_timeout_action,
            min_balance_sec, max_balance_sec, global_validity_min,
            brackets_json, is_active, created_at
        ) VALUES (
            'pisofi_time_v1', 3, 3600, 'resume', 0, NULL, 1440, ?, 1, ?
        )
    """, (json.dumps(default_brackets), now))

    # 2. legacy_ecofi_pause_v1 (Preserves existing Eco-Fi forfeiture behavior for old sessions)
    c.execute("""
        INSERT OR IGNORE INTO time_policy_versions (
            id, pause_count_max, pause_duration_sec, pause_timeout_action,
            min_balance_sec, max_balance_sec, global_validity_min,
            brackets_json, is_active, created_at
        ) VALUES (
            'legacy_ecofi_pause_v1', NULL, 86400, 'expire', 0, NULL, NULL, '[]', 0, ?
        )
    """, (now,))


def init_time_schema(conn):
    """
    Initialize the PisoFi-style time schema tables in the provided SQLite connection.
    Safe and idempotent.
    """
    c = conn.cursor()
    for stmt in TIME_SCHEMA_STATEMENTS:
        c.execute(stmt)
    seed_default_policies(conn)
    conn.commit()
