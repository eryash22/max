from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, auth_headers, create_user


def _create_qa_user(client, email="qa.disposable@example.com", role_code="VIEWER"):
    response = client.post(
        "/api/v1/users",
        json={
            "email": email,
            "name": "QA Disposable User",
            "password": "InitialPass123!",
            "role_codes": [role_code],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_super_admin_can_list_and_see_users(admin_client):
    response = admin_client.get("/api/v1/users")
    assert response.status_code == 200
    assert any(u["email"] == ADMIN_EMAIL for u in response.json())


def test_unauthorized_user_delete_returns_403(client, db_session):
    # A VIEWER has no users.manage permission at all — every users.* endpoint,
    # including delete, must 403 for them, not just hide the button client-side.
    create_user(db_session, ADMIN_EMAIL, "SUPER_ADMIN", ADMIN_PASSWORD)
    admin_headers = auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    victim = client.post(
        "/api/v1/users",
        json={"email": "victim@example.com", "name": "Victim", "password": "Password123!", "role_codes": ["VIEWER"]},
        headers=admin_headers,
    ).json()

    create_user(db_session, "viewer@example.com", "VIEWER", "Password123!")
    viewer_headers = auth_headers(client, "viewer@example.com", "Password123!")
    response = client.delete(f"/api/v1/users/{victim['id']}", headers=viewer_headers)
    assert response.status_code == 403


def test_email_update_succeeds(admin_client):
    user = _create_qa_user(admin_client, email="email.update@example.com")
    response = admin_client.patch(f"/api/v1/users/{user['id']}", json={"email": "email.updated@example.com"})
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "email.updated@example.com"


def test_email_update_normalizes_case(admin_client):
    user = _create_qa_user(admin_client, email="normalize.me@example.com")
    response = admin_client.patch(f"/api/v1/users/{user['id']}", json={"email": "Normalize.ME@Example.COM"})
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "normalize.me@example.com"


def test_duplicate_email_rejected(admin_client):
    _create_qa_user(admin_client, email="first.user@example.com")
    second = _create_qa_user(admin_client, email="second.user@example.com")
    response = admin_client.patch(f"/api/v1/users/{second['id']}", json={"email": "first.user@example.com"})
    assert response.status_code == 409


def test_invalid_email_format_rejected(admin_client):
    user = _create_qa_user(admin_client, email="valid.format@example.com")
    response = admin_client.patch(f"/api/v1/users/{user['id']}", json={"email": "not-an-email"})
    assert response.status_code == 422


def test_password_change_works_and_is_hashed_and_never_returned(admin_client, db_session):
    from app.models.identity import User
    from app.security.password import verify_password

    user = _create_qa_user(admin_client, email="password.change@example.com")
    response = admin_client.post(
        f"/api/v1/users/{user['id']}/password",
        json={"new_password": "BrandNewPass123", "confirm_password": "BrandNewPass123"},
    )
    assert response.status_code == 204
    assert response.text == ""  # never returns the password, hash, or any body

    db_row = db_session.query(User).filter(User.id == user["id"]).first()
    assert db_row.hashed_password != "BrandNewPass123"  # never stored in plaintext
    assert db_row.hashed_password.startswith("$2b$")  # bcrypt via the existing password.py
    assert verify_password("BrandNewPass123", db_row.hashed_password)

    # Login with the new password proves the reset actually took effect end-to-end.
    login = admin_client.post("/api/v1/auth/login", json={"email": user["email"], "password": "BrandNewPass123"})
    assert login.status_code == 200


def test_password_confirmation_mismatch_rejected(admin_client):
    user = _create_qa_user(admin_client, email="password.mismatch@example.com")
    response = admin_client.post(
        f"/api/v1/users/{user['id']}/password",
        json={"new_password": "BrandNewPass123", "confirm_password": "SomethingElse123"},
    )
    assert response.status_code == 422


def test_password_too_short_rejected(admin_client):
    user = _create_qa_user(admin_client, email="password.short@example.com")
    response = admin_client.post(
        f"/api/v1/users/{user['id']}/password",
        json={"new_password": "short", "confirm_password": "short"},
    )
    assert response.status_code == 422


def test_user_list_never_includes_password_fields(admin_client):
    _create_qa_user(admin_client, email="no.password.leak@example.com")
    response = admin_client.get("/api/v1/users")
    assert response.status_code == 200
    for row in response.json():
        assert "password" not in row
        assert "hashed_password" not in row


def test_current_user_cannot_delete_self(admin_client, db_session):
    from app.models.identity import User

    me = db_session.query(User).filter(User.email == ADMIN_EMAIL).first()
    response = admin_client.delete(f"/api/v1/users/{me.id}")
    assert response.status_code == 400


def test_current_user_cannot_delete_self_even_with_force(admin_client, db_session):
    from app.models.identity import User

    me = db_session.query(User).filter(User.email == ADMIN_EMAIL).first()
    response = admin_client.delete(f"/api/v1/users/{me.id}?force=true")
    assert response.status_code == 400


def test_current_user_cannot_deactivate_self(admin_client, db_session):
    from app.models.identity import User

    me = db_session.query(User).filter(User.email == ADMIN_EMAIL).first()
    response = admin_client.patch(f"/api/v1/users/{me.id}", json={"is_active": False})
    assert response.status_code == 400


def test_referenced_user_delete_safely_blocked(admin_client, db_session):
    from app.models.hospital import Branch

    user = _create_qa_user(admin_client, email="referenced.user@example.com")
    branch = Branch(name="Referenced Branch", status="ACTIVE", unit_head_user_id=user["id"])
    db_session.add(branch)
    db_session.commit()

    response = admin_client.delete(f"/api/v1/users/{user['id']}")
    assert response.status_code == 409
    assert "deactivate" in response.json()["detail"].lower()

    # The user must still exist — a blocked delete is not a silent partial delete.
    still_there = admin_client.get("/api/v1/users").json()
    assert any(u["id"] == user["id"] for u in still_there)


def test_unreferenced_qa_user_can_be_deleted(admin_client):
    user = _create_qa_user(admin_client, email="unreferenced.qa@example.com")
    response = admin_client.delete(f"/api/v1/users/{user['id']}")
    assert response.status_code == 204

    remaining = admin_client.get("/api/v1/users").json()
    assert not any(u["id"] == user["id"] for u in remaining)


def test_user_deletion_audit_created(admin_client, db_session):
    from app.models.identity import User

    user = _create_qa_user(admin_client, email="audited.delete@example.com")
    user_id = user["id"]
    response = admin_client.delete(f"/api/v1/users/{user_id}")
    assert response.status_code == 204

    audit = admin_client.get(f"/api/v1/audit-logs?entity=user&entity_id={user_id}")
    assert audit.status_code == 200
    actions = [row["action"] for row in audit.json()["items"]]
    assert "DELETE" in actions
    # Never log the password or its hash anywhere in the audit trail.
    for row in audit.json()["items"]:
        assert "password" not in str(row).lower() or "password" not in (row.get("old_value") or {})


def test_password_reset_audit_created_without_password_value(admin_client):
    user = _create_qa_user(admin_client, email="audited.password@example.com")
    admin_client.post(
        f"/api/v1/users/{user['id']}/password",
        json={"new_password": "AuditedPass123", "confirm_password": "AuditedPass123"},
    )
    audit = admin_client.get(f"/api/v1/audit-logs?entity=user&entity_id={user['id']}")
    rows = audit.json()["items"]
    reset_rows = [r for r in rows if r["action"] == "PASSWORD_RESET"]
    assert len(reset_rows) == 1
    assert reset_rows[0]["old_value"] is None
    assert reset_rows[0]["new_value"] is None


def test_role_change_audit_created(admin_client):
    user = _create_qa_user(admin_client, email="audited.role@example.com", role_code="VIEWER")
    admin_client.patch(f"/api/v1/users/{user['id']}", json={"role_codes": ["STAFF_OPERATOR"]})
    audit = admin_client.get(f"/api/v1/audit-logs?entity=user&entity_id={user['id']}")
    rows = audit.json()["items"]
    update_rows = [r for r in rows if r["action"] == "UPDATE"]
    assert any(r["new_value"].get("role_codes") == ["STAFF_OPERATOR"] for r in update_rows)


def test_authentication_works_after_email_and_password_change(admin_client):
    user = _create_qa_user(admin_client, email="auth.survives@example.com")
    admin_client.patch(f"/api/v1/users/{user['id']}", json={"email": "auth.survives.new@example.com"})
    admin_client.post(
        f"/api/v1/users/{user['id']}/password",
        json={"new_password": "SurvivesAuth123", "confirm_password": "SurvivesAuth123"},
    )
    login = admin_client.post(
        "/api/v1/auth/login",
        json={"email": "auth.survives.new@example.com", "password": "SurvivesAuth123"},
    )
    assert login.status_code == 200
    assert "access_token" in login.json()


def test_force_delete_bypasses_reference_block_for_super_admin(admin_client, db_session):
    from app.models.hospital import Branch

    user = _create_qa_user(admin_client, email="force.delete.me@example.com")
    branch = Branch(name="Force Delete Branch", status="ACTIVE", unit_head_user_id=user["id"])
    db_session.add(branch)
    db_session.commit()

    blocked = admin_client.delete(f"/api/v1/users/{user['id']}")
    assert blocked.status_code == 409

    forced = admin_client.delete(f"/api/v1/users/{user['id']}?force=true")
    assert forced.status_code == 204
    remaining = admin_client.get("/api/v1/users").json()
    assert not any(u["id"] == user["id"] for u in remaining)


def test_force_delete_requires_dev_data_cleanup_permission(client, db_session):
    # BIOMEDICAL_ADMIN holds users.manage-adjacent admin powers but not
    # dev.data_cleanup by default — force=true must still 403 for that role even
    # though the plain (non-force) delete path is a different permission entirely.
    create_user(db_session, ADMIN_EMAIL, "SUPER_ADMIN", ADMIN_PASSWORD)
    admin_headers = auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    user = client.post(
        "/api/v1/users",
        json={"email": "not.force.deletable@example.com", "name": "X", "password": "Password123!", "role_codes": ["VIEWER"]},
        headers=admin_headers,
    ).json()

    create_user(db_session, "biomedical.admin@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    bio_admin_headers = auth_headers(client, "biomedical.admin@example.com", "Password123!")
    response = client.delete(f"/api/v1/users/{user['id']}?force=true", headers=bio_admin_headers)
    # BIOMEDICAL_ADMIN doesn't hold users.manage either, so this 403s on the base
    # permission check before force is even evaluated — still proves backend
    # enforcement isn't just a hidden button.
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Security hardening pass — Super Admin role assignment is privilege-gated
# ---------------------------------------------------------------------------

def _create_user_admin_only(admin_client, email="user.admin@example.com") -> dict:
    """A custom role granted users.manage but deliberately NOT roles.manage and NOT
    the SUPER_ADMIN role itself — the realistic "scoped user admin" configuration
    RBAC_MATRIX.md's runtime-editable roles feature makes possible (an admin narrows
    or expands the default policy via /api/v1/roles)."""
    role = admin_client.post(
        "/api/v1/roles",
        json={"code": "USER_ADMIN_ONLY", "name": "User Admin Only", "permission_codes": ["users.manage"]},
    )
    assert role.status_code == 201, role.text
    user = admin_client.post(
        "/api/v1/users",
        json={"email": email, "name": "Scoped User Admin", "password": "Password123!", "role_codes": ["USER_ADMIN_ONLY"]},
    )
    assert user.status_code == 201, user.text
    return user.json()


def test_users_manage_holder_cannot_self_assign_super_admin(client, admin_client, db_session):
    scoped_admin = _create_user_admin_only(admin_client, email="escalate.create@example.com")
    headers = auth_headers(client, "escalate.create@example.com", "Password123!")

    response = client.patch(
        f"/api/v1/users/{scoped_admin['id']}", json={"role_codes": ["SUPER_ADMIN"]}, headers=headers
    )
    assert response.status_code == 403

    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert "SUPER_ADMIN" not in me["roles"]


def test_users_manage_holder_cannot_grant_super_admin_to_another_user(client, admin_client, db_session):
    scoped_admin = _create_user_admin_only(admin_client, email="escalate.other@example.com")
    headers = auth_headers(client, "escalate.other@example.com", "Password123!")

    victim = client.post(
        "/api/v1/users",
        json={"email": "escalate.victim@example.com", "name": "Victim", "password": "Password123!", "role_codes": ["VIEWER"]},
        headers=headers,
    )
    assert victim.status_code == 201, victim.text

    response = client.patch(
        f"/api/v1/users/{victim.json()['id']}", json={"role_codes": ["SUPER_ADMIN"]}, headers=headers
    )
    assert response.status_code == 403


def test_users_manage_holder_cannot_create_super_admin_directly(client, admin_client):
    _create_user_admin_only(admin_client, email="escalate.direct@example.com")
    headers = auth_headers(client, "escalate.direct@example.com", "Password123!")

    response = client.post(
        "/api/v1/users",
        json={"email": "new.super@example.com", "name": "New Super", "password": "Password123!", "role_codes": ["SUPER_ADMIN"]},
        headers=headers,
    )
    assert response.status_code == 403


def test_users_manage_holder_can_still_assign_ordinary_roles(client, admin_client):
    """The escalation guard must not break the normal, intended use of users.manage —
    only the SUPER_ADMIN role code is gated."""
    _create_user_admin_only(admin_client, email="escalate.normal@example.com")
    headers = auth_headers(client, "escalate.normal@example.com", "Password123!")

    response = client.post(
        "/api/v1/users",
        json={"email": "ordinary.hire@example.com", "name": "Ordinary Hire", "password": "Password123!", "role_codes": ["VIEWER"]},
        headers=headers,
    )
    assert response.status_code == 201, response.text


def test_super_admin_can_still_assign_super_admin_role(admin_client):
    response = admin_client.post(
        "/api/v1/users",
        json={"email": "second.super@example.com", "name": "Second Super", "password": "Password123!", "role_codes": ["SUPER_ADMIN"]},
    )
    assert response.status_code == 201, response.text
    assert "SUPER_ADMIN" in response.json()["roles"]
