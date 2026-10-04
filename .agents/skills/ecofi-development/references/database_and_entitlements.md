# SQLite Database Schema & Entitlement Engine Reference
> **Database File:** `/opt/ecofi/vendo_sessions.db` | **Engine:** `host/transition_engine.py` & `host/time_policy.py`.

---

## 1. Schema Specifications & Hard Constraints

### The `pause_budgets` Table (Critical Architectural Rule)
```sql
CREATE TABLE IF NOT EXISTS pause_budgets (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES credit_owners(id),
    pause_count_max INTEGER,
    used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0),
    created_at INTEGER NOT NULL
);
```
> [!CAUTION]
> **`pause_budgets` DOES NOT HAVE AN `updated_at` COLUMN!**  
> Any SQL statement attempting `UPDATE pause_budgets SET ..., updated_at=?` will instantly crash with `sqlite3.OperationalError: no such column: updated_at`, raising a 503 `storage_unavailable` error on the captive portal.

---

## 2. PisoFi Alignment & Wallet Decommissioning

- **Wallet / Member System Decommissioned:**
  - The legacy PisoFi member wallet system has been completely removed from VMC ECO-VENDO.
  - The client's hardware MAC address is the permanently authoritative identity key.
- **Deposit Lifecycle:**
  - Inserting a bottle directly credits time into the active entitlement session.
  - Automatically unpauses the client if previously paused.
  - Resets the client's `used_count` in `pause_budgets` to allow new pause cycles.

---

## 3. Dynamic Time Policy & Expiration Rules

Managed via `host/time_policy.py`:
- **Validity Expiration Rules:**
  - Tiers specify how long accumulated minutes stay valid (e.g., balance up to 30 mins valid for 3 hours, balance up to 60 mins valid for 12 hours).
- **Pause Timeout Actions:**
  - `resume`: Auto-resumes network connectivity and begins burning remaining time once pause duration expires.
  - `expire`: Instantly terminates and zeroes remaining time upon pause timeout.
  - `keep_paused`: Preserves pause state until session validity window expires.

---

## 4. Regression Testing Suite (`test_entitlement_regressions.py`)

- **Location:** `host/test_entitlement_regressions.py`.
- **Test Count:** 87 test cases verifying:
  - Database migrations, schema constraints, and rollback safety.
  - Concurrent deposit transactions and race conditions.
  - Pause budget limits and zero-balance gating.
  - Voucher redemption and session state transitions.
  - Portal resume / pause API contract and CNA redirects.
- **Execution Command:**
  ```bash
  cd host && python -m unittest test_entitlement_regressions
  ```
