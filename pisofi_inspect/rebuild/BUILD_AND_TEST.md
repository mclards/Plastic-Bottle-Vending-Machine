# PisoFi Custom Test v1

This is a separate, flashable test image built from the inspected PisoFi image. It retains the original Armbian kernel, drivers, PHP portal, database schema, coin/bill handling, and session/traffic-control code. It uses a new local signed licensing system. It is a functional test build, not a migration to a newer Linux distribution.

## Files

- Image: [PisoFi_Custom_Test_v1.img](../../resources/PisoFi_Custom_Test_v1.img)
- Checksum: [PisoFi_Custom_Test_v1.img.sha256](../../resources/PisoFi_Custom_Test_v1.img.sha256)
- Login credentials: [TEST_CREDENTIALS.txt](private/TEST_CREDENTIALS.txt)
- Build result and checks: [build_result.json](logs/build_result.json)
- Full file/content/permission changes: [final_changes.json](logs/final_changes.json)
- License signing key: `private/license-signing.pem`. This stays on the PC and is not included in the image.

The original `PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img` was preserved and its SHA-256 rechecked. The output retains the original boot region and partition layout.

## First hardware test

1. Write the new `.img` to a spare microSD card using your usual image writer. This replaces the contents of that card.
2. Power off the Orange Pi, insert the test card, and connect it to an isolated bench network. This image retains broad administrative privileges from the original system.
3. For two Ethernet interfaces, use onboard `eth0` for upstream DHCP/WAN and USB Ethernet `eth1` for the computer/access point on the LAN. For a one-interface bench test, use onboard `eth0` as LAN; that mode does not provide a separate WAN connection.
4. Boot and allow a few minutes for the original filesystem-expansion process and first-boot key generation. Expansion may cause a reboot.
5. The management address is **10.0.0.1/19**. If DHCP is not yet available, set the test computer to **10.0.0.2**, subnet mask **255.255.224.0**.
6. Open **http://10.0.0.1/admin** and sign in as **administrator**, using the generated password in `private/TEST_CREDENTIALS.txt`.
7. SSH is **root@10.0.0.1**, port **22**. The same generated console password also applies to `pi`. The web password and console password are different.

SSH host keys are generated afresh on first boot. A timer restores management addressing and LAN SSH rules after firewall reloads. Network database defaults are adapted when the system switches between single- and dual-port modes, rather than overwritten on every timer tick.

Verify coin and bill pulses, credit addition, session start/pause/resume/expiry, client routing, bandwidth limits, and a reboot on the real board. Those electrical and boot behaviors cannot be established by the offline checks alone.

## What changed

- Linux root/pi credentials and the existing web administrator password were reset to generated values.
- Licensing now verifies an RSA/SHA-256 signature using a public key on the image. The private signing key remains on the PC.
- The included **TEST** license is non-expiring and allows any device identity, charging, 256 vendos, and 256 desktop entitlements. It is deliberately convenient for hardware testing. Device-bound and expiring licenses are also supported by the verifier.
- Portal licensing, registration compatibility, and background validation were separated from the vendor licensing API.
- Both copies of the scheduler's destructive license branch were removed.
- Both background `eval` paths and the six encoded repair/archive asset copies were removed, preventing the investigated 542-file restoration mechanism from reverting changes.
- Vendor update/patch/verifier installation, application rollback, vendor activation/revocation, cloud remote-management toggles, and database restore through the old UI are disabled in this test build. The replacement returns an explanatory response rather than executing the old operation.
- ngrok, ZeroTier, vendor remote backup/subscriber, and data-sync units are masked. Original vendor-cloud functionality is not provided by the replacement license.
- PHP uses generated session IDs; successful administrator login regenerates the session ID. Failed submitted passwords are redacted before logging.
- Administrative speed-test server IDs are restricted to digits before shell use.
- Saved network profiles no longer bind to the original hardware MAC addresses. NetworkManager and database settings adapt for the selected bench interface layout.
- SSH host keys and the machine ID are regenerated for the new installation. The build emulator, temporary tests, test sessions, provisioning password hash, and database build logs were removed from the exported image.

The build does **not** claim that every inherited security issue has been fixed. Core root-running services and broad sudo permissions remain for compatibility and access during testing. Third-party integrations that require external accounts were not runtime-tested.

## Licensing commands

On the device:

```sh
ecofi-license status
```

This displays the detected hardware ID and verified license details. The included wildcard license is sufficient for the initial test.

To issue a device-bound license later, run the PC-side generator from Ubuntu WSL, substituting the hardware ID shown on the device:

```sh
cd /mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine/pisofi_inspect/rebuild
python3 issue_license.py --device YOUR_DEVICE_ID --output private/device-license.json
```

Omit `--expires` for a permanent license. To set an expiry, supply a UTC Unix timestamp with `--expires`. The expiry and device identity are covered by the signature.

Copy that JSON to the device using your SSH client, then install it as root:

```sh
ecofi-license install /tmp/device-license.json
```

The installer validates the signature, identity, expiry and entitlements before atomically replacing the license file. SSH remains the recovery path if a license is missing or invalid. The old vendor activation form is not a custom-license installer.

## Offline validation performed

- Source-image SHA-256 matched the inspected baseline before copying and before export.
- Original first 4 MiB boot region compared byte for byte with the rebuilt image.
- The image's actual ARM PHP 7.0 runtime was used under QEMU for 469 PHP syntax checks; the subsequently added network helper and license CLI were also checked.
- Nine signed-license cases passed: valid test, valid bound device, unsigned tampering, wrong hardware, expiry, invalid production wildcard, wrong issuer, malformed entitlements, and malformed envelope.
- The image's actual ARM MariaDB started against the staged database using a Unix socket with TCP networking disabled.
- Actual application authentication rejected a wrong password, accepted the generated administrator password, created a valid admin session, and loaded the new license through the original Composer autoloader.
- The failed-login model stored the redacted value, and the one existing administrator record was preserved.
- Single-port and dual-port database transitions were tested against the real schema, including its unique subnet constraint.
- MariaDB reported `OK` for the changed settings/network/user tables and the core active-client/session tables, then shut down cleanly.
- Generated root/pi password hashes were verified against the saved credentials. Target OpenSSH key generation and effective SSH configuration were checked; temporary host keys were then removed.
- Final unmounted read-only ext4 check passed. The exported file is hashed and compared to the staged image before receiving its final filename.

No Orange Pi boot, physical GPIO test, live client forwarding test, or complete browser-driven portal test has been performed yet.

## Build records

The scripts in this folder preserve the preparation, patching, signing, validation, provisioning and export steps. The Linux staging workspace is `/var/tmp/pisofi-custom-test-v1`; `prepare.sh` refuses to overwrite an existing staged image and `export_image.py` refuses to replace an existing final image. Building another revision should use a new workspace/output name.

Private credentials, the signing key, and generated logs are excluded from Git by this folder's `.gitignore`. No changes were made to the existing EcoFi builder, application source, or earlier EcoFi image files.
