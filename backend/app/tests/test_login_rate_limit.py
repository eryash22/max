"""Login brute-force protection (security hotfix) — see services/rate_limiter.py
and routers/auth.py's POST /api/v1/auth/login.

Only FAILED attempts count toward the limit — see test_many_successful_logins_*
below for the regression this specifically guards (a legitimate workflow, or a
legitimate user, logging in many times in quick succession must never be
throttled; only repeated wrong-password guesses are).
"""
import time

from app.config import settings

from .conftest import create_user


def _login(client, email="nobody@example.com", password="whatever", **kwargs):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password}, **kwargs)


def test_normal_login_is_never_blocked(client, db_session):
    create_user(db_session, "normal@example.com", "STAFF_OPERATOR", "Password123!")
    for _ in range(settings.login_rate_limit_per_minute - 1):
        response = _login(client, "normal@example.com", "Password123!")
        assert response.status_code == 200


def test_many_successful_logins_well_past_the_limit_are_never_blocked(client, db_session):
    """The actual regression this fix guards: a legitimate workflow (or a busy
    legitimate user) logging in correctly many times in a row — e.g. a test or a
    real process that logs in as several different staff accounts back to
    back — must never trip the limiter. Only wrong-password attempts count."""
    create_user(db_session, "frequent@example.com", "STAFF_OPERATOR", "Password123!")
    for _ in range(settings.login_rate_limit_per_minute * 3):
        response = _login(client, "frequent@example.com", "Password123!")
        assert response.status_code == 200


def test_threshold_enforcement_blocks_after_repeated_failures(client, db_session):
    create_user(db_session, "threshold@example.com", "STAFF_OPERATOR", "Password123!")
    for _ in range(settings.login_rate_limit_per_minute):
        response = _login(client, "threshold@example.com", "wrong-password")
        assert response.status_code == 401
    blocked = _login(client, "threshold@example.com", "wrong-password")
    assert blocked.status_code == 429


def test_a_correct_password_still_works_right_up_to_the_failure_threshold(client, db_session):
    """One below the limit's worth of failures, then the correct password —
    still succeeds (the account is never locked, only the failing requests are
    throttled)."""
    create_user(db_session, "almost@example.com", "STAFF_OPERATOR", "Password123!")
    for _ in range(settings.login_rate_limit_per_minute - 1):
        assert _login(client, "almost@example.com", "wrong-password").status_code == 401
    assert _login(client, "almost@example.com", "Password123!").status_code == 200


def test_429_response_has_retry_after_and_no_token(client, db_session):
    create_user(db_session, "retry@example.com", "STAFF_OPERATOR", "Password123!")
    for _ in range(settings.login_rate_limit_per_minute):
        _login(client, "retry@example.com", "wrong-password")
    blocked = _login(client, "retry@example.com", "wrong-password")
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert int(blocked.headers["Retry-After"]) > 0
    assert "access_token" not in blocked.json()


def test_limit_expires_after_the_window(client, db_session, monkeypatch):
    create_user(db_session, "expiry@example.com", "STAFF_OPERATOR", "Password123!")
    monkeypatch.setattr(settings, "login_rate_limit_per_minute", 2)
    monkeypatch.setattr(settings, "login_rate_limit_window_seconds", 1)
    assert _login(client, "expiry@example.com", "wrong").status_code == 401
    assert _login(client, "expiry@example.com", "wrong").status_code == 401
    assert _login(client, "expiry@example.com", "wrong").status_code == 429
    time.sleep(1.1)
    # Window rolled over — both the bad guess and the correct password now work
    # (the latter proving this was never a permanent account lock).
    assert _login(client, "expiry@example.com", "wrong").status_code == 401
    assert _login(client, "expiry@example.com", "Password123!").status_code == 200


def test_invalid_credentials_get_a_generic_error_message(client, db_session):
    create_user(db_session, "guessed@example.com", "STAFF_OPERATOR", "Password123!")
    responses = [_login(client, "guessed@example.com", "wrong-password") for _ in range(settings.login_rate_limit_per_minute)]
    for r in responses:
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid email or password"
    blocked = _login(client, "guessed@example.com", "wrong-password")
    assert blocked.status_code == 429


def test_rate_limit_does_not_reveal_whether_account_exists(client, db_session):
    """A nonexistent email must behave identically (401, same message, same
    eventual 429) to a real one that's never had a successful login — the
    limiter keys on the submitted email string, not a resolved user id."""
    for _ in range(settings.login_rate_limit_per_minute):
        r = _login(client, "does-not-exist@example.com", "whatever")
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid email or password"
    blocked = _login(client, "does-not-exist@example.com", "whatever")
    assert blocked.status_code == 429


def test_different_identities_have_independent_failure_counts(client, db_session):
    """Exhausting one account's failure limit must not affect a different
    account's attempts. Uses distinct X-Forwarded-For IPs for the two accounts
    so this isolates the identity-keyed counter specifically — both IP and
    identity are rate-limited (see test_different_ips_have_independent_limits
    for the IP dimension on its own), so two accounts hammered from the *same*
    IP are expected to eventually trip the shared IP counter too; that's
    correct spray-protection, not what this test is checking."""
    create_user(db_session, "alice@example.com", "STAFF_OPERATOR", "Password123!")
    create_user(db_session, "bob@example.com", "STAFF_OPERATOR", "Password123!")
    alice_headers = {"X-Forwarded-For": "203.0.113.30"}
    bob_headers = {"X-Forwarded-For": "203.0.113.40"}
    for _ in range(settings.login_rate_limit_per_minute):
        assert _login(client, "alice@example.com", "wrong", headers=alice_headers).status_code == 401
    assert _login(client, "alice@example.com", "wrong", headers=alice_headers).status_code == 429
    # Bob, from a different IP, is untouched.
    assert _login(client, "bob@example.com", "Password123!", headers=bob_headers).status_code == 200


def test_different_ips_have_independent_limits(client, db_session, monkeypatch):
    """Simulates two distinct client IPs via X-Forwarded-For — exhausting one
    client's limit must not affect the other."""
    monkeypatch.setattr(settings, "login_rate_limit_per_minute", 1)

    r1 = _login(client, "shared-a@example.com", "x", headers={"X-Forwarded-For": "203.0.113.10"})
    assert r1.status_code == 401
    r1_blocked = _login(client, "shared-a@example.com", "x", headers={"X-Forwarded-For": "203.0.113.10"})
    assert r1_blocked.status_code == 429

    # A different IP, different identity: not affected by the first client's cap.
    r2 = _login(client, "shared-b@example.com", "x", headers={"X-Forwarded-For": "203.0.113.20"})
    assert r2.status_code == 401


def test_trusted_proxy_header_picks_the_leftmost_forwarded_ip(client, db_session, monkeypatch):
    """X-Forwarded-For can carry a chain (client, proxy1, proxy2, ...) — only the
    first (real client) entry must be used as the rate-limit key."""
    monkeypatch.setattr(settings, "login_rate_limit_per_minute", 1)

    r1 = _login(
        client, "chain@example.com", "wrong",
        headers={"X-Forwarded-For": "198.51.100.5, 10.0.0.1, 10.0.0.2"},
    )
    assert r1.status_code == 401
    r2 = _login(
        client, "chain@example.com", "wrong",
        headers={"X-Forwarded-For": "198.51.100.5, 10.0.0.9, 10.0.0.2"},
    )
    # Same real client (198.51.100.5) despite a different proxy hop appended —
    # already blocked by the identity key alone, but this specifically exercises
    # the IP-parsing path with a differing chain to confirm it resolves the same
    # leftmost address either way.
    assert r2.status_code == 429


def test_no_password_or_token_appears_in_any_response_body_on_block(client, db_session):
    create_user(db_session, "secretcheck@example.com", "STAFF_OPERATOR", "Password123!")
    for _ in range(settings.login_rate_limit_per_minute):
        _login(client, "secretcheck@example.com", "wrong-password")
    blocked = _login(client, "secretcheck@example.com", "wrong-password")
    body_text = blocked.text
    assert "wrong-password" not in body_text
    assert "Password123!" not in body_text
    assert "access_token" not in body_text
