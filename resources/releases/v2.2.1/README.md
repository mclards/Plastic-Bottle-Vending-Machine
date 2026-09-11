# Eco-Fi v2.2.1

- [Orange Pi image](../../EcoFi_Opi_v2.2.1.img)
- [ESP32 package](EcoFi_ESP32_v2.2.1.zip)
- [Verification report](../../../audits/2026-09-08-v2.2.1/REPORT.md)

The clock check supports the live OPi systemd version without a trust override. Restored accounting and UART safeguards retain the branding and friendly messages. Wallet balances display exactly two decimal places; stored credit keeps its full precision. Both new and previously issued HWID signature formats are accepted with the original expiry binding.

Back up the current Orange Pi database and license before reflashing. This clean image does not include customer data or an activated license. Preserve ESP32 NVS when receipts are pending. Flash offsets are in esp32/flash-layout.txt.

Candidate runtime checks ran on the live Orange Pi with disposable data. The production application was not replaced or restarted and no hardware was flashed. Physical acceptance remains necessary.
