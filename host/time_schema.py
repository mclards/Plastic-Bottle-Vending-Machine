# -*- coding: utf-8 -*-
"""
Eco-Fi PisoFi-Style Time & Pause Schema Management
Strictly compatible with Python 3.5.3 (NO f-strings, NO variable annotations).

Provides additive table definitions and schema initialization without
disrupting existing legacy tables.
"""

import json
import time
import time_policy

SCHEMA_VERSION = 2

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
        used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0),
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
        remaining_seconds REAL NOT NULL CHECK(remaining_seconds >= 0),
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
        status TEXT NOT NULL CHECK(status IN ('OPEN', 'CLOSED', 'EXPIRED')),
        closed_at_utc INTEGER,
        created_at INTEGER NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_grant_pauses_grant ON grant_pauses (grant_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_grant_pauses_deadline ON grant_pauses (effective_deadline_utc);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_grant_pauses_open ON grant_pauses (grant_id) WHERE status = 'OPEN';",

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
    # Pre-configured with customer-friendly validity brackets so recycled bottles never prematurely expire
    default_brackets = list(time_policy.DEFAULT_VALIDITY_BRACKETS)

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


def _create_tables(conn):
    """
    Initialize the PisoFi-style time schema tables in the provided SQLite connection.
    Safe and idempotent.
    """
    c = conn.cursor()
    for stmt in TIME_SCHEMA_STATEMENTS:
        c.execute(stmt)
    seed_default_policies(conn)


# Integer microseconds are authoritative; old seconds columns are projections.
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

SCALE = 1000000
MAX_SECONDS = 315360000


def to_us(seconds):
    if isinstance(seconds, bool):
        raise ValueError('invalid_seconds')
    try:
        value = Decimal(str(seconds))
        if not value.is_finite() or abs(value) > MAX_SECONDS:
            raise ValueError('invalid_seconds')
        return int((value*SCALE).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError):
        raise ValueError('invalid_seconds')


@contextmanager
def transaction(conn):
    """A helper must never commit its caller's transaction."""
    # Python 3.5's sqlite3 implicitly commits DDL/SAVEPOINT in managed mode.
    # Use explicit transaction control before BEGIN; never change it mid-transaction.
    if not conn.in_transaction and conn.isolation_level is not None:
        conn.isolation_level=None
    if conn.in_transaction and conn.isolation_level is not None:
        raise RuntimeError('Open SQLite with isolation_level=None before starting a transaction')
    nested = conn.in_transaction
    name = 'ecofi_' + uuid.uuid4().hex
    conn.execute('SAVEPOINT '+name if nested else 'BEGIN IMMEDIATE')
    try:
        yield conn
        conn.execute('RELEASE SAVEPOINT '+name) if nested else conn.commit()
    except BaseException:
        if nested:
            conn.execute('ROLLBACK TO SAVEPOINT '+name)
            conn.execute('RELEASE SAVEPOINT '+name)
        else:
            conn.rollback()
        raise


def metadata(conn, key, default=None):
    row = conn.execute('SELECT value FROM time_metadata WHERE key=?',(key,)).fetchone()
    return row[0] if row else default


def set_metadata(conn, key, value):
    conn.execute('INSERT OR REPLACE INTO time_metadata(key,value) VALUES (?,?)',(key,str(value)))


ADDITIONS = {
    'time_grants': [('issued_us','INTEGER'),('remaining_us','INTEGER'),('pause_allowed','INTEGER NOT NULL DEFAULT 1'),
                    ('dl_kbps','INTEGER DEFAULT 3072'),('ul_kbps','INTEGER DEFAULT 1536'),('speed_override','INTEGER NOT NULL DEFAULT 0')],
    'connections': [('boot_id','TEXT'),('last_mono_us','INTEGER'),('authorized_until_us','INTEGER'),
                    ('admin_suspended','INTEGER NOT NULL DEFAULT 0'),('service_suspended','INTEGER NOT NULL DEFAULT 0'),
                    ('disconnect_paused','INTEGER NOT NULL DEFAULT 0'),('access_error',"TEXT NOT NULL DEFAULT ''")],
    'time_policy_versions': [('pause_allowed','INTEGER NOT NULL DEFAULT 1')],
    'time_ledger': [('journal_id','TEXT'),('account_id','TEXT'),('delta_us','INTEGER'),('before_us','INTEGER'),('after_us','INTEGER')],
    'transfer_claims': [('operation_id','TEXT')]
}

EXTRA_TABLES = '''
CREATE TABLE IF NOT EXISTS ledger_accounts (
 id TEXT PRIMARY KEY,owner_id TEXT,grant_id TEXT UNIQUE REFERENCES time_grants(id),balance_us INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS owner_bindings (
 owner_id TEXT PRIMARY KEY REFERENCES credit_owners(id),connection_id TEXT NOT NULL REFERENCES connections(id));
CREATE TABLE IF NOT EXISTS time_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS legacy_imports (
 source_key TEXT PRIMARY KEY,grant_id TEXT REFERENCES time_grants(id),amount_us INTEGER NOT NULL,
 disposition TEXT NOT NULL,details_json TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS network_intents (
 id INTEGER PRIMARY KEY AUTOINCREMENT,connection_id TEXT NOT NULL REFERENCES connections(id),
 version INTEGER NOT NULL,ip TEXT NOT NULL,mac TEXT NOT NULL,desired_state TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING',created_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS deposit_sessions (
 id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES credit_owners(id),connection_id TEXT NOT NULL REFERENCES connections(id),
 status TEXT NOT NULL,pricing_json TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,response_json TEXT,error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS deposit_events (
 event_id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES deposit_sessions(id),bottles INTEGER NOT NULL CHECK(bottles>0),received_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS deposit_recovery (
 event_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,reason TEXT NOT NULL,received_at REAL NOT NULL,resolved_at REAL);
'''


def init_time_schema(conn):
    if not conn.in_transaction:
        conn.execute('PRAGMA foreign_keys=ON')
    if not conn.execute('PRAGMA foreign_keys').fetchone()[0]:
        raise RuntimeError('Enable foreign_keys before starting the transaction')
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    with transaction(conn):
        _create_tables(conn)
        for stmt in EXTRA_TABLES.split(';'):
            if stmt.strip():
                conn.execute(stmt)
        for table, fields in ADDITIONS.items():
            present = {r[1] for r in conn.execute('PRAGMA table_info('+table+')')}
            for name, kind in fields:
                if name not in present:
                    conn.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+kind)
        if metadata(conn,'schema_version') is None:
            has_legacy = bool(conn.execute('SELECT 1 FROM time_grants LIMIT 1').fetchone())
            for table, predicate in [('active_sessions','remaining_seconds>0 OR pending_bottles>0'),
                                     ('members','wallet_minutes>0'),('time_transfers','is_claimed=0')]:
                if table in existing:
                    cols = {r[1] for r in conn.execute('PRAGMA table_info('+table+')')}
                    if table=='active_sessions' and 'pending_bottles' not in cols:
                        predicate='remaining_seconds>0'
                    has_legacy = has_legacy or bool(conn.execute('SELECT 1 FROM '+table+' WHERE '+predicate+' LIMIT 1').fetchone())
            set_metadata(conn,'ready',0 if has_legacy else 1)
            set_metadata(conn,'active_policy','pisofi_time_v1')
            set_metadata(conn,'pause_allowed',1)
        if 'vouchers' in existing:
            columns={r[1] for r in conn.execute('PRAGMA table_info(vouchers)')}
            if 'policy_version_id' not in columns:
                conn.execute('ALTER TABLE vouchers ADD COLUMN policy_version_id TEXT')
        set_metadata(conn,'schema_version',SCHEMA_VERSION)
        conn.execute('CREATE INDEX IF NOT EXISTS idx_pending_intents ON network_intents(status,id)')
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_pause ON grant_pauses(grant_id) WHERE status='OPEN'")
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_one_grant_binding ON connections(selected_grant_id) WHERE selected_grant_id IS NOT NULL')
        conn.execute('''CREATE TRIGGER IF NOT EXISTS immutable_grant_policy BEFORE UPDATE OF
            pause_count_max,pause_duration_sec,pause_timeout_action,min_balance_sec,max_balance_sec,
            global_validity_min,brackets_json,pause_allowed ON time_policy_versions
            WHEN EXISTS(SELECT 1 FROM time_grants WHERE policy_version_id=OLD.id)
            BEGIN SELECT RAISE(ABORT,'create a new policy version for existing grants'); END''')
        conn.execute('''CREATE TRIGGER IF NOT EXISTS exact_grant_balance_update BEFORE UPDATE OF remaining_us ON time_grants
            WHEN NEW.remaining_us<0 BEGIN SELECT RAISE(ABORT,'negative grant balance'); END''')
        conn.execute('''CREATE TRIGGER IF NOT EXISTS finite_pause_count BEFORE UPDATE OF used_count ON pause_budgets
            WHEN NEW.used_count<0 OR (NEW.pause_count_max IS NOT NULL AND NEW.used_count>NEW.pause_count_max)
            BEGIN SELECT RAISE(ABORT,'invalid pause budget'); END''')
