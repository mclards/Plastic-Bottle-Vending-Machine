# v2.2.3 credit queue display correction

The live OPi had an ACTIVE credit with 3 of 3 pauses used and two UNUSED credits. SWITCH calls the same pause eligibility check as PAUSE before stopping the current credit. Thus switching was rejected, while the portal still offered an enabled Use Credit button.

The portal now disables switching when the current active credit cannot pause or the administrator suspended access. Unused rows show Queued and a visible explanation. When switching is available from an active credit, the portal explains that it consumes one pause. Paused credits can still be switched without an additional pause. Unused credits advance automatically in order, subject to validity. Pause, wallet, accounting, and switching policies are unchanged.

Verification: 66 entitlement/Flask regression tests passed, including a new exhausted-pause test that proves blocked switching preserves the queued balance and that natural exhaustion activates the queued credit with its own 3 pauses. Five JavaScript render cases passed (zero pauses, available pause, paused credit, administrative suspension, other pause limits); JavaScript syntax and git whitespace checks passed. The new engine test also passed under Python 3.5 on the live OPi using an in-memory database. Customer credits were not switched or adjusted for testing.

Updated live app to v2.2.3 after verifying the prior deployment hashes. Only portal.py, static/time_controls.js, and VERSION were replaced. Served content and cache version match; worker, clock, and license checks passed. Backup: /var/backups/ecofi/20260909T003131Z-before-v2.2.3. No OS or ESP32 was flashed. The saved image remains v2.2.1 and does not contain this update.
