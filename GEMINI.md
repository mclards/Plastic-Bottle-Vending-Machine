# Eco-Fi Project Rules for Gemini / Antigravity
> This repository uses **`AGENTS.md`** as the authoritative source of truth.

### Primary Directives for Gemini / Antigravity
1. **Reference Document:** Review [`AGENTS.md`](./AGENTS.md) for full hardware, database, captive portal, and licensing specifications.
2. **Python 3.5.3 Strictness:** Target platform is Python 3.5.3 on 32-bit ARM. Never use f-strings or modern Python 3.6+ features on target code in `host/`.
3. **Database Integrity:**
   - Table `pause_budgets` does NOT have an `updated_at` column.
   - Database operations must pass `test_entitlement_regressions.py` (all 66 tests).
4. **Live Deployment Reference:**
   - Live OPi is the debugging ground truth.
   - When deploying to `/opt/ecofi/`, restart `ecofi_portal.service` and verify status.
   - Ensure the OS release image in `resources/` is rebuilt to match the live system.
