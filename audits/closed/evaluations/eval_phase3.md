# Re-Evaluation: Phase 3 Fixes (Post-Implementation) — System Reliability & Secondary Security

**Evaluated Against:** [`portal.py`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/host/portal.py)  
**Fix Reference:** [`phase_3_reliability_security.md`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/audits/fixes/phase_3_reliability_security.md)  
**Previous Evaluation:** [`eval_phase3.md`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/audits/evaluations/eval_phase3.md)  
**Re-Evaluation Date:** August 17, 2026 (Code read directly from live `portal.py`)

---

## Fix 1: SEC-10 — Rate Limiting on Member PIN Login + Admin Login

### Code Found — `portal.py` L1565–L1596 (Member), L1817–L1853 (Admin)

**Member login rate limiting (L1573–L1596):**

```python
login_attempts = {}                                     # L1565 — module-level

now = time.time()
if username in login_attempts:
    count, last_time = login_attempts[username]
    if now - last_time < 300:
        if count >= 5:                                  # ← nested; only blocks within window
            return jsonify({"success": False, "error": "Too many attempts. Try again in 5 minutes."})
    else:
        login_attempts[username] = [0, now]             # window expired — fresh reset
else:
    login_attempts[username] = [0, now]                 # first attempt for this user

...
if not row or not check_password_hash(row[0], pin):
    login_attempts[username][0] += 1                    # increment count
    login_attempts[username][1] = time.time()           # slide last_time
    return jsonify({...})

if username in login_attempts:
    del login_attempts[username]                        # clear on success
return jsonify({"success": True, ...})
```

**Admin login rate limiting (L1817–L1853):**

```python
admin_login_attempts = {}                               # L1817 — module-level, IP-keyed

client_ip = request.remote_addr or "127.0.0.1"
now = time.time()
if client_ip in admin_login_attempts:
    count, last_time = admin_login_attempts[client_ip]
    if now - last_time < 300:
        if count >= 5:
            return render_template_string(LOGIN_HTML, error="Too many attempts. Try again in 5 minutes.")
    else:
        admin_login_attempts[client_ip] = [0, now]
else:
    admin_login_attempts[client_ip] = [0, now]

...
if row and check_password_hash(row[0], password):
    if client_ip in admin_login_attempts:
        del admin_login_attempts[client_ip]             # clear on success
    session['admin_logged_in'] = True
    session['admin_username'] = username                # ← stored for force_password_change
    ...
else:
    admin_login_attempts[client_ip][0] += 1
    admin_login_attempts[client_ip][1] = time.time()
    error = "Invalid username or password"
```

### Verdict: ✅ PASS — Complete and Secure

**What's good:**
- **Window logic is now correct.** The nested `if count >= 5` inside `if now - last_time < 300:` correctly means: "only block if we are *still inside* the window AND have hit the limit." The outer `else` resets cleanly when the window expires — no ambiguity.
- **Admin login is now protected.** `admin_login_attempts` tracks by IP (not username), which is the correct approach for a form-based auth screen. A local network attacker is now throttled after 5 failures.
- **Counters clear on success** for both routes — no persistent lockout of legitimate users after a successful login.
- **`session['admin_username']`** is stored at login time (L1845), which is a necessary prerequisite for the `admin_force_password_change` fix in SEC-06.
- Both dictionaries are **module-level** (not inside the route function), so state persists across requests within the same Python process lifetime. ✅

**One remaining note (architectural, non-blocking):**
- `login_attempts` and `admin_login_attempts` are unbounded in-memory dicts. A targeted DoS could enumerate millions of usernames/IPs and bloat the dict. For this embedded single-user use case this is completely acceptable — a periodic cleanup task would be the hardening step if ever needed.

---

## Fix 2: SEC-07 — CSRF via `SameSite=Lax` Cookie

### Code Found — `portal.py` L119

```python
app.config.update(SESSION_COOKIE_SAMESITE="Lax")
```

### Verdict: ✅ PASS — Correct Placement, Appropriate Level

**What's good:**
- **Module-level placement** (L119, immediately after `app.secret_key = ...`) ensures this applies under all deployment modes including WSGI (gunicorn/uWSGI). ✅
- **`Lax` is the correct choice** for a captive portal. `Strict` would cause browsers to drop the admin session cookie when navigating to the admin panel from a bookmark or external link — common in this use-case. `Lax` blocks cross-origin POST-based CSRF attacks while allowing top-level GET navigation.

**No remaining issues.**

---

## Fix 3: SEC-06 — Forced Admin Password Change

### Code Found — `portal.py` L1797–L1814

```python
@app.before_request
def check_default_password():
    if request.path.startswith('/admin') \
       and not request.path.startswith('/admin/login') \
       and not request.path.startswith('/admin/logout') \
       and not request.path.startswith('/admin/force_password_change') \
       and not request.path.startswith('/static'):
        if session.get('admin_logged_in') and session.get('must_change_password'):
            return redirect('/admin/force_password_change')

@app.route("/admin/force_password_change", methods=["GET", "POST"])
def admin_force_password_change():
    if not session.get('admin_logged_in'): return redirect("/admin/login")
    if request.method == "POST":
        new_pw = request.form.get("new_password", "").strip()
        if new_pw and len(new_pw) >= 6 and new_pw != "admin123":    # ← server-side check
            admin_user = session.get('admin_username', 'admin')      # ← dynamic username
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("UPDATE admins SET password_hash=? WHERE username=?",
                             (generate_password_hash(new_pw), admin_user))
            session.pop('must_change_password', None)
            return redirect("/admin")
    return render_template_string(FORCE_PASS_HTML)
```

### Verdict: ✅ PASS — Robust and Correct

**What's good:**
- **Server-side `len(new_pw) >= 6`** enforced in Python, not just the HTML `minlength="6"`. Direct POST bypass is now blocked. ✅
- **Dynamic `admin_user`** from `session.get('admin_username', 'admin')` — uses the session value stored at login (L1845), not a hardcoded string. Falls back to `'admin'` safely if session key is somehow missing. ✅
- **`before_request` middleware** correctly excludes `login`, `logout`, `force_password_change`, and `static` paths — no infinite redirect loops. ✅
- **`must_change_password` fires on `admin123` plaintext comparison at login time** (L1846) — correct and unavoidable. ✅

**One remaining note:**
- `admin_logout` at L1855–L1858 only pops `admin_logged_in` but not `admin_username`. If the same browser then logs in as a *different* admin user who also needs to change the default password, `session['admin_username']` from the previous session might still be present. However, since the `admin_username` is set fresh on every successful login (L1845), this is not a real bug — the new value overwrites the old one.

---

## Fix 4: GAP-05 — Network Interface / DAD Recovery

### Code Found — `portal.py` L232–L245, L523–L524

```python
def check_network_health():
    """Monitor for network configuration loss and recover."""
    if platform.system() == "Windows": return
    try:
        lan_iface = get_lan_interface()                             # ← uses LAN, not WAN
        res = subprocess.run(["ip", "addr", "show", lan_iface],    # ← list-form, no shell
                             capture_output=True, text=True)
        if "10.0.0.1" not in res.stdout:
            subprocess.run(["systemctl", "restart", "networking"]) # ← list-form
            subprocess.run(["systemctl", "restart", "dnsmasq"])    # ← list-form
            time.sleep(5)
            setup_firewall()
    except Exception:
        pass

# Scheduling in time_daemon:
if tick % 300 == 0:                                                # L523 — every 300 ticks
    check_network_health()
```

### Verdict: ✅ PASS — Functionally Correct

**What's good:**
- **Correct interface.** `get_lan_interface()` returns the LAN interface (e.g., `eth0`). The `10.0.0.1` static IP is on the LAN interface — so the health check now actually makes sense. ✅
- **No `shell=True`.** All subprocess calls are list-form throughout. ✅
- **No duplicate call.** The previous `tick % 300` duplication was cleaned up (only one call at L523–L524). ✅
- **Tick counter behavior:** The Phase 1 fix confirmed that `tick` is now an unbounded incrementing counter (it does **not** reset at 60). Therefore `tick % 300 == 0` fires legitimately every 300 seconds (5 minutes) as designed. ✅

**One remaining observation (non-blocking):**
- `setup_firewall()` calls `setup_bandwidth_control(get_lan_interface())` and `apply_walled_garden_and_macs()` — both are safe to re-run. However, `setup_bandwidth_control` does a hard `tc qdisc del dev <iface> root` before re-adding — this will briefly interrupt all client bandwidth shaping for ~1 second during a recovery event. This is acceptable (recovery is by definition a degraded state). ✅

---

## Phase 3 Overall Re-Evaluation Summary

| Fix ID | Description | Previous Eval Verdict | Live Code Verdict |
|--------|-------------|----------------------|-------------------|
| SEC-10 | Rate Limiting — Member & Admin Login | ✅ PASS (self-report) | ✅ **CONFIRMED PASS** — Logic correct; admin route protected; cleanup on success |
| SEC-07 | CSRF via `SameSite=Lax` | ✅ PASS (self-report) | ✅ **CONFIRMED PASS** — Module-level; `Lax` correct for captive portal |
| SEC-06 | Forced Admin Password Change | ✅ PASS (self-report) | ✅ **CONFIRMED PASS** — Server-side length check; dynamic username from session |
| GAP-05 | Network / DAD Recovery | ✅ PASS (self-report) | ✅ **CONFIRMED PASS** — LAN interface; list-form subprocess; single scheduling call |

> [!NOTE]
> All four Phase 3 fixes are confirmed clean against the live `portal.py` code. No regressions introduced. Phase 3 is closed.
