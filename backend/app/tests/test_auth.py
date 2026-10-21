from app.models.audit import AuditLog

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, auth_headers, create_user


def test_login_succeeds_with_correct_password(client, db_session):
    create_user(db_session, ADMIN_EMAIL, "SUPER_ADMIN", ADMIN_PASSWORD)
    response = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body and body["access_token"]
    assert body.get("token_type") == "bearer"


def test_login_fails_with_wrong_password(client, db_session):
    create_user(db_session, ADMIN_EMAIL, "SUPER_ADMIN", ADMIN_PASSWORD)
    response = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"})
    assert response.status_code == 401


def test_inactive_user_cannot_login(client, db_session):
    """Login-regression investigation — confirms the existing is_active gate in
    services/auth_service.authenticate() (unrelated to the branch-scoping/RBAC
    commit) still rejects a deactivated account with a 401, same as a wrong
    password, rather than a 500 or a misleading success."""
    user = create_user(db_session, "inactive.login@example.com", "STAFF_OPERATOR", "Password123!")
    user.is_active = False
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login", json={"email": "inactive.login@example.com", "password": "Password123!"}
    )
    assert response.status_code == 401


def test_login_token_works_immediately_and_loads_me(client, db_session):
    """Login-regression investigation — a freshly issued token must be usable on
    the very next request with no extra round trip (no cache warm-up, no delayed
    permission materialization)."""
    create_user(db_session, "immediate.login@example.com", "STAFF_OPERATOR", "Password123!")
    login_response = client.post(
        "/api/v1/auth/login", json={"email": "immediate.login@example.com", "password": "Password123!"}
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "immediate.login@example.com"


def test_unit_head_and_tl_can_login_and_load_permissions(client, db_session):
    """Login-regression investigation — the branch-scoping/RBAC commit (device.read
    -> device.read_picker swap, migration 0022) touched UNIT_HEAD/TL's permission
    bundle only; confirms it did not break authentication or /me for those roles."""
    for role_code, email in [("UNIT_HEAD", "login.unithead@example.com"), ("TL", "login.tl@example.com")]:
        create_user(db_session, email, role_code, "Password123!")
        headers = auth_headers(client, email, "Password123!")
        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        body = me.json()
        assert role_code in body["roles"]
        assert "device.read_picker" in body["permissions"]
        assert "device.read" not in body["permissions"]


def test_password_is_not_stored_in_plaintext(client, db_session):
    user = create_user(db_session, ADMIN_EMAIL, "SUPER_ADMIN", ADMIN_PASSWORD)
    assert user.hashed_password != ADMIN_PASSWORD
    assert user.hashed_password.startswith("$2b$")


def test_me_requires_bearer_token(client):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_returns_roles_and_permissions(admin_client):
    response = admin_client.get("/api/v1/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert "SUPER_ADMIN" in body["roles"]
    assert "hospital.write" in body["permissions"]


# ---------------------------------------------------------------------------
# Module 19 — self-service contact fields (PATCH /api/v1/auth/me)
# ---------------------------------------------------------------------------

def test_update_me_sets_own_contact_fields(admin_client, db_session):
    response = admin_client.patch(
        "/api/v1/auth/me",
        json={"whatsapp_number": "+91 91234 56789", "notification_preference": "WHATSAPP"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["whatsapp_number"] == "+91 91234 56789"
    assert body["notification_preference"] == "WHATSAPP"

    me = admin_client.get("/api/v1/auth/me").json()
    assert me["whatsapp_number"] == "+91 91234 56789"
    assert me["notification_preference"] == "WHATSAPP"


def test_update_me_rejects_invalid_notification_preference(admin_client):
    response = admin_client.patch("/api/v1/auth/me", json={"notification_preference": "CARRIER_PIGEON"})
    assert response.status_code == 422


def test_update_me_cannot_touch_another_user(client, db_session):
    """There is no user_id parameter on PATCH /auth/me — it structurally can only ever
    touch the caller's own row. Two users each set their own value; neither sees the
    other's leak through."""
    create_user(db_session, "self.a@example.com", "STAFF_OPERATOR", "Password123!")
    create_user(db_session, "self.b@example.com", "STAFF_OPERATOR", "Password123!")
    headers_a = auth_headers(client, "self.a@example.com", "Password123!")
    headers_b = auth_headers(client, "self.b@example.com", "Password123!")

    client.patch("/api/v1/auth/me", json={"whatsapp_number": "+1 111"}, headers=headers_a)
    client.patch("/api/v1/auth/me", json={"whatsapp_number": "+2 222"}, headers=headers_b)

    me_a = client.get("/api/v1/auth/me", headers=headers_a).json()
    me_b = client.get("/api/v1/auth/me", headers=headers_b).json()
    assert me_a["whatsapp_number"] == "+1 111"
    assert me_b["whatsapp_number"] == "+2 222"


def test_update_me_writes_audit_log(admin_client, db_session):
    admin_client.patch("/api/v1/auth/me", json={"whatsapp_number": "+91 90000 11111"})
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "user", AuditLog.action == "UPDATE").all()
    assert any(log.new_value and log.new_value.get("whatsapp_number") == "+91 90000 11111" for log in logs)


def test_non_admin_cannot_patch_another_users_record_via_users_endpoint(client, db_session):
    create_user(db_session, "victim@example.com", "STAFF_OPERATOR", "Password123!")
    attacker = create_user(db_session, "attacker@example.com", "STAFF_OPERATOR", "Password123!")
    headers = auth_headers(client, "attacker@example.com", "Password123!")
    response = client.patch(f"/api/v1/users/{attacker.id}", json={"whatsapp_number": "+9 999"}, headers=headers)
    assert response.status_code == 403


def test_admin_can_set_contact_fields_on_another_user(admin_client, db_session):
    target = create_user(db_session, "managed@example.com", "STAFF_OPERATOR", "Password123!")
    response = admin_client.patch(
        f"/api/v1/users/{target.id}",
        json={"whatsapp_number": "+91 90000 22222", "notification_preference": "BOTH"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["whatsapp_number"] == "+91 90000 22222"
    assert body["notification_preference"] == "BOTH"


# ---------------------------------------------------------------------------
# Security hardening pass — password reset invalidates prior tokens
# ---------------------------------------------------------------------------

def test_password_reset_invalidates_prior_token(client, db_session):
    """A token issued before an admin-driven password reset must stop working
    immediately (dependencies/auth.py compares the token's "tv" claim to
    User.token_version) instead of remaining valid until it naturally expires —
    otherwise an attacker holding a stolen token survives the very reset meant to
    lock them out."""
    create_user(db_session, ADMIN_EMAIL, "SUPER_ADMIN", ADMIN_PASSWORD)
    admin_headers = auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    victim = create_user(db_session, "reset.victim@example.com", "STAFF_OPERATOR", "OldPass123!")
    old_headers = auth_headers(client, "reset.victim@example.com", "OldPass123!")

    # The old token works before the reset.
    assert client.get("/api/v1/auth/me", headers=old_headers).status_code == 200

    reset = client.post(
        f"/api/v1/users/{victim.id}/password",
        json={"new_password": "NewPass456!", "confirm_password": "NewPass456!"},
        headers=admin_headers,
    )
    assert reset.status_code == 204, reset.text

    # The pre-reset token must now be rejected...
    stale_response = client.get("/api/v1/auth/me", headers=old_headers)
    assert stale_response.status_code == 401

    # ...while a fresh login with the new password issues a token that works.
    new_headers = auth_headers(client, "reset.victim@example.com", "NewPass456!")
    assert client.get("/api/v1/auth/me", headers=new_headers).status_code == 200
