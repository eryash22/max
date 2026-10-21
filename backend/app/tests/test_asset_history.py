from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _make_asset(admin_client, asset_number="AST-HIST-001"):
    branch = admin_client.post("/api/v1/branches", json={"name": f"Branch-{asset_number}", "status": "ACTIVE"}).json()
    model = admin_client.post(
        "/api/v1/device-models", json={"name": f"Model-{asset_number}", "category": "Ventilator"}
    ).json()
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": model["id"], "branch_id": branch["id"], "status": "Pending"},
    ).json()
    return branch, model, asset


def _engineer_headers(client, db_session, email="eng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


# ---- Auto-logged history on asset PATCH ----

def test_status_change_auto_logs_history(admin_client):
    _, _, asset = _make_asset(admin_client)
    response = admin_client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={**{k: asset[k] for k in ("asset_number", "device_model_id", "branch_id")}, "status": "In Service"},
    )
    assert response.status_code == 200

    history = admin_client.get(f"/api/v1/assets/{asset['id']}/history").json()
    assert history["total"] == 1
    entry = history["items"][0]
    assert entry["event_type"] == "STATUS_CHANGE"
    assert entry["from_value"] == "Pending"
    assert entry["to_value"] == "In Service"
    assert entry["asset_id"] == asset["id"]


def test_branch_transfer_auto_logs_history(admin_client):
    _, model, asset = _make_asset(admin_client)
    new_branch = admin_client.post("/api/v1/branches", json={"name": "New Branch", "status": "ACTIVE"}).json()

    response = admin_client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"asset_number": asset["asset_number"], "device_model_id": model["id"], "branch_id": new_branch["id"], "status": "Pending"},
    )
    assert response.status_code == 200

    history = admin_client.get(f"/api/v1/assets/{asset['id']}/history").json()
    event_types = {item["event_type"] for item in history["items"]}
    assert "BRANCH_TRANSFER" in event_types


def test_no_op_update_does_not_log_history(admin_client):
    _, _, asset = _make_asset(admin_client)
    response = admin_client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={k: asset[k] for k in ("asset_number", "device_model_id", "branch_id", "status")},
    )
    assert response.status_code == 200
    history = admin_client.get(f"/api/v1/assets/{asset['id']}/history").json()
    assert history["total"] == 0


def test_history_records_audit_log(admin_client, db_session):
    _, _, asset = _make_asset(admin_client)
    admin_client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"asset_number": asset["asset_number"], "device_model_id": asset["device_model_id"],
              "branch_id": asset["branch_id"], "status": "Decommissioned"},
    )
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "asset").all()
    assert any(log.action == "UPDATE" for log in logs)


# ---- Manual history entry ----

def test_manual_history_entry_create(admin_client):
    _, _, asset = _make_asset(admin_client)
    response = admin_client.post(
        f"/api/v1/assets/{asset['id']}/history",
        json={"event_type": "LIFECYCLE_TRANSITION", "from_value": "PROCUREMENT", "to_value": "COMMISSIONED", "notes": "Handover complete"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["event_type"] == "LIFECYCLE_TRANSITION"
    assert body["notes"] == "Handover complete"
    assert body["changed_by"] is not None


def test_manual_history_entry_records_audit_log(admin_client, db_session):
    _, _, asset = _make_asset(admin_client)
    admin_client.post(
        f"/api/v1/assets/{asset['id']}/history",
        json={"event_type": "OWNERSHIP_CHANGE", "from_value": "Vendor", "to_value": "Hospital"},
    )
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "asset_history").all()
    assert len(logs) == 1
    assert logs[0].action == "CREATE"


def test_manual_history_entry_invalid_event_type_rejected(admin_client):
    _, _, asset = _make_asset(admin_client)
    response = admin_client.post(
        f"/api/v1/assets/{asset['id']}/history",
        json={"event_type": "NOT_A_REAL_EVENT"},
    )
    assert response.status_code == 422


def test_manual_history_entry_requires_existing_asset(admin_client):
    response = admin_client.post(
        "/api/v1/assets/does-not-exist/history",
        json={"event_type": "LIFECYCLE_TRANSITION"},
    )
    assert response.status_code == 404


def test_manual_history_entry_validates_branch_fk(admin_client):
    _, _, asset = _make_asset(admin_client)
    response = admin_client.post(
        f"/api/v1/assets/{asset['id']}/history",
        json={"event_type": "BRANCH_TRANSFER", "branch_id": "does-not-exist"},
    )
    assert response.status_code == 404


# ---- List / filter / pagination ----

def test_history_list_requires_existing_asset(admin_client):
    response = admin_client.get("/api/v1/assets/does-not-exist/history")
    assert response.status_code == 404


def test_history_list_filters_by_event_type(admin_client):
    _, _, asset = _make_asset(admin_client)
    admin_client.post(f"/api/v1/assets/{asset['id']}/history", json={"event_type": "LIFECYCLE_TRANSITION"})
    admin_client.post(f"/api/v1/assets/{asset['id']}/history", json={"event_type": "OWNERSHIP_CHANGE"})

    response = admin_client.get(f"/api/v1/assets/{asset['id']}/history", params={"event_type": "OWNERSHIP_CHANGE"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["event_type"] == "OWNERSHIP_CHANGE"


def test_history_list_pagination(admin_client):
    _, _, asset = _make_asset(admin_client)
    for _ in range(5):
        admin_client.post(f"/api/v1/assets/{asset['id']}/history", json={"event_type": "LIFECYCLE_TRANSITION"})

    response = admin_client.get(f"/api/v1/assets/{asset['id']}/history", params={"page": 1, "page_size": 2})
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2


def test_history_scoped_to_asset(admin_client):
    _, _, asset_a = _make_asset(admin_client, "AST-HIST-A")
    _, _, asset_b = _make_asset(admin_client, "AST-HIST-B")
    admin_client.post(f"/api/v1/assets/{asset_a['id']}/history", json={"event_type": "LIFECYCLE_TRANSITION"})

    response_a = admin_client.get(f"/api/v1/assets/{asset_a['id']}/history").json()
    response_b = admin_client.get(f"/api/v1/assets/{asset_b['id']}/history").json()
    assert response_a["total"] == 1
    assert response_b["total"] == 0


# ---- RBAC ----

def test_engineer_can_read_and_write_asset_history(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)

    write = client.post(
        f"/api/v1/assets/{asset['id']}/history", json={"event_type": "LIFECYCLE_TRANSITION"}, headers=headers
    )
    assert write.status_code == 201
    read = client.get(f"/api/v1/assets/{asset['id']}/history", headers=headers)
    assert read.status_code == 200


def test_doctor_cannot_write_asset_history(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")

    response = client.post(
        f"/api/v1/assets/{asset['id']}/history", json={"event_type": "LIFECYCLE_TRANSITION"}, headers=headers
    )
    assert response.status_code == 403


def test_doctor_can_read_asset_history(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")

    response = client.get(f"/api/v1/assets/{asset['id']}/history", headers=headers)
    assert response.status_code == 200
