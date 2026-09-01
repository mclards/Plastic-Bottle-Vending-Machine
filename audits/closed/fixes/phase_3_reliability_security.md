# Phase 3 Implementation: System Reliability & Secondary Security

**Status:** Completed  
**Target File:** `host/portal.py`  
**Date:** August 17, 2026

---

## Implemented Fixes

### 1. SEC-10: Rate Limit Member PIN Login
**Issue:** The `/api/member/login` endpoint had no protection against brute-force attacks on the 4-digit PINs.
**Fix Implemented:**
- Implemented a memory-based tracking dictionary `login_attempts` that maps usernames to failure counts and timestamps.
- If a user fails to login 5 times within a 5-minute window, subsequent attempts are blocked with a `429 Too Many Requests`-style error ("Too many attempts. Try again in 5 minutes.").
- The failure count resets automatically after a successful login or when the 5-minute timeout expires.

### 2. SEC-07: Add CSRF Protection (`SameSite=Strict`)
**Issue:** The application lacked CSRF protection, making it vulnerable if an admin browsed malicious sites while logged into the portal on the same device.
**Fix Implemented:**
- Set `app.config.update(SESSION_COOKIE_SAMESITE="Strict")` globally.
- This ensures the Flask session cookie is never sent along with cross-site requests, providing robust protection against standard CSRF attacks for all endpoints.

### 3. SEC-06: Default Admin Credentials Security
**Issue:** The system deployed with a default password of `admin123`. If an operator forgot to change it, anyone on the network could gain full admin access.
**Fix Implemented:**
- Modified the `/admin/login` endpoint. If an admin successfully logs in using the `admin123` password hash, the session is flagged with `must_change_password = True`.
- Added a `@app.before_request` middleware that intercepts all requests to the `/admin/*` routes.
- If the flag is present, the admin is forcibly redirected to a new `/admin/force_password_change` endpoint.
- This endpoint renders a dedicated UI requiring them to set a new password before they can access any dashboard features.

### 4. GAP-05: DAD / Network Interface Recovery
**Issue:** The Orange Pi could silently drop off the network if duplicate address detection (DAD) failed or if the DHCP/networking daemon crashed, requiring a physical hard reboot.
**Fix Implemented:**
- Added a `check_network_health()` function that verifies the active LAN interface actually has the `10.0.0.1` IP address assigned.
- Hooked this check into the `time_daemon()` loop to run every 5 minutes (300 ticks).
- If the IP is missing, it automatically attempts to recover by restarting the `networking` and `dnsmasq` systemd services, followed by re-applying the firewall rules.
