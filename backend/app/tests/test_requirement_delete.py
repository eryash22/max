"""Final admin/inventory/vendor/dashboard polish pass — Part 7: Super Admin
requirement delete, and a regression guard on the pre-existing self-approval
block."""

from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _make_branch(admin_client, name="Delete-Req-Branch"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_device_model(admin_client, name="Delete-Req-Model"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


def _basic_items(device_model_id):
    return [{"item_type": "DEVICE_MODEL", "device_model_id": device_model_id, "requested_quantity": 1, "unit": "UNIT"}]


def _create_requirement(client, headers, branch_id, device_model_id, **overrides):
    payload = {"branch_id": branch_id, "reason": "Delete-test requirement", "items": _basic_items(device_model_id)}
    payload.update(overrides)
    response = client.post("/api/v1/requirements", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---- Super Admin delete ----

def test_super_admin_can_delete_draft_requirement(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement = _create_requirement(admin_client, admin_client.headers, branch["id"], model["id"])

    response = admin_client.delete(f"/api/v1/requirements/{requirement['id']}")
    assert response.status_code == 204

    get_response = admin_client.get(f"/api/v1/requirements/{requirement['id']}")
    assert get_response.status_code == 404


def test_super_admin_delete_non_draft_requirement_rejected(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl@example.com", "Password123!")
    requirement = _create_requirement(client, tl_headers, branch["id"], model["id"])

    submit_response = client.post(f"/api/v1/requirements/{requirement['id']}/submit", headers=tl_headers)
    assert submit_response.status_code == 200

    response = admin_client.delete(f"/api/v1/requirements/{requirement['id']}")
    assert response.status_code == 409
    assert "cancel" in response.json()["detail"].lower()

    still_there = admin_client.get(f"/api/v1/requirements/{requirement['id']}")
    assert still_there.status_code == 200


def test_non_super_admin_cannot_delete_requirement(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement = _create_requirement(admin_client, admin_client.headers, branch["id"], model["id"])

    create_user(db_session, "mgr@example.com", "MANAGER", "Password123!")
    mgr_headers = auth_headers(client, "mgr@example.com", "Password123!")

    response = client.delete(f"/api/v1/requirements/{requirement['id']}", headers=mgr_headers)
    assert response.status_code == 403


def test_requirement_delete_not_found(admin_client):
    response = admin_client.delete("/api/v1/requirements/does-not-exist")
    assert response.status_code == 404


def test_requirement_delete_is_audited(admin_client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement = _create_requirement(admin_client, admin_client.headers, branch["id"], model["id"])

    admin_client.delete(f"/api/v1/requirements/{requirement['id']}")
    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "requirement", AuditLog.entity_id == requirement["id"], AuditLog.action == "DELETE"
    ).first()
    assert log is not None


# ---- Regression: self-approval still blocked for Super Admin ----

def test_super_admin_cannot_approve_own_requirement(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement = _create_requirement(admin_client, admin_client.headers, branch["id"], model["id"])
    admin_client.post(f"/api/v1/requirements/{requirement['id']}/submit")

    response = admin_client.post(f"/api/v1/requirements/{requirement['id']}/approve")
    assert response.status_code == 403
    assert "own" in response.json()["detail"].lower()


def test_super_admin_cannot_reject_own_requirement(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement = _create_requirement(admin_client, admin_client.headers, branch["id"], model["id"])
    admin_client.post(f"/api/v1/requirements/{requirement['id']}/submit")

    response = admin_client.post(f"/api/v1/requirements/{requirement['id']}/reject")
    assert response.status_code == 403
    assert "own" in response.json()["detail"].lower()
