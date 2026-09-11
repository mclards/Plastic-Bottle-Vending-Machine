# Live button investigation — deployment pending

Running release remains v2.3.2. No replacement image or application update was deployed in this investigation.

## Observed live behavior

- Reported client: 10.0.7.117. Initial API state: HELD, admin_paused true, desired/applied DISCONNECTED. The OPi kernel authorization set had zero entries; its forwarding rules blocked unauthorized traffic.
- A new HTTPS connection explicitly bound to Ethernet address 10.0.7.117 failed (HTTP 000). The same destination through Wi-Fi address 192.168.3.145 returned HTTP 200. This PC has another internet path that the OPi does not control.
- Clicked the real Admin Resume button. HTTPS explicitly bound to 10.0.7.117 then returned HTTP 200. The kernel authorization set contained this IP with a 14-second remaining lease, and forwarding ACCEPT counters increased. The administrator row changed to ACTIVE.
- When testing resumed after interruption, the client row was DISCONNECTED. A second Resume attempt could not be verified: Windows no longer listed the Ethernet adapter or 10.0.7.117, and SSH to 10.0.0.1 timed out. The button remained disabled while its request waited.
- Asked the user to reconnect Ethernet. The final live Pause/Kick cycle and deployment remain pending. No destructive customer-balance operation was used for this investigation.

## Confirmed local fixes

- Dashboard active_clients counted every positive saved balance, including kicked/paused clients. It now counts only active grants with confirmed, unexpired network authorization, healthy worker, trusted clock and valid license.
- Administrator mutations now stop waiting after 30 seconds, release disabled client buttons, and explain that the operation may have completed and must be checked before retrying. A timeout does not claim that the server rolled back the operation. Existing operation retry identities remain intact.

## Validation

93 host tests passed, including active-count behavior through Pause, Kick, Resume and worker expiry. JavaScript tests passed for all 33 administrator mutations, pending/error responses, duplicate clicks, exact edit payloads, and a hung request releasing its buttons without displaying success.

Local changes are in host/portal.py, host/test_entitlement_regressions.py and tools/verify_controls_ui.cjs. They have not yet been tested on the live OPi or included in a new image. Physical live Pause/Kick verification requires the OPi Ethernet connection to return.
