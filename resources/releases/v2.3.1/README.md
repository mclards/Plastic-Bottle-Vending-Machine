# Eco-Fi v2.3.1

Fixes corrupted time/credit separators with encoding-safe JavaScript escapes. Removes obsolete wallet accounting helpers and archives old wallet balances during startup and legacy import, preserving their ledger history without permitting activation or withdrawal. Existing issued internet credits continue normally. All Member screens, controls and APIs remain removed.

- [Orange Pi image](../../EcoFi_Opi_v2.3.1.img)
- [ESP32 package](EcoFi_ESP32_v2.3.1.zip)
- [Verification report](../../../audits/2026-09-09-wallet-cleanup/REPORT.md)

The running OPi app is updated. No SD or ESP32 was flashed. This clean image excludes customer data and activated licenses. Preserve the current database, license and ESP32 NVS before reflashing; offsets are in esp32/flash-layout.txt.
