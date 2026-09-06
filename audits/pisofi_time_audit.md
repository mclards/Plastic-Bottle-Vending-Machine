# PisoFi Time & Expiration Audit

I have performed a deep-dive technical audit of the original PisoFi backend code (specifically analyzing `kicker.php`, `ClientSession.php`, `ConnectionSessionController.php`, and the database schema) to determine exactly how time and expiration were handled natively.

Below are the findings.

## 1. Core Time Management (`remaining_time`)
When a client inserts a coin, their time is stored in the `remaining_time` column (in seconds) within the `client_sessions` (or `connection_sessions`) table. 
- The `kicker.php` daemon continuously runs in the background.
- For active clients (`status = 1`), it subtracts the elapsed seconds from `remaining_time`.
- Once `remaining_time <= 0`, the client's status is immediately updated to `4` (`STATUS_EXPIRED`), effectively terminating their internet access.

## 2. Expiration Date (Global & Promo specific)
In addition to `remaining_time`, PisoFi implements a strict `expiration_date` column for each session.
- **Packages & Promos:** Time/Data promos can have a strict `expiration_time` (e.g., "valid for 3 days"). This calculates an exact `expiration_date` timestamp.
- **Global Settings:** The global configuration has an option `session_expiration_enabled` which acts as a default TTL (Time-To-Live) for all sessions (e.g., 1440 minutes = 1 day), regardless of remaining time.
- **Verification:** The backend `isExpired()` method explicitly checks:
  ```php
  $expiration = Carbon::parse($this->expiration_date)->format('U');
  $now = time();
  return ($expiration < $now) || $this->status === 4;
  ```
  If this returns `true`, the session is treated as expired even if they still have unused `remaining_time`.
- **Note on Paused Sessions:** While a session is explicitly "Paused" (`status = 0`), its `expiration_date` pushes forward *unless* pause limits are enabled (see below).

## 3. Pause Validity & Timeouts (`pauseTimeValidity`)
PisoFi contains explicit database settings (`portal_allow_pause_validity` and `portal_pause_validity`) to prevent clients from hoarding paused time indefinitely.
- The `kicker.php` daemon periodically executes the following SQL logic:
  ```sql
  TIMESTAMPDIFF(MINUTE, last_paused, NOW()) >= {$pauseTimeValidity}
  ```
- If a client pauses their time and leaves it paused longer than the `pauseTimeValidity` setting (in minutes), the system forcefully expires their remaining time.
- Additionally, PisoFi has `max_pause_limit` and `portalMinimumTimeAllowedPauseInSeconds` logic, restricting users from pausing if they have too little time left.

## 4. Session Purging (`autoRemoveExpiredSessions`)
By default, expired sessions are kept in the database with `status = 4`.
However, the system relies on the `auto_remove_expired_sessions` setting. When set to `1` (which is standard), a cron job automatically purges or moves these expired sessions into the `old_client_sessions` history table to keep the active tables fast and lightweight.

## 5. Membership & Client Accounts
PisoFi natively supports a full membership registration system for end-users directly from the captive portal.
- **Client Accounts Database (`client_accounts`)**: When a user registers via the portal, they provide a username (`client_id`) and a password (stored as a hashed `passkey`).
- **Device Binding Pipeline**: Upon registration (e.g., `/account/register`), the system automatically retrieves the client's current MAC address and IP address, and associates them with the newly created account.
- **Digital Wallet Integration**: Registered members have a `wallet` column in their account, allowing them to accumulate and store balance/time centrally rather than solely on an ephemeral MAC address session. This enables users to log in securely from different devices and access their saved time.

---

### Summary for your ECO-Fi Migration:
If you wish to fully mimic the original PisoFi behavior in ECO-Fi, you will need to consider:
1. **Adding an `expiration_date` column** to your DB if you want to implement "promos valid for X days" rather than just infinite accumulating time.
2. **Adding a `last_paused` timestamp** column if you want to implement a rule that deletes time if a user stays paused for too many days.
3. **Session cleanup** to move completely depleted sessions into a history log, keeping the live `active_clients` table small and fast.
4. **Client Accounts & Wallet (Already Implemented)**: You have successfully built a comparable Membership & Wallet system in ECO-Fi using Python, achieving parity with PisoFi's `client_accounts` architecture. Your current logic for replacing inactive/paused IP addresses upon fresh member logins successfully solves the cross-device IP ghosting issue that PisoFi originally faced.
