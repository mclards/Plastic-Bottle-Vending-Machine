# -*- coding: utf-8 -*-
"""
Eco-Fi Time & Pause Policy Evaluator (PisoFi-style)
Strictly compatible with Python 3.5.3 (NO f-strings, NO variable annotations).

Implements pure functions for:
- Bracket validity lookup (TimeExpiration)
- Pause eligibility (count gate, balance window, permission, suspension)
- Pause deadline and auto-resume calculations
- Calendar validity precedence
- Full-use slack calculation
- Maximum future pause allowance bounds
"""

import math

# Default preset values (pisofi_time_v1)
DEFAULT_PAUSE_COUNT_MAX = 3
DEFAULT_PAUSE_DURATION_SEC = 3600  # 60 minutes
DEFAULT_GLOBAL_VALIDITY_MIN = 1440  # 24 hours
DEFAULT_MIN_PAUSE_BALANCE_SEC = 0
DEFAULT_MAX_PAUSE_BALANCE_SEC = 5400  # 90 minutes (when enabled)

DEFAULT_VALIDITY_BRACKETS = [
    {'value': 30, 'expiration': 1440, 'enabled': True},       # Up to 30 mins: 24 Hours (1 Day)
    {'value': 60, 'expiration': 2880, 'enabled': True},       # Up to 1 hour: 48 Hours (2 Days)
    {'value': 180, 'expiration': 4320, 'enabled': True},      # Up to 3 hours: 72 Hours (3 Days)
    {'value': 360, 'expiration': 10080, 'enabled': True},     # Up to 6 hours: 7 Days (1 Week)
    {'value': 720, 'expiration': 21600, 'enabled': True},     # Up to 12 hours: 15 Days (~2 Weeks)
    {'value': 1440, 'expiration': 43200, 'enabled': True},    # Up to 24 hours: 30 Days (1 Month)
    {'value': 4320, 'expiration': 86400, 'enabled': True},    # Up to 3 days: 60 Days (2 Months)
    {'value': 10080, 'expiration': 129600, 'enabled': True},  # Up to 7 days: 90 Days (3 Months)
    {'value': 43200, 'expiration': 259200, 'enabled': True},  # Up to 30 days: 180 Days (6 Months)
]



def calculate_bracket_validity(purchased_seconds, brackets, global_validity_min=DEFAULT_GLOBAL_VALIDITY_MIN):
    """
    Look up validity duration in seconds using PisoFi bracket rules.
    - Matches the smallest enabled ceiling (bracket['value']) >= purchased minutes.
    - If no bracket matches: falls back to max(global_validity_min * 60, purchased_seconds).
    - If global validity disabled (None or <= 0): returns None (no expiry).
    
    brackets is a list of dicts: [{'value': int_min_ceiling, 'expiration': int_validity_min, 'enabled': bool}]
    """
    ok, reason = validate_brackets(brackets)
    if not ok:
        raise ValueError(reason)
    if purchased_seconds <= 0:
        return None

    purchased_min = int(math.floor(purchased_seconds / 60.0))

    # Filter enabled brackets and sort ascending by value
    enabled_brackets = [b for b in brackets if b.get('enabled', True)]
    enabled_brackets.sort(key=lambda b: b['value'])

    # Find smallest ceiling >= purchased_min
    for b in enabled_brackets:
        if b['value'] >= purchased_min:
            val_min = b.get('expiration', 0)
            if val_min > 0:
                return val_min * 60

    # Fallback to global validity
    if global_validity_min and global_validity_min > 0:
        global_sec = global_validity_min * 60
        return max(global_sec, purchased_seconds)

    return None


def calculate_activation_validity(now_utc, validity_duration_sec):
    """
    Calculate the calendar validity timestamp for an activation-relative grant.
    Returns unix timestamp (int) or None.
    """
    if validity_duration_sec is None or validity_duration_sec <= 0:
        return None
    return now_utc + validity_duration_sec


def can_pause_grant(grant_state, remaining_seconds, pause_count_used, now_utc, valid_until_utc=None,
                    pause_count_max=DEFAULT_PAUSE_COUNT_MAX,
                    min_balance_sec=DEFAULT_MIN_PAUSE_BALANCE_SEC,
                    max_balance_sec=None,
                    global_pause_allowed=True,
                    grant_pause_allowed=True,
                    admin_suspended=False):
    """
    Evaluates whether an active grant can transition to PAUSED.
    
    Returns tuple: (is_allowed: bool, denial_reason: str or None)
    
    Denial reasons:
    - 'not_active'
    - 'admin_suspended'
    - 'global_pause_disabled'
    - 'grant_pause_disabled'
    - 'depleted'
    - 'calendar_expired'
    - 'pause_limit_reached'
    - 'below_min_balance'
    - 'above_max_balance'
    """
    if grant_state != 'ACTIVE':
        return False, 'not_active'

    if admin_suspended:
        return False, 'admin_suspended'

    if not global_pause_allowed:
        return False, 'global_pause_disabled'

    if not grant_pause_allowed:
        return False, 'grant_pause_disabled'

    if remaining_seconds <= 0:
        return False, 'depleted'

    if valid_until_utc is not None and now_utc >= valid_until_utc:
        return False, 'calendar_expired'

    # Count gate: finite N requires C < N
    if pause_count_max is not None:
        if pause_count_used >= pause_count_max:
            return False, 'pause_limit_reached'

    # Balance window (inclusive)
    if min_balance_sec is not None and min_balance_sec > 0:
        if remaining_seconds < min_balance_sec:
            return False, 'below_min_balance'

    if max_balance_sec is not None and max_balance_sec > 0:
        if remaining_seconds > max_balance_sec:
            return False, 'above_max_balance'

    return True, None


def calculate_pause_deadlines(now_utc, pause_duration_sec=DEFAULT_PAUSE_DURATION_SEC, valid_until_utc=None):
    """
    Calculate pause deadlines and determines the next immediate event.
    Returns dict:
    {
        'pause_deadline_utc': int or None,
        'effective_next_deadline_utc': int or None,
        'next_event_type': 'resume' | 'expire' | 'none'
    }
    """
    pause_deadline = None
    if pause_duration_sec and pause_duration_sec > 0:
        pause_deadline = now_utc + pause_duration_sec

    if pause_deadline is None and valid_until_utc is None:
        return {
            'pause_deadline_utc': None,
            'effective_next_deadline_utc': None,
            'next_event_type': 'none'
        }

    if pause_deadline is None:
        return {
            'pause_deadline_utc': None,
            'effective_next_deadline_utc': valid_until_utc,
            'next_event_type': 'expire'
        }

    if valid_until_utc is None:
        return {
            'pause_deadline_utc': pause_deadline,
            'effective_next_deadline_utc': pause_deadline,
            'next_event_type': 'resume'
        }

    # Both deadlines exist: earlier one wins. If tie, expiry wins.
    if valid_until_utc <= pause_deadline:
        return {
            'pause_deadline_utc': pause_deadline,
            'effective_next_deadline_utc': valid_until_utc,
            'next_event_type': 'expire'
        }
    else:
        return {
            'pause_deadline_utc': pause_deadline,
            'effective_next_deadline_utc': pause_deadline,
            'next_event_type': 'resume'
        }


def calculate_full_use_slack(remaining_seconds, valid_until_utc, now_utc):
    """
    Calculates remaining calendar slack: W - R.
    W = max(0, valid_until_utc - now_utc)
    If W < R: returns negative slack (impossible to burn all credit).
    If valid_until_utc is None: returns infinity (float('inf')).
    """
    if valid_until_utc is None:
        return float('inf'), True

    window = max(0, valid_until_utc - now_utc)
    slack = window - remaining_seconds
    can_fully_use = (slack >= 0)
    return slack, can_fully_use


def calculate_max_nominal_pause_allowance(pause_count_used, pause_count_max=DEFAULT_PAUSE_COUNT_MAX,
                                          pause_duration_sec=DEFAULT_PAUSE_DURATION_SEC):
    """
    Theoretical maximum non-consuming pause time that can still be taken in the future.
    S_count = max(0, N - C) * P
    If N is None (unlimited): returns infinity.
    """
    if pause_count_max is not None and pause_count_used >= pause_count_max:
        return 0
    if pause_count_max is None or pause_duration_sec is None:
        return float('inf')
    pauses_left = max(0, pause_count_max - pause_count_used)
    return pauses_left * pause_duration_sec


def seconds_until_pausable_by_max(remaining_seconds, max_balance_sec):
    """
    If balance is currently above max_balance_sec, calculate how many
    active seconds must be consumed before pausing is allowed.
    Returns 0 if already eligible.
    """
    if max_balance_sec is None or max_balance_sec <= 0:
        return 0
    if remaining_seconds <= max_balance_sec:
        return 0
    return remaining_seconds - max_balance_sec


def validate_brackets(brackets):
    """
    Validates a set of bracket policies.
    Returns (True, None) if valid, or (False, error_reason).
    Checks:
    - positive duration ceilings and expirations
    - ascending/unique ceilings
    """
    if not isinstance(brackets, list):
        return False, 'invalid_format'
    
    seen_ceilings = set()
    for b in brackets:
        if not isinstance(b, dict) or not isinstance(b.get('enabled', True), bool):
            return False, 'invalid_bracket'
        val = b.get('value'); exp = b.get('expiration')
        if isinstance(val, bool) or isinstance(exp, bool) or not isinstance(val, int) or not isinstance(exp, int):
            return False, 'non_integer_bracket'
        if val <= 0 or exp <= 0 or max(val,exp)*60 > 315360000:
            return False, 'invalid_bracket_duration'
            
        if val in seen_ceilings:
            return False, 'duplicate_ceiling'
            
        seen_ceilings.add(val)
        
    return True, None
