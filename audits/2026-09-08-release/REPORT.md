# Eco-Fi v2.2.0 â€” verified rebuild

Version 2.2.0 advances the previous v2.1 image. The Orange Pi image, ESP32 binary boot message, portal startup and backup metadata use the repository VERSION file. The earlier unverified v2.2.0 preview was moved to resources/drafts and is not the release image.

## Additional fixes before this rebuild

- Routed commands to the explicitly selected simulator or physical UART, serialized physical writes, bounded serial input and write timeouts, and rejected an unavailable device instead of reporting successful gate opening.
- Ignored receipts from the inactive backend so disabled simulation cannot issue credit and a physical device cannot inject events into a simulated session.
- Required licensed, healthy and acknowledged network access before captive probes report online.
- Removed hard-coded walled-garden bypass addresses and a lock-order inversion between policy updates and accounting; fixed a missing DNS resolver import and added coverage for configured public/private DNS results.
- Selected the actual USB LAN interface consistently for gateway enforcement and neighbor identity; DHCP options now use an explicit interface-independent tag.
- Added physical gate-open/closed telemetry and prevented a close flag from the previous session from cancelling the next intake.
- Preserved and acknowledged receipts after license loss while preventing another intake from opening.
- Fixed Windows quoting in the firmware version stamp using a generated build header.
- Fixed shell line-ending portability, removed hard-coded build paths, prevented overwriting release filenames, and added ARM runtime and filesystem gates before final image publication.

These changes supplement the accounting, license, backup, configuration and firmware fixes documented in ../2026-09-08-local/REPORT.md.

## Pre-build evidence

- 78 host regression tests passed.
- 40 gateway/license audit tests passed.
- Current source passed smoke tests under the actual image's ARM Python 3.5 runtime: Flask rendering/authentication, voucher credit, network acknowledgement, pause, duplicate receipts, deposit finalization, diagnostics and SQLite backup.
- Firmware compiled for esp32doit-devkit-v1 with the v2.2.0 stamp. RAM: 47,864 bytes (14.6%). Flash: 864,461 bytes (66.0%).
- Cppcheck, compile-time hardware-setting bounds, Node syntax checking, Python 3.5 grammar/global-name binding checks and Bash syntax checking passed.

## Flashing and compatibility

The Orange Pi image and ESP32 v2.2.0 package are paired. The image contains no customer database, machine-bound license or simulator journal. Preserve the existing installation's database and license before replacing its SD card.

The ESP32 package lists the exact PlatformIO flash offsets. An application-only update uses firmware.bin at 0x10000 with the existing partition layout. Do not erase NVS with pending bottle receipts. GPIO34 needs its physical external pull-up. Missing NIR or servo hardware blocks intake.

Linux time-dependent access waits for a synchronized clock. Perpetual activation PINs remain compatible. Legacy dated licenses require a vendor key that binds the expiry date.

No physical device was flashed or operated. Mechanical calibration, real power-loss recovery, USB hotplug/WAN failures and live client traffic must still pass acceptance testing; these software checks do not establish that no undiscovered defects remain.

## Final build and artifact verification

The complete v2.2.0 image was rebuilt after the fixes above. The packaged source passed ARM Python 3.5 runtime smoke tests and dnsmasq configuration validation. All five read-only e2fsck passes completed successfully.

Post-build verification passed the full image SHA-256, version stamps, byte-for-byte comparison of nine runtime modules and 50 static assets, service units, private-state exclusion and the embedded file manifest. The packaged nginx site passed syntax validation using the image's ARM nginx binary. Its initial read-only error-log warning is a verification mount limitation; the configuration test completed successfully.

The ESP32 application image passed esptool checksum and SHA validation, matches the current compiled binary, and contains the 2.2.0 version stamp. The source fingerprint was rechecked unchanged after the image verification.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| [Orange Pi image](../../resources/EcoFi_Opi_v2.2.0.img) | 3162022400 | `2c828d8960b252f70a201b822cccae09fe93ca177ab83b64b754f59c9c651fc2` |
| [ESP32 package](../../resources/releases/v2.2.0/EcoFi_ESP32_v2.2.0.zip) | 566316 | `d4e19a227f0731913011991e00e9a6f1588ce78af72c11f2de5d03e28db9f3e4` |

Evidence: [image build](final-image-build.log), [read-only image verification](final-image-verification.log), [ESP32 image verification](final-esp32-verification.log), and [source fingerprints](source-sha256.txt).

Software verification and release packaging are complete. Physical acceptance remains open; no device was flashed.
