# Eco-Fi v2.3.0 — Member wallet removal

The user requested complete removal of the Member feature. This release removes the customer Member tab, login/registration, wallet saving and withdrawal, administrator Member navigation/forms/modals/APIs, the enable/disable setting, wallet fetch handling, engine wallet-save/withdraw operations and account rebinding, and Member entries from Excel reports. It does not introduce a monetary wallet.

Fresh databases no longer create the members table or member session column. Old configuration cannot re-enable the feature. Unknown public API requests return JSON 404 rather than redirecting to the captive page; captive browser probes retain their established behavior. Former Member API paths are absent from the route registry.

Existing active/queued grants, pause budgets, historical account identities and ledger records remain compatible. Legacy wallet balances remain archival records with no public or administrator save/withdraw path. No balance was converted, merged, reset or deleted. The full pre-update application/database backup is on the OPi at `/var/backups/ecofi/20260909T021630Z-before-v2.3.0`.

The preceding display fix is included: Other Credits is hidden while switching is unavailable; when it is available, credit durations show days, hours, minutes and seconds. Wallet removal does not change the rules for bottle/voucher credit, pauses, transfers or automatic queue progression.

## Verification

- 78 host tests and 40 gateway/license tests passed. Retired feature tests were removed and replaced with removal contracts, blocked re-enabling, fresh-schema/export checks, and continuity of legacy earned credit without wallet operations. Historical migration/accounting tests remain.
- Actual OPi ARM Python 3.5 candidate smoke passed using disposable storage and mocked hardware/network effects: portal/admin rendering, vouchers, pause, bottle receipts, network acknowledgements, diagnostics, backups, exports, and Member route/UI removal.
- Live app v2.3.0: all deployed source hashes matched; all eight removed customer/admin APIs returned 404; rendered portal/admin/static files contain no Member controls. Worker, clock and license checks passed. Database integrity was `ok`, with zero ledger/account mismatches and no old enable setting.
- Report verification caught SQLite report connections left open. The export now closes those read connections explicitly; the final host suite passed cleanly.
- UI smoke confirmed hidden unavailable credits, restored controls when usable, and complete duration formatting. Rendered JavaScript syntax passed as part of the host suite.
- Full OS build passed packaged ARM runtime smoke, dnsmasq validation and all five read-only e2fsck passes. Final image verification passed source equality for nine runtime modules and 50 static assets, embedded checksums, version stamps, service units, exclusion of customer state, and ARM nginx syntax. The nginx log warning is from the read-only verification mount.
- ESP32 build passed: RAM 47,864 bytes (14.6%), flash 864,461 bytes (66.0%). Esptool checksum and validation hash passed; package contents and ZIP integrity matched the build. ESP32 functional code is unchanged; its release stamp is v2.3.0.

## Artifacts

- [Orange Pi v2.3.0 image](../../resources/EcoFi_Opi_v2.3.0.img), SHA-256 `0a50e6b2becbc6f357ea6155ca5c4fe50b71e5c4d17f32cc91b14cdb72a76bf2`.
- [ESP32 v2.3.0 package](../../resources/releases/v2.3.0/EcoFi_ESP32_v2.3.0.zip), SHA-256 `13d700186e2f9628ff8104cbb5e41b25f8d456342d41dfaebcfe4d66360d3833`.

The running OPi application is updated. No SD image or ESP32 was flashed, and mechanical/bottle acceptance was not exercised by the isolated software smoke tests.
