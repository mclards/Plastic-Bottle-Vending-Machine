# Audit 04: Security Audit

**Date:** August 17, 2026  
**Scope:** Both original PisoFi v5.3.0 image and ECO-Fi `portal.py`

---

## Findings in Original PisoFi Image

### SEC-01: Hardcoded MySQL Credentials [CRITICAL]
**Location:** `/etc/environment`  
**Issue:** MySQL credentials (`wipi`/`wipi`) are stored in plaintext in the environment file and sourced by every bash script.

**Our Status:** ✅ Not applicable — ECO-Fi uses SQLite with no authentication (file-level access only, which is appropriate for an embedded single-user device).

---

### SEC-02: World-Writable Executables [HIGH]
**Location:** `/home/pi/.dat/devnull/.../` and `/usr/local/bin/`  
**Issue:** All PisoFi scripts have permissions `rwxrwxrwx` (777). Any user on the system can modify the firewall scripts, coin reader logic, or kicker daemon.

**Our Status:** ✅ Not applicable — `build_ecofi_img.sh` does not set 777 on our files.

---

### SEC-03: Ngrok Backdoor [CRITICAL]
**Location:** `/usr/local/bin/ngrok` (30MB binary), `pisofi_ngrok.service`  
**Issue:** The original image ships with a full Ngrok binary and service that creates a public tunnel to the admin panel. This effectively opens a remote backdoor to the device. Anyone with the Ngrok URL can access the full admin panel.

**Our Status:** ✅ Removed — ECO-Fi intentionally does not include Ngrok or any remote access tunnel.

---

### SEC-04: Obfuscated Phone-Home Scripts [HIGH]
**Location:** `pisofi_remotesubscriber.service`, `pisofi_datasync.service`, `pisofi_remotebackup.service`  
**Issue:** Multiple services connect to external PisoFi cloud servers. The PHP code is obfuscated, making it impossible to audit what data is being exfiltrated. Includes `check_status` script that phones home on every boot.

**Our Status:** ✅ Removed — ECO-Fi is fully self-contained with no external connections.

---

## Findings in ECO-Fi (`portal.py`)

### SEC-05: Hardcoded Flask Secret Key [HIGH]
**Location:** [portal.py line 31](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/host/portal.py#L31)  
**Code:** `app.secret_key = "eco_fi_super_secret_key_change_in_production"`

**Issue:** The Flask session secret key is hardcoded. If an attacker knows this string (which is in the source code), they can forge admin session cookies and gain full admin access without knowing the password.

**Recommended Fix:**
```python
import secrets
# Generate a random key on first run, persist to config
secret = get_config("flask_secret_key", "")
if not secret:
    secret = secrets.token_hex(32)
    set_config("flask_secret_key", secret)
app.secret_key = secret
```

---

### SEC-06: Default Admin Credentials [MEDIUM]
**Location:** [portal.py line 102](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/host/portal.py#L102)  
**Code:** `c.execute("INSERT OR IGNORE INTO admins (username, password_hash) VALUES ('admin', ?)", (default_hash,))`

**Issue:** Default admin password is `admin123`. If the operator doesn't change it, anyone who knows the default can access the full admin panel, manage clients, generate vouchers, and modify system settings.

**Recommended Fix:**
- Force password change on first login
- Or generate a random initial password and display it on the LCD/OLED on first boot

---

### SEC-07: No CSRF Protection [MEDIUM]
**Location:** All POST endpoints in portal.py

**Issue:** None of the API endpoints have CSRF tokens. An attacker could craft a malicious webpage that submits POST requests to the portal API (e.g., `/api/client/pause`, `/admin/api/client/action`) when visited by an authenticated admin on the same network.

**Recommended Fix:**
- For admin endpoints: Add Flask-WTF or manual CSRF token validation
- For client API endpoints: These are called via AJAX from the portal page itself, so same-origin policy provides some protection, but adding `SameSite=Strict` cookie attribute would help

---

### SEC-08: Shell Injection via IP Address [HIGH]
**Location:** `sync_client_firewall()`, `update_firewall()` functions

**Issue:** The `ip` parameter from `request.remote_addr` is passed directly into shell commands via `subprocess.run(f"ipset add ecofi_auth {ip} ...", shell=True)`. While Flask's `request.remote_addr` is typically safe (set by the web server), if the Nginx `X-Real-IP` header is ever spoofed or if the IP validation is bypassed, an attacker could inject shell commands.

**Recommended Fix:**
```python
import ipaddress

def update_firewall(ip, action, timeout_sec=0):
    if platform.system() == "Windows":
        return
    # Validate IP before using in shell command
    try:
        validated_ip = str(ipaddress.ip_address(ip))
    except ValueError:
        return  # Invalid IP, reject silently
    
    try:
        if action == "add":
            subprocess.run(["ipset", "add", "ecofi_auth", validated_ip, 
                          "timeout", str(int(timeout_sec)), "-exist"], check=True)
        elif action == "del":
            subprocess.run(["ipset", "del", "ecofi_auth", validated_ip, "-exist"], check=True)
    except Exception:
        pass
```

Note: Using `subprocess.run()` with a **list** instead of `shell=True` prevents shell injection entirely.

---

### SEC-09: SQLite Database Not Protected [LOW]
**Location:** `vendo_sessions.db` in working directory

**Issue:** The SQLite database file has no encryption. Anyone with physical access to the SD card can read all admin password hashes, member PINs, voucher codes, and session history.

**Recommended Fix:**
- For the current embedded use case, this is acceptable (physical access = game over anyway)
- If needed, use `sqlcipher` (encrypted SQLite) for the database file
- Ensure the SD card image has proper file permissions (`chmod 600 vendo_sessions.db`)

---

### SEC-10: Member PIN Stored as Hash but No Rate Limiting [LOW]
**Location:** `/api/member/login` endpoint

**Issue:** Member PINs are hashed (good), but there is no rate limiting on login attempts. An attacker could brute-force a 4-digit PIN (only 10,000 combinations) in seconds.

**Recommended Fix:**
```python
login_attempts = {}  # {username: (count, last_attempt_time)}

@app.route("/api/member/login", methods=["POST"])
def api_member_login():
    username = request.form.get("username", "").strip()
    
    # Rate limiting
    now = time.time()
    if username in login_attempts:
        count, last_time = login_attempts[username]
        if now - last_time < 300 and count >= 5:  # 5 attempts per 5 minutes
            return jsonify(ok=False, msg="Too many attempts. Try again in 5 minutes.")
        if now - last_time >= 300:
            login_attempts[username] = (0, now)
    
    # ... existing login logic ...
```

---

## Security Comparison Summary

| Category | PisoFi v5.3.0 | ECO-Fi |
|----------|---------------|--------|
| Credentials | Hardcoded MySQL creds in plaintext | Hardcoded Flask secret (fixable) |
| Remote Access | Ngrok backdoor + cloud phone-home | No remote access (secure) |
| Code Visibility | Obfuscated PHP (security by obscurity) | Clean Python (auditable) |
| File Permissions | 777 on all scripts | Standard permissions |
| Injection | PHP eval/exec patterns | Shell injection risk in `ipset` calls (fixable) |
| Authentication | PHP session-based | Flask session-based (same level) |
| Data at Rest | MySQL (unencrypted) | SQLite (unencrypted, same level) |

**Overall:** ECO-Fi is significantly more secure than PisoFi by design (no backdoors, no phone-home, readable code), but has 3 issues that should be fixed before production deployment (SEC-05, SEC-08, SEC-07).
