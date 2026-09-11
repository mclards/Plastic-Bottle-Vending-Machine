# Live OPi update — September 9, 2026

Deployed the verified v2.2.1 application modules, wallet JavaScript, VERSION and portal service unit to 10.0.0.1. Only the portal service was restarted. No OS image or ESP32 firmware was flashed.

The application, database (including SQLite sidecars), license and service configuration were backed up while the portal was stopped. Backup on the OPi: `/var/backups/ecofi/20260908T162810Z-before-v2.2.1` (root access only). The deployment preserves the machine license, customer data and hardware configuration. No open or held deposit sessions existed at preflight.

Post-update checks passed: worker health, clock synchronization without a trust override, license activation, authenticated diagnostics and balanced ledger, both status APIs, admin authentication enforcement, served two-decimal wallet formatting and hashes of all deployed files. See [verification](verification.txt) and [deployment status](deployment.json).

The application now reports v2.2.1. The base OS image and ESP32 were not replaced. Mechanical, paid-traffic and user acceptance testing remain for the operator; this update did not create vouchers, alter wallets or operate the gate.
