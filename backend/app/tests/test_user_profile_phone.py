"""User profile: the generic `phone` field, and the guards around profile editing.

`whatsapp_number` already existed but is the WhatsApp delivery address
communication_service actually sends to, so it can't double as a general contact
number. `phone` is added alongside it (migration 0024), never replacing it.

Also pins the two things that must not regress while the profile form grows: no
credential material ever leaves through UserOut, and branch/department/role are only
settable to real, permitted values.
"""
from app.models.identity import Role, User

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, auth_headers, create_user

PASSWORD = "Password123!"


def _branch(admin_client, name):
    response = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"})
    assert response.status_code == 201, response.text
    return response.json()


def _create_user(admin_client, email, **extra):
    payload = {"email": email, "name": "QA Profile User", "password": PASSWORD, "role_codes": ["VIEWER"], **extra}
    response = admin_client.post("/api/v1/users", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ---- phone round-trip ----

def test_phone_round_trips_through_create_read_update(admin_client):
    created = _create_user(admin_client, "profile.phone@example.com", phone="+91 90000 00001")
    assert created["phone"] == "+91 90000 00001"

    listed = admin_client.get("/api/v1/users").json()
    row = next(u for u in listed if u["email"] == "profile.phone@example.com")
    assert row["phone"] == "+91 90000 00001"

    updated = admin_client.patch(f"/api/v1/users/{created['id']}", json={"phone": "+91 90000 00002"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["phone"] == "+91 90000 00002"


def test_phone_and_whatsapp_number_are_independent(admin_client):
    """The whole reason for a separate column: changing one must not touch the other,
    or WhatsApp delivery would silently start going somewhere else."""
    created = _create_user(admin_client, "profile.both@example.com", phone="+91 11111 11111")
    admin_client.patch(f"/api/v1/users/{created['id']}", json={"whatsapp_number": "+91 22222 22222"})

    fetched = next(u for u in admin_client.get("/api/v1/users").json() if u["id"] == created["id"])
    assert fetched["phone"] == "+91 11111 11111"
    assert fetched["whatsapp_number"] == "+91 22222 22222"

    admin_client.patch(f"/api/v1/users/{created['id']}", json={"phone": "+91 33333 33333"})
    refetched = next(u for u in admin_client.get("/api/v1/users").json() if u["id"] == created["id"])
    assert refetched["whatsapp_number"] == "+91 22222 22222", "editing phone must not disturb WhatsApp delivery"


def test_phone_defaults_to_null_when_not_supplied(admin_client):
    created = _create_user(admin_client, "profile.nophone@example.com")
    assert created["phone"] is None


# ---- credential material never leaks ----

def test_user_out_never_exposes_password_material(admin_client):
    created = _create_user(admin_client, "profile.secret@example.com", phone="+91 90000 00009")
    for payload in (created, admin_client.get("/api/v1/users").json()):
        blob = str(payload).lower()
        for forbidden in ("password", "hashed_password", "hash", "token_version"):
            assert forbidden not in blob, f"{forbidden} must never appear in UserOut"


# ---- authorization ----

def test_non_admin_cannot_edit_users(client, db_session, admin_client):
    target = _create_user(admin_client, "profile.target@example.com")
    create_user(db_session, "profile.viewer@example.com", "VIEWER", PASSWORD)
    headers = auth_headers(client, "profile.viewer@example.com", PASSWORD)

    response = client.patch(f"/api/v1/users/{target['id']}", json={"phone": "+91 00000 00000"}, headers=headers)
    assert response.status_code == 403, response.text


def test_forged_branch_id_is_rejected(admin_client):
    """hospital_scope_id decides the user's entire data scope, so an unknown id must be
    a clean 400 rather than reaching the database."""
    created = _create_user(admin_client, "profile.branch@example.com")
    response = admin_client.patch(
        f"/api/v1/users/{created['id']}", json={"hospital_scope_id": "not-a-real-branch-id"}
    )
    assert response.status_code == 400, response.text
    assert "branch" in response.text.lower()


def test_forged_department_id_is_rejected(admin_client):
    created = _create_user(admin_client, "profile.dept@example.com")
    response = admin_client.patch(
        f"/api/v1/users/{created['id']}", json={"department_id": "not-a-real-department-id"}
    )
    assert response.status_code == 400, response.text
    assert "department" in response.text.lower()


def test_valid_branch_change_is_applied(admin_client):
    branch = _branch(admin_client, "Profile Branch")
    created = _create_user(admin_client, "profile.movebranch@example.com")
    response = admin_client.patch(f"/api/v1/users/{created['id']}", json={"hospital_scope_id": branch["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["hospital_scope_id"] == branch["id"]


def test_non_super_admin_cannot_grant_super_admin(client, db_session, admin_client):
    """Pre-existing escalation guard must survive the profile changes."""
    target = _create_user(admin_client, "profile.escalate@example.com")

    role = db_session.query(Role).filter(Role.code == "VIEWER").first()
    codes = sorted({rp.permission.code for rp in role.permissions} | {"users.manage"})
    admin_client.patch(f"/api/v1/roles/{role.id}", json={"permission_codes": codes})

    create_user(db_session, "profile.useradmin@example.com", "VIEWER", PASSWORD)
    headers = auth_headers(client, "profile.useradmin@example.com", PASSWORD)

    response = client.patch(
        f"/api/v1/users/{target['id']}", json={"role_codes": ["SUPER_ADMIN"]}, headers=headers
    )
    assert response.status_code == 403, response.text


# ---- audit ----

def test_branch_and_phone_changes_are_audited(admin_client, db_session):
    """Branch is security-sensitive — it is what branch_scope_id() reads — so a change
    to it has to be reconstructable from the audit trail. It previously was not
    recorded at all."""
    from app.models.audit import AuditLog

    branch = _branch(admin_client, "Audit Branch")
    created = _create_user(admin_client, "profile.audit@example.com", phone="+91 55555 00000")

    response = admin_client.patch(
        f"/api/v1/users/{created['id']}",
        json={"phone": "+91 55555 11111", "hospital_scope_id": branch["id"]},
    )
    assert response.status_code == 200, response.text

    log = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity == "user", AuditLog.entity_id == created["id"], AuditLog.action == "UPDATE")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert log is not None, "a user profile update must be audited"
    assert log.old_value["phone"] == "+91 55555 00000"
    assert log.new_value["phone"] == "+91 55555 11111"
    assert log.old_value["hospital_scope_id"] is None
    assert log.new_value["hospital_scope_id"] == branch["id"]
    # And the audit row itself must never carry credential material.
    assert "password" not in str(log.new_value).lower()
