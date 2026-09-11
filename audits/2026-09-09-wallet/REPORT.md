# Member wallets: PisoFi comparison and v2.2.2 controls

Date: September 9, 2026.

## Reproduced cause and correction

The reproduction creates an existing member credit, consumes its three pauses, records two separate protocol-v2 bottle receipts on a second anonymous device, then logs into the member account there. Before correction, login selected the older account credit and displayed zero pauses. The newly earned credit still had three pauses; it was no longer selected. Database row order incorrectly decided which credit to use. See [reproduction](wallet-reproduction.log).

Login now prioritizes the receiving device's eligible current credit, falling back to the account's previous credit if necessary. Balance, expiry, policy and pause budget remain attached to each grant. Other available credits display their individual pause allowances. Existing paused-login handling remains unchanged: login does not automatically consume another pause or reset the allowance.

## Original PisoFi findings

Reference image: `resources/PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img`. Six relevant PHP source files were verified byte-for-byte against their extracted evidence using a read-only mount. See [source verification](original-source-verification.json). Original vendor services and hardware routines were not executed.

| Original behavior | Source evidence | Treatment |
| --- | --- | --- |
| Sign-in prioritizes the receiving device's existing session before the account's previous session. | `ConnectionApiController::signIn`, line 3833 onward; `$active` reconnect precedes `$userActiveSession`. | Matched for eligible device credit. |
| Pause counts belong to individual sessions. Connecting copies that session's count; user-context pause increments it. | `SessionManager::connectResponse` and `pauseResponse`, lines 232 and 373. | Preserve individual budgets and show each credit's allowance. |
| Login bookkeeping changes session status without increasing pause_count. | `ConnectionSession::pauseSessions`, line 111. | Login neither charges nor replenishes pauses. |
| Switching sessions pauses the previous active session through a user-context manager. The explicit cap check is only on the requested pause action. | `ConnectionApiController::manageSession`, line 427 onward. | Existing Eco-Fi switch eligibility remains. The original path's ability to exceed the pause cap was not copied. |
| The wallet stores money. Available coins become wallet transactions; buying Wi-Fi debits money and converts it through rates. | `addAvailableCoinsToWallet`, line 3216; `WalletController::buyWifi`, line 34. | Retain Eco-Fi's saved-minutes wallet; no currency conversion or credit migration. |
| Merging is configurable. Merge adds time to a retained session, not a sum of every session's pause allowances. | `ConnectionSession::mergeSessions`, line 138; `WalletController::buyWifi`. | Keep existing separate grants and their policies. |

This matches the confirmed login-selection behavior; it is not an exact clone of PisoFi's monetary wallet. Eco-Fi's minutes-saving and validity rules have no direct original-wallet equivalent and were not redesigned in this update.

Readable references: [ConnectionApiController](../../pisofi_inspect/analysis/readable/.cache/tmp/55/05/pfi/app/Controllers/ConnectionApiController.php.txt), [WalletController](../../pisofi_inspect/analysis/readable/.cache/tmp/55/05/pfi/app/Controllers/WalletController.php.txt), [ConnectionSession](../../pisofi_inspect/analysis/readable/.cache/tmp/55/05/pfi/app/Models/ConnectionSession.php.txt), [SessionManager](../../pisofi_inspect/analysis/readable/.cache/tmp/55/05/pfi/app/Pisofi/SessionManager.php.txt).

## Entire Member feature switch

Location: **Admin → Member Wallets → Member Wallet Availability**. `member_wallet_enabled` defaults to enabled, preserving current installations until the administrator changes it.

Disabling the switch hides the Member tab and panel, including already-open portals after their status poll. It also blocks public registration, login, saving and withdrawal inside the same database transaction as each operation. Direct API calls and stale pages cannot bypass it.

Accounts, saved balances, pause counts and active credit are preserved. Active time retains its normal countdown and expiry. Bottles, vouchers and other non-member controls continue to work. Administrators retain account management and can re-enable wallets. Re-enabling restores access to existing balances without migration or resets.

The setting requires administrator authentication and validates its input. The deployed switch remains enabled by default; it has not been automatically turned off.

## Tests

- [89 host regressions](wallet-host-tests.log) passed.
- [42 gateway/license regressions](wallet-gateway-tests.log) passed.
- [10 wallet smoke cases under ARM Python 3.5/QEMU](wallet-arm-smoke.log) passed with disposable storage and mocked hardware/network effects.
- [UI checks](wallet-ui-smoke.log) passed: rendered enabled/disabled portal and admin JavaScript; hiding an already-open Member panel; restoring availability on re-enable.
- Runtime Python 3.5 grammar and whitespace checks passed.

Coverage includes the reported two-device sequence, repeated login, paused credit, expiry fallback, failed-withdrawal rollback, idempotent withdrawal, service restoration, feature disable/enable with unchanged balances and budgets, administrator-only setting changes, and vouchers while Member is disabled. The injected SQLite traceback belongs to a passing rollback test.

## Live update

The OPi was initially unreachable, then became reachable again. Its source matched v2.2.1, diagnostics showed no ledger mismatch, and no intake session was open. The application update uses v2.2.2 with a stopped-service backup and automatic code rollback on failed startup checks. See [deployment status](deployment.json).

No customer pause counters were manually reset and no customer credit was reselected. The login fix prevents the reproduced selection error on subsequent logins; existing balances are preserved. No OS image or ESP32 was flashed, and the v2.2.1 image has not been rebuilt with these changes.

### Completed live verification

The application update completed successfully and reports **v2.2.2**. All [10 wallet smoke cases on the real OPi](live-arm-smoke.log) passed using copies of the deployed modules, disposable databases, and mocked hardware/network effects. No production wallet operations were used for these smoke tests.

[Live checks](live-verification.txt) verified every deployed file against its manifest, healthy worker/clock, active license, balanced ledger, the admin availability control and the served JavaScript. The Member feature remains enabled until the administrator changes the switch.

Backup on the OPi: `/var/backups/ecofi/20260908T230457Z-before-v2.2.2`. The prior app, customer database, license and service unit are retained there. Only the portal application/service was updated; the previously built v2.2.1 OS image and ESP32 firmware remain unchanged.
