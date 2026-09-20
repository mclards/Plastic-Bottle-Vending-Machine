---
name: ecofi-development
description: >-
  Authoritative guidance, runbooks, and architectural reference for developing,
  testing, and deploying software on the VMC ECO-VENDO Reverse Vending Machine Orange Pi One stack.
---

# VMC ECO-VENDO Reverse Vending Machine Development Skill

## Overview
This skill provides comprehensive instructions, pitfall warnings, and operational runbooks for developing on the VMC ECO-VENDO Reverse Vending Machine platform (Student Thesis: *Eco-Vendo: An Empty Bottle-Initiated Internet Access Vending System*).

## Quick Reference
1. **Target Hardware:** Orange Pi One (Allwinner H2+/H3, 32-bit ARMv7).
2. **Python Runtime:** Python 3.5.3. NEVER use f-strings or Python 3.6+ syntax in `host/` modules.
3. **Live Hardware:** Always treat the Live OPi (`10.0.0.1`) as ground truth using `tools/opi_access.py`.
4. **HTML/JS Injections:** In `portal.py`, escape all quotes in JS strings (`&quot;` or `\'`). Validate with `node --check`.
5. **Database:** In SQLite (`vendo_sessions.db`), `pause_budgets` does NOT have an `updated_at` column.
6. **Licensing:** Uses developer code `mclards23` with 32-character enterprise hexadecimal keys (`XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX`).
7. **Regression Testing:** Run `python -m unittest test_entitlement_regressions` before deploying.
8. **OS Image Rebuilding:** Run `wsl -d Ubuntu -u root -- bash build_ecofi_img.sh` and ensure matching MD5 and SHA-256 hashes are recorded in `resources/`.

