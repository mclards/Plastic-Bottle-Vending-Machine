# Eco-Fi v2.2.1 review and release verification

Reviewed Gemini's workspace and live Orange Pi changes against the verified v2.2.0 image. Kept the branding, shorter voucher format, friendly errors and existing UI layout. Restored only confirmed regressions and added the requested two-decimal wallet display. The v2.2.0 artifacts remain unchanged.

## Confirmed findings and corrections

- **False clock rejection:** live systemd 232 reports synchronization through `timedatectl status`, but rejects the previous `show -p` command. The check now parses English status output with a two-second timeout, supports old and new synchronization labels, and retries an unready clock every two seconds. A successful check is cached for ten seconds. Removed the service's unconditional trust override and the workaround that trusted any year after 2020 or set a fabricated date.
- **Accounting regressions:** restored atomic, idempotent wallet deductions, balanced member deletion, immediate access reconciliation and the missing disconnect helper. The review initially reproduced duplicate deductions, unbalanced deletion and a failing disconnect endpoint.
- **UART regressions:** restored backend event fencing, physical gate status, hardware-unavailable handling, FINISH/FINISH_ACK handling and receipt preservation without reopening intake after license loss. These functions had diverged from the previously verified implementation.
- **Access regressions:** restored `/api/status`, serialized worker passes and license checks during network reconciliation and grants.
- **License compatibility:** the HWID branding change altered signature input. The new display format remains; verification and activation accept previously issued prefix-based signatures as well as new signatures. Dated licenses still bind expiry cryptographically. Pre-existing unsigned-expiry legacy licenses are not newly enabled.
- **Wallet display:** formats minutes with exactly two decimal places, including trailing zeros. Removed the login assignment that overwrote the formatted balance with a raw floating-point value. Underlying credit remains exact; this is display formatting only.
- **Build portability:** normalized shell script line endings after the first build attempt exposed CRLF commands under WSL.

## Verification evidence

Initial review: 78 host tests produced six failures and two errors; 40 gateway/license tests produced one failure and two errors. See [initial host results](gemini-host-tests.log) and [initial gateway results](gemini-gateway-tests.log).

After corrections: **121 automated tests passed** — [79 host regressions](review-fixed-host.log) and [42 gateway/license regressions](review-fixed-gateway.log). New cases cover old/new systemd status, failed probes, previously issued activation PINs and expiry tampering. The intentional injected SQLite exception in the host log belongs to a passing rollback test.

On the real Orange Pi at 10.0.0.1:

- Confirmed systemd 232, NTP synchronized, active portal/nginx/dnsmasq/timesyncd, and healthy admin diagnostics.
- Uploaded the candidate only to a temporary folder. Its actual ARM Python 3.5 runtime detected synchronization with `ECOFI_TRUST_CLOCK=0`.
- Candidate Flask rendering/authentication, voucher credit, network acknowledgement, pause, duplicate receipts, deposit finalization, diagnostics and backup passed against disposable storage. Network and mechanical operations were mocked in this isolated smoke test.
- Read-only production database checks returned integrity `ok`, zero inconsistent ledger entries and zero grant/account mismatches.

See [live baseline](gemini-live-baseline.log) and [candidate runtime/database results](review-opi-candidate.log). The baseline's rejected timedatectl command is the original defect reproduced during review.

[Wallet formatting and rendered portal/login/admin JavaScript checks](review-ui.log) passed. Runtime modules also parsed under Python 3.5 grammar. [ESP32 build](v221-firmware.log) passed: 47,864 bytes RAM (14.6%), 864,461 bytes flash (66.0%). [Esptool verification](v221-esp32-verification.log) validated the application checksum and hash.

## Deployment boundary

No production application files were replaced, no services were restarted, and no hardware was flashed. The running installation still contains Gemini's deployed version; the corrections above are in the candidate and rebuilt release. Live client traffic, mechanical operation, power-loss and reboot acceptance were not exercised by the isolated runtime test. Software verification does not establish that no undiscovered defects remain.

Use the paired release artifacts. Back up the current database and machine license before replacing the Orange Pi SD card. Preserve ESP32 NVS when receipts are pending. Flash offsets are included in the ESP32 package.

## Final artifacts — passed

The [complete image build](v221-image-build.log) passed packaged ARM runtime tests, dnsmasq validation and all five read-only e2fsck passes. [Final image verification](v221-image-verification.log) passed SHA-256, version stamps, nine runtime modules, 50 static assets, service units, customer-state exclusion, the embedded file manifest and packaged ARM nginx syntax. The nginx error-log warning is caused by the read-only verification mount; its configuration test passed.

The source fingerprints remained unchanged through final verification. ESP32 ZIP integrity, segment checksums and equality with the compiled application also passed.

| Artifact | SHA-256 |
| --- | --- |
| [Orange Pi v2.2.1 image](../../resources/EcoFi_Opi_v2.2.1.img), 3,162,022,400 bytes | `3e819792dfcecf63d84785f1e608093183aa1833d92e8c313bacbb136f1028db` |
| [ESP32 v2.2.1 package](../../resources/releases/v2.2.1/EcoFi_ESP32_v2.2.1.zip) | `74c091b07f55baaf737ac18c08644981d94e0f45a82af79bab5e61ac2e12ca2c` |

Release packaging is complete. The live production installation has not been upgraded.
