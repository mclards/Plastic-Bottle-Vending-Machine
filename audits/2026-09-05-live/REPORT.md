# OPi live verification — FAILED

Date: September 5, 2026 (workstation time, Asia/Taipei).

**The current installation does not satisfy the requested licensing, per-client access, timing, and PisoFi compatibility requirements.** Bandwidth shaping works in the measured cases, but several paths grant unauthorized access or lose/duplicate credit. The historical August 17 “all findings resolved” report is not a valid sign-off for this installation.

This was a verification/debugging run. No application fixes were deployed, no image was flashed, and no production source files were changed. The findings below remain open.

## What was actually tested

* SSH access to the real OPi using credentials documented in the workspace.
* OPi `eth0 = 192.168.23.69/24`, default gateway `192.168.23.1`; `eth1 = 10.0.0.1/19`.
* Physical Ethernet from the PC (`10.0.7.117`, MAC `00:00:00:00:02:43`) to OPi USB-LAN. Windows confirmed DHCP from `10.0.0.1`, `/19`, gateway/DNS `10.0.0.1`, and a 72-hour lease.
* External HTTPS sockets explicitly bound to `10.0.7.117`. The PC's separate Wi-Fi/Tailscale connectivity therefore did not establish the test results.
* Download/upload traffic across the physical USB-LAN and IFB shaper, using a temporary traffic server on the OPi. These measurements test LAN shaping, not ISP capacity.
* A brief live missing-license interval, with restoration performed by the remote process even if the test connection closes.
* **38 isolated regression tests: 17 passed, 21 failed.** Tests used a temporary copy of the same application source and a disposable database. They did not create vouchers, members, or simulated bottle deposits on the production OPi.
* Read-only inspection of the original PisoFi image and the saved ECO-Fi image. The original was not booted and its cloud services were not contacted.

The supplied `D:\PROJECTS\_IO\...\resources\PisoFi\_Opi1&PC\_v5.3.0-05-10-26\_EXT.img` path was absent. The original image available and inspected in this workspace was `resources/PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img` (3,162,022,400 bytes).

## Live results

| Check | Result | Evidence |
|---|---|---|
| DHCP, portal, SSH, WAN route | PASS at inspection | Correct client subnet/gateway/DNS; portal/nginx/dnsmasq active |
| Zero credit blocks a new external HTTPS connection | PASS | Connection timed out through the bound LAN address |
| Positive credit permits external HTTPS by public IP | PASS | HTTP 200 from `https://1.1.1.1/cdn-cgi/trace` |
| Paid client resolves a normal website | **FAIL** | `example.com` resolves to `10.0.0.1` |
| Paid captive probe reports online | **FAIL** | Nginx `/generate_204` returns 302 to the portal; backend returns 204 |
| Active countdown | PASS under light load | 119 seconds → 116 seconds over approximately 3 seconds |
| Manual pause freezes credit | PASS | 116 seconds remained 116 |
| Pause blocks new connections | PASS | New external HTTPS timed out |
| Pause terminates existing access | **FAIL** | An existing HTTPS connection still returned HTTP 200 |
| Resume restores access | PASS | New external HTTPS returned HTTP 200 |
| One-minute session reaches zero | PASS under light load | Zero observed after 61.44 seconds with 2-second polling |
| Expiry blocks new connections | PASS | New external HTTPS timed out |
| Expiry terminates existing access | **FAIL** | Existing HTTPS still returned HTTP 200 after balance reached zero |
| Missing license detected | PASS | Live admin API returned `UNLICENSED` |
| Missing license prevents newly granted internet | **FAIL** | New credit and an external HTTPS request succeeded while unlicensed |
| Original license restored | PASS | Original `ACTIVATED`, `COMMERCIAL`, `PERPETUAL` status restored |
| Admin/simulator API authentication | PASS | Unauthenticated requests returned 401 |
| Client identity resists forged forwarding headers | **FAIL** | Same physical PC changed from 60 seconds to another IP's zero-credit session using `X-Forwarded-For` |
| New MAC block stops the selected client | **FAIL** | Adding the test PC's MAC to `block` still allowed a new external HTTPS request |

Raw results: [traffic](live-traffic-tests.json), [expiry](live-expiry-tests.json), [licensing](live-license-tests.json), [DNS/identity/MAC](live-policy-tests.json), [baseline](live-baseline.txt).

### Measured bandwidth

| Configured limit, Kbps | Measured payload rate, Kbps | Result |
|---|---:|---|
| Download 3072 | 2719.4 | PASS |
| Upload 1536 | 1480.1 | PASS |
| Download 1024 | 985.3 | PASS |
| Upload 512 | 495.8 | PASS |

The probe used 2 MiB downloads and 512 KiB uploads. Payload rates include connection overhead. Acceptance for these bounded checks was 65–115% of the configured rate. This does not establish multi-client fairness, sustained maximum capacity, or behavior under congestion. See [raw bandwidth measurements](live-bandwidth-tests.json).

## Open defects

| ID | Priority | Finding and consequence | Location / proof |
|---|---|---|---|
| LIVE-01 | Critical | License validity is informational. Unlicensed clients can receive credit and internet; gate opening and voucher redemption also ignore license state. | `host/portal.py`: `sync_client_firewall`, `api_open_gate`, `api_voucher_redeem`; live missing-license test and three isolated regressions |
| LIVE-02 | Critical | Blanket `ESTABLISHED,RELATED` forwarding keeps existing connections alive after pause/expiry. Removing a client's shaping class also removes that flow's per-client limit while the connection remains permitted. Continued access is live-proven; post-expiry throughput was not measured. | `setup_firewall`, `update_firewall`; two live connection tests |
| LIVE-03 | Critical | Arbitrary forwarding headers select another client's session. Nginx appends forwarded headers, while `get_client_ip()` trusts the first user-supplied address. The Flask backend is also exposed on all interfaces at port 5000. | `get_client_ip`, `ProxyFix`, app startup, nginx config; live and isolated identity tests |
| LIVE-04 | Critical | Normal website DNS is hijacked even for paid clients by `address=/#/10.0.0.1`. Public-IP access succeeds while ordinary browsing fails. | Live `/etc/dnsmasq.conf`; paid DNS query |
| LIVE-05 | Critical | Two concurrent requests redeem the same voucher twice. Two concurrent transfer claims likewise both succeed. Two wallet withdrawals can spend the same balance. | `api_voucher_redeem`, `api_transfer_claim`, `api_member_use_wallet`; three deterministic concurrent tests against SQLite |
| LIVE-06 | Critical | A real UART `CREDIT_ADD` updates bottle statistics but not the simulator count used by `api_vendo_done`. One accepted hardware event followed by Done awards **0 minutes**. | `on_esp32_uart_output`, `api_vendo_status`, `api_vendo_done`; isolated real-message-path test |
| LIVE-07 | High | Repeated identical hardware credit events double-count statistics. There is no session/event deduplication. | `on_esp32_uart_output`; duplicate-event regression |
| LIVE-08 | High | Successful voucher redemption commits the voucher's used flag before durable session credit. A process loss before the next save can consume a voucher and lose its credit. | `api_voucher_redeem`, `save_sessions_to_db`; immediate recovery regression |
| LIVE-09 | High | Session restoration deletes the durable session table after loading it. Another restart before the next save loses those sessions. | `restore_sessions_from_db`; two consecutive recovery calls |
| LIVE-10 | High | Specific legitimate custom bandwidth values change after restart: 5120/1024 Kbps becomes the defaults. | `restore_sessions_from_db`; bandwidth recovery regression |
| LIVE-11 | High | Changing a client's IP for the same MAC leaves the old IP's authorization behind. The old session is removed from accounting without revoking that IP. | `ensure_client_session`; captured firewall calls omit old-IP deletion |
| LIVE-12 | High | A different MAC assigned an existing IP inherits that IP's session because existing entries are returned without checking the neighbor MAC. | `ensure_client_session`; MAC reassignment regression |
| LIVE-13 | High | Administrator pause can be undone by the client's resume endpoint. Admin pause does not persist an admin override. | `admin_api_client_action`, `api_client_pause`; isolated regression |
| LIVE-14 | High | An expired paused session can resume before the timer's next expiry check; resume clears its expiry first. | `api_client_pause`; isolated expired-session regression |
| LIVE-15 | High | Manual/automatic/admin pause reasons are not saved. Restore loses `user_paused`, `auto_paused`, and `admin_paused` state. | Session persistence schema and regression |
| LIVE-16 | High | The timer subtracts one second per loop rather than elapsed time. A delayed loop over 10 seconds deducted only 3 seconds in the controlled clock test. | `time_daemon`; delayed-loop regression |
| LIVE-17 | High | Auto-pause skips checks when the ARP table is empty, so the last disconnected client is not paused even with the option enabled. ARP presence also does not prove current physical connectivity. | `time_daemon`; empty-neighbor regression |
| LIVE-18 | High | MAC changes do not immediately update firewall rules. Additionally, the live authorization jump precedes the MAC-block jump, allowing an authorized packet before the block is checked. | MAC CRUD endpoints, live `iptables-save`, live MAC-block test |
| LIVE-19 | High | `ipset` is missing. The fallback installs non-expiring per-IP ACCEPT rules; it loses the advertised kernel timeout. Whitelisting invokes unavailable `ipset` directly. | Live shell/package check, image inspection, `update_firewall`, `time_daemon` |
| LIVE-20 | High | Walled-garden code changes NAT only and adds no FORWARD permission for unpaid clients. Rule ordering also puts the captive chain before the garden in the live rules. | `apply_walled_garden_and_macs`, live firewall snapshot; static rule inspection |
| LIVE-21 | High | Paid clients remain captive: DNS probe overrides and unconditional nginx redirects bypass the state-aware Flask responses. | Live paid `/generate_204`: nginx 302, Flask 204 |
| LIVE-22 | High | The board clock is May 12, 2026 while the workstation and upstream HTTP date are September 5. NTP is disabled/unsynchronized. Date-based expiry and reports cannot be trusted. | `timedatectl`, `date -u`, upstream response |
| LIVE-23 | High | An invalid expiry string is accepted as a valid license because the parser catches the error and proceeds. Expiry metadata is not included in the activation-PIN calculation. | `host/license_manager.py`: `verify_license`; malformed-expiry regression |
| LIVE-24 | Medium | `eth1` also has a link-local address and an extra default route, despite the intended WAN/LAN split. Two DHCP client processes were present. WAN worked during testing; recovery behavior is not established. | Live address/route/process snapshot |
| LIVE-25 | High | The saved ECO-Fi image differs from the live/workspace code. Comparison differs after newline normalization and also at Python AST level. | [Image comparison](image-comparison.json) |
| LIVE-26 | Medium | Physical finish-button behavior is not implemented in the inspected firmware's normal runtime; `PIN_FINISH_BTN` is read at boot for configuration. `OPEN_GATE` does not parse its transmitted per-command timeout, and the hardware session total is not reset per portal session. | `src/main.cpp`: button and serial command paths; source-only finding, physical firmware identity unverified |

These are behavioral failures, not merely missing UI labels. The isolated proof output is in [isolated-regressions.txt](isolated-regressions.txt); executable cases are in [test_gateway_audit.py](../../tools/test_gateway_audit.py).

## Original PisoFi comparison

The original image was mounted read-only with journal replay disabled. Selected source files were copied as evidence; no original PHP was executed.

* [DeviceLicense](original_DeviceLicense.php), [NoLicense](original_NoLicense.php), and [TrialLicense](original_TrialLicense.php) implement distinct license states. NoLicense reports expired; trial validity uses an expiration date. The original also has server registration and license verification paths. ECO-Fi's offline license file is a different design and currently is not enforced.
* [DeviceConfigurationMiddleware](original_DeviceConfigurationMiddleware.php) contains registration/no-license screens, online license refresh, and offline verification handling. [PortalController](original_PortalController.php) checks license expiry in vending connection paths. This establishes a source-level difference; exact original cloud responses and all existing-session behavior were not live-tested.
* Original [NetworkManager](original_NetworkManager.php) exposes configurable auto-pause/auto-resume settings. [pauseconnections.php](original_pauseconnections.php) has conditional boot behavior. The old audit's claim that original PisoFi unconditionally pauses every session at boot was too broad.
* Original [Pisofier](original_Pisofier.php) passes MAC, IP, mark, and download/upload rates into its networking implementation. ECO-Fi's live fallback authorizes only by IP.
* The original controller/model inventory includes WiPass, data plans, desktop sessions, connection transfers, and additional credit/account features. The old comparison's claim that transfer capability was wholly exclusive to ECO-Fi is not supported by the inspected original sources.

| Feature area | Current ECO-Fi verification status |
|---|---|
| WAN/LAN addressing, DHCP, NAT | Working at inspection; recovery/extra-route concerns open |
| Captive portal and paid DNS/probe release | Failing |
| No-license/expired-license behavior | Failing enforcement; original server/trial behavior not equivalent |
| Per-client upload/download caps | Measured caps work; restart persistence fails |
| Per-client internet grant/revoke/expiry | New-connection behavior works; existing connections bypass revocation |
| Per-client identity and IP/MAC reassignment | Failing |
| Manual/admin/automatic pause and resume | Basic manual pause works; override, expiry, disconnect, persistence failures |
| Sessions across power loss/restart | Isolated recovery failures; physical reboot/power loss not exercised |
| Voucher, member wallet, transfer | Sequential paths pass; concurrency and durability fail |
| Promo bottle rates | Arithmetic examples pass; physical hardware credit fails in the message-path test |
| MAC whitelist/blacklist | Not reliable on this live installation |
| Walled garden/free sites | Incomplete forwarding enforcement |
| Admin and simulator authentication | Basic unauthenticated API denial and default-password-change checks pass |
| Physical PET/metal/NIR/drop/bin/servo/finish behavior | Not verified on hardware |
| CSV/XLSX accounting and full admin UI | Code inspected only; report accuracy and rendering not certified |
| Alerts/Telegram | Not sent; no external-recipient messaging performed |
| Tethering control | TTL rewrite exists; no tethered-device test and no anti-tethering guarantee |
| Multiple simultaneous physical clients/AP isolation | Not verified; isolated logical clients do not prove this |
| Cloud registration, remote management/backup, PPPoE, desktop/charging, complete original feature set | Not established as implemented/equivalent in ECO-Fi |
| WAN outage, USB unplug/replug recovery, cold boot, long soak, maximum client count | Not certified by this run |

The original's complete behavior cannot be certified by static inspection or by running only the replacement application. A full 1:1 claim would need an original-image test board plus a feature-by-feature acceptance specification, including which original PC/coin/cloud features are intended in this bottle-vending adaptation.

## Test restoration and limitations

The original license was restored, the test client's credit is zero, its limits are back to 3072/1536 Kbps, and its temporary MAC policy was removed. The temporary port-5210 server stopped and its script was deleted. The test client's authorization rules were removed. Portal, nginx, and dnsmasq remained active. See [final state](live-final-state.json).

The identity probe created a **zero-credit, unauthorized** synthetic session for `10.0.7.118`; the current app has no client-record deletion endpoint. That inert record remains in the client list. Other pre-existing client balances continued their normal countdown. No existing credited sessions were reset, and no physical reboot was performed.

The OPi listed onboard UART devices but no `/dev/ttyUSB*` or `/dev/ttyACM*` at inspection. This does not establish whether an ESP32 is wired to GPIO UART. Hardware availability was requested; no physical deposit was performed during this run. The real UART message-path failure is proven against the identical source, not against a physically inserted bottle.

The scope has **not** reached “everything works properly.” The system fails core acceptance checks before the remaining hardware/endurance checks can establish a release sign-off.

## Repair and acceptance order

1. Enforce license and client identity in every credit/access path; preserve activation/admin recovery access. Make malformed license data fail closed.
2. Correct paid DNS/probe behavior and directional forwarding. Revocation must stop existing connections, MAC blocks must precede allows, and access must expire without relying solely on a live Python timer.
3. Make voucher, transfer, wallet, and session persistence changes atomic; preserve exact bandwidth and pause state. Use elapsed time for accounting and revoke old identities during migration.
4. Make hardware event/session ownership authoritative, persistent, and deduplicated; reconcile the actual ESP32 protocol, finish button, and timeouts.
5. Reconcile network management, time synchronization, image dependencies, and the saved image with the tested source. Rebuild and hash a reproducible image only after the application checks pass.
6. Repeat bound-source live tests, then run two physical clients, real bottle flows, restart/power-loss recovery, link/WAN failures, and a sustained soak. Update this report from observed results rather than copying the old closure claims.

Replay scripts: [traffic](../../tools/verify_opi_live.py), [bandwidth](../../tools/verify_opi_bandwidth.py), [license](../../tools/verify_opi_license.py), [expiry](../../tools/verify_opi_expiry.py), [policy](../../tools/verify_opi_policy.py). They intentionally target the audited PC address and require its balance to be zero before temporary-credit tests.
