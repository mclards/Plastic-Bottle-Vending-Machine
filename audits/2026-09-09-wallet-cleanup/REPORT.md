# Eco-Fi v2.3.1 — Encoding fix and wallet pipeline cleanup

The date/credit separator was already mojibake in `host/static/time_controls.js`: UTF-8 text had passed through a mismatched text encoding. Both separators now use ASCII JavaScript Unicode escapes. UTF-8 is explicit in the editing/verification scripts. The complete asset is ASCII-safe, and rendered separators were checked against the expected middle dot. Decoded strings in runtime Python templates were also scanned for corruption.

Removed unused wallet total/projection helpers and the remaining wallet expiration branch. Wallet is no longer a live grant state. Startup/schema initialization retires existing WALLET grants to ARCHIVED; restoring an older database goes through the same idempotent retirement. Legacy wallet import writes ARCHIVED records, retaining the original value in the ledger rather than rebuilding a usable wallet. Archived balances cannot be switched to, queued, automatically activated, or expired away by the running credit engine. Existing internet grants, including time previously withdrawn from a wallet, retain their normal device credit behavior. Historical member owner identifiers remain only for data compatibility; there are no login, save/withdraw, account management or feature-toggle paths.

## Verification

- 80 host tests and 40 gateway/license tests passed. New regressions cover idempotent archival, intact balances, rejected archive selection, queue progression that excludes archives, and legacy imports that cannot become usable wallets.
- UI smoke passed separator rendering, no mojibake, full duration precision, and hiding unavailable credit controls. Runtime Python 3.5 syntax and decoded template strings passed checks.
- Real OPi Python 3.5 candidate smoke passed with disposable databases and mocked network/hardware: portal/admin pages, vouchers, pause, bottle receipts, network acknowledgements, diagnostics, backups, exports and removed Member paths. Both new archive tests passed on the OPi too.
- Live v2.3.1 deployment passed source hash, worker, clock, license, served JavaScript escape and removed endpoint/UI checks. The eight former Member APIs return 404. A read-only comparison against the pre-update backup found 0 WALLET rows, 2 ARCHIVED rows, 0 changed archived balances/budgets, 0 lost grants and 0 ledger mismatches; integrity is ok.
- The full image passed ARM runtime tests, dnsmasq syntax and all five read-only e2fsck passes. Final image verification passed nine runtime modules, 50 static assets, version stamps, service units, customer-state exclusion, embedded checksums and ARM nginx syntax. The nginx log warning is from the read-only verification mount.
- ESP32 build, checksum, validation hash and ZIP integrity passed. RAM: 47,864 bytes (14.6%); flash: 864,461 bytes (66.0%). Its functional code is unchanged; the release stamp matches v2.3.1.

Backup: `/var/backups/ecofi/20260909T062619Z-before-v2.3.1`. The running application is updated; no SD card or ESP32 was flashed. Physical bottle acceptance was not exercised by these isolated software tests.

## Release

- [Orange Pi image](../../resources/EcoFi_Opi_v2.3.1.img), SHA-256 `67c4056490265a9aec662675b4780e846fbd6e2a71a0b4cc49eb0cc60e06c3d1`.
- [ESP32 package](../../resources/releases/v2.3.1/EcoFi_ESP32_v2.3.1.zip), SHA-256 `f4ac0384d5c8bf0add200ae19b413be6d71d506597eb94ea8fc82f9df54a247f`.
