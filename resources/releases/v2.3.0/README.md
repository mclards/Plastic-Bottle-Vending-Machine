# Eco-Fi v2.3.0

Member wallets have been removed from the customer portal, administrator UI, public/admin APIs, settings, and Excel exports. There is no replacement funds wallet. Fresh installs do not create Member accounts or wallet tables. Existing installations retain historical credit/ledger records for compatibility; active and queued credit continue normally.

- [Orange Pi image](../../EcoFi_Opi_v2.3.0.img)
- [ESP32 package](EcoFi_ESP32_v2.3.0.zip)
- [Verification report](../../../audits/2026-09-09-member-removal/REPORT.md)

The running OPi application has been updated to v2.3.0. No SD image or ESP32 was flashed. The clean image excludes customer data and activated licenses. Back up the current database and machine license before reflashing. ESP32 flash offsets are in esp32/flash-layout.txt; preserve NVS and pending receipts.
