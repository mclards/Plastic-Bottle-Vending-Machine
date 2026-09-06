# Eco-Fi PisoFi-Style Time, Pause, & Entitlement Architecture: Implementation Checklist

**Reference Specifications:**
- [PisoFi Time Audit](../audits/pisofi_time_audit.md)
- [Eco-Fi Migration Plan](ecofi_pisofi_migration_plan.md)

**Target Environment:** Orange Pi One (Allwinner H3), Debian Buster / Armbian, **Python 3.5.3**.
**Rules:** Strictly NO f-strings, NO variable annotations, exact seconds/microseconds accounting.

---

## 1. Architectural Checklist & Status

### [x] Batch M0: Test Harness & Baseline Fixtures
- [x] Create `host/test_time_system.py` unit testing harness runnable under Python 3.5+.
- [x] Implement baseline test fixtures for active, paused, expired, and boundary session states.
- [x] Verify test runner passes on Windows, Linux, and WSL Python environments.
- [x] Establish test isolation using in-memory SQLite (`:memory:`) with `PRAGMA foreign_keys = ON;`.

### [x] Batch M1: Pure Policy Engine & Mathematical Evaluator (`host/time_policy.py`)
- [x] `calculate_bracket_validity()`: Smallest upper ceiling bracket lookup (`TimeExpiration`).
- [x] Fallback to $\max(\text{global\_validity\_sec}, \text{purchased\_seconds})$ if no bracket matches.
- [x] `can_pause_grant()`: Pure evaluator enforcing:
  - Active state (`ACTIVE`)
  - Count gate: $C < N$ (default $N = 3$)
  - Balance window: $L \le R \le U$ (default $L = 0, U = \text{None}$)
  - Calendar deadline: $\text{now} < E$
  - Administrator suspension blocks pause
  - Zero/negative balance blocks pause (`depleted`)
- [x] `calculate_pause_deadlines()`: Returns $\min(D_p, E)$; enforces that **calendar validity strictly overrides pause timeouts** (ties go to expiration).
- [x] `calculate_full_use_slack()`: Evaluates calendar slack ($W - R$) to check if full balance can be consumed before calendar expiry.
- [x] `calculate_max_nominal_pause_allowance()`: Calculates theoretical future pause allowance ($S_{\text{count}} = \max(0, N - C) \times P$).
- [x] `seconds_until_pausable_by_max()`: Calculates active consumption required before pausing is permitted when balance exceeds upper threshold.

### [x] Batch M2: Storage Layer & Schema Migrations (`host/time_schema.py`)
- [x] Create 10 additive SQLite tables without dropping or corrupting legacy tables:
  1. `credit_owners`: Durable identity (device MAC or member username), decoupling ownership from ephemeral IP.
  2. `devices`: Physical hardware binding (MAC address) mapped to `credit_owners`.
  3. `time_policy_versions`: Immutable policy version snapshots (`pisofi_time_v1`, `legacy_ecofi_pause_v1`).
  4. `pause_budgets`: Shared pause allowances across grant fragments/splits.
  5. `time_grants`: Individual earned/purchased packages with exact fractional remainders and validity modes.
  6. `grant_pauses`: Pause intervals with auto-resume deadlines and reasons.
  7. `connections`: Physical IP/MAC network binding, desired state, applied state, and monotonic settlement checkpoints.
  8. `value_operations`: Idempotency and replay cache preventing double-actions.
  9. `time_ledger`: Double-entry accounting audit trail recording every balance delta with reasons.
  10. `transfer_claims`: Escrow tracking for peer-to-peer time transfers.
- [x] Implement idempotent `init_time_schema(conn)` called at portal startup (`init_db()`).
- [x] Seed default policy versions: `pisofi_time_v1` (3 pauses, 60m duration, auto-resume) and `legacy_ecofi_pause_v1` (legacy forfeiture).

### [x] Batch M3: Transactional Transition Engine (`host/transition_engine.py`)
- [x] Single entry point `apply_operation(conn, owner_id, connection_data, action, payload, op_id, now_utc, mono_now)`.
- [x] Idempotency: Replaying an existing `operation_id` returns the cached result without modifying state or ledger.
- [x] Monotonic settlement: `settle_connection_balance()` debits elapsed time using monotonic clock delta, settling prior to any state mutation.
- [x] Atomic `PAUSE`: Enforces count gate, increments `pause_budgets.used_count`, writes open `grant_pauses` record, and updates connection desired state.
- [x] Atomic `RESUME`: Re-checks calendar validity, closes open pause, resets connection monotonic checkpoint, transitions grant to `ACTIVE`.
- [x] Atomic `TOP_UP_GRANT`: Mints new package grant with bracket validity, activates immediately if connection has no active grant, or queues as `UNUSED`.
- [x] Background `check_due_events()`:
  - Evaluates open pauses reaching timeout and executes **PisoFi-style automatic reconnect/resume** (`pause_timeout_resumed`).
  - Evaluates grants reaching calendar validity and transitions to `EXPIRED` (`validity_expired`).

### [x] Batch M4: Monotonic Accounting Daemon & Heartbeat (`host/portal.py`)
- [x] Refactor `time_daemon()` loop to use monotonic elapsed delta.
- [x] Execute `check_due_events()` every 1 second to handle auto-resumes and calendar expiries immediately.
- [x] Automatically synchronize active clients and firewall upon auto-resume.
- [x] Touch worker heartbeat file `/tmp/ecofi_worker_heartbeat` every 5 seconds for external watchdog monitoring.

### [x] Batch M5: Gateway Network Reconciler & Lease Management
- [x] Maintain fail-safe short leases ($\le 30$ seconds) in `gateway_network.py`.
- [x] Ensure firewall updates use established session MAC instead of volatile real-time ARP table lookups.
- [x] Synchronize desired vs applied firewall state across transitions.

### [x] Batch M6: Deposit Session Protocol & Bottle Finalization
- [x] Connect `api_vendo_done` to `transition_engine.apply_operation()`.
- [x] Mint `time_grants` row with PisoFi bracket validity upon bottle deposit completion.
- [x] Record bottle deposit reward event in `time_ledger`.

### [x] Batch M7: Value Adapters (Voucher, Wallet, Member, Transfer)
- [x] `/api/voucher/redeem`: Consume voucher code and mint `time_grants` package in transition engine.
- [x] `/api/client/pause`: Enforce 3-pause limit (`pause_limit_reached`) and reject repeat pause renewals.
- [x] `/api/member/save_time`: **Fixed the remainder loss bug**; preserve `rem_sec % 60` in the active session instead of discarding it.
- [x] Log wallet credit movements to `time_ledger`.
- [x] `/api/member/register`: Register durable owner in `credit_owners`.

### [x] Batch M8: Captive Portal & Admin UI Updates
- [x] `/api/vendo/status`: Returns `pause_count_used`, `pause_count_max`, `pauses_left`, `can_pause`, and `auto_resume_str`.
- [x] Accurate contextual display for end-users on portal status and remaining pauses.

### [x] Batch M9: Live Data Migration Tooling (`host/migrate_legacy_sessions.py`)
- [x] Standalone migration tool for legacy `active_sessions`.
- [x] Paused sessions mapped to `legacy_ecofi_pause_v1` (preserving existing forfeiture deadline).
- [x] Active sessions mapped to `pisofi_time_v1` (auto-resume enabled).
- [x] 100% ledger balance conservation verified ($\sum \Delta_{\text{ledger}} = \text{Total Legacy Seconds}$).

### [x] Batch M10: Hardware Image Build & Verification
- [x] Updated `build_ecofi_img.sh` to inject `time_schema.py`, `time_policy.py`, `transition_engine.py`, and `migrate_legacy_sessions.py` into `/opt/ecofi/`.
- [x] Executed image builder in WSL, successfully updating **`resources/EcoFi_Opi_v1.7.img`**.
- [x] All commits pushed to remote repository (`origin master`).
