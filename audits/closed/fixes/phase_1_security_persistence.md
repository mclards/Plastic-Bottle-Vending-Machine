# Phase 1 Implementation: Critical Security & Data Persistence

**Status:** Completed  
**Target File:** `host/portal.py`  
**Date:** August 17, 2026

---

## Implemented Fixes

### 1. SEC-05: Dynamic Flask Secret Key
**Issue:** `app.secret_key` was hardcoded in the source code as `"eco_fi_super_secret_key_change_in_production"`. This allowed forging of admin sessions.
**Fix Implemented:** 
- Modified the application initialization in the `if __name__ == "__main__":` block.
- The system now reads the secret key from the SQLite `config` table using `get_config()`.
- If no key exists (first run), it generates a secure 32-byte hexadecimal token using Python's `secrets.token_hex(32)`.
- It saves this token to the database via `set_config()`.
- The application is now fully protected against session forgery.

### 2. SEC-08: Shell Injection via IP Address
**Issue:** `update_firewall()` and `setup_firewall()` were taking IP strings directly from Flask requests and placing them in `subprocess.run(..., shell=True)` commands, which is a vector for shell injection if the Nginx `X-Real-IP` header is spoofed.
**Fix Implemented:**
- Imported `ipaddress` module.
- Added strict IP validation: `ip = str(ipaddress.ip_address(ip))`. If validation fails, it throws a `ValueError` and silences the function, preventing execution.
- Changed `subprocess.run` to use lists (e.g., `["ipset", "add", "ecofi_auth", ip, ...]`) instead of `shell=True` to explicitly prevent shell injection at the OS level.

### 3. GAP-01: Sessions Lost on Power Cycle
**Issue:** `active_clients` dictionary was stored only in RAM. Any reboot or crash permanently wiped all active user timers.
**Fix Implemented:**
- Added `save_sessions_to_db()` function which creates and writes to an `active_sessions` SQLite table. It saves IP, MAC, remaining time, pause status, speed allocations, and pending bottles.
- Added `restore_sessions_from_db()` which loads these sessions on startup and automatically sets them to `is_paused = True`. This mimics PisoFi's safe behavior, ensuring users don't accidentally drain time if the box reboots while they are away.
- Registered `save_sessions_to_db()` to run cleanly on shutdown using `atexit.register()`.
- Upgraded the `time_daemon()` loop to trigger a periodic save every 30 seconds for crash resilience.
