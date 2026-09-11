# v2.3.2 — client controls and complete manual-credit removal

The running OPi is updated to v2.3.2. The final image and matching ESP32 package are rebuilt and verified.

## Changes

- Removed the entire Other Credits / Use Credit panel, its `/api/client/switch` route and the engine SWITCH operation. Valid queued credits still activate automatically after the current credit ends. Removing the selector does not erase issued time. Member login, wallet APIs, wallet administration and exports remain removed; old wallet balances remain archival only.
- Kick now holds the selected credit and blocks access until Admin Resume. A customer Resume, worker pass, restart or whitelist renewal cannot bypass the administrative hold. Credit and pause budgets are preserved; the client row shows DISCONNECTED and offers Resume.
- Admin Resume also resumes a customer-paused or held credit. +15m/+60m extend the selected credit without creating an extra queued credit or resetting its pause budget.
- Client actions require a real target. A missing target cannot silently act on the administrator's device. Failed firewall revocations remain pending and are retried; the interface reports pending confirmation instead of a successful disconnect.
- Edit shows two decimal places and only submits a time correction when the administrator changes the time field. Bandwidth-only edits preserve exact fractional credit. Invalid edits roll back atomically.
- Added response validation for 33 administrator POST call sites, plus duplicate-click protection for client actions. Rejected requests cannot display a false success message. MAC-rule renames use a single transaction and reject destination conflicts without losing the original rule.
- Adaptive bandwidth and the advertised gaming bandwidth allocation were incomplete. Their panels now clearly say unavailable, and attempts to enable them are rejected. Fixed bandwidth controls remain available. No new bandwidth implementation was introduced.

## Verification

- 91 host tests and 40 tool tests passed, plus one additional administrator API-route contract test (132 total). The regressions cover Kick persistence, denied customer Resume, whitelist suspension, failed-revoke retry, pause budget preservation, fractional edits, rollback, MAC rename conflicts and rejected bandwidth settings.
- JavaScript/template checks passed: syntax, all 33 mutation call sites, tab IDs, removal of manual credit controls, separator encoding, HTTP/JSON/network errors, pending status, duplicate Kick clicks and exact edit payloads.
- Candidate smoke passed on the actual OPi ARM/Python 3.5 runtime using disposable databases and mocked hardware/network operations. It exercised vouchers, pause, bottle receipt deduplication, +15m, Kick/Resume, backups, exports, diagnostics and removed routes. Actual clock synchronization was checked separately without a trust override.
- Live source hashes match the release. Served pages and JavaScript contain no Member or Use Credit controls; retired API routes return 404. Worker, clock and license are healthy. Read-only checks found database integrity OK, zero lost grants, zero changed archived balances and zero ledger mismatches.
- Browser verification: refreshed portal/admin, correct DISCONNECTED/Resume row, Edit displays 172.45 minutes, Cancel closes without saving, and fixed/adaptive/gaming tabs navigate correctly. No customer credit was modified for these checks.
- Image checks passed: ARM runtime smoke, dnsmasq syntax, all five filesystem checks, SHA-256, nine runtime modules, 50 static assets, version stamps, service units, customer/private-state exclusion, embedded manifest and ARM nginx syntax. The nginx log warning results from the read-only verification mount.
- ESP32 build and image checksum/validation hash passed. ZIP entries match the packaged binaries. RAM 47,864 bytes (14.6%); flash 864,461 bytes (66.0%). Functional ESP32 code is unchanged; its release stamp is v2.3.2.

Backup: `/var/backups/ecofi/20260909T231222Z-before-v2.3.2`.

These checks do not certify physical bottle acceptance, every external integration or a newly flashed board. No customer was kicked for testing, no reboot/shutdown was triggered, and no SD card or ESP32 was flashed. The live client already had a held credit from the earlier Kick; its accurate DISCONNECTED status now replaces the misleading ACTIVE display.

## Artifacts

- [Orange Pi image](../../resources/EcoFi_Opi_v2.3.2.img), SHA-256 `cc38dbc04ec2e38ce06c58388a098fea0c82f23ded4b5db4cd0f97233aa6e327`.
- [Matching ESP32 package](../../resources/releases/v2.3.2/EcoFi_ESP32_v2.3.2.zip), SHA-256 `f32310195faf4730581213830c61b0f510250c7f844bfb1c05cb979c83969bf1`.
