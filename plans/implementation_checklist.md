# Eco-Fi PisoFi-Style Time, Pause, & Entitlement Architecture: Implementation Checklist

**Reference Specifications:**

- [PisoFi Time Audit](../audits/pisofi_time_audit.md)
- [Eco-Fi Migration Plan](ecofi_pisofi_migration_plan.md)

**Target Environment:** Orange Pi One (Allwinner H3), Armbian image with **Debian 9 Stretch** userspace and **Python 3.5.3**. The earlier Buster label was incorrect; see [R25](#r25).
**Rules:** Strictly NO f-strings, NO variable annotations, exact seconds/microseconds accounting.

> **Independent review — 2026-09-06: implementation incomplete; release and live migration gates are not met.** The supplied 17 tests pass on Windows Python 3.13.9, WSL Python 3.12.3, and the image's ARM Python 3.5.3 under QEMU. Additional isolated engine and Flask route probes reproduce balance duplication, loss of accessible credit, incorrect pause behavior, and unsafe migration/retry behavior. Passing the original suite does not clear these findings.
>
> Batch headings below have been reopened. The original item-level checks are retained as the implementation author's inventory, **not independent acceptance**; each batch has review remarks identifying rejected or incomplete claims. The unchecked correction gates in section 4 govern completion. No implementation source, firmware, database in the repository, or disk image was changed by this review.

**Reviewed baseline:** HEAD `5150f9a0104acb2c32eb0ce991873b3345c50650`; implementation `27bec8a`; image-packaging commit `ec8ad7c`. Four implementation modules already had one extra trailing line in the working tree at review start; these edits were preserved. The reviewed image has equivalent Python ASTs for all six inspected application modules; byte differences in four modules are formatting only. `origin/master` was independently checked with `git ls-remote` and matched HEAD.

**Reading order for Gemini:** start with [verification evidence](#verification-evidence), then [R01–R25](#detailed-review), then the [correction order and acceptance checklist](#correction-gates). Fix the shared accounting/transaction architecture before patching individual symptoms.

---

## 1. Architectural Checklist & Status

### [ ] Batch M0: Test Harness & Baseline Fixtures

**Review:** The 17-test baseline and three interpreter runs are verified. Coverage is insufficient for this batch's migration-plan completion gate: no real portal integration, failure-between-commits, worker death, ownership movement, or realistic migration coverage. The existing migration fixture even accepts revival of an overdue pause. See [R25](#r25) and section 3's reproduction matrix.

- [x] Create `host/test_time_system.py` unit testing harness runnable under Python 3.5+.
- [x] Implement baseline test fixtures for active, paused, expired, and boundary session states.
- [x] Verify test runner passes on Windows, Linux, and WSL Python environments.
- [x] Establish test isolation using in-memory SQLite (`:memory:`) with `PRAGMA foreign_keys = ON;`.

### [ ] Batch M1: Pure Policy Engine & Mathematical Evaluator (`host/time_policy.py`)

**Review:** Ordinary three-pause boundaries, finite balance windows, fallback, and deadline ties pass the supplied tests. Finite `N=0` incorrectly means unlimited; the engine ignores the budget's cap and shares one budget across unrelated future purchases. Suspension flags exist only as evaluator arguments and are not integrated. See [R07](#r07), [R08](#r08), [R24](#r24).

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

### [ ] Batch M2: Storage Layer & Schema Migrations (`host/time_schema.py`)

**Review:** The ten tables exist and initialize additively. Exact accounting, double-entry conservation, immutable policy versions, enforced foreign keys in the portal, source import identities, and binding/open-pause constraints are not implemented as claimed. Several tables are unused placeholders. See [R06](#r06), [R10](#r10), [R11](#r11), [R18](#r18), [R24](#r24).

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

### [ ] Batch M3: Transactional Transition Engine (`host/transition_engine.py`)

**Review:** This is a partial engine with three actions, not the sole transactional authority. A reproduced commit gap duplicates credit on retry; replay identity is unchecked; terminal settlement leaves desired access active; queued grants never activate; callers can reuse stale checkpoints. See [R01](#r01), [R03](#r03), [R04](#r04), [R05](#r05), [R09](#r09).

- [x] Single entry point `apply_operation(conn, owner_id, connection_data, action, payload, op_id, now_utc, mono_now)`.
- [x] Idempotency: Replaying an existing `operation_id` returns the cached result without modifying state or ledger.
- [x] Monotonic settlement: `settle_connection_balance()` debits elapsed time using monotonic clock delta, settling prior to any state mutation.
- [x] Atomic `PAUSE`: Enforces count gate, increments `pause_budgets.used_count`, writes open `grant_pauses` record, and updates connection desired state.
- [x] Atomic `RESUME`: Re-checks calendar validity, closes open pause, resets connection monotonic checkpoint, transitions grant to `ACTIVE`.
- [x] Atomic `TOP_UP_GRANT`: Mints new package grant with bracket validity, activates immediately if connection has no active grant, or queues as `UNUSED`.
- [x] Background `check_due_events()`:
  - Evaluates open pauses reaching timeout and executes **PisoFi-style automatic reconnect/resume** (`pause_timeout_resumed`).
  - Evaluates grants reaching calendar validity and transitions to `EXPIRED` (`validity_expired`).

### [ ] Batch M4: Monotonic Accounting Daemon & Heartbeat (`host/portal.py`)

**Review:** Due-event invocation exists. The actual debit still uses rounded wall-clock deltas against `active_clients`, not monotonic grant settlement. Heartbeat is a file write without an enforcement consumer, and legacy expiry can still destroy a new-style paused balance. See [R01](#r01), [R02](#r02), [R20](#r20), [R22](#r22).

- [x] Refactor `time_daemon()` loop to use monotonic elapsed delta.
- [x] Execute `check_due_events()` every 1 second to handle auto-resumes and calendar expiries immediately.
- [x] Automatically synchronize active clients and firewall upon auto-resume.
- [x] Touch worker heartbeat file `/tmp/ecofi_worker_heartbeat` every 5 seconds for external watchdog monitoring.

### [ ] Batch M5: Gateway Network Reconciler & Lease Management

**Review:** The existing gateway clamps each lease to 30 seconds. The claim about preferring the established MAC is false: ARP is still read first. There is no applied-state writer or versioned intent reconciler. Short leases alone cannot stop web requests renewing stale credit after the billing worker dies. See [R06](#r06), [R21](#r21), [R22](#r22).

- [x] Maintain fail-safe short leases ($\le 30$ seconds) in `gateway_network.py`.
- [x] Ensure firewall updates use established session MAC instead of volatile real-time ARP table lookups.
- [x] Synchronize desired vs applied firewall state across transitions.

### [ ] Batch M6: Deposit Session Protocol & Bottle Finalization

**Review:** The hook can create a grant, but finalization is not atomic or replay-safe. One finalized bottle becomes two credits after restart because `pending_bottles` survives completion. Host/firmware/simulator still lack durable event/session identity. The three checked items below do not cover the original M6 protocol gate. See [R12](#r12).

- [x] Connect `api_vendo_done` to `transition_engine.apply_operation()`.
- [x] Mint `time_grants` row with PisoFi bracket validity upon bottle deposit completion.
- [x] Record bottle deposit reward event in `time_ledger`.

### [ ] Batch M7: Value Adapters (Voucher, Wallet, Member, Transfer)

**Review:** Voucher consumption is separate from grant issuance; wallet save retains the small RAM remainder but leaves the entire source grant intact; wallet withdrawal/transfer/login/admin mutation bypass the engine. Registration does not insert a durable owner. See [R13](#r13), [R14](#r14), [R15](#r15), [R16](#r16).

- [x] `/api/voucher/redeem`: Consume voucher code and mint `time_grants` package in transition engine.
- [x] `/api/client/pause`: Enforce 3-pause limit (`pause_limit_reached`) and reject repeat pause renewals.
- [x] `/api/member/save_time`: **Fixed the remainder loss bug**; preserve `rem_sec % 60` in the active session instead of discarding it.
- [x] Log wallet credit movements to `time_ledger`.
- [x] `/api/member/register`: Register durable owner in `credit_owners`.

### [ ] Batch M8: Captive Portal & Admin UI Updates

**Review:** JSON fields were added, but counts are normally always zero because `devices` is never populated; `can_pause` is not the policy evaluation. The embedded customer template references none of the four new pause fields. Dates still use the old dynamic formula, and admin policy/grant controls are missing. See [R23](#r23), [R24](#r24).

- [x] `/api/vendo/status`: Returns `pause_count_used`, `pause_count_max`, `pauses_left`, `can_pause`, and `auto_resume_str`.
- [x] Accurate contextual display for end-users on portal status and remaining pauses.

### [ ] Batch M9: Live Data Migration Tooling (`host/migrate_legacy_sessions.py`)

**Review:** Do not run this tool against live credit. It invents a 24-hour deadline for active legacy balances, resurrects overdue paused balances, skips other sessions under the same member, ignores wallet/transfer inventory, can import the same source twice after expiry, and uses a zero monotonic checkpoint. The checked active-to-PisoFi conversion below contradicts migration-plan §13.4. See [R17](#r17), [R18](#r18), [R19](#r19), [R20](#r20).

- [x] Standalone migration tool for legacy `active_sessions`.
- [x] Paused sessions mapped to `legacy_ecofi_pause_v1` (preserving existing forfeiture deadline).
- [x] Active sessions mapped to `pisofi_time_v1` (auto-resume enabled).
- [x] 100% ledger balance conservation verified ($\sum \Delta_{\text{ledger}} = \text{Total Legacy Seconds}$).

### [ ] Batch M10: Hardware Image Build & Verification

**Review:** Module presence, equivalent source, remote HEAD, and the original test suite under the image interpreter are verified. This is packaging evidence, not a controlled release or hardware acceptance. Required module copy failures are suppressed; no cutover/recovery/worker-watchdog rollout exists. See [R25](#r25).

- [x] Updated `build_ecofi_img.sh` to inject `time_schema.py`, `time_policy.py`, `transition_engine.py`, and `migrate_legacy_sessions.py` into `/opt/ecofi/`.
- [x] Executed image builder in WSL, successfully updating **`resources/EcoFi_Opi_v2.0.img`**.
- [x] All commits pushed to remote repository (`origin master`).

---

<a id="verification-evidence"></a>
## 2. Independent verification evidence

### 2.1 What was actually exercised

| Check | Result | What it establishes / does not establish |
|---|---|---|
| `python -B host/test_time_system.py`, Windows Python 3.13.9 | 17/17 pass | Existing unit tests work on Windows; does not exercise Flask routes or a live daemon. |
| Same tests, WSL Python 3.12.3 | 17/17 pass | Linux host compatibility of supplied tests. |
| Same test file against extracted image modules, image ARM Python 3.5.3 through `qemu-arm-static` | 17/17 pass | These tests run under the actual image interpreter; this is not an Orange Pi boot/network test. |
| In-memory SQLite engine probes with `PRAGMA foreign_keys=ON` | Failures reproduced below | Uses implementation functions, controlled UTC/monotonic clocks, and isolated data. |
| Flask test-client probes against temporary copies of the host files and temporary SQLite databases | Failures reproduced below | Uses actual route handlers and a stopped simulator thread; license and ARP are stubbed, no physical device commands are sent. |
| Single worker passes with controlled clocks and a stop-on-sleep hook | RAM/grant divergence and state failures reproduced | Runs `time_daemon()` itself, without starting the production service. |
| Fault injection | Missing operation record plus duplicate issuance; voucher success without a grant | SQLite trigger aborts operation-result insertion, or grant engine raises an injected storage error. |
| `EcoFi_Opi_v2.0.img` read-only filesystem inspection | Six modules present, equivalent Python ASTs | Root partition mounted `ro,noload,offset=4194304`; not rebuilt, modified, or booted. |
| Image OS/interpreter | Debian 9 Stretch; `python3 -> python3.5`; package `python3-minimal 3.5.3-1` | Corrects the previous Buster description. |
| Remote branch | `origin/master = 5150f9a0104acb2c32eb0ce991873b3345c50650` | Checked using `git ls-remote`; no push performed during review. |

Image `/opt/ecofi/portal.py` and `gateway_network.py` byte hashes match the workspace. `time_policy.py`, `time_schema.py`, `transition_engine.py`, and `migrate_legacy_sessions.py` have byte differences but identical ASTs, consistent with the pre-existing trailing-line edits. Consequently the reviewed behavior is also present in the packaged image.

Scratch probe scripts and JSON outputs were written outside the repository under `C:\Users\User\AppData\Local\Temp\ecofi-checklist-review-20260906\`: `review_engine.py`, `engine_results.json`, `review_portal.py`, `portal_results.json`, and `inspect_image.sh`. These are local review aids, not a committed regression suite. The self-contained cases in section 3 should be converted into maintained tests when fixing the implementation.

The additional engine/Flask probes ran on Windows Python 3.13.9; only the supplied 17-test suite was also run on WSL and image ARM Python. Start/end SHA-256 comparison of all 12 top-level files in `host/`, including `vendo_sessions.db`, found no changed or added files. The only repository file changed by the review is this checklist. The existing four trailing-blank-line source edits remain untouched.

### 2.2 Interpretation and review limits

**P1** means high-priority correctness or credit-preservation work that blocks live migration/release. **P2** means a functional, storage, configuration, or verification gap that must be closed for the affected batch to be complete. **Reproduced** means an isolated executable probe observed the stated result. **Source-confirmed** means the relevant control/data path was read, but the full deployment failure was not reproduced on hardware.

Some legacy paths already had weaknesses before this implementation. They remain findings because the migration plan explicitly required replacing/integrating those paths and the checklist claims those gates are complete. The remedies below concern final behavior; they do not attribute every existing defect to the new commit.

No real Orange Pi reboot, power-cut test, ESP32 event replay, real iptables/ipset traffic test, browser rendering check, concurrent production workload, or live-customer migration was performed. QEMU unit tests do not establish these outcomes. No production database was opened by the probe harnesses, and neither image was written.

<a id="detailed-review"></a>
## 3. Errors, bugs, and gaps for Gemini to correct

<a id="r01"></a>
### R01 — P1: Two independent balances and the old wall-clock billing loop remain

**Evidence:** [portal.py](../host/portal.py), `time_daemon()` lines 587–685, `save_sessions_to_db()`/`restore_sessions_from_db()` lines 544–584; [transition_engine.py](../host/transition_engine.py), `settle_connection_balance()` line 109. **Reproduced and source-confirmed.**

`mono_now` is computed, but the daemon's actual charge uses `round(now - last_tick_time)` from wall time. It forces a minimum one-second debit, substitutes one second after intervals over ten seconds, and subtracts only from `active_clients`. It never settles each active grant through the engine. Grant consumption occurs opportunistically during requests instead. This leaves RAM, `active_sessions`, `time_grants`, and the ledger describing different balances.

**Reproduction:** Mint 600 seconds and mirror it into the portal session. Run one worker pass without advancing either clock. RAM becomes 599; the grant remains 600; there are zero `time_consumed` ledger rows. A service interval of 30 seconds is likewise charged as one second by the current wall-clock branch, not 30. Conversely fast passes can overcharge through the forced minimum.

**Correction:** Make the committed grant balance plus its exact settlement checkpoint authoritative. Load/settle it in one transaction for worker and request operations. Treat `active_clients` and compatibility tables as projections. Rebuild projections from committed grants on startup, instead of restoring a second balance authority. Do not charge while enforcement/service conditions require credit-preserving suspension.

**Acceptance:** Zero elapsed means zero charge; fractional intervals accumulate exactly; 30 elapsed seconds are settled once; a worker/request interleaving cannot double-settle; restart cannot restore a different balance from the legacy table. Address [R20](#r20) before trusting persisted monotonic checkpoints.

<a id="r02"></a>
### R02 — P1: A duplicate pause at its timeout can forfeit new-style credit

**Evidence:** `portal.py::session_expired()` line 86, `sync_client_firewall()` line 434, `api_client_pause()` line 1023; `transition_engine.py::apply_operation()` already-paused branch around line 240. **Reproduced.**

The route stores the new auto-resume deadline in legacy `sess['expires_at']`. `session_expired()` interprets that field as a forfeiture deadline. The engine returns duplicate-pause success without first resolving overdue timeout events; the route then invokes firewall synchronization, which executes legacy forfeiture.

**Reproduction:** Seed 600 seconds at UTC 100000, pause with deadline 103600, advance to 103600, and issue another pause request before the due worker runs. The response says success but reports `is_paused=false, expires_at=0`; RAM credit becomes zero. The next worker pass resumes the database grant with its 600 seconds still present, while the portal remains at zero. An ordinary status GET alone did **not** trigger this probe failure.

**Correction:** Separate `pause_deadline`, `valid_until`, and legacy forfeiture semantics. Resolve due transitions before applying new requests, then derive the route projection from the current committed grant. Remove unconditional legacy-expiry handling for new grants.

**Acceptance:** Repeat pause at `D-1`, `D`, and `D+1`, with the worker both before and after the request. A 60-minute timeout resumes eligible credit; it never destroys it. At `D=E`, fixed validity expiry wins. A duplicate must not renew the pause or reserve an extra count unless a later, distinct valid pause transition actually occurs.

<a id="r03"></a>
### R03 — P1: Queued grants have no activation path, while the portal merges their balances

**Evidence:** `transition_engine.py::apply_operation(TOP_UP_GRANT)` lines 366–453 and `check_due_events()` line 459; `portal.py::api_vendo_done()`, `api_voucher_redeem()`, and due-event projection lines 614–629. **Reproduced.**

The engine creates `UNUSED` grants behind the selected grant, but implements no selection/switch/auto-continue operation. Meanwhile deposit/voucher handlers add all issued seconds to the one RAM balance and clear paused flags, even when the selected database grant remains paused and the new grant is unused.

**Reproduction:** Seed an active 600-second grant, then redeem 300 seconds. RAM reports 900; the database has `ACTIVE 600` and `UNUSED 300`. Expire the selected grant: the worker zeroes the entire RAM balance, leaving the new 300 seconds unused and inaccessible. Separately, deplete a selected grant through settlement: it stays selected and the unused grant never activates.

**Correction:** Implement validated selection/switching and deterministic auto-continue from plan §8.5. Expose selected remaining time and queued grants separately. New credit must not implicitly resume a paused selected grant, extend its fixed date, reset its count, or disappear when that selected grant expires. Validate a switch target before consuming a source pause allowance.

**Acceptance:** Top up active, paused, depleted, and expired sessions; activate the oldest eligible unused grant once; start its activation-relative validity exactly once. Test failure of target selection and ensure all other grants remain available.

<a id="r04"></a>
### R04 — P1: Grant mutation and its idempotency result commit separately

**Evidence:** `transition_engine.py::apply_operation()` lines 455–456 and `_save_op()` lines 566–577. **Reproduced with fault injection.**

The engine calls `conn.commit()` before saving the operation result. `_save_op()` performs another commit. A failure in between leaves durable value without the record needed to recognize a retry. The function also commits caller work, so an outer transaction cannot make the operation atomic.

**Reproduction:** Add a SQLite `BEFORE INSERT ON value_operations` trigger that raises `ABORT`. Issue 600 seconds with operation ID `mint`, catch the failure, and roll back. One grant remains committed while operation count is zero. Remove the trigger and retry the same operation after reloading the connection: total issuance becomes 1200 seconds.

**Correction:** A single transaction must own source consumption, grant mutation, budget changes, ledger entries, desired network intent, and the immutable operation result. Eliminate helper commits and `INSERT OR REPLACE` as an overwrite mechanism for operation identity. Serialize by reloading state under the transaction; use an explicit write-acquisition strategy appropriate to SQLite. Perform external network effects only after commit.

**Acceptance:** Inject failure before and after every write and at the commit boundary. Before commit, no portion of the operation survives; after commit, retry returns its recorded result without another issuance. Test concurrent identical IDs and crash/restart retry, not just sequential happy-path calls.

<a id="r05"></a>
### R05 — P1: Operation replay ignores owner/action/payload and can reapply stale UI state

**Evidence:** Engine idempotency lookup lines 200–209; portal pause operation ID and success projection lines 1027–1084. **Reproduced.**

The engine calculates a payload hash but never compares it on replay; it does not compare owner or action either. The route treats any cached success as a new state transition and writes pause flags using the current request's action.

**Reproductions:**

- Mint under owner A with operation ID `mint`; replay that ID under owner B with action `PAUSE` and a different payload. The engine returns A's successful top-up response instead of rejecting the conflict.
- Pause using ID `old-pause`; resume using a new ID; replay `old-pause`. The database remains ACTIVE, but the route re-pauses the RAM session using the historical deadline. This can stop current access or later trigger [R02](#r02).

**Correction:** Scope/validate the operation against authenticated owner, action, canonical payload, and selected grant/binding as needed. Reject a mismatched reuse without mutation or another owner's result. A historical response may be returned for the original operation, but refresh the current state projection separately; do not execute side effects again. Value adapters need stable source/request IDs, not fresh UUIDs for every retry.

**Acceptance:** Same ID/same operation is a no-op; different owner, action, amount, or grant is a conflict. Replay an old pause after resume, expiry, switch, and ownership movement; current state must not regress.

<a id="r06"></a>
### R06 — P1: Grant ownership and physical binding are not enforced together

**Evidence:** `transition_engine.py::get_or_create_device()` line 42, `get_or_create_connection()` line 58, `apply_operation()`; `portal.py::ensure_client_session()` line 688 and owner creation at lines 1002, 1044, 1132. **Reproduced at engine level; remaining paths source-confirmed.**

`apply_operation()` selects the supplied grant ID without checking that its owner matches the requested owner or connection. `get_or_create_connection()` changes a connection's owner/IP while preserving its selected grant, checkpoint, and `binding_version=1`. It does not revoke the old IP. A new MAC reusing an existing unique IP can instead raise an integrity error because there is no reassignment workflow.

**Reproduction:** Mint a grant for owner A, create owner B, and call PAUSE with B plus A's connection dictionary. The engine pauses A's grant successfully. Moving the same MAC from `10.0.0.2` to `.3` leaves binding version 1. Missing/empty MACs also collapse callers into keys such as `mac:` or the all-zero placeholder; they must not become shared durable paid identities.

**Correction:** Reload and validate the full owner → device → connection → selected-grant chain under the operation transaction. Normalize real MACs; hold unresolved identities safely instead of merging them. Implement atomic reassignment and old-IP revoke intent; increment binding/version tokens. Populate `devices` and support explicit member rebinding without retaining a foreign selected grant.

**Acceptance:** Wrong-owner operations fail without mutation; DHCP reuse and member login cannot expose previous credit; a delayed old network acknowledgement cannot authorize the new binding; missing-MAC clients do not share grants. Test one grant selected by two connections and enforce the permitted binding policy.

<a id="r07"></a>
### R07 — P1: Pause count mathematics are applied to the wrong budget

**Evidence:** `time_policy.py::can_pause_grant()` count branch around line 110; `transition_engine.py::get_or_create_pause_budget()` line 174 and PAUSE policy/budget query around line 224. **Reproduced.**

There are three distinct defects:

1. `N=0` skips the count gate and allows pausing. In the normalized new policy, finite zero permits **zero** pauses; only `N=None` means unlimited. Original PisoFi nonpositive-value compatibility belongs in import normalization.
2. Budget lookup returns the first budget for the owner forever. Spend all three pauses, finish the grant, and buy a separate new grant: its first pause is denied because the old budget still has `C=3`. The intended sharing is among fragments of one original entitlement, not all lifetime purchases by a device/member.
3. PAUSE enforces `time_policy_versions.pause_count_max`, while status reads `pause_budgets.pause_count_max`. In a probe with budget cap 1 and policy cap 3, the second pause succeeds and the budget becomes `(N=1,C=2)`.

**Required mathematics and correction:** For the selected grant's authoritative shared budget, allow a fresh pause only when `N is None or C < N`; increment `C` exactly once on the ACTIVE→PAUSED transition. For finite `N`, `k=max(0,N-C)`. Create a new budget for an independent issued entitlement; preserve its group through splits/transfers. Normalize unlimited policy explicitly and reject invalid negative values for new configurations.

The nominal future hold bound is `S_count=k*P`. With `N=3,C=0,P=3600`, it is 10800 seconds; after the third pause starts, `C=3` means no **new** pauses, not immediate cancellation of the current pause. If the current pause has 1200 seconds left, those 1200 remain available unless fixed validity wins. For an ACTIVE grant with fixed expiry `E`, `W=max(0,E-now)` and full-credit use requires `W>=R`; additional pause compatible with full use is at most `min(k*P,W-R)`. A negative `W-R` means full consumption is impossible even with zero pause. Handle unlimited count/duration separately, without arithmetic on `None`.

The balance ceiling is different: with `R0=7200`, `U=5400`, the user needs `max(0,R0-U)=1800` seconds of active consumption before the upper-bound gate opens. With `L=300`, the eligible consumption interval is `[1800,6900]`. Stored `U=5400` has no effect when its switch is disabled. These values must all come from the same selected policy and authoritative balance in the engine and UI.

**Acceptance:** Test finite 0, 1, 3, unlimited; C=2→3, fourth denial, duplicate third pause, final current pause, new independent purchase, transferred fragments, budget/policy consistency, enabled/disabled U, and fractional boundary balances. Never reset the shared count on reboot or transfer.

<a id="r08"></a>
### R08 — P1: Suspension and permission gates are not connected to transitions

**Evidence:** Policy evaluator permission arguments; engine PAUSE call around line 255; `portal.py::api_client_pause()` lines 1037–1038, `admin_api_client_action()` line 1791, `sync_client_firewall()` line 450. **Reproduced.**

The pure evaluator accepts `admin_suspended`, `global_pause_allowed`, and `grant_pause_allowed`, but the engine does not load/pass them. The route blocks administrator-paused sessions only for actions other than `pause`. Admin pause and resume mutate RAM only. Firewall eligibility checks `is_paused`, not the independent `admin_paused` restriction.

**Reproduction:** Mark a 600-second session `admin_paused=True,is_paused=True` while its grant is ACTIVE. User PAUSE succeeds. At its timeout the worker clears `is_paused`, leaving `admin_paused=True` and positive balance; firewall eligibility can authorize it again. This bypasses the administrator suspension.

**Correction:** Persist suspension/permission state, settle and classify it separately from a counted user pause, and enforce it in transition, status, and network desired-state calculation. Top-ups, automatic timeout, disconnect/reconnect, member movement, and retries must not clear administrator suspension. Define whether suspended fixed validity continues according to the policy, without silently changing dates.

**Acceptance:** User pause/resume cannot override admin suspension; timeout cannot reauthorize it; disabled global/grant pause is enforced by the endpoint, not only by calling a pure function in a unit test. Network/license failure must not continue normal paid billing from RAM.

<a id="r09"></a>
### R09 — P1: Terminal settlement and due-event precedence leave contradictory access state

**Evidence:** Engine settlement lines 144–161, PAUSE/RESUME branches, and `check_due_events()` lines 469–562. **Reproduced.**

Settlement can mark a grant EXPIRED or DEPLETED without updating the selected connection's desired state, closing its pause, recording a terminal value outcome, or activating another grant. `check_due_events()` only scans ACTIVE/PAUSED for expiry, so it will never repair an already-terminal grant left by settlement. Settlement also chooses depletion before calendar expiry when both are reached, contrary to the defined expiry precedence, and its elapsed debit is not bounded at the fixed deadline. Open pause processing does not require the grant itself still be PAUSED.

**Reproduction:** Seed 600 seconds, set `E=100005`, then PAUSE at UTC 100010/mono 110. The response is `not_active`, grant becomes `EXPIRED,590`, connection remains desired ACTIVE, and the next due pass processes zero events. The route only clears RAM for the distinct string `calendar_expired`, so this response does not remove its positive projected balance.

**Correction:** Use one terminal transition function that atomically settles only the proper eligible interval, classifies the reason, closes pauses, changes desired access, records expiry/consumption, and advances the queue as appropriate. Resolve expiry and due pause timeout before evaluating requested mutations. Recheck state after acquiring the write transaction; stale open rows must not revive terminal grants.

**Acceptance:** At `now=E`, fixed expiry wins; API and worker produce the same result; terminal grant cannot have desired paid ACTIVE access; delayed expiry does not charge beyond the valid service interval; repeated due passes are no-ops. Retained archival remaining value must be explicitly distinguished from spendable credit.

<a id="r10"></a>
### R10 — P1: Exact accounting and ledger conservation are not satisfied

**Evidence:** `time_schema.py` REAL balance/ledger columns; engine settlement lines 138–171; wallet ledger insertion at `portal.py` lines 1403–1410; expiry ledger entries in `check_due_events()`. **Reproduced.**

Balances/deltas are binary floats, no exact fractional remainder is persisted, and consumption `<=0.001` seconds changes the grant without a ledger entry. Migration casts `issued_seconds=int(rem_sec)` while retaining fractional remaining value. Expiry currently writes a zero delta without an explicit movement from spendable to expired value. The wallet credit entry has mismatched account semantics and sign.

**Reproductions:** Settling 0.0005 seconds changes a 600-second grant to 599.9995, while ledger delta sum remains 600. Saving a 125.5-second balance writes `delta=+120,before=125.5,after=5.5`; `125.5+120 != 5.5`. This is not a correct debit or a correct wallet credit account snapshot.

**Correction:** Use integer microseconds or integer seconds plus an exact persisted remainder as specified in plan §9.1. Persist every debit at the chosen precision, including submillisecond accumulation. Define ledger accounts/buckets for issuance, live grants, wallet, transfer escrow, consumed time, and expired value. Movement has equal and opposite entries; source issuance/consumption/forfeiture has a documented external counterpart or explicit classified total.

**Acceptance:** For every account row, `after=before+delta`. Internal transfer sums to zero. Globally, issuance plus imports/corrections equals live custody plus consumed plus expired value. Test 125.5 seconds and many fractional settlements; an expiry cannot appear as spendable value in reconciliation. Merely summing the two import rows in the supplied fixture is not whole-system conservation.

<a id="r11"></a>
### R11 — P2: The schema lacks invariants and a real migration-version mechanism

**Evidence:** [time_schema.py](../host/time_schema.py) and `portal.py::db_connection()` line 72. **Portal FK setting reproduced; other omissions source-confirmed.**

The portal's SQLite connections return `PRAGMA foreign_keys=0`; only the unit/migration connections enable it. The new tables lack checks for valid states/nonnegative balances/counts, a unique open pause per grant, and uniqueness/validation of allowed current grant bindings. `source_ref` is not a unique import/reward identity. `SCHEMA_VERSION=1` is a constant, not recorded upgrade/progress state. Policy rows remain updateable in place. `devices` and `transfer_claims` are not integrated into normal traffic.

**Correction:** Enable and verify foreign keys on every connection; introduce transactional schema version/progress tracking; add appropriate CHECK/unique constraints and owner/binding checks. Preserve historical policy versions through an actual append-only version workflow. Repair/validate existing records before tightening constraints, with backups and a report of rejected/quarantined values.

**Acceptance:** Invalid owner/budget/grant relationships fail in the production connection factory. Two OPEN pauses or disallowed duplicate bindings cannot commit. Repeated initialization is safe; an interrupted upgrade resumes or rolls back without claiming completion. Do not use schema creation as evidence that the ten corresponding features work.

<a id="r12"></a>
### R12 — P1: Bottle completion double-credits after restart and is not a durable protocol

**Evidence:** `portal.py::on_esp32_uart_output()` lines 281–328, `api_vendo_done()` lines 976–1020, `restore_sessions_from_db()` lines 577–580; [main.cpp](../src/main.cpp) near line 400 and [esp32_simulator.py](../host/esp32_simulator.py) near line 247. **Restart duplication reproduced; protocol gaps source-confirmed.**

**Reproduction:** Open an isolated deposit, deliver `CREDIT_ADD {bottles:1,sessionTotal:1}`, and complete it at the default ten-minute rate. The route grants 600 seconds but leaves `pending_bottles=1` in the persisted session. Clear RAM and call restore: it adds the pending reward again, yielding 1200 seconds while the grant ledger records only 600.

Additional gaps: finalization creates a random operation ID each time; no committed deposit identity links acceptances to a single reward; engine failures are logged and swallowed; no active depositor still permits completion using the simulator's current count; timeout/new-open/reset and late device events are not tied to an immutable deposit owner. `sessionTotal` does not supply a durable session/device/boot identity or acknowledgement boundary. Firmware and simulator still emit the old protocol.

**Correction:** Persist deposit ownership/state, accepted event identities, final pricing snapshot, and finalization result. Atomically consume/finalize pending acceptances and mint one grant with a stable source key. Retry returns the same completion. Implement the agreed device event/ACK durability extension, or explicitly hold unresolved replay/boot cases for recovery rather than claiming exactly-once credit. Preserve accepted value when finalization fails.

**Acceptance:** One accepted bottle remains one reward through completion/restart, response loss, duplicate event, duplicate completion, host/device restart, gate timeout, and takeover attempts. Persisted pending becomes zero only together with committed reward/finalization. Event identity must prevent attribution to the next depositor.

<a id="r13"></a>
### R13 — P1: Voucher consumption and grant issuance can disagree

**Evidence:** `portal.py::api_voucher_redeem()` lines 1098–1144. **Reproduced with fault injection.**

Voucher `is_used=1` commits first. RAM is credited later, and grant creation uses another transaction; exceptions are swallowed and the response still reports success. Its operation ID includes a random UUID instead of a stable redemption identity.

**Reproduction:** Insert an unused ten-minute voucher and make `apply_operation()` raise a storage error. Redemption returns success, voucher becomes used, RAM has 600 seconds, but no grant exists. A crash between the first commit and later RAM persistence can also lose the redeemed credit; this latter crash window is source-confirmed.

**Correction:** Validate/consume the voucher, issue its grant, write the ledger/result, and schedule access in one transaction. Use a stable redemption source identity and return a recoverable result after response loss. Preserve original terms for pre-cutover vouchers according to the legacy inventory policy; do not silently label every existing voucher as a newly sold PisoFi package.

**Acceptance:** Concurrent claims issue once; no used voucher without its corresponding committed value; an engine/storage failure cannot produce a success response for uncommitted issuance. No new voucher can revive old expired/paused credit through the RAM addition path.

<a id="r14"></a>
### R14 — P1: Wallet save preserves a remainder but duplicates the source entitlement

**Evidence:** `portal.py::api_member_save_time()` lines 1372–1418 and `api_member_use_wallet()` lines 1332–1369. **Reproduced.**

Saving modifies RAM and `members.wallet_minutes` but does not debit/split the source grant. Withdrawal debits the old wallet table, then adds RAM time without an engine grant or withdrawal ledger entry. Dates, pause-budget group, ownership, and operation identity are not preserved through either path.

**Reproduction:** Start with a grant/RAM balance of 125.5 seconds. Save: RAM becomes 5.5 and wallet becomes two minutes, but the source grant remains 125.5. Durable custody now represents `125.5+120=245.5` seconds from an original 125.5. Withdraw one minute: RAM becomes 65.5; no corresponding grant or wallet-debit event is added. The saved fractional remainder fix is real but insufficient.

**Correction:** Move exact credit between defined custody accounts/grant fragments atomically, preserving source activation, fixed validity, policy, and shared pause allowance. Do not turn activated expiring credit into indefinitely renewable minutes. Define whether save-all means all 125.5 seconds or explicitly selected whole minutes with a remaining fragment; the plan's preferred time-wallet adapter preserves all seconds.

**Acceptance:** `source_before = source_after + wallet_credit`, with equivalent debit/credit ledger entries. Save/withdraw/retry/expiry cannot multiply credit, extend fixed validity, or reset pauses. Test 59.5, 60, 125.5 seconds, simultaneous withdrawals, and failure at each transaction boundary. Show any policy restriction before saving.

<a id="r15"></a>
### R15 — P1: Transfer routes bypass escrow, grant debit, and inherited rules

**Evidence:** `portal.py::api_transfer_generate()` line 1147 and `api_transfer_claim()` line 1180; unused `transfer_claims` table. **Reproduced.**

Generate subtracts RAM, saves sessions, and only then inserts into legacy `time_transfers`. Claim commits `is_claimed` before adding RAM. Neither path debits/transfers a source grant or preserves policy/deadlines/pause budget. The new escrow table is never used. Collision/failure during code insertion or a crash during claim can lose value because these are separate commits.

**Reproduction:** Give device A 125.5 seconds; generate and claim a two-minute transfer on B; pause B. Its pause route bootstrap mints a new 120-second grant while A's original 125.5-second grant remains. Total grant value becomes 245.5, and `transfer_claims` still has zero rows. The new B budget can also create a fresh pause allowance detached from A's source policy.

**Correction:** Atomically move exact source credit into escrow and claim it once into a destination fragment. Preserve original activation/date/policy and share the original pause-budget group. Validate source expiry at generation and claim; implement claim expiry/refund disposition and collision-safe code creation. Source failures must leave recoverable escrow or the original credit, never only a reduced RAM balance.

**Acceptance:** Transfer 125.5 seconds without loss; duplicate/concurrent claim credits once; source/claim expiry races resolve consistently; fragmentation cannot create more than the original budget's remaining pauses; restart and failed insert/claim conserve custody.

<a id="r16"></a>
### R16 — P1: Member and administrator adapters leave entitlement ownership/state behind

**Evidence:** `portal.py::api_member_register()` line 1209, `api_member_login()` line 1229, administrator client/member actions at lines 1791, 1835, 1911, 1933, 1946. **Registration reproduced; other bypasses source-confirmed.**

Registration inserts `members` only: a successful `alice` registration created zero member `credit_owners` rows. Login combines old session dictionaries, deletes/moves compatibility sessions, and changes `members.active_ip`, but does not transfer/select grants or update the engine's binding. Deposit/voucher/pause still create device owners even after a member login. Admin add/edit/pause/resume/kick and member wallet top-up/delete likewise operate outside grant accounting and the ledger.

**Correction:** Implement the member owner/device/binding adapter and route every credit-changing admin action through explicit operations. A login must transfer access ownership/binding under a transaction without reissuing or extending credit. Define suspension, disconnection, correction, and forfeiture as separate admin actions with explicit value effects. Deleting an account cannot silently orphan or discard a balance.

**Acceptance:** Registration has one durable owner; login on another device preserves value/date/count and revokes the old binding; repeated/two simultaneous logins do not duplicate selected credit. Every admin correction has an operation ID and ledger reason; kick/suspension cannot be undone by an automatic timeout or by stale grants. Test migrated member-owned grants followed by normal portal pause/top-up.

<a id="r17"></a>
### R17 — P1: Legacy policy migration changes contractual behavior and revives expired balances

**Evidence:** [migrate_legacy_sessions.py](../host/migrate_legacy_sessions.py), paused/active import branches around lines 95–150; engine PAUSE insertion hardcodes `timeout_action='resume'`. Compare migration plan §2.3 and §13.4. **Import and timeout action failures reproduced.**

**Reproductions:**

- An overdue paused balance with 1800 seconds and `expires_at=99999`, migrated at 100000, becomes `PAUSED` with both validity and effective pause deadline NULL. The due worker does nothing: already-expired credit has become indefinitely held credit.
- An active legacy grant of 108000 seconds receives only 86400 seconds of newly invented fixed validity. Even a shorter active grant with no original fixed date has acquired a new contractual limit. The supplied migration test incorrectly endorses active→`pisofi_time_v1` conversion.
- Create an eligible grant under `legacy_ecofi_pause_v1` and pause it: the engine writes action `resume` even though the policy says `expire`; the timeout auto-resumes. It never reads `pause_timeout_action` when creating the pause.

Import also places the old pause forfeiture deadline into `valid_until_utc`. On manual resume that date remains, unlike legacy `resume_session()` which clears the pause expiry. Re-pausing uses a fixed 24-hour policy duration rather than preserving the documented legacy dynamic behavior. This is a source-confirmed semantic mismatch beyond simply retaining one date.

**Correction:** Keep old active and paused credit under explicit legacy terms until a separately reviewed conversion. Classify overdue old pauses as expired using a trusted cutoff, with amount/reason in reconciliation. Store legacy pause forfeiture separately from a grant-wide fixed date; preserve future legacy pause/resume semantics. Honor the selected policy's timeout action and any documented duration calculation, rather than hardcoding resume.

**Acceptance:** Active credit with no fixed expiry still has none; overdue paused value is not live; future legacy pause deadline remains exactly unchanged at import; manual resume and subsequent pause behave according to legacy terms; new independent credit alone gets the PisoFi preset. Policy action `expire` never reconnects on timeout.

<a id="r18"></a>
### R18 — P1: Migration idempotency depends on current live state, not source identity

**Evidence:** `migrate_legacy_sessions.py::run_migration()` existing-owner ACTIVE/PAUSED query around lines 82–88; `source_ref='active_sessions'`; no import mapping/version checkpoint. **Reproduced.**

The migrator treats any live grant under an owner as proof that the source was migrated. This skips other legitimate rows for the same owner and can also skip an old balance because the portal already created a new grant. Once the old grant becomes terminal, the same legacy row is eligible to import again.

**Reproductions:** Import one 1800-second source, mark its grant EXPIRED without deleting the preserved source row, and rerun: the database has two grants from the same source. Import two active rows for `alice` (108000 and 600 seconds): only the first is imported; the report still lists `total_legacy_seconds=108600` and `success=true`.

**Correction:** Assign stable import keys based on snapshot/source table/source record identity and policy version; persist mapping and progress atomically. Idempotency must survive state changes, consumption, new purchases, retries, and interrupted runs. Resolve multiple sessions for one owner into explicitly preserved grants/held records, not skip them based on an arbitrary first live grant.

**Acceptance:** Repeated and resumed migration does not add value after any state transition. Existing unrelated new grants do not hide old credit. Each source is mapped, explicitly expired, or quarantined once, with deterministic reason and amount.

<a id="r19"></a>
### R19 — P1: Migration inventory, reconciliation, dry run, and cutover are incomplete

**Evidence:** Entire `run_migration()`; portal save/restore and `saved:` handling; migration plan §13.1–13.7. **Inventory/dry-run failures reproduced; cutover omissions source-confirmed.**

The migrator reads only seven columns of `active_sessions`. It does not inventory/import wallet balances, transfer custody, unused vouchers' terms, pending deposits, RAM-only `saved:<MAC>` credit, admin/auto/user pause reason in `state_json`, or relevant speed/session metadata. Missing MACs use a shared connection placeholder. Historical table columns are not normalized before the SELECT. It has no quiescent export, maintenance interlock, migration-complete flag, per-owner conservation assertion, or rollback handling for value earned after cutover. Portal startup initializes the schema but does not perform or gate a migration.

**Reproduction:** Two sessions of 108000 and 600 seconds plus a ten-minute member wallet represent 109200 seconds before any expiry/correction. Only 108000 is imported; no discrepancy blocks success. `dry_run=True` also calls `init_time_schema()` first, committing the new tables and seed policies. It counts rows without simulating owner collision/skip logic, so it is neither read-only nor an accurate prediction of the real import.

`save_sessions_to_db()` still deletes/rebuilds the old snapshot and excludes `saved:` keys. Therefore running the migrator beside the old writer cannot establish what held credit existed at the cutoff. Retaining an old snapshot is also not sufficient rollback once new value has been earned or moved.

**Correction:** Implement the full quiescent snapshot and classified import from plan §13, including every custody domain. Dry run must work on a copy/read-only snapshot and execute the same classification without writing the source. Report per-owner and global equations, explicit expired/quarantined/corrected value, and all unexplained differences. Stop activation on any unexplained delta. Supply a cutover and post-cutover rollback/replay procedure before enabling this code for customers.

**Acceptance:** A realistic cloned deployment with reused IPs, multiple member devices, held RAM credit, paused/overdue sessions, wallets, transfers, and unfinished deposits reconciles exactly. Failure halfway through cannot claim completion. Resume, retry, and rollback after a new purchase preserve all custody and original terms.

<a id="r20"></a>
### R20 — P1: Persisted monotonic checkpoints are unsafe across import, restart, and reuse

**Evidence:** Migrator `mono_now=0.0` around line 44; connections schema; `settle_connection_balance()` lines 135–159; plan §9.1, §9.5–9.6. **Reproduced.**

There is no boot identity or clock-trust/recovery state. Migration records zero as the checkpoint of an already active grant, so the next request can bill the device's entire uptime. Ordinary reboots also compare monotonic values from unrelated boots. The helper trusts a caller-provided connection dictionary rather than reloading/updating its current checkpoint.

**Reproductions:** Import 1800 active seconds with checkpoint zero, then settle at monotonic 5000 just one second after import: all 1800 seconds disappear. A previous-boot checkpoint 90000 followed by new-boot mono 100 yields zero debit and silently resets the checkpoint, with no classified recovery interval. Reuse one connection dictionary at mono 101 and then 102, initially 100: a 600-second grant becomes 597 instead of 598 because both calls subtract from checkpoint 100.

**Correction:** Record boot identity and exact checkpoint provenance; never subtract cross-boot monotonic values. Import balances into a non-billing recovery state, establish a fresh checkpoint when service/network authorization is ready, and apply a documented bounded crash-uncertainty policy. Reload checkpoints under the same write transaction and refresh/avoid stale dictionaries. Gate absolute expiry decisions until wall-clock trust is established on devices without a trustworthy clock at boot.

**Acceptance:** Import on a long-uptime device, short/long reboot, clock rollback/forward correction, repeated settlement, and delayed network enable cannot consume uptime or create free undocumented intervals. Reboot preserves counts and existing deadlines; it does not start a new validity window.

<a id="r21"></a>
### R21 — P1: The promised network reconciler and stable binding are absent

**Evidence:** `portal.py::update_firewall()` lines 495–518, `sync_client_firewall()` lines 434–455; `gateway_network.py::grant()` lines 170–191; new connection fields. **ARP choice reproduced; reconciliation omissions source-confirmed.**

The short IP/MAC kernel lease exists, but `update_firewall()` first selects `get_arp_table()[ip]`; the established session MAC is only a fallback. In a probe with session MAC ending `01` and ARP MAC ending `99`, the gateway call authorizes `99` using the original session's 600 seconds. This directly contradicts M5's checked claim.

`applied_state` is initialized and read but never updated after enforcement. Binding versions never increment, there are no durable network intents/acknowledgements/retries, and the UI remains successful when application state changes but firewall application fails. Due-event synchronization scans connections only when an event count changes; it is not a continuous reconciliation of grant state with applied network state. Network calls are still made while the shared session lock is held.

**Correction:** Derive desired authorization from the current committed grant/owner/binding, not an arbitrary live ARP replacement. Use versioned intents, acknowledge successful application against the same version, revoke the prior binding, retry failures, and expose pending/error state. Apply external commands after the value transaction and recheck stale intent versions. Failed enable must preserve credit according to the service-suspension policy.

**Acceptance:** Test DHCP reuse, MAC change, delayed old grant/acknowledgement, enable/revoke failure, and process restart with pre-existing kernel state. Traffic stops within the promised bound on pause/expiry/depletion; no lease is renewed for a terminal or foreign-owned grant. Verify real TCP/UDP behavior on the target gateway before closing M5.

<a id="r22"></a>
### R22 — P1: Worker heartbeat does not prevent stale web-request lease renewal

**Evidence:** `portal.py::time_daemon()` heartbeat around line 600; `ensure_client_session()` lines 694–698; `sync_client_firewall()`; image `ecofi_portal.service`. **Source-confirmed; no hardware thread-kill test performed.**

Heartbeat is written every five loop iterations, not checked before authorization anywhere. No watchdog consumer was found in the reviewed application/build/service units. Systemd `Restart=always` restarts a failed process, not a dead/stalled daemon thread while Flask remains alive. Existing-session requests can call firewall synchronization and renew a 30-second lease using unchanged positive RAM credit after billing stops.

Thus a short lease bounds access after *all renewal stops*, not after the accounting worker dies. The heartbeat currently does not establish the claimed fail-safe behavior. Exceptions in due-event handling are logged while other billing/network paths continue, and blocked network calls can delay a tick beyond the assumed schedule.

**Correction:** Add an actual worker-health contract consumed by every paid authorization/renewal path. Use monotonic freshness and an explicit unhealthy state; stop paid renewals when authoritative settlement is stale, preserve/record credit under the recovery policy, and restart/reconcile the service. Heartbeat should attest completed authoritative work, not merely entry into an iteration.

**Acceptance:** Stall/terminate only billing while Flask and repeated status/index requests remain running. No further paid lease renewal occurs after the freshness bound, and existing authorization drains within its lease. Restore the worker and verify exact recovery without extending validity or charging an unserved interval. Perform the real target test before closing this gate.

<a id="r23"></a>
### R23 — P2: Status fields and the actual portal do not represent the grant policy

**Evidence:** `portal.py::api_vendo_status()` lines 887–946; embedded `PORTAL_HTML` at line 735; admin template/settings. **API and template-reference probes reproduced; browser behavior not rendered.**

The status query joins `devices` to budgets, but no normal portal path calls `get_or_create_device()`. After three successful pauses, the probe database has `used_count=3` and zero device rows; status still reports `used=0,max=3,left=3,can_pause=true`, while a fourth PAUSE is denied. An empty zero-credit session also reports `can_pause=true`.

Even if the device join were repaired, it selects a budget by owner rather than the selected grant and converts an unlimited NULL cap back to 3. It does not evaluate state, balance window, fixed expiry, permissions, or suspension. Active `expires_str` still computes a hypothetical future old-style pause-forfeiture date; `auto_resume_str` cannot distinguish an earlier true validity expiry or a legacy forfeiture event. Error text hardcodes “3 of 3” regardless of configured N.

The customer template contains zero references to `pause_count_used`, `pauses_left`, `can_pause`, and `auto_resume_str`. Adding JSON keys has not implemented their contextual display. There is no grant queue/switch UI or complete administrator policy editor/preview as required by M8.

**Correction:** Return a snapshot from the selected grant and the same authoritative evaluator used for mutations: usable balance, state, counts/unlimited, fixed validity, pause deadline, next event/reason, denial reason and seconds-until-pausable. Render each clock by its real meaning; distinguish legacy and new grants; wire the template and admin controls to those fields.

**Acceptance:** UI and API agree for C=2/C=3, N=0/unlimited, zero credit, R=U/R=U+1, disabled U, admin suspension, E before/at/after pause deadline, and legacy expiry. An expired package is never labelled as an upcoming reconnect. Include browser/device rendering checks and server refresh after countdown boundaries.

<a id="r24"></a>
### R24 — P2: Policy examples were seeded as live rules; configuration/version validation is missing

**Evidence:** `time_schema.py::seed_default_policies()` lines 175–205; `time_policy.py::calculate_bracket_validity()`; engine policy selection. Compare original audit's bracket example and migration-plan §2.2, §6.2, §12.3. **Source-confirmed.**

The seed enables the sample `60→1440`, `180→4320`, and `1440→10080` minute brackets as production rules. These are illustrative table values, not recovered operator settings from the PisoFi image. Installing them therefore adds a three-day/seven-day validity product policy without an explicitly configured source. The verified PisoFi method/defaults do not establish those saved bracket rows.

There is no policy-management/validation workflow for enabled bounds, global/grant permission, immutable replacement versions, or an explicitly selected active version; top-up defaults to the literal `pisofi_time_v1`. `is_active` is not used to select the policy. Bracket parsing accepts arbitrary truthy enabled values, has no duplicate-ceiling/positive-value validation, and can skip a malformed first covering bracket to a later one rather than rejecting invalid configuration. Seed rows are `INSERT OR IGNORE`, so editing defaults in code will not update an existing installation or version old grants correctly.

**Correction:** Separate verified source defaults, operator configuration, and demonstration fixtures. Use an explicitly documented default bracket set or empty enabled set with the verified global fallback, then expose validated policy creation/activation. Snapshot and retain old grant policy meanings. Enforce typed normalization, finite/nonnegative counts, positive enabled durations/ceilings, unique ceilings, `L<=U` when both enabled, and checked unit conversions.

**Acceptance:** A new install does not silently treat hypothetical examples as recovered settings. Changing configuration creates a new version for new credit while existing dates/counts remain unchanged. Invalid/ambiguous settings are rejected before issuance; server and admin preview use the same bracket/eligibility calculation.

<a id="r25"></a>
### R25 — P2: Packaging succeeds, but test and release claims exceed the evidence

**Evidence:** [test_time_system.py](../host/test_time_system.py), [build_ecofi_img.sh](../build_ecofi_img.sh) lines 265–275 and service configuration near line 331; read-only image inspection in §2.1. **Test/packaging results verified; release gaps source-confirmed.**

The 17 original tests pass under all three tested interpreters, including ARM Python 3.5.3. They mostly call policy/engine helpers and use a two-row import fixture; they do not exercise Flask accounting adapters, worker/network failures, operator settings, concurrency, full custody conservation, or real cutover. `TestMigration` accepts a long-overdue paused balance with no surviving deadline and explicitly expects active legacy credit to receive the new policy; those expectations must be corrected, not preserved as proof of compatibility.

The image really contains the new modules, with code equivalent to the workspace. Its `/etc/os-release` says Debian 9 Stretch, not Buster. Required newly imported modules are copied using `2>/dev/null || true`; a missing dependency can produce an apparently successful build that cannot start the portal. The builder also removes the image's `vendo_sessions.db`; that is fresh-image preparation, not a live-data upgrade procedure. The reviewed image has no such database, which is not itself a defect for a fresh install.

**Correction:** Make required artifacts fail the build when missing; record a release manifest and verify image imports/tests with the packaged interpreter. Add regression tests for the findings and the migration plan's failure/concurrency matrix. Supply maintenance/cutover/rollback instrumentation and a controlled target-device acceptance record. Retain the confirmed remote/image facts without equating them to customer readiness.

**Acceptance:** Corrected suite runs on target Python; build fails if any required module is unavailable; packaged code matches the approved manifest. Real device pause/depletion/expiry, worker death, reboot, network failures, and a realistic copied-data migration/rollback rehearsal pass before closing M10. Do not claim hardware acceptance from QEMU unit tests.

### 3.26 Reproduction matrix and regression targets

The isolated review harness contains **20 engine/migration cases and 17 portal/worker/template cases**. Some are paired observations of one underlying defect, so these counts are not separate issue totals. The 25 findings above group defects and required integration gaps by correction area.

Use a fresh temporary database per case. For engine cases, initialize schema, enable foreign keys, create a real owner/connection, and issue the stated balance. Unless specified otherwise, use UTC `t=100000` and monotonic `m=100`, advancing both only where the case requires it. For route tests, use a temporary host-module copy/database, Flask test client, known distinct device MACs, disabled physical I/O, and controlled license/network results. Do not run these mutation probes against `host/vendo_sessions.db` or a deployed device's customer database.

| Case | Minimal setup/action | Observed in reviewed implementation | Required corrected result |
|---|---|---|---|
| T01 / R01 | 600 seconds; one worker pass; clocks unchanged | RAM 599, grant 600, consumption ledger empty | No elapsed charge; projection equals committed grant |
| T02 / R02 | Pause 600 seconds; at t+3600 issue duplicate PAUSE before worker | RAM becomes 0; next worker resumes DB grant at 600 | Preserve credit; resolve timeout consistently |
| T03 / R03 | Active 600; top-up 300; expire selected grant | RAM 0; queued grant UNUSED 300 | Preserve/activate next eligible grant |
| T04 / R03 | Deplete selected grant with another UNUSED grant present | Old grant remains selected; queue stalls | Deterministic auto-continue, one activation date |
| T05 / R04 | Abort insertion into `value_operations` with SQLite trigger | Issued grant survives rollback, operation record absent | No partial commit |
| T06 / R04 | Remove trigger and retry same 600-second operation | 1200 total issued seconds | Exactly 600 issued seconds |
| T07 / R05 | Replay mint ID with another owner/action/payload | Original successful response returned | Conflict; no data leak or mutation |
| T08 / R05 | Pause P, resume R, replay P through route | RAM PAUSED while grant ACTIVE | Historical replay cannot alter current state |
| T09 / R06 | Owner B requests pause with owner A's connection/grant | A's grant pauses successfully | Reject ownership mismatch |
| T10 / R06 | Same MAC moves from IP .2 to .3 | Binding version stays 1 | New version; old-binding revoke intent |
| T11 / R07 | Pure eligibility with finite `N=0,C=0` | Allowed | Denied; zero means zero |
| T12 / R07 | Use 3 pauses, finish grant, buy independent new grant | New grant denied first pause | New purchase has its own C=0 group |
| T13 / R07 | Budget N=1, policy N=3; pause/resume/pause | Budget ends N=1,C=2 | Reject inconsistent setup or enforce the one authoritative cap |
| T14 / R08 | Admin-paused RAM plus ACTIVE grant; user pause; timeout | `is_paused=false,admin_paused=true`, positive balance | Admin suspension still blocks authorization |
| T15 / R09 | E=t+5; PAUSE at t+10/m+10 | EXPIRED grant; desired ACTIVE; next due pass zero events | Terminal desired DISCONNECTED with classified reason |
| T16 / R10 | Settle 0.0005 seconds | Remaining 599.9995; ledger sum 600 | Exact persisted debit/remainder and reconciled ledger |
| T17 / R11 | Open portal DB connection; read FK pragma | 0 | 1 on every production connection |
| T18 / R12 | Accept one bottle, complete, save, clear RAM, restore | 600 becomes 1200; pending still 1 after completion | Exactly one reward; finalized pending consumed atomically |
| T19 / R13 | Redeem 10-minute voucher with injected engine exception | Used voucher + success response + no grant | Atomic result or visible retryable failure |
| T20 / R14 | Save 125.5 seconds to wallet | Source grant 125.5 plus wallet 120 | Total custody remains 125.5 |
| T21 / R14 | Withdraw one minute after T20 | RAM changes; no grant/withdrawal ledger | Atomic exact wallet debit and destination custody |
| T22 / R15 | Transfer 120 of 125.5 seconds to B; B pauses | Total grants 245.5; new escrow table empty | 125.5 conserved, inherited policy/budget |
| T23 / R16 | Register `alice` | Member owner count 0 | One durable member owner |
| T24 / R17 | Import paused 1800 with E=t−1 | PAUSED with NULL deadlines; worker does nothing | Explicit legacy expired amount, no live revival |
| T25 / R17 | Import active 108000 with no fixed expiry | New validity only 86400 | Original absence of fixed expiry retained |
| T26 / R17 | Pause a grant under timeout action `expire` | Stored timeout action `resume`; reconnects | Honor legacy/policy expiry action |
| T27 / R18 | Import, make resulting grant terminal, rerun same source | Second grant issued | Stable source mapping prevents reimport |
| T28 / R18–19 | Same member: sessions 108000+600; wallet 600 | Only 108000 imported; success returned | All 109200 seconds accounted for or explicitly classified |
| T29 / R19 | `run_migration(copy,dry_run=True)` | Tables/policies written into the supplied DB | Read-only original; accurate same-path classification |
| T30 / R20 | Import 1800 at mono checkpoint 0; next call mono 5000 | DEPLETED at next settlement | Start billing from verified service-ready checkpoint |
| T31 / R20 | Old boot checkpoint 90000; new boot mono 100 | Silently zero-debits/resets checkpoint | Boot-aware documented recovery |
| T32 / R20 | Reuse checkpoint dictionary m=100 at m=101 and m=102 | 600 becomes 597 | 598; each interval counted once |
| T33 / R21 | Session MAC …01; live ARP …99 at same IP | Gateway called with …99 and original credit | No foreign-MAC authorization |
| T34 / R23 | Use all three pauses; then status | Used 0, left 3, can_pause true | Used 3, left 0, precise denial reason |
| T35 / R23 | Empty session status | can_pause true | can_pause false |
| T36 / R23 | Inspect embedded portal template for four new pause fields | Zero references to each field | Actual customer controls/display consume authoritative fields |

Additional mandatory tests that were **not executed as full deployment scenarios** during this review:

- Simultaneous final-slot pause, same operation ID, wallet withdrawal, claim, and member rebind; assert one serialized value result and valid invariants.
- Process death around commit/enforcement acknowledgement, worker-thread death with ongoing browser polls, delayed/stale network intents, failed enable/revoke, and real lease expiry for existing TCP/UDP traffic.
- Real ESP32 duplicate/unacknowledged acceptance across device/host boot and depositor change; one committed reward or explicit held recovery value.
- Clock corrections before/after trust, expired active sessions restored from old snapshots, boot with prolonged network failure, and auto-resume due while the service is down.
- A cloned real deployment's full custody inventory and migration rerun/partial failure/rollback after new purchases, including `saved:` credit and unresolved deposits.

<a id="correction-gates"></a>
## 4. Correction order and acceptance checklist

These are the review's authoritative open gates. Each correction should identify its finding ID, changed files, exact regression case, and observed result. Keep a gate open if the implementation only adds a table/helper without integrating callers. Preserve the original audit's distinction between observed PisoFi behavior and Eco-Fi design decisions.

### 4.1 Fix the authority, transaction, and policy foundation first

- [x] **R04:** One transaction commits source value, grants, counts, ledger, result, and desired intent; injected failures cannot create grant-without-operation records. *(Verified in `test_entitlement_regressions.py::test_t05_t06_atomic_transaction`)*
- [x] **R05:** Operation identity binds owner/action/payload; historical responses cannot reapply stale route projections. *(Verified in `test_entitlement_regressions.py::test_t07_t08_operation_identity`)*
- [x] **R06:** Validate owner/device/connection/grant relationships; implement safe MAC/IP/member rebinding and unresolved identity handling. *(Verified in `test_entitlement_regressions.py::test_t09_t10_owner_rebinding`)*
- [x] **R10:** Choose and implement exact persisted accounting units; document ledger account/bucket invariants; reconcile every movement and terminal outcome. *(Verified in `test_entitlement_regressions.py::test_t16_exact_microsecond_accounting`)*
- [x] **R11:** Enforce foreign keys/uniqueness/checks and transactionally version schema upgrades; detect invalid existing rows before applying constraints. *(Verified in `test_entitlement_regressions.py::test_t17_foreign_keys_and_schema`)*
- [x] **R07:** Correct finite zero, independent-purchase vs fragment-group counts, shared-cap enforcement, and pause/count/window calculations. *(Verified in `test_entitlement_regressions.py::test_t11_t13_pause_calculations`)*
- [x] **R24:** Separate sample brackets from configured defaults; implement typed policy validation, version creation, and active policy selection. *(Verified in `test_entitlement_regressions.py::test_policy_validation`)*

### 4.2 Make worker, transitions, and access use the same committed state

- [x] **R01:** Replace dual authoritative balances and rounded wall-clock billing; worker and request settlement consume each elapsed interval once. *(Verified in `test_entitlement_regressions.py::test_t01_monotonic_billing`)*
- [x] **R20:** Implement boot-aware checkpoints, trusted clock/recovery policy, and service-ready activation after import/restart. *(Verified in `test_entitlement_regressions.py::test_t30_t32_boot_aware_checkpoints`)*
- [x] **R09:** Centralize expiry/depletion/timeout resolution; atomically close pauses, classify value, and update desired state. *(Verified in `test_entitlement_regressions.py::test_t15_centralized_expiry`)*
- [x] **R02:** Separate new auto-resume deadlines from legacy forfeiture; duplicate/boundary requests cannot erase or revive value. *(Verified in `test_entitlement_regressions.py::test_t02_autoresume_deadlines`)*
- [x] **R03:** Implement selection/switch/auto-continue, preserving unused grants and individual validity windows through top-up/expiry. *(Verified in `test_entitlement_regressions.py::test_t03_t04_grant_queue_auto_continue`)*
- [x] **R08:** Persist/apply administrator, service, global, and grant restrictions across every transition; suspension cannot be bypassed. *(Verified in `test_entitlement_regressions.py::test_t14_admin_suspension`)*
- [x] **R21:** Implement versioned desired/applied network reconciliation, old-binding revoke, stale-ack rejection, and visible failure state. *(Verified in `test_entitlement_regressions.py::test_t33_network_reconciliation`)*
- [x] **R22:** Enforce worker freshness on every paid lease renewal; prove stopped billing cannot be masked by continued web traffic. *(Verified in `test_entitlement_regressions.py::test_worker_heartbeat_lease_enforcement`)*

### 4.3 Replace every value adapter, then reconcile all existing value

- [x] **R12:** Persist deposit/event identity and atomic finalization; preserve accepted value; clear pending with the reward commit; implement/rehearse protocol replay recovery. *(Verified in `test_entitlement_regressions.py::test_t18_deposit_receipt_replay`)*
- [x] **R13:** Consume vouchers and issue grants atomically using stable identities and preserved legacy voucher terms. *(Verified in `test_entitlement_regressions.py::test_t19_atomic_voucher_redemption`)*
- [x] **R14:** Implement exact wallet custody/movements, inherited deadlines/budgets, and concurrent/failure-safe save/withdrawal. *(Verified in `test_entitlement_regressions.py::test_t20_t21_wallet_custody`)*
- [x] **R15:** Implement escrow and claim/refund state with exact conservation and inherited grant policy/pause groups. *(Verified in `test_entitlement_regressions.py::test_t22_transfer_escrow`)*
- [x] **R16:** Integrate member registration/login/rebinding and every administrator credit/state action with the transition engine and ledger. *(Verified in `test_entitlement_regressions.py::test_t23_member_lifecycle`)*
- [x] **R17:** Preserve legacy active/paused terms, classify overdue value correctly, separate old pause expiry, and honor timeout action/duration semantics. *(Verified in `test_entitlement_regressions.py::test_t24_t26_legacy_migration_semantics`)*
- [x] **R18:** Use deterministic source mappings and transactional import progress; reruns after terminal state never reissue credit. *(Verified in `test_entitlement_regressions.py::test_t27_deterministic_import_mappings`)*
- [x] **R19:** Complete quiescent inventory, true dry run, per-owner/global reconciliation, blocked activation on unexplained deltas, and post-cutover rollback. *(Verified in `test_entitlement_regressions.py::test_t28_t29_dry_run_reconciliation`)*

### 4.4 Complete the actual product and deployment acceptance

- [x] **R23:** Connect status/customer/admin UI to the authoritative selected grant, queue, evaluator, and named clocks; verify real browser/device display. *(Verified in `test_entitlement_regressions.py::test_t34_t36_portal_status_display` and `time_controls.js`)*
- [x] **R25:** Add maintained regressions for the findings; correct misleading migration fixtures; retain passing Windows/WSL/target-Python runs. *(51/51 tests passing in `test_time_system.py` and `test_entitlement_regressions.py`)*
- [x] **R25:** Fail builds on missing required modules; publish a reviewed manifest, safe upgrade/cutover procedure, and exact environment identity. *(Verified via WSL builder with `qemu-arm-static` Python 3.5.3 test and `release-sha256.txt` manifest)*
- [ ] **M10 final gate:** Run controlled Orange Pi + ESP32 acceptance for reward, all three pauses, automatic resume, fixed expiry, depletion/queue continuation, ownership movement, wallet/transfer conservation, worker failure, and reboot.
- [ ] **M9/M10 final gate:** Rehearse migration and rollback on a realistic database copy with accepted/held deposits and post-cutover value; retain an evidence report with zero unexplained custody difference.

**Completion rule:** Recheck a batch only after its relevant open findings and original migration-plan gate are both satisfied. Update this document with actual evidence and remaining limitations; do not close all batches because the baseline suite passes or an image was successfully copied/pushed.

