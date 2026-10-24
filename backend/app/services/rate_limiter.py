"""Login brute-force protection — a small in-process, sliding-window limiter.

Not a general-purpose rate limiter and not wired into any other endpoint: this
exists specifically to slow down password-guessing against POST /api/v1/auth/login.

Only FAILED login attempts count toward the limit — a successful login is never
blocked no matter how many were made, so a legitimate user (or a legitimate
workflow that logs in as several different staff accounts in quick succession,
as several of this app's own tests do) is never at risk under normal use.
Credential-guessing is, by definition, a run of failures before any eventual
success, so counting failures alone still throttles an attacker effectively.

Why in-process and not Redis/an external store: both Render services run on the
Free plan, which is a single instance with no horizontal scaling (confirmed via
the Render dashboard — Free plans don't support multiple instances), so a
process-local counter gives real protection here. This stops being true the
moment the app ever runs with more than one worker/instance; in that case this
module's counters would need to move to a shared store (e.g. Redis) since each
instance would otherwise track attempts independently and the effective limit
would multiply by instance count. That migration is out of scope for this fix
and is called out explicitly rather than silently assumed to still work.

Windows expire on their own (sliding window), so this can never become a
permanent lockout — the limit is recent-failure frequency, not an account flag.
"""
import time
from collections import defaultdict
from threading import Lock

from ..config import settings

# key -> list of FAILED-attempt timestamps (monotonic seconds) within the
# current window. Two independent keyspaces share this store: "ip:<address>"
# and "id:<email>" — both are checked on every login attempt (see check()
# below), so an attacker is throttled whether they rotate IPs against one
# account or spray many accounts from one IP.
_attempts: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limit exceeded, retry after {retry_after_seconds}s")


def _prune(key: str, now: float, window_seconds: int) -> list[float]:
    cutoff = now - window_seconds
    kept = [t for t in _attempts[key] if t > cutoff]
    _attempts[key] = kept
    return kept


def check(keys: list[str]) -> None:
    """Call before attempting authentication. Raises RateLimitExceeded if ANY
    key already has `limit` or more recorded failures within the window.
    Records nothing — only record_failure() below does that."""
    limit = settings.login_rate_limit_per_minute
    window = settings.login_rate_limit_window_seconds
    now = time.monotonic()

    with _lock:
        max_retry_after = 0
        for key in keys:
            kept = _prune(key, now, window)
            if len(kept) >= limit:
                oldest = min(kept)
                retry_after = max(1, int(window - (now - oldest)) + 1)
                max_retry_after = max(max_retry_after, retry_after)
        if max_retry_after:
            raise RateLimitExceeded(max_retry_after)


def record_failure(keys: list[str]) -> None:
    """Call only after authentication has actually failed for this request —
    never for a successful login."""
    now = time.monotonic()
    with _lock:
        for key in keys:
            _attempts[key].append(now)


def reset_for_tests() -> None:
    """Test-only: clears all counters so isolated tests don't interfere with each
    other's windows. Never called from application code."""
    with _lock:
        _attempts.clear()
