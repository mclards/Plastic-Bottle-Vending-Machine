# Local overall verification and remediation — September 8, 2026

**Result: local software checks pass after fixes. Physical system acceptance remains open.**

This run reviewed the current working tree, including substantial pre-existing edits. It did not revert those edits, deploy software, flash firmware, rebuild an OS image, send alerts, or operate the real machine. All database mutation tests used disposable databases. The September 5 live report remains historical evidence of that installation; this report does not claim those live failures have been retested.

## Confirmed fixes

| Area | Defect | Change |
|---|---|---|
| Member accounting | Deleting a member directly zeroed grants without matching ledger entries or immediate access revocation. | Settle usage, retire balances through balanced journal entries, refresh connection intents and revoke access. |
| Wallet deductions | A repeated negative admin adjustment could spend twice, consume non-wallet credit, or partially succeed with insufficient funds. | Transactional balance check, wallet-only deduction, signed operation payload and replay/conflict protection. |
| Time brackets | Flooring purchase minutes assigned fractional purchases above a ceiling to the lower bracket. | Compare the exact duration against bracket ceilings. Update a stale test to the shipped 48-hour default for 60 minutes. |
| Authentication | TESTING=1 silently logged requests in as administrator; simulator enablement ran ahead of authentication. | Remove the environment bypass, authenticate first, then enforce simulator availability. |
| Access licensing | Request-driven reconciliation could renew authorization before the worker noticed license loss. | Recheck license validity at reconciliation and the grant boundary. |
| License storage | Activation truncated the existing certificate before replacement succeeded. | Write/fsync a private temporary file and atomically replace it. |
| Dated licenses | Expiry metadata was not included in the activation key. | Bind dated expiry into the key; modifying/removing that date invalidates the certificate. Existing perpetual PINs are unchanged. |
| Clock trust | Any date after 2020 was trusted; an epoch clock could be set to an invented release timestamp. The default service bypassed synchronization. | Require a successful bounded NTP-status check on Linux, cache its result briefly, remove invented clock writes and the default trust override. |
| Captive detection | Seeded walled-garden entries let unpaid connectivity probes bypass the portal. | Remove only the known auto-seeded entries. Preserve operator entries. |
| Gateway failure recovery | Partial authorization/shaping could leave stale pair/filter state; zero credit was coerced into a lease; failed license transitions could be cached. | Track and clean partial installations, reject zero leases and the subnet broadcast, retry uncertain license transitions with grants disabled. Close unspecified LAN input services. |
| Admin disconnect/flush | Legacy routes modified projection data without changing authoritative grants or revoking access. | Route through the entitlement engine, preserving held credit for recovery and revoking access. |
| Backup/restore | Export copied a live SQLite main file without its WAL; restore did not accept its own ZIP and overwrote the live file. | Create a transactional standalone snapshot; validate ZIP/raw SQLite integrity, references and matching schema; restore through a rollback-capable SQLite transaction and reset connection checkpoints. |
| Hardware settings | Invalid delays/angles could be saved; UART configuration moved servos during a deposit. | Validate API, form and UART input; persist portal settings together; queue settings until the next completed mechanical cycle in firmware and simulator. |
| Firmware concurrency | ISR flags were volatile rather than atomic; multi-task output could split JSON/newline writes. | Atomic event flags and one UART write per complete line. |
| Hardware protocol | OPEN_GATE ignored its timeout; the runtime finish button was absent; serial lines had no explicit size bound. | Honor bounded per-command timeouts, debounce finish and retry FINISH until acknowledged after credit receipts complete, cap line buffering at 1024 bytes. |
| Firmware recovery | PCA availability was assumed, absent NIR skipped material validation, task/queue creation failures were unchecked. | Detect peripherals, block intake without required validation hardware, check task/queue allocation, defer configuration restart until a cycle completes. |

## Verification evidence

- **70/70 host tests passed**, covering exact balances, concurrent operations, pause/expiry, recovery, migration, wallet/member behavior, backups, clock trust, firmware event adapters and gateway failure injection.
- **40/40 gateway/license audit tests passed.** All 38 previous audit cases were retained and updated to seed the authoritative ledger, use protocol-v2 receipt identifiers and assert bounded leases. Two additional cases cover expiry tampering and atomic certificate replacement. The old tests' projection-dictionary mutation no longer represented real credit issuance.
- The portal, administrator, simulator and login pages rendered through Flask. Their inline JavaScript passed Node syntax checking; `host/static/time_controls.js` also passed `node --check`.
- All nine shipped host Python modules passed Python 3.5 grammar parsing. Execution here used Python 3.13; this does **not** replace testing against the ARM Python 3.5 dependencies.
- The image builder passed `bash -n`. The destructive image-build process was not executed.
- Cppcheck reported no warning/performance/portability diagnostics for project firmware with a 32-bit target model. This is source analysis, not a full model of third-party Arduino drivers.
- Configuration boundary assertions in `tools/machine_config_checks.cpp` passed compile-time evaluation using the installed ESP32 compiler.
- The declared `esp32doit-devkit-v1` environment built successfully with `-Wall -Wextra`. A full dependency rebuild exposed one pre-existing unused-variable warning inside SparkFun AS726X; no project-source warning was observed.
- Final firmware: **47,864 / 327,680 bytes RAM (14.6%)**, **864,101 / 1,310,720 bytes flash (65.9%)**. These are linker estimates, not measured runtime stack/heap peaks.

The intentional `sqlite3.OperationalError: injected` traceback in the host test log belongs to a passing rollback test.

## Compatibility and remaining acceptance work

- The finish-button protocol requires this portal and firmware together. GPIO34 requires a physical external pull-up; firmware cannot provide one.
- Intake now remains closed if the servo controller or NIR sensor is unavailable. Exercise this failure behavior and recovery on the actual wiring.
- Linux time-dependent service waits for synchronized time by default. `ECOFI_TRUST_CLOCK=1` remains an explicit operator override for a separately verified clock, rather than a shipped default.
- Perpetual licenses retain their original keys. Legacy **dated** certificates need vendor reissuance using `compute_activation_pin(hwid, tier, expiry_date)`; unsigned expiry metadata is intentionally no longer accepted. This remains the existing shared-secret license architecture, not a guarantee against an owner who can rewrite the installed program.
- Restore accepts current-schema backups up to 32 MiB and requires a fresh admin login. Different-schema backups need offline migration. Restore intentionally returns balances to the selected snapshot; it is not a merge of later customer transactions.
- Hardware deposits, sensor calibration/faults, long-running driver stalls, mechanical jams, receipt recovery after real power loss, boot/link/WAN outages, two-client traffic isolation, actual kernel firewall/shaping, stack/heap peaks and sustained load still require physical acceptance testing. No claim of live reliability or complete PisoFi feature parity is made.
- Existing `test_full_suite.py`, `test_complete_system.py`, `test.py` and live OPi scripts target running services and may change their state. They were not run against an unknown live installation; equivalent isolated tests were used where available.
- Existing unrelated whitespace warnings remain in portions of the pre-edited portal/gateway files. The changed firmware, policy, adapters and test files passed their scoped whitespace check.

## Repeat the local checks

```text
python -B -m unittest discover -s host -p "test_*.py" -q
python -B -m unittest discover -s tools -p test_gateway_audit.py -q
node --check host/static/time_controls.js
bash -n build_ecofi_img.sh
platformio run
```

Raw test/build logs and a SHA-256 manifest of the reviewed runtime sources are stored beside this report.
