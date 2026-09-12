# Eco-Fi Project Rules for Claude (Anthropic)
> This repository uses **`AGENTS.md`** as the single source of truth for architectural guidelines, deployment rules, hardware constraints, and testing procedures.

### Primary Instructions for Claude
1. **Always consult [`AGENTS.md`](./AGENTS.md)** before designing or modifying backend services, UI templates, or network rules.
2. **Target Python Compatibility:** The live hardware runtime is **Python 3.5.3** (Allwinner H2+ ARMv7). Never write Python 3.6+ code (no f-strings, no inline type annotations) for target files in `host/` or deployment scripts.
3. **Live OPi Alignment:** The Orange Pi at `10.0.0.1` is the ground truth. Always test live with `tools/opi_access.py` and ensure the release `.img` matches the live board.
4. **Monolithic Strings in `portal.py`:** `PORTAL_HTML` and `ADMIN_HTML` are single-line strings. Never inject unescaped single quotes into JavaScript within them. Validate with `node --check`.
5. **Testing:** Run `python -m unittest test_entitlement_regressions` locally before pushing or deploying changes.

