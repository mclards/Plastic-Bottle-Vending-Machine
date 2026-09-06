# Eco-Fi migration to PisoFi-style time and pause behavior

**Status:** Detailed implementation specification; documentation only. No implementation, database migration, firmware change, or deployment has been performed.

**Prepared:** 2026-09-06. **Evidence baseline:** [PisoFi time audit](../audits/pisofi_time_audit.md), using the original image identified there and Eco-Fi commit `4ab6710222e878c1097fb2c622831115e010a8e9`.

## 1. Target and scope

Implement PisoFi's principal time-service behavior in Eco-Fi: separate purchased/earned sessions; seconds consumed only while active; fixed session validity; bounded pause duration followed by automatic resume; pause-count and eligible-balance restrictions; session switching/continuation; durable member ownership; explicit archival and recovery.

Bottle acceptance remains Eco-Fi's source of earned value. The existing time wallet remains a time wallet. Copying PisoFi's currency wallet, cash peripherals, charging outlets, desktop agent, mobile load sales, and data-quota products is outside this time-system migration. They are not silently declared equivalent or implemented.

This plan selects **separate entitlements**, corresponding to Scope B in the audit. An entitlement means one earned/purchased package with its own balance, validity, speed, origin, and pause policy. A device connection selects one entitlement. Member identity and credit ownership do not depend on an IP address.

The plan mimics verified intended time behavior, while deliberately fixing ambiguous expiry boundaries, incomplete transactions, unrestricted pause resets, and cleanup-dependent enforcement. These deliberate differences are listed explicitly. It does not promise byte-for-byte reproduction of every original defect or untested runtime branch.

### 1.1 Reading order

| Need | Sections |
|---|---|
| Exact target defaults and compatibility choices | 2 |
| Pause maths, limits, examples, and maximum holding time | 3–5 |
| Pricing, expiry, top-up, wallet and transfer equations | 6 |
| Database/state/transaction design | 7–9 |
| Endpoint, device and user interface integration | 10–12 |
| Migration, implementation batches, rollback and acceptance | 13–16 |

Sections 2–16 are a concrete proposed design. Statements identified as original evidence refer back to the audit. Recommendations become implemented behavior only after a later coding task.

## 2. Compatibility contract and configuration

### 2.1 Proposed new-credit preset

Call the preset `pisofi_time_v1`. Version it; do not modify the meaning of old grants when settings change.

| Policy | New-credit preset | Basis / qualification |
|---|---|---|
| Time unit | Integer seconds with fractional accounting remainder | Original remaining time is seconds; remainder is Eco-Fi precision improvement |
| Global validity | Enabled, 1,440 minutes | Original source default, not recovered saved setting |
| Rule selection | Smallest enabled purchased-minute ceiling covering the grant | Original bracket rule; enabled filtering fixes missing source filter |
| Global fallback length | At least purchased browsing duration | Verified ordinary cash/wallet purchase branches |
| Pause permission | Enabled globally and on new ordinary time grants | Source defaults |
| Pause timeout | Enabled, 60 minutes, action `resume` | Original source default and traced reconnect behavior |
| Maximum pause count | 3 per original entitlement/pause-budget group | Source count default; shared budget across fragments is an explicit Eco-Fi safeguard |
| Minimum eligible balance | Disabled; stored threshold 0 minutes | Original source default |
| Maximum eligible balance | Disabled; stored threshold 90 minutes | Original source default; 90 is not enforced while disabled |
| Session switching | Enabled; switching away from an active grant follows pause rules | Original switching concept; explicit enforcement contract |
| Auto-continue | Enabled, lowest creation sequence among eligible nonterminal grants | Close to original minimum-ID selection |
| Auto-merge | Disabled for all origins | Original source defaults; optional merge described later |
| Archive removal | Disabled initially | Original optional removal default; access expiry still enforced |
| Disconnect auto-pause | Disabled | Original source default |
| Resume on reconnect | Only disconnect-paused sessions when enabled | Intentionally narrower than original broad ARP resume |
| Boot behavior | Reconcile and resume previously active eligible grants when service ready | Mimics non-auto-pause branch; does not override explicit user/admin pause |
| Time wallet | Preserve seconds and original activated deadlines | Eco-Fi adapter, not PisoFi currency-wallet parity |
| Deadline equality | Expired at `now >= deadline` | Consistency improvement over original mixed comparisons |

A 90-minute setting does **not** mean the user can pause for 90 minutes. It limits eligible remaining credit only when the maximum-balance switch is enabled. A 60-minute timeout does **not** grant 60 minutes of additional internet. It temporarily stops the consumption clock.

### 2.2 Persisted settings and normalization

| External setting | Internal normalized value | Validation |
|---|---|---|
| Global/user pause enabled | Boolean | Strict boolean; no arbitrary truthy strings |
| Maximum pause count | Null for unlimited, otherwise nonnegative integer cap | Compatibility import may map original `<= 0` to unlimited; new UI uses an explicit unlimited switch |
| Pause timeout enabled + minutes | Null deadline when disabled; positive integer seconds when enabled | Reject enabled zero/negative duration; reject overflow |
| Minimum eligible minutes | Nonnegative integer seconds + enabled flag | Require positive credit independently |
| Maximum eligible minutes | Positive integer seconds + enabled flag | If both bounds enabled, minimum must not exceed maximum |
| Global validity minutes | Positive duration when enabled | Disabled means no global fallback date |
| Validity bracket | Positive minute ceiling, positive validity, enabled flag | Unique ceiling within policy version; sorted deterministically |
| Automatic continuation | Boolean and selection strategy | Strategy cannot silently change for old active grants |
| Merge policy | Disabled or named/versioned merge algorithm | No generic ambiguous `merge=true` |
| Clock health | Trusted / awaiting trust / degraded | Service state, not a freely supplied client field |

Store durations using checked conversions: seconds = minutes × 60; days × 86,400. Use an implementation-defined maximum accepted duration and checked integer arithmetic. Document that maximum when coding; do not infer a limit from the old 30-day dynamic formula.

### 2.3 Existing-credit policy

Existing balances must not suddenly start burning after one hour just because new purchases use the PisoFi preset. Import each recoverable legacy session into `legacy_ecofi_pause_v1` with its existing applicable pause deadline and current pause reason. Keep legacy forfeiture behavior until the preserved deadline, or migrate through a separately reviewed compensation/conversion policy.

New grants use `pisofi_time_v1`. The portal shows which active grant's rule applies. An old grant and a new grant may coexist under one owner; they must not be flattened and assigned an invented shared date.

## 3. Pause mathematics: definitions and eligibility

### 3.1 Symbols and units

| Symbol | Unit | Definition |
|---|---|---|
| `t` | UTC seconds | Trusted decision time |
| `R` | Seconds | Settled remaining browsing time of selected grant |
| `C` | Count | Pauses already consumed in its pause-budget group |
| `N` | Count or infinity | Maximum allowed counted pauses |
| `P` | Seconds or infinity | Allowed duration of one pause |
| `L` | Seconds | Minimum eligible balance, or 0 if disabled |
| `U` | Seconds or infinity | Maximum eligible balance, or infinity if disabled |
| `E` | UTC seconds or infinity | Activated fixed validity deadline |
| `t_p` | UTC seconds | Time current pause began |
| `D_p` | UTC seconds or infinity | Pause timeout deadline |
| `G` | Boolean | Global pause permission |
| `A` | Boolean | Per-grant pause permission |
| `B` | Boolean | Administrator/service suspension blocks transitions |

“Settled” means elapsed use and existing deadlines have been applied before reading the balance. Neither the browser's countdown nor an old SQLite snapshot is an authoritative `R`.

### 3.2 Maximum number of pauses

For a finite cap:

```text
pauses_left = max(0, N - C)
count_allows_new_pause = C < N
```

For unlimited cap, `pauses_left` is represented as null/unlimited and the count gate allows another pause. Continue counting events for history even when unlimited.

Increment only after a successful transition from active to paused:

```text
C_after = C_before + 1
```

With `N = 3`, the first pause changes 0→1, second 1→2, third 2→3. The third pause is valid and remains paused until resume/deadline. A **fourth new pause** is denied. Do not immediately resume the third pause because `C == N`.

Original evidence: normal API rejects new pause at `C >= N`; the kicker's excessive-count predicate is `C > N`. The migration uses the API interpretation as the normal contract. `C > N` after importing corrupt state or reducing a cap is handled by explicit policy reconciliation, not a broad reconnect SQL query.

### 3.3 Eligible remaining-balance window

```text
balance_allows_pause = R > 0 AND R >= L AND R <= U
```

Both bounds are inclusive. Disabled maximum gives `U = infinity`; disabled minimum gives `L = 0` while the independent positive-balance check remains.

The global and grant pause permissions are both required. A normal new user pause is allowed only when:

```text
can_pause = selected grant is ACTIVE
            AND G AND A
            AND NOT B
            AND R > 0
            AND t < E
            AND C < N
            AND L <= R <= U
```

If count or date is unlimited, omit the corresponding bound. Data-quota grants are not supported in this migration and must not be accidentally treated as time grants with zero remaining seconds.

### 3.4 Solve when an over-large balance becomes pausable

If maximum balance is enabled and `R > U`, the client must consume:

```text
required_active_consumption = R - U
```

At one billed second per eligible active second, earliest eligibility without top-up/interruption is `t + (R - U)`, provided the grant remains valid and the resulting balance also satisfies the minimum.

Example: 180 minutes left, maximum eligible 90 minutes. Consume `180 - 90 = 90` active minutes first. Pausing at 90:00 remaining is allowed; at 90:01 it is not.

If `R < L`, further consumption cannot make that same unmodified grant eligible. It requires compatible new credit under a permitted merge, a different grant, or policy change. A date alone cannot fix an insufficient balance.

### 3.5 Solve the eligibility window

For a continuously active grant with initial `R0`, no intervening top-up and no expiry:

```text
R(x) = R0 - x
eligible active-consumption interval:
    max(0, R0 - U) <= x <= R0 - L
    with R0 - x > 0
```

`x` measures **consumed active seconds**, not wall time including pauses. If `R0 < L`, the interval is empty. Validity adds `t0 + elapsed_wall < E`; count and suspension can remove eligibility even inside this balance window.

Example: `R0 = 120 min`, `L = 5 min`, `U = 90 min`. Pause eligibility starts after 30 active minutes and lasts until 115 active minutes have been consumed, inclusive at five minutes remaining. One second later, it is below the minimum. A count limit of three still permits only three transitions within that window.

### 3.6 Atomic count reservation

Two pause requests must not both observe `C = 2` and consume the last slot independently. Within the same write transaction, verify current grant state/version and reserve the budget only if below cap. Commit state and count together.

Repeated request ID returns the original result. A different request ID for an already paused grant returns its current pause with no additional count, timestamp change, or deadline extension. A request arriving after expiry is processed as expired before considering a new pause.

Never implement pause-count protection solely in JavaScript or by hiding the button.

## 4. Pause duration, fixed validity, and maximum holding time

### 4.1 One pause

On a successful counted pause:

```text
t_p = t
D_p = t_p + P                   when timeout enabled
D_p = infinity                  when timeout disabled
effective_next_deadline = min(D_p, E)
```

Before that deadline, browsing seconds are preserved. When `E <= D_p`, validity expiry takes precedence, including equality. When `D_p < E`, timeout attempts to resume the still-eligible grant.

Store `D_p` separately from `E`. Capping the displayed next event does not rewrite the stored fixed validity.

### 4.2 Remaining pause duration

```text
pause_seconds_left = max(0, D_p - t)
validity_seconds_left = max(0, E - t)
effective_pause_seconds_left = min(pause_seconds_left, validity_seconds_left)
```

Return a reason as well as a duration: `automatic_resume` or `validity_expiry`. Null/infinite dates must be handled without subtracting a sentinel value.

Example: paused at 10:00, `P = 60 min`, `E = 10:45`. At 10:30 the next event is expiry in 15 minutes, not automatic resume in 30. At 10:45 credit expires; the 11:00 pause event cannot revive it.

### 4.3 Original minute predicate and worker delay

The source predicate is `TIMESTAMPDIFF(MINUTE, last_paused, NOW()) >= pause_minutes`. For a normal positive interval it first becomes true after the configured whole-minute duration. Example: pause 10:00:30, 60 minutes: threshold 11:00:30, not 11:00:00.

Eco-Fi should calculate the exact timestamp deadline directly. Scheduler delay means actual resume can occur later:

```text
actual_resume = D_p + scheduler_delay + application_delay
```

Do not claim an exact three-second bound without measured worst-case delays. Target a one-second deadline scan and instrument observed lateness. This is an implementation target, not a measured result.

### 4.4 Do not retroactively bill the automatic-resume delay

Selected contract: a scheduled resume becomes billable only when the connection is actually activated/reconciled. If the worker processes an 11:00 deadline at 11:00:04, do not subtract four seconds of paused time that was not reauthorized. Record the four-second lateness.

After activation, active time continues to consume even if the customer is not browsing, matching the vending-time concept. A network-application failure keeps the connection in pending/suspended state and preserves browsing credit; fixed validity still advances. That failure behavior is an explicit Eco-Fi reliability improvement.

This avoids treating UTC `now - D_p` as unconditional billing. A process/host outage is handled by its own recovery rule.

### 4.5 Maximum future pause allowance for one active grant

Assume no new credit, policy change, reboot compensation, transfer-created budget, or administrator/service suspension; finite pause count and duration; and the grant can reach the eligible balance window before expiry.

```text
k = max(0, N - C)
S_count = k * P
maximum theoretical future non-consuming user-pause time = S_count
```

This is a bound, not an automatically credited pool of extra time. Each actual pause must pass eligibility. Unused allowance is not added to browsing credit or wallet value.

For `N = 3`, `C = 0`, `P = 60 min`, maximum is 180 pause minutes. With 120 browsing minutes, latest theoretical depletion without fixed validity is 300 wall minutes after now, assuming all three pauses are used and other assumptions hold.

Repeated HTTP requests do not increase this bound. Creating new legitimate grants can create new allowances, so there is no global lifetime bound across unlimited purchases.

### 4.6 Maximum pause while still using all remaining credit before fixed expiry

Let the remaining calendar window be `W = max(0, E - t)`. To consume all `R` browsing seconds before expiry, at least `R` active seconds are required. Therefore:

```text
slack = W - R
if W < R:
    consuming all credit is impossible, even with no pause
else:
    S_full_use <= min(k * P, W - R)
```

The full-use bound also becomes zero if there is no reachable pause-eligible balance window, or permissions/count prevent pausing. It assumes service availability and no other non-consuming time.

Do not clamp negative slack and then say “all credit can still be used.” Negative slack is a separate impossibility result.

Example: 120 browsing minutes, expiry in 240 minutes, three pauses of 60 allowed. Nominal pause allowance is 180 minutes, but using all browsing credit leaves only `240 - 120 = 120` pause minutes. Spending all 180 pause minutes means at least 60 browsing minutes remain unused at expiry in an ideal otherwise-continuous schedule.

At exact depletion/expiry equality, all browsing time up to the deadline may have been delivered; expiry wins as the terminal reason if events tie. No access is granted after the deadline.

### 4.7 Already-paused grant

The current pause has already consumed one count. Do not count it again when calculating future allowance:

```text
current_hold = max(0, min(D_p, E) - t)
future_new_pauses = max(0, N - C)
remaining_nominal_hold <= current_hold + future_new_pauses * P
```

Example: third pause is in progress, `N = C = 3`, 20 minutes remain. Current pause may last 20 more minutes, but no new counted pause is available. Showing “0 pauses left” must not imply the current pause is invalid.

For a full-credit-use calculation, additionally cap by remaining calendar slack as above. If current fixed validity has already passed, the grant is expired, not paused with a negative clock.

### 4.8 Unlimited cases and things that invalidate the bound

| Configuration/operation | Consequence |
|---|---|
| Unlimited count, finite pause duration | Repeated valid pauses can defer depletion without a finite count-derived bound |
| Finite count, unlimited pause duration | One allowed pause can hold indefinitely if no fixed expiry |
| Fixed validity exists | Access never extends beyond `E`, regardless of pause allowance |
| Unused grant has activation-relative validity and no activation cutoff | No finite wall-clock lifetime from purchase before first activation |
| Administrator/service suspension | May add non-consuming time outside user budget; do not apply `R + kP` blindly |
| Reboot grants fresh pause allowance | Would defeat the bound; this plan prohibits count reset on reboot |
| Transfer/split duplicates budget | Would defeat the bound; share/preserve pause-budget group |
| Wallet custody defers connection | The time-wallet adapter can defer actual resume; use fixed validity for lifetime, not just `R + kP` |
| New legitimate purchase | Can add its own separately documented allowance |

The maximum **user pause count** is not a maximum credit lifetime. Fixed validity and activation rules answer lifetime questions.

## 5. Worked examples and comparison with old Eco-Fi maths

### 5.1 Count and balance boundary table

Assume pause permitted, active positive credit, no suspension, validity in future, `N = 3`, enabled balance window 5–90 minutes.

| Remaining balance | Count before | New pause? | Result |
|---|---:|---|---|
| 90 min 1 sec | 0 | No | Above maximum by one second |
| Exactly 90 min | 0 | Yes | Count becomes 1 |
| Exactly 5 min | 2 | Yes | Third pause is valid; count becomes 3 |
| 4 min 59 sec | 0 | No | Below minimum by one second |
| 30 min | 3 | No | No new pause slots |
| 0 sec | 0 | No | Depleted, regardless of disabled minimum |
| 30 min, already paused | 3 | Idempotent response | Preserve original deadline/count |
| 180 min, maximum disabled | 0 | Yes | Stored 90-minute threshold is ignored |

### 5.2 Full timeline with three pauses

Grant has 120 browsing minutes, starts 08:00, validity next day 08:00, pause duration 60 minutes, count cap 3, balance bounds disabled.

| Wall time | Event | Browsing left | Count |
|---|---|---:|---:|
| 08:00 | Activate | 120 min | 0 |
| 08:20 | Pause 1 | 100 min | 1 |
| 09:20 | Auto-resume | 100 min | 1 |
| 09:40 | Pause 2 | 80 min | 2 |
| 10:40 | Auto-resume | 80 min | 2 |
| 11:00 | Pause 3 | 60 min | 3 |
| 12:00 | Auto-resume | 60 min | 3 |
| 12:10 | Fourth pause attempt denied | 50 min | 3 |
| 13:00 | Depletion | 0 | 3 |

Total browsing is 120 minutes; total pause is 180; wall duration 300. No credit was erased by a pause deadline and no browsing credit was created by a pause.

### 5.3 Deriving a cap for an operator-selected holding-time target

PisoFi's source default count is a configured integer, not calculated from a square-root formula. If an Eco-Fi operator instead asks for a maximum nominal user-pause allowance `S_target`, with fixed per-pause duration `P > 0`:

```text
N_max = floor(S_target / P)
```

Example: allow at most 150 total pause minutes with 60-minute individual pauses. At most two full pauses fit; three would allow 180. Alternatively, three pauses require `P <= floor(150 / 3) = 50` minutes each.

For a total idealized wall-lifetime target `H_target` after activation and browsing grant `R`, require `N * P <= H_target - R`. This requires nonnegative slack and the assumptions in section 4. It does not replace fixed validity for outages, unlimited activation delay, or new purchases.

These are optional configuration calculations, not formulas used by original PisoFi. Keep the actual preset at three pauses × 60 minutes unless intentionally changed.

**Zero-count trap:** if `floor(S_target / P) = 0`, the derived result means no full pause is allowed. Do not send zero to an original-compatible `max_pause_limit` field and assume it disables pause: original nonpositive values mean unlimited in the checked paths. Use an explicit pause-disabled policy or an internal finite cap of zero with a distinct limited/unlimited flag. Serialization must preserve that distinction.

### 5.4 Old Eco-Fi dynamic formula is a different policy

Old Eco-Fi computes a forfeiture delay from remaining minutes `M`:

```text
H(M) = clamp(12 + 1.2 * sqrt(M) + 0.025 * M, 24, 720) hours
```

It answers how long paused credit survives before forfeiture, not how many pauses are allowed. The PisoFi preset replaces this for **new grants** with independent `N = 3` and `P = 60 minutes`, plus calendar validity. Preserve old calculated deadlines on legacy grants rather than recomputing them during migration.

For an uncapped target `H`, let `x = sqrt(M)`. Solving the old formula gives:

```text
0.025*x^2 + 1.2*x + (12 - H) = 0
x = (-1.2 + sqrt(1.44 + 0.1*(H - 12))) / 0.05
M = x^2
```

Use the nonnegative root. Below the 24-hour floor or above the 720-hour cap there is no unique inverse. At the floor many small balances map to 24 hours; at the cap many large balances map to 720 hours. This inversion is explanatory only and must not be used to manufacture PisoFi pause counts.

The calculated floor threshold is approximately **72.184626 minutes** and the cap threshold **21,312.564725 minutes**. Substituting these into the uncapped expression produces 24 and 720 hours respectively. These values describe the old Eco-Fi function only.

## 6. Reward, validity, merge, and movement equations

### 6.1 Preserve bottle pricing independently

The current `calculate_minutes_for_bottles()` uses descending configured bottle tiers greedily. For tier `(b_i, m_i)` and residual bottles `q_i`:

```text
n_i = floor(q_i / b_i)
earned_minutes += n_i * m_i
q_(i+1) = q_i mod b_i
```

After tiers, residual bottles use the one-bottle/default rate. Snapshot rates when a deposit starts, so changing settings during a deposit cannot change the value of already accepted bottles.

Illustrative current source defaults: 1→10 minutes, 3→40, 5→75, 10→180. Eight bottles calculate as `75 + 40 = 115 minutes`; thirteen as `180 + 40 = 220 minutes`. This preserves the current greedy pricing rule; it is not an optimization algorithm selecting the highest possible reward from all combinations.

Finalize one grant per deposit transaction by default. Ten bottles in one deposit can produce a different reward and one pause budget, while ten separate deposits produce separate grants/budgets. Show that clearly; do not unintentionally mint a fresh three-pause allowance for every UART pulse in one deposit.

### 6.2 Validity rule selection

Let grant duration be `T` seconds and purchased whole minutes `M = floor(T / 60)`, matching inspected original call sites. Ordinary bottle rewards are whole minutes, so there is no fractional-minute ambiguity there.

For enabled bracket rows `(v_i, e_i)` in minutes:

```text
i* = argmin(v_i such that v_i >= M and rule is enabled)
if i* exists:
    validity_duration = 60 * e_i*
else if global validity enabled:
    validity_duration = max(60 * global_minutes, T)
else:
    validity_duration = none
```

Zero/negative bracket validity is rejected in new configuration. This avoids the original caller's zero-means-fallback ambiguity. A disabled bracket must not match.

For sub-minute admin grants, explicitly use the same whole-minute lookup or a named alternative. The preset uses the compatibility floor, but all grants still require positive exact duration. No bracket match falls back normally.

An explicit bracket can be shorter than `T`, as in the original examined rule branch. Admin preview must warn that not all browsing time can be consumed before expiry. The global fallback floor does not silently override an intentional bracket or promo.

### 6.3 Activation-relative and absolute validity

Represent validity as a mode, not an overloaded date:

| Mode | Creation | First activation | Later pause/resume |
|---|---|---|---|
| `activation_relative` | Store duration; fixed date unset | Set `E = activation_time + duration` once | Preserve `E` |
| `absolute` | Store explicit `E` | Reject if already expired | Preserve `E` |
| `none` | No fixed date | No fixed date | No fixed date |
| `legacy_pause_expiry` | Import original pause deadline separately | Apply legacy transition contract | Do not reinterpret as new general validity |

New ordinary bottle/voucher time uses activation-relative validity, matching the original unused-session concept. If the product should expire before first use, also set `activate_before`. The preset does not invent an activation cutoff that was not established from original source defaults.

The first activation and assignment of `E` must be atomic and repeatable without rebasing again. Queries and APIs use the same rule; this intentionally fixes original unused-helper versus auto-continue SQL inconsistency.

### 6.4 Auto-merge disabled is the first release

With merge off, a top-up creates grant B, leaving grant A's `R`, `C`, `E`, speed, and pause state intact. Grant B is queued if A is still selected. If no live selection exists, activate B after reconciliation. Do not silently suspend a currently browsing grant to activate new credit.

This yields predictable queued time and avoids reviving expired balances. Explicit user switching is available subject to source pause eligibility. A top-up while user-paused preserves the selected pause; the user chooses whether to resume or switch through the documented flow.

### 6.5 Optional merge algorithms, implemented only with their own tests

Default off. If later enabled, store the selected algorithm and verify it with original runtime fixtures. The following are distinct source-derived formulas, not interchangeable:

| Algorithm | Existing expiry `E_old` | Incoming validity/time | Candidate resulting expiry |
|---|---|---|---|
| Examined cash extension, matching bracket | Present | Bracket duration `V` | `E_old + V` |
| Examined cash extension, no bracket, global on | Present | Incoming seconds `T`, global duration `Gv` | `E_old + max(T, Gv)` in the corresponding branch |
| Examined cash extension, no bracket/global off | Present | `T` | `E_old + T` |
| Examined cash extension, existing undated | Absent | Incoming date considered by caller | Can remain undated in original branch |
| Admin merge, both dated | Present | Future incoming `E_in` | `E_old + (E_in - now)` for validated future input |
| Admin merge, old undated/new dated | Absent | `E_in`, old balance `R_old` | `E_in + R_old` |
| Admin merge, old dated/new undated | Present | `T` | `E_old + T` |
| Model `mergeSessions()` time branch | Present | Sum of other remaining seconds `S` | `E_old + S` |

Original origin/type-specific branches can choose a new auto session instead. Do not apply the cash formula outside its conditions. Reject past incoming dates rather than using an absolute time difference to accidentally extend credit.

For a future safe merge in Eco-Fi: settle both sources; merge only eligible grants with compatible policies/ownership/speed; keep survivor's used pause budget; never reset `C` or copy unused allowance into multiple survivors. Preserve an event linking absorbed grants. If exact count-combination policy differs from original behavior, label it explicitly.

### 6.6 Wallet and transfer conservation

Use exact time units for accounting. An implementation can maintain integer microseconds internally (`1 second = 1,000,000 units`) and expose/display whole seconds; this is equivalent to seconds plus retained fractional remainder and avoids repeated rounding loss.

For a movement of `x` units:

```text
source_after = source_before - x
destination_after = destination_before + x
source_after + destination_after = source_before + destination_before
```

Keep all source validity, activation, origin, and pause-budget group metadata when splitting. A transfer of one grant into two fragments must not create two independent three-pause allowances. Both fragments reference the same original budget group; taking a counted pause on either consumes that shared group's slot.

An original unused duration may start independently on first activation of each split only if the product explicitly permits that. Default: do not split an unactivated grant into independently activating products; move it whole. Activated splits keep the same `E`.

Wallet saving is a custody movement, not an expiry reset. For a selected active grant it requires an allowed counted pause first; otherwise saving/reloading could bypass the pause limit. A selected already paused grant moves with its existing deadline. While held in a time wallet, that deadline still applies: at timeout, preserve the grant's eligibility/policy state and report `resume_due`; do not fabricate an IP or burn time on a nonexistent connection. Fixed expiry continues. This is an explicit Eco-Fi time-wallet adapter, not exact original currency-wallet behavior.

Because wallet custody can defer actual connection, `R + kP` is not a universal wall-lifetime bound for wallet-held credit. Fixed validity supplies the bound. If strict auto-consumption without a bound device is required, that is a separate continuous-time product and must not be introduced silently.

### 6.7 Transaction ledger invariant

Across all owners and escrow accounts in exact accounting units:

```text
opening_value + minted_rewards + admin_positive_adjustments + compensation
    = live_value + escrow_value + consumed_value + expired_value
      + explicit_negative_adjustments
```

Wallet/transfer moves cancel out globally. Unused pause allowance has no entry as minted value. Record expiry of remaining value explicitly before operational cleanup. Do not count the same grant in both wallet and session totals.

## 7. Proposed storage model

This is a schema specification, not executable DDL. Exact migrations and indexes must be implemented/tested later.

### 7.1 Tables and responsibilities

| Table | Required fields/concepts | Constraints and purpose |
|---|---|---|
| `credit_owners` | ID; member/device identity; creation time | Stable owner, independent of IP; link existing member account |
| `devices` | ID; normalized MAC; owner association; last-seen data | Device observation, not proof of member identity by itself |
| `time_policy_versions` | ID/version; durations; count/window settings; merge/activation modes | Immutable policy snapshot; saved settings create new versions |
| `time_grants` | ID; owner; origin; source operation; issued seconds; remaining exact units; state; validity mode/duration/date; activation date; policy; budget group; version | Positive issuance, nonnegative remainder, unique source issuance; terminal grants cannot be revived |
| `pause_budgets` | Group ID; finite cap or null; used count; version | Shared across fragments; atomic reservation |
| `grant_pauses` | Pause ID; grant; reason; began/deadline; resume due; ended; event ID | One open pause per grant; duplicate pause does not append another |
| `connections` | ID; device; owner; selected grant; desired/applied state; binding version; IP/MAC; last accounting checkpoint | One current binding/selection under defined owner policy |
| `value_operations` | Idempotency key; owner; request kind; input hash; result; state | Same key with different payload rejected; durable original result |
| `time_ledger` | Event ID; operation; debit/credit accounts or delta/reason; exact units; grant/source references; time | Explain creation, movement, consumption, expiry, adjustment |
| `transfer_claims` | Code hash/identifier; source grants; escrow owner; creation/claim deadline; claimant; state | Unique code; exactly one claim; preserved source eligibility |
| `deposit_sessions` | Durable deposit ID; owner; rate/policy snapshot; device/session identifiers; accepted count; finalized state | Exactly one finalization |
| `deposit_events` | Device boot/session ID; event sequence; accepted count/delta; raw event reference | Unique event identity prevents repeated reward |
| `network_intents` | Connection/binding version; desired access/rate; deadline; retry/error | Durable post-commit network work, replaceable by newer intent |
| `migration_runs` / mapping records | Run ID; old source keys; target IDs; input hashes; reconciliation | Resume/review migration without importing value twice |

Existing `members`, vouchers, pricing, configuration, and device policy tables remain or receive additive versioned integration. Do not delete old accounting tables until cutover reconciliation and rollback decisions are complete.

### 7.2 Storage invariants

- Grant ID and original source operation are immutable.
- Each unit of time has exactly one owner/custody location at a time.
- Only one selected time grant is billed by a connection.
- One member connection at a time is the initial policy; device login movement revokes the previous binding. Supporting simultaneous member devices is a separate explicit product decision.
- A grant cannot be simultaneously active in two connections, wallet custody, or claim escrow.
- Terminal reason is explicit: depleted, validity expired, legacy pause expired, admin forfeiture, merged, or moved/closed.
- A pause record cannot extend itself through a duplicate request.
- Activated validity cannot be rebased through login, pause, transfer, reboot, or ordinary top-up.
- `pause_budgets.used` never decreases except an explicit audited correction; fragment creation does not copy an independent budget.
- Connection bindings are versioned. Late network work cannot reauthorize an old IP after movement.
- Policy edits are prospective unless an explicit bulk adjustment with preview applies them to existing grants.

### 7.3 Indexes and operational retention

Index owner plus nonterminal state, connection selected grant, normalized device identity, due fixed deadlines, open pause deadlines, unique operation keys, unique event sequences, and outstanding network intents. Use a partial/filtered uniqueness approach supported by the chosen SQLite schema to enforce one current selection; do not depend only on application loops.

Archive/delete operational rows only after ledger and movement links remain resolvable. Expiration enforcement must run regardless of archive retention. Retain terminal event reason even if detailed network telemetry ages out.

## 8. Transition engine and transaction ordering

### 8.1 One entry point for value/state mutation

Proposed interface, illustrative only:

```text
apply_operation(owner, connection, operation_id, expected_version,
                action, payload, trusted_clock_snapshot)
    -> committed state + network intent + replayable result
```

Routes must not edit dictionaries, grant rows, or pause counts independently. The worker invokes the same state engine for due events. A process-local lock can reduce contention, but database constraints/transactions enforce correctness across retries and restarts.

### 8.2 Ordering and failure paths

1. Validate/authenticate caller and normalize request.
2. Begin a short write transaction using one connection.
3. Check operation ID/payload hash; return prior result if already committed.
4. Load grant, owner, budget, connection version, and policy.
5. Settle eligible active interval up to depletion/fixed deadline.
6. Resolve expired/depleted states before pause/resume/top-up/movement.
7. Resolve administrator/service suspension and due pause action.
8. Check action preconditions and reserve count/value atomically.
9. Write grants, budget, ledger, result, and desired network intent.
10. Commit, releasing database locks.
11. Apply latest network intent; record application result separately.
12. Return committed state and honest connection status.

No subprocess, serial exchange, DNS lookup, or slow network operation runs while holding the database write transaction. Avoid lock inversion between member lookup and global session lock. Keep network effects out of automatic database retry bodies.

### 8.3 Event precedence

| Simultaneous conditions | Decision |
|---|---|
| Fixed validity and pause timeout | Expire; no resume |
| Depletion and pause request | Terminal/depleted; no counted pause |
| Last counted pause already open | Let it continue; deny only another transition |
| Admin suspension and pause timeout | Remain suspended; retain/mark timeout due; do not grant |
| Top-up and expired old grant | Terminalize old; create new separately |
| Grant expiry and transfer claim | Expire underlying value; claim cannot resurrect it |
| Duplicate operation and changed policy | Replay original result, then show separately refreshed state |
| Late old-binding authorization and newer login | Discard stale intent by binding version |

### 8.4 Counted versus uncounted transitions

| Trigger | Count increment | Deadline behavior |
|---|---|---|
| User pause from active | +1 | New pause deadline |
| Switch away from active grant | +1 on source | New source pause; target activation separately validated |
| Duplicate/already-paused request | 0 | Preserve original deadline |
| Manual resume | 0 | Close pause without rebasing validity |
| Automatic resume | 0 | Close due pause once access activates |
| Disconnect pause when enabled | +1, capped and checked by normal eligibility | Align with original counting concept; deny pause when no allowance |
| Administrator suspension | 0 | Separate suspension; no fresh user allowance |
| Boot/service interruption | 0 | Preserve original budget and user deadline |
| Wallet save from active | +1 through allowed source pause | Preserve fixed validity; custody adapter in section 6 |
| Transfer of active selected credit | Require permitted source pause or explicit atomic partial-value operation | Never reset/duplicate group budget |

If disconnect auto-pause is enabled but count/window disallows it, keep the time product logically active and consuming under that policy. Explain this in the portal. Do not silently grant an unlimited non-consuming disconnect pause.

### 8.5 Session switching and auto-continue

User switch is atomic: settle source; verify source may be paused if still active; reserve source count; pause source; validate/activate target; change selection; commit one network intent. If any target check fails, source remains unchanged. Do not spend a pause count on a failed switch.

Auto-continue occurs after a selected grant terminalizes, so it does not require another source pause slot. Select the earliest-created eligible grant under the preset. Exclude expired, held-in-wallet, transfer-escrow, administrator-suspended, and explicitly user-paused grants that are not due/authorized to resume. This is a deliberate clarification of original candidate selection.

Do not expose a moment when both old and new grants are billed or two IP bindings have live authorization. Old binding is revoked/replaced; new activation is reconciled with its own speed and deadline.

### 8.6 Reconcile policy or balance changes during a pause

Ordinary settings are prospective, so most changes do not affect existing grants. If an administrator explicitly applies a new policy or adjusts a paused grant, use the transition engine with a preview:

| Change affecting existing paused grant | Required outcome |
|---|---|
| Fixed validity is now reached | Expire before considering resume |
| New minimum/maximum excludes balance | Ordinary user/disconnect pause becomes resume due, as in the original kicker; administrator suspension still wins |
| Per-grant pause permission removed | Ordinary pause becomes resume due; do not erase time |
| Pause duration shortened | Recompute `D_p = original_pause_start + new_P`, not `now + new_P`; process if already due |
| Pause duration explicitly lengthened | Recompute from original start and record extension; preserve fixed validity |
| Count reduced below used count | Deny future new pauses; preserve already accepted pause under this migration contract |
| Count increased | More future slots may become available; used count is not reset |
| Balance adjusted under admin suspension | Apply value correction; retain suspension |
| Optional merge into paused grant | Follow named merge/reconciliation; no implicit count/deadline reset |

Preserving the current pause after lowering a cap differs deliberately from the original broad `C > N` reconnect predicate. Record the difference; that predicate could override unrelated state. Immediately ending an accepted pause is a separate explicit administrator action.

Global feature removal for new grants does not mutate existing grants. Applying it to existing grants requires enumerating and reconciling those grants, not changing a flag that different workers interpret differently.

### 8.7 Accounting does not prove packet delivery

Exact-unit balances and transaction invariants ensure internal accounting consistency. They do not prove every billed second carried traffic. This product sells enabled access time. Browser inactivity is not a refund trigger; host/network authorization failure follows the service-recovery policy. Use applied state and lease history to bound uncertainty, not to claim packet-by-packet delivery accounting.

## 9. Worker, network, clock, and recovery

### 9.1 Exact elapsed accounting

Use integer exact accounting units internally, such as microseconds, while APIs display remaining whole seconds and pricing mints whole seconds. For a healthy uninterrupted active interval:

```text
delta_us = max(0, monotonic_now_us - last_settled_monotonic_us)
debit_us = min(remaining_us, eligible_active_interval_us)
remaining_us_after = remaining_us_before - debit_us
```

`eligible_active_interval_us` is the part of `delta_us` before expiry/depletion and within active applied service. Paused/pending-activation intervals contribute zero. Persist balance/checkpoint together and advance the shared settlement checkpoint even when the worker and a request both ask for current state, so the interval is not billed twice.

Example: ten 1.4-second active intervals total 14 seconds. Rounding each to one would bill 10; rounding each upward would bill 20. Exact accounting records 14. A 125.5-second remainder stays 125.5 internally even if displayed as 125; moving all credit preserves the half second.

Do not clamp every long delay to one second as current Eco-Fi does. Distinguish an active service interval from an actual outage, using worker/connection health and bounded lease history. Do not claim historical packet delivery can be reconstructed exactly from a wall-clock gap.

### 9.2 Scheduling

Recommended implementation targets, to measure during testing:

- One second or less between normal deadline scans on the target hardware.
- Renew paid leases approximately every ten seconds, only from settled healthy state.
- Lease ceiling remains 30 seconds, additionally capped by remaining time and fixed validity.
- Durable accounting checkpoint target: at most five seconds during active use, plus every value movement/state change.
- Heartbeat records last successful settlement/deadline pass, not just loop entry.
- Network intent retry uses bounded backoff and skips stale versions.

Use monotonic elapsed scheduling, not `tick % N` as the only timer. Query due deadlines via indexes. A wake-up signal after purchase/pause/movement avoids waiting for the next periodic scan.

Five-second checkpoints are a proposed reliability/performance tradeoff requiring SD-write measurement. State transitions and credit movements still commit synchronously. Do not issue a durable event row for every microsecond; batch accounting intervals without losing the arithmetic.

### 9.3 Crash uncertainty must be explicit

A database checkpoint and a network packet cannot be committed atomically. With checkpoint interval `Q` and lease ceiling `L_net`, an abrupt crash can leave an uncertain service interval influenced by both, plus processing delay. Do not promise exactly-once physical service from exactly-once ledger operations.

Selected customer-preserving recovery: resume from last durable eligible balance; do not charge powered-off time; record crash-recovery uncertainty/compensation policy. Target the uncertain window with checkpoint/lease limits and fault tests. Fixed validity still applies once trusted time is available.

Kernel rules can outlive a killed application until leases expire. On process restart, revoke/rebuild from current committed state before allowing renewal. A request must not extend access while the accounting heartbeat is stale.

### 9.4 Desired versus applied network state

Represent at least `pending_enable`, `enabled`, `pending_disable`, `disabled`, and `error` separately from grant lifecycle. A pause intent becomes durable once count/state are reserved; actual network revocation is then reconciled. Return pending/error honestly if revocation fails. Retry the same intent without another count increment.

A residual old lease may permit a bounded interval until revoke/timeout; disclose and measure this rather than claiming instant atomic disconnect. Never grant new access merely to compensate for a failed old revoke. If an operator cancels a failed pause, any count correction is an explicit idempotent event.

Activation begins billing after successful enable reconciliation. A crash between kernel enable and application acknowledgement is part of the bounded uncertainty above. Binding versions prevent stale asynchronous work from restoring a previous device's access.

### 9.5 Clock trust

Store UTC epoch deadlines; display Asia/Taipei initially, with configurable display timezone. Changing display timezone must not shift persisted UTC dates.

During a process lifetime, monotonic time measures consumption. After reboot, a persisted monotonic checkpoint is invalid unless its boot identity matches. Obtain trusted UTC before destructive fixed-expiry decisions. Detect implausible dates/backward jumps against the last durable trusted reference.

In `awaiting_clock_trust`, preserve value and report the state; do not grant potentially expired paid products until reconciliation. Network-independent administration/recovery may remain available. Fixed validity is not extended merely because time synchronization took time; operator compensation is separate.

If clock steps forward, settle already delivered active time using monotonic history and terminalize grants whose trusted fixed deadline passed. If it steps backward, do not re-open terminal grants or rebase activated dates. Test both around an active pause deadline.

### 9.6 Reboot rules

| Before shutdown/crash | After startup reconciliation |
|---|---|
| Active, valid, positive | Rebind known eligible device; resume when network/clock ready; no outage debit |
| User paused, deadline future | Restore pause/count/deadline unchanged |
| User paused, resume deadline passed, validity future | Mark resume due; activate when eligible device/service available; no retroactive outage debit |
| Fixed validity passed | Terminalize with reason and revoke; do not wait for archive cleanup |
| Administrator suspended | Preserve suspension |
| Pending deposit | Reconcile accepted event IDs and finalize once when allowed |
| Transfer escrow | Preserve escrow and claim/underlying deadlines |
| Unbound saved credit | Keep under durable owner, without invented IP |

## 10. Application integration map

Proposed modules organize future work; no files are created by this plan other than documentation. Names can be adjusted during implementation if the responsibilities remain clear.

| Module/responsibility | Current code to migrate | Result |
|---|---|---|
| Time-policy evaluator | Pause formula/helpers and settings reads in `host/portal.py` | Pure eligibility/deadline/validity calculations |
| Credit repository and migrations | `ensure_session_schema`, save/restore and independent SQL | One durable grant/ledger model |
| Session transition service | Route-local dictionary mutations and `resume_session` | Central transactions and replayable operations |
| Billing/deadline worker | `time_daemon` | Monotonic settlement, due events, heartbeat |
| Network reconciler | `sync_client_firewall` and gateway integration | Versioned desired/applied access, short leases |
| Deposit accounting | UART handler, simulator counters, completion | Durable accepted events and one finalization |
| Member/transfer adapter | Login/save/use/generate/claim | Stable ownership and policy-preserving movement |
| Portal view model | `api_vendo_status` and embedded template data | Clear multiple-grant status and countdowns |

### 10.1 API contracts

Preserve existing route URLs initially where possible. Routes become adapters, not alternative accounting engines. Each mutating request uses a client operation ID plus authenticated owner/session context; support a server-generated ID for legacy clients only with an explicitly limited retry contract.

| Endpoint | Proposed behavior |
|---|---|
| `/api/vendo/open_gate` and alias | Reserve a deposit session/owner and snapshot pricing/policy; repeated same operation returns existing session |
| `/api/vendo/done` | Finalize identified deposit once; return new grant ID, exact earned time, activation/queue status |
| `/api/client/pause` | Explicit `pause`/`resume`; reject unknown actions; expected grant/version; count/deadline rules above |
| New session-select endpoint | Atomic source pause/target activation; no failed-switch count loss |
| `/api/voucher/redeem` | Consume voucher and create owned grant in one transaction; preserve voucher product policy |
| `/api/transfer/generate` | Validate source, settle, reserve/move eligible value into escrow once |
| `/api/transfer/claim` | Resolve source/claim expiry; claim once; retain origin/activation/pause budget |
| `/api/member/login` | Authenticate and rebind owned credit; no IP-only balance lookup/merge |
| `/api/member/save_time` | Explicit selected grant/all-eligible selection; preserve exact value/policy and pause rules |
| `/api/member/use_wallet` | Activate/move eligible held credit; not mint a fresh unqualified balance |
| Admin add/edit | Create adjustment/new grant or explicit audited correction; do not revive a terminal grant |
| Admin pause/resume | Separate suspension/release; release rechecks expiry and due pause before access |
| Admin kick | Define disconnect versus forfeit as separate actions; no ambiguous delete-all-credit button |

### 10.2 Response shape

Illustrative field specification, not an implemented JSON response:

```text
operation_id, replayed, state_version, server_time_utc, clock_status
selected_grant:
    id, origin, remaining_seconds, state, activated_at, valid_until
    pause_reason, pause_started_at, pause_deadline, pause_timeout_action
    pause_count_used, pause_count_max, pauses_left
    can_pause, pause_denial_reason, can_resume, resume_denial_reason
    min_pause_balance_seconds, max_pause_balance_seconds
    download_rate, upload_rate
connection:
    desired_state, applied_state, binding_version, error
other_grants:
    id, available balance, state, validity mode/deadline, selection eligibility
```

The server decides `can_pause`; the browser does not derive permission from a rounded balance. Use precise denial reasons: `above_max_balance`, `below_min_balance`, `pause_limit_reached`, `expired`, `admin_suspended`, `clock_untrusted`, `no_selected_grant`.

### 10.3 Compatibility fields

During frontend transition, `remaining_seconds` and `client_time_remaining` may represent the selected grant only. Add separately named total-owned and queued balances. Do not silently make the old field mean all grants while the countdown bills only one.

`expires_at` must not interchange fixed validity and pause deadline. Keep it only in explicitly legacy responses or deprecate it with a clear contract. New UI consumes named deadlines. Remove the active-state hypothetical pause date from the fixed-expiry display.

## 11. Bottle device and simulator integration

### 11.1 Current evidence and missing guarantee

Current firmware emits `CREDIT_ADD`, `bottles`, and `sessionTotal`. These help count a session but do not by themselves establish a globally unique persistent event identity across device resets/reconnects. A host-only claim of guaranteed exactly-once reward would be too strong.

### 11.2 Proposed protocol extension for a later implementation

Add a host-issued deposit ID acknowledged by the device, device boot/session identity, monotonic event sequence, cumulative accepted count, and event acknowledgement/replay semantics. Host persists an event before acknowledging it; duplicate identity returns the original acknowledgement.

The device must retain/replay unacknowledged accepted events across the supported failure window or expose enough durable cumulative state to reconcile. Choose flash-safe persistence/buffering with measured wear. Resetting `sessionTotal` without an identifiable new deposit/boot must not be treated as a new reward sequence.

This is proposed future protocol work, not authorization to edit firmware during this documentation task. The simulator must implement the same event identities and retry/reset cases for meaningful host tests.

### 11.3 Deposit states

```text
reserved -> device_acknowledged -> accepting -> closing -> finalized
                                     |             |
                                     +-> recovery_required
```

Owner/rate snapshot is bound before opening the gate. Closing is idempotent. Finalization atomically marks events consumed, zeros pending accounting, creates one reward grant, and writes ledger/result. Device close acknowledgement and credit finalization are coordinated but not falsely described as one cross-device transaction.

If original hardware cannot support the required event identity/durability immediately, document the bounded loss/duplication window and hold ambiguous deposits for reconciliation. Do not guess whether a reset counter represents old accepted bottles or a new session.

### 11.4 Required cases

- Accepted event arrives twice, before and after acknowledgement.
- Host crashes after persisting acceptance but before acknowledgement.
- Host crashes after grant commit but before completion response.
- Device reboots with unacknowledged acceptance.
- API completion called twice with the same and different request IDs.
- Gate times out with zero accepted bottles versus some accepted bottles.
- Admin changes pricing while deposit is open.
- Customer IP changes during a deposit; owner remains bound to deposit ID.
- Serial disconnect occurs while event acknowledgement is pending.

## 12. Portal and administrator experience

### 12.1 Customer display

Show selected package balance/countdown, independent fixed validity, state, and pause action. Example:

```text
Browsing time: 1 h 20 min
Valid until: Sep 7, 8:00 AM
Pauses used: 2 of 3
Each pause: up to 60 minutes; then time resumes automatically
Pause available with 5–90 minutes remaining
```

While the third pause is open, show “Paused until 12:00; 0 additional pauses left.” If validity comes first, show “Credit expires at 11:45 before the pause can resume.” Do not describe the 90-minute balance ceiling as a 90-minute pause duration.

Display queued grants individually when dates/speeds differ. A selected-grant countdown should not imply all queued credit shares its deadline. Show activation-relative unused validity as “Valid for X after first activation,” not a fabricated ticking deadline.

### 12.2 Local countdown

Use server time offset and a local monotonic elapsed estimate for display; resynchronize after visibility changes and successful operations. Locally reaching zero changes the display but never performs authoritative credit deletion. Server response determines terminal/pause/connection state.

Round labels for readability but enforce boundaries with exact server units. At 90:01 left, an interface showing “90 min” must still explain why maximum-balance pause is not yet allowed.

### 12.3 Admin configuration

Group controls separately:

1. **Pause count:** limited/unlimited and maximum count.
2. **Pause duration:** timeout enabled and minutes; action automatic resume for PisoFi preset.
3. **Eligible balance:** independent minimum/maximum switches and thresholds.
4. **Product validity:** global fallback, bracket table, activation mode/cutoff.
5. **Selection/merge:** auto-continue and named merge policy.
6. **Recovery/retention:** boot behavior, clock health, worker heartbeat, archive retention.

Add a preview calculator using the same pure policy evaluator: input grant minutes, current balance/count, activation/pause time, and validity; output pause eligibility, earliest balance eligibility, next event, count allowance, and full-use slack. This calculator is an admin aid, not a different implementation of the maths.

Changing settings creates a new policy version. “Apply to existing grants” is a separate previewed bulk operation showing affected balances, active pauses, count changes, and dates. Lowering count below already-used count does not unexpectedly revoke a valid in-progress pause; future requests are denied and any exceptional intervention is explicit.

### 12.4 Diagnostics and reporting

Report last successful accounting pass, deadline lateness, oldest pending network intent, current lease budget, clock trust, unmatched device events, reconciliation totals, and grant expiry reasons. Separate customer browsing debit from operator forfeiture and outage compensation.

## 13. Data migration and cutover specification

### 13.1 Do not migrate from an unexplained live snapshot

The current application has both memory state and SQLite snapshots. A database-only backup may omit newer credit or `saved:` held balances. Before a later live migration, add/export a reviewed quiescent-state inventory through the old application while it still owns its memory. Do not assume a periodic snapshot captured every customer balance.

The actual cutover procedure must stop new deposits and value-changing requests, let an open deposit finalize or mark it for recovery, settle active usage, revoke/stop paid authorization under the maintenance policy, then export final memory plus database and hashes. Record the cutoff time. Avoid a gap where both old and new workers bill the same credit.

No live extraction or maintenance operation is performed now.

### 13.2 Inventory and classification

| Source record/state | Migration action |
|---|---|
| Live memory and snapshot agree | Import once with mapping of both source references |
| Live memory newer than snapshot | Use reconciled live export; record difference and checkpoint time |
| Database-only positive session | Reconcile owner, state, expiry and last checkpoint before import |
| `saved:MAC` memory balance | Import to durable device/owner with no routable binding |
| Member record plus session `member_username` | Verify association; do not trust `active_ip` alone |
| IP currently reused by another MAC | Keep separate owners/grants; never sum by IP |
| Unknown owner or incompatible duplicate | Quarantine for explicit reconciliation; retain source evidence |
| Overdue legacy paused session | Apply existing legacy expiry using trusted cutoff time; record amount/reason |
| Existing active session | Import remaining eligible value without invented historical validity |
| Legacy wallet minutes | Convert exactly to seconds; import as documented legacy wallet grant/value |
| Unclaimed transfer | Import eligible escrow once; preserve available source metadata; label missing provenance |
| Used voucher | Preserve consumption marker; do not mint again |
| Unused voucher | Preserve promised minutes and existing product terms; assign explicit legacy voucher policy |
| Pending bottles and credited session coexist | Reconcile deposit evidence; do not blindly add both |
| Invalid JSON/negative balance/deadline corruption | Report/quarantine rather than coercing into valid credit |

A known old double-credit path is not proof a specific user's pending count is duplicated. Do not remove credit based only on the possibility; reconcile evidence or flag the ambiguity.

### 13.3 Import idempotency

Each imported item gets a deterministic migration operation identity based on migration run and source record identity/hash. Record target owner/grant IDs and imported value. Re-running import resumes/skips verified mappings rather than minting again.

Schema-version updates, imported rows, and per-batch progress must have a recoverable transaction boundary. A failure halfway through an import must not leave the system claiming a completed migration.

### 13.4 Legacy policy details

- A paused legacy grant retains its original `paused_at` and `expires_at` under `legacy_pause_expiry`, including forfeiture meaning.
- Do not assign an artificial original pause count of three used or zero used and then retroactively apply new restrictions. Legacy policy has explicitly imported/unknown count semantics until converted.
- An active old session with no fixed validity retains that fact. Giving it a 24-hour deadline at cutover is a new policy, not a neutral migration.
- Existing whole-minute wallet value converts to seconds without loss; historical discarded remainders cannot be reconstructed without evidence.
- Legacy transfers with missing source dates remain under documented legacy rules, not fabricated original expiry.
- When a legacy grant depletes or expires, its replacement purchases naturally use the new preset. Optional voluntary conversion must preview exact value and rule changes.

### 13.5 Reconciliation equations and acceptance

For each owner and globally, calculate:

```text
reconciled_legacy_live_value
    = imported_live_value + explicit_legacy_expired_value
      + quarantined_value + explicit_corrections
```

Treat quarantine as held value, not silently lost credit. Separately reconcile wallet and transfer custody without double-counting value present in both a stale session snapshot and a movement record. Pending deposits are reported separately until resolved, not presumed to be already minted browsing time.

Before activation require:

- All deterministic source records mapped exactly once.
- No unexplained per-owner/global value delta.
- No duplicate live binding or grant owner.
- No terminal grant eligible for authorization.
- Every active pause has a coherent reason and appropriate deadline/policy.
- Legacy and new policies are visibly distinguishable.
- Recovery/quarantine report has actionable identifiers and amounts.

### 13.6 Cutover sequence

1. Complete isolated schema/engine/network tests and prepare compatible release artifacts.
2. Take pre-maintenance backups and record versions/configuration.
3. Enter documented maintenance; stop new value movement/deposits.
4. Finalize or safely hold open deposits; capture quiescent old state.
5. Stop old accounting writer and revoke/reconcile old paid leases.
6. Import to new schema, preserving source backups; run reconciliation.
7. Start new engine with customer authorization disabled; validate clock and desired state.
8. Reconcile device bindings and network rules from committed grants.
9. Test a controlled client for pause, deadline, depletion, and restart before reopening normal use.
10. Reopen new deposits/operations with new preset; monitor counters and error thresholds.
11. Retain old data/release until rollback and retention gates are satisfied.

This sequence is future operational work. A later deployment task must consider actual machine access and existing user authorization; this document is not evidence that cutover occurred.

### 13.7 Rollback without destroying new value

Before reopening customers, rollback can restore the backed-up old state after accounting for any controlled test transactions. After new production grants/movements have occurred, restoring an old database would lose those events.

Post-reopen rollback requires either a tested reverse migration preserving all new value/policies or keeping the new ledger with a compatible application version while fixing the failed component. Default operational response is maintenance and forward repair of the new ledger, not blindly replacing it with an old backup.

Define a point of no simple restore at reopening, keep all operation/ledger records, and test rollback with post-cutover purchases and transfers. Exporting only aggregate minutes cannot safely represent multiple different validity policies in old code.

## 14. Implementation batches and completion gates

Each batch below is a bounded future change with specific evidence required. Do not implement all routes through parallel ad hoc patches before the accounting contract exists.

| Batch | Deliverables | Dependencies | Gate before next batch |
|---|---|---|---|
| M0: Baseline fixtures | Isolated old-state examples, audited settings mapping, policy contract | This plan | Fixtures cover active/paused/expired/member/pending/IP reuse |
| M1: Pure maths | Validity lookup, pause gates, count/deadline/slack evaluator | M0 | Boundary/property tests and all worked examples pass |
| M2: Schema/repository | Additive migrations, owner/grants/budgets/operations/ledger | M1 | Constraints, transactions, migration resume and conservation tests pass |
| M3: Transition engine | Pause/resume/activate/switch/terminalize/suspend; idempotency | M2 | Race, duplicate, expiry-order and count-budget tests pass |
| M4: Accounting worker | Monotonic settlement, deadline scans, checkpoints, heartbeat | M3 | No double debit, long-stall and clock/reset tests pass |
| M5: Network reconciler | Desired/applied state, binding versions, capped leases | M3–M4 | Live controlled packet/depletion/expiry/failure tests pass |
| M6: Reward finalization | Deposit identity/events, one-time reward, simulator/protocol integration | M2–M3 | Retry/crash/reset accepted-event matrix passes |
| M7: Value adapters | Voucher, member, wallet, transfers, admin operations | M3–M6 | Atomic movement/ownership/validity tests pass |
| M8: Portal/admin UI | Named clocks, grants, count/window messages, settings preview | M1, M3, M7 | UI agrees with authoritative evaluator at boundaries |
| M9: Migration tooling | Quiescent export, import mapping, reconciliation, rollback | M2, M6–M8 | Full realistic copy migration and recovery rehearsal passes |
| M10: Controlled release | Service packaging, cutover runbook, instrumentation | All prior | Small controlled client run, reboot and fault checks accepted |

M6 may require a focused ESP32 protocol change for event identity/durability. It must avoid unrelated sensor, classification, servo, or vending state-machine changes. The host and simulator should share a versioned contract, with explicit behavior for older firmware.

### 14.1 Required implementation artifacts

- Versioned policy configuration and validation documentation.
- Schema migrations with forward/recovery tests and supported downgrade policy.
- A single transactional transition service and exact value ledger.
- Worker and network reconciliation integration.
- Deposit event protocol specification and simulator fixtures.
- Route compatibility notes and customer/admin interface changes.
- Migration inventory/reconciliation report format.
- Runbook covering clock failure, frozen worker, pending network intent, ambiguous deposit, and recovery.
- Acceptance report distinguishing source-unit, API/database, and actual packet/hardware tests.

### 14.2 Performance/reliability gates

Measure on the actual intended Orange Pi/storage/network combination:

- Deadline lateness under realistic client count and slow operations.
- Database lock contention during simultaneous claims, deposits, and accounting.
- SD write volume with proposed checkpoints and ledger batching.
- Worker death while web requests continue.
- Revocation of already-established traffic at expiry/depletion.
- Memory growth and cleanup of terminal sessions/history queries.
- Cold boot without internet/time trust and later synchronization.

No specific client capacity or timing bound is asserted until measured. Adjust batching/scheduling without changing visible count, validity, or conservation rules.

## 15. Test specification and mathematical assertions

### 15.1 Pure pause predicate cases

Use exact integer time units; exhaustively vary finite small values of `R`, `L`, `U`, `C`, `N`, state, permissions, and expiry relation. Required properties:

```text
can_pause => R > 0
can_pause => valid and active and globally/per-grant permitted
can_pause with finite N => C < N
successful new pause => C_after = C_before + 1
duplicate pause => C_after = C_before and D_after = D_before
finite budget => no more than N counted successful pauses
```

Increasing `C` cannot make a denied count gate become allowed. Enabling a tighter maximum/minimum cannot expand balance eligibility. Disabling a balance bound removes only that gate, not count/expiry/admin restrictions.

### 15.2 Arithmetic fixtures

| Test | Inputs | Expected |
|---|---|---|
| Third pause | `N=3, C=2`, eligible active grant | Allowed; `C=3`; not immediately auto-resumed |
| Fourth pause | `N=3, C=3`, active | Denied |
| Duplicate third pause | Already paused, `C=3` | Same pause/deadline |
| Maximum equality | `R=5400, U=5400` | Balance gate passes |
| One second above | `R=5401, U=5400` | Denied; one active second until window |
| Minimum equality | `R=300, L=300` | Balance gate passes |
| One below minimum | `R=299, L=300` | Denied; further consumption cannot fix |
| Maximum off | `R=10800`, stored maximum 5400 disabled | Maximum gate passes |
| Eligibility interval | `R0=7200,L=300,U=5400` | `1800 <= active_consumption <= 6900` |
| Timeout | Pause 10:00:30, 60 minutes | Due 11:00:30 |
| Earlier fixed date | Pause 10:00, timeout 11:00, fixed 10:45 | Expire 10:45; no 11:00 revival |
| Nominal budget | `N=3,C=0,P=3600` | 10,800 seconds hold bound |
| Full-use slack | `W=14400,R=7200,N=3,P=3600` | At most 7,200 pause seconds while spending all |
| Impossible full use | `W=3600,R=7200` | Cannot spend all even with zero pause |
| Current last pause | `N=C=3`, 1,200 seconds hold left | Current pause valid; zero new pauses |
| Old formula inverse floor | `H=24` | Approximately 72.184626 minutes threshold |
| Bottle reward | Default source tiers, 8 bottles | 115 minutes |
| Bottle reward | Default source tiers, 13 bottles | 220 minutes |
| Global fallback | 30 hours browsing, global 24 hours | 30-hour relative validity |
| Value transfer | 125.5 sec, move all | 125.5 sec retained, not 125 or 120 |

### 15.3 Transaction/concurrency fixtures

- Two simultaneous last-slot pause requests: exactly one new transition/count.
- Duplicate pause/resume after response loss: one result; no refreshed deadline.
- Pause and expiry in the same scheduling interval: expiry wins at the boundary.
- User switch target fails validation: no source count debit or unintended disconnect.
- Two simultaneous wallet withdrawals/transfer claims: no negative source or duplicate credit.
- Claim racing source validity expiry: serialize to an eligible claim or explicit expiry, never both value outcomes.
- Split/transfer fragments: shared pause budget cannot be multiplied.
- Two member logins race: one current binding policy, stale intent rejected.
- Same idempotency key with different payload: reject without state/value change.
- Process exception injected before/after each transaction commit: conservation and replay remain valid.
- Admin pause/release racing top-up: no unintended authorization.

### 15.4 Worker/network fixtures

- Fractional and long elapsed intervals settle once across worker plus request.
- Kill billing thread while web requests continue; no stale-balance lease renewal.
- Kill process just before/after checkpoint/enable acknowledgement; quantify uncertainty.
- Old TCP/UDP traffic stops at the configured network bound after pause, depletion, and date expiry.
- Expiry enforced with cleanup disabled.
- New IP/MAC binding cannot be authorized by a delayed old intent.
- Network enable fails: preserve browsing balance and report pending/error; no false success.
- Network revoke fails: no new pause count on retry; old lease expires; report lateness.
- Clock forward/backward steps do not rebase activated grants or revive terminal grants.

### 15.5 Migration/UI/hardware fixtures

- Import memory-only held credit, database-only credit, and reused IP without wrong-owner merge.
- Repeated/restarted migration imports no duplicate value.
- Legacy pause expiry remains legacy; new credit uses 60-minute automatic resume.
- Post-cutover purchase followed by rollback rehearsal preserves new value.
- Completion/reboot does not reward pending bottles twice.
- Duplicate/replayed/rebooted device events follow protocol guarantees or enter explicit recovery.
- Last allowed in-progress pause displays zero additional pauses without implying it has ended.
- Disabled 90-minute maximum does not block a 180-minute grant.
- Rounded UI balance does not change exact backend min/max decisions.
- Wallet custody and transfer display original fixed validity and shared pause allowance honestly.

### 15.6 Definition of done

Implementation is complete only when all of the following hold:

1. New credits use the chosen versioned PisoFi-style preset and separate entitlements.
2. Pause count, duration, balance window, and calendar validity follow the documented equations and ties.
3. Every value-changing route uses the same durable transactional accounting contract.
4. Login/IP movement, wallet, transfers, and reboot preserve exact eligible value and policy.
5. Accepted deposit events finalize exactly once within the explicitly supported hardware failure model.
6. Billing-worker health and network leases are tested on the target gateway.
7. Migration reconciles all value without unexplained delta and has a tested recovery/rollback policy.
8. Portal/admin language matches actual enforced behavior.
9. Deliberate deviations from original PisoFi and remaining unsupported products are documented.
10. Validation report identifies actual measured results rather than treating this plan as execution evidence.

## 16. Evidence and plan verification

### 16.1 Source-to-design mapping

| Design fact | Original/repository anchor |
|---|---|
| Three-pause source default and balance bounds | `app/Pisofi/PortalManager.php` |
| Normal count gate `>=`, increment in user context | `ConnectionApiController::manageSession()`, `SessionManager::pauseResponse()` |
| Pause timeout reconnects | `scripts/kicker.php`, labels `GQujK`, `EY725`, `QzB2x`, `AhYlb`, `hZ2Qq` |
| Bracket validity lookup | `app/Models/TimeExpiration.php` |
| Global validity/merge defaults | `app/Pisofi/SessionOptionsManager.php` |
| Activation-relative unused validity | `ConnectionSession::expirationDate()`, `SessionManager` constructor/connect |
| Different top-up date formulas | `PisofiServerEventHandler`, `ConnectionSessionController`, `ConnectionSession::mergeSessions()` |
| Current greedy bottle reward | `host/portal.py`, `calculate_minutes_for_bottles()` |
| Current dynamic pause formula | `host/portal.py`, `calculate_pause_validity_seconds()` |
| Current device event fields | `src/main.cpp` and `host/esp32_simulator.py`, `CREDIT_ADD`/`sessionTotal` |
| Existing short paid authorization leases | `host/gateway_network.py`, `grant()` |

Original paths are inside the exact image identified and hashed in the linked audit. New schema/module names, scheduling targets, wallet custody rules, shared fragment budgets, and recovery procedures are proposed engineering choices, not claims that the original image implements them.

### 16.2 Checks performed while preparing this plan

Re-read the current audit and key source anchors. Checked arithmetic for the pause budget, fixed-validity slack, over-maximum balance waiting time, full timeline, inverse legacy formula, and eight/thirteen-bottle reward examples using isolated calculations. Checked boundary cases for a third/fourth pause, minimum/maximum balance, and finite-zero versus unlimited allowance. Enumerated 700 small reference cases to verify count-gate monotonicity and valid count increments. These verify the specification's equations, not a future implementation.

Verified local markdown links, paired code fences, UTF-8 text, and whitespace. Repository status confirms only documentation work for this follow-up; the pre-existing untracked `pisofi_routes.tar.gz` remains untouched.

No application or firmware code was edited, no database was migrated, and no live customer/network state was changed. The planned tests above remain future acceptance work.
