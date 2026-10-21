from .conftest import auth_headers, create_user


def _make_branch(admin_client, name="Saket"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_department(admin_client, branch_id, name="ICU"):
    master = admin_client.post("/api/v1/department-masters", json={"name": name}).json()
    return admin_client.post(
        "/api/v1/departments", json={"branch_id": branch_id, "department_master_id": master["id"]}
    ).json()


def _make_device_model(admin_client, name="Patient Monitor"):
    return admin_client.post(
        "/api/v1/device-models", json={"name": name, "category": "MONITOR"}
    ).json()


def _make_inventory_item(admin_client, code="SP-001"):
    return admin_client.post(
        "/api/v1/inventory/items", json={"item_code": code, "name": "Sensor Cable", "is_consumable": False}
    ).json()


def _make_consumable(admin_client, code="CON-001"):
    return admin_client.post(
        "/api/v1/consumables", json={"item_code": code, "name": "Syringe", "unit": "PCS"}
    ).json()


def _basic_items(device_model_id):
    return [
        {
            "item_type": "DEVICE_MODEL",
            "device_model_id": device_model_id,
            "requested_quantity": 2,
            "unit": "UNIT",
        }
    ]


def _create_requirement(client, headers, branch_id, device_model_id, **overrides):
    payload = {
        "branch_id": branch_id,
        "reason": "ICU needs additional patient monitors",
        "items": _basic_items(device_model_id),
    }
    payload.update(overrides)
    return client.post("/api/v1/requirements", json=payload, headers=headers)


# ---------------------------------------------------------------------------
# Create / validation
# ---------------------------------------------------------------------------

def test_unit_manager_can_create_requirement(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl@example.com", "Password123!")

    response = _create_requirement(client, headers, branch["id"], model["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "DRAFT"
    assert body["request_number"].startswith("REQ-")
    assert len(body["items"]) == 1
    assert body["items"][0]["requested_quantity"] == 2


def test_create_requirement_with_multiple_items(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    item = _make_inventory_item(admin_client)
    consumable = _make_consumable(admin_client)
    create_user(db_session, "tl2@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl2@example.com", "Password123!")

    response = _create_requirement(
        client, headers, branch["id"], model["id"],
        items=[
            {"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 2},
            {"item_type": "SPARE_PART", "inventory_item_id": item["id"], "requested_quantity": 10, "unit": "PCS"},
            {"item_type": "CONSUMABLE", "consumable_id": consumable["id"], "requested_quantity": 3},
            {"item_type": "OTHER", "description": "Custom mounting bracket", "requested_quantity": 1},
        ],
    )
    assert response.status_code == 201, response.text
    assert len(response.json()["items"]) == 4


def test_create_requirement_invalid_branch(admin_client, client, db_session):
    model = _make_device_model(admin_client)
    create_user(db_session, "tl3@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl3@example.com", "Password123!")

    response = _create_requirement(client, headers, "does-not-exist", model["id"])
    assert response.status_code == 404


def test_create_requirement_invalid_department(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    other_branch = _make_branch(admin_client, "Vashi")
    other_dept = _make_department(admin_client, other_branch["id"])
    model = _make_device_model(admin_client)
    create_user(db_session, "tl4@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl4@example.com", "Password123!")

    response = _create_requirement(
        client, headers, branch["id"], model["id"], department_id=other_dept["id"],
    )
    assert response.status_code in (404, 422)


def test_create_requirement_invalid_item_reference(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "tl5@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl5@example.com", "Password123!")

    response = _create_requirement(client, headers, branch["id"], "missing-device-model")
    assert response.status_code == 404


def test_create_requirement_quantity_must_be_positive(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl6@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl6@example.com", "Password123!")

    response = _create_requirement(
        client, headers, branch["id"], model["id"],
        items=[{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 0}],
    )
    assert response.status_code == 422


def test_unauthorized_requester_cannot_create(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "viewer@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "viewer@example.com", "Password123!")

    response = _create_requirement(client, headers, branch["id"], model["id"])
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

def test_submit_draft_requirement(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl7@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl7@example.com", "Password123!")

    created = _create_requirement(client, headers, branch["id"], model["id"]).json()
    response = client.post(f"/api/v1/requirements/{created['id']}/submit", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PENDING_MANAGER_APPROVAL"


def test_cannot_submit_already_submitted_requirement(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl8@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl8@example.com", "Password123!")

    created = _create_requirement(client, headers, branch["id"], model["id"]).json()
    client.post(f"/api/v1/requirements/{created['id']}/submit", headers=headers)
    response = client.post(f"/api/v1/requirements/{created['id']}/submit", headers=headers)
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Approval workflow
# ---------------------------------------------------------------------------

def test_manager_sees_pending_approval(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl9@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr1@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    tl_headers = auth_headers(client, "tl9@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr1@example.com", "Password123!")

    created = _create_requirement(client, tl_headers, branch["id"], model["id"]).json()
    client.post(f"/api/v1/requirements/{created['id']}/submit", headers=tl_headers)

    listing = client.get("/api/v1/requirements?status_filter=PENDING_MANAGER_APPROVAL", headers=mgr_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


def test_unauthorized_user_cannot_approve(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl10@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl10@example.com", "Password123!")

    created = _create_requirement(client, tl_headers, branch["id"], model["id"]).json()
    client.post(f"/api/v1/requirements/{created['id']}/submit", headers=tl_headers)

    response = client.post(f"/api/v1/requirements/{created['id']}/approve", headers=tl_headers, json={})
    assert response.status_code == 403


def test_manager_can_approve_and_records_decision(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl11@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr2@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    tl_headers = auth_headers(client, "tl11@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr2@example.com", "Password123!")

    created = _create_requirement(client, tl_headers, branch["id"], model["id"]).json()
    client.post(f"/api/v1/requirements/{created['id']}/submit", headers=tl_headers)

    response = client.post(
        f"/api/v1/requirements/{created['id']}/approve", json={"remarks": "Stock justified"}, headers=mgr_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "APPROVED"
    assert body["manager_decision"] == "APPROVED"
    assert body["manager_remarks"] == "Stock justified"
    assert body["manager_decision_at"] is not None


def test_manager_can_reject(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl12@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr3@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    tl_headers = auth_headers(client, "tl12@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr3@example.com", "Password123!")

    created = _create_requirement(client, tl_headers, branch["id"], model["id"]).json()
    client.post(f"/api/v1/requirements/{created['id']}/submit", headers=tl_headers)

    response = client.post(
        f"/api/v1/requirements/{created['id']}/reject", json={"remarks": "Not needed"}, headers=mgr_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REJECTED"
    assert response.json()["manager_decision"] == "REJECTED"


def test_rejected_requirement_cannot_become_approved(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl13@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr4@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    tl_headers = auth_headers(client, "tl13@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr4@example.com", "Password123!")

    created = _create_requirement(client, tl_headers, branch["id"], model["id"]).json()
    client.post(f"/api/v1/requirements/{created['id']}/submit", headers=tl_headers)
    client.post(f"/api/v1/requirements/{created['id']}/reject", json={}, headers=mgr_headers)

    response = client.post(f"/api/v1/requirements/{created['id']}/approve", json={}, headers=mgr_headers)
    assert response.status_code == 409


def test_manager_cannot_approve_own_request(admin_client, client, db_session):
    create_user(db_session, "mgr5@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    mgr_headers = auth_headers(client, "mgr5@example.com", "Password123!")
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)

    created = _create_requirement(client, mgr_headers, branch["id"], model["id"]).json()
    client.post(f"/api/v1/requirements/{created['id']}/submit", headers=mgr_headers)

    response = client.post(f"/api/v1/requirements/{created['id']}/approve", json={}, headers=mgr_headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Cancel / status transitions
# ---------------------------------------------------------------------------

def test_cancel_draft_requirement(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl14@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl14@example.com", "Password123!")

    created = _create_requirement(client, headers, branch["id"], model["id"]).json()
    response = client.post(f"/api/v1/requirements/{created['id']}/cancel", json={"remarks": "no longer needed"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_cancelled_requirement_cannot_move_to_approval(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl15@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl15@example.com", "Password123!")

    created = _create_requirement(client, headers, branch["id"], model["id"]).json()
    client.post(f"/api/v1/requirements/{created['id']}/cancel", json={}, headers=headers)

    response = client.post(f"/api/v1/requirements/{created['id']}/submit", headers=headers)
    assert response.status_code == 409


def test_invalid_status_transition_returns_409(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl16@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr6@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    headers = auth_headers(client, "tl16@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr6@example.com", "Password123!")

    created = _create_requirement(client, headers, branch["id"], model["id"]).json()
    # Draft cannot go straight to approve.
    response = client.post(f"/api/v1/requirements/{created['id']}/approve", json={}, headers=mgr_headers)
    assert response.status_code == 409


def test_approved_requirement_cannot_be_edited(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl17@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr7@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    headers = auth_headers(client, "tl17@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr7@example.com", "Password123!")

    created = _create_requirement(client, headers, branch["id"], model["id"]).json()
    client.post(f"/api/v1/requirements/{created['id']}/submit", headers=headers)
    client.post(f"/api/v1/requirements/{created['id']}/approve", json={}, headers=mgr_headers)

    response = client.patch(f"/api/v1/requirements/{created['id']}", json={"reason": "changed my mind"}, headers=headers)
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# History / audit / pagination / filters / search / RBAC
# ---------------------------------------------------------------------------

def test_status_history_is_created(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl18@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr8@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    headers = auth_headers(client, "tl18@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr8@example.com", "Password123!")

    created = _create_requirement(client, headers, branch["id"], model["id"]).json()
    client.post(f"/api/v1/requirements/{created['id']}/submit", headers=headers)
    client.post(f"/api/v1/requirements/{created['id']}/approve", json={}, headers=mgr_headers)

    history = client.get(f"/api/v1/requirements/{created['id']}/history", headers=mgr_headers)
    assert history.status_code == 200
    to_statuses = [h["to_status"] for h in history.json()]
    assert to_statuses == ["DRAFT", "SUBMITTED", "PENDING_MANAGER_APPROVAL", "APPROVED"]


def test_audit_records_are_created(admin_client, client, db_session):
    from app.models.audit import AuditLog

    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl19@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl19@example.com", "Password123!")

    created = _create_requirement(client, headers, branch["id"], model["id"]).json()
    client.post(f"/api/v1/requirements/{created['id']}/submit", headers=headers)

    logs = db_session.query(AuditLog).filter(AuditLog.entity == "requirement", AuditLog.entity_id == created["id"]).all()
    actions = {log.action for log in logs}
    assert "CREATE" in actions
    assert "SUBMIT" in actions


def test_pagination_works(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl20@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl20@example.com", "Password123!")

    for _ in range(3):
        _create_requirement(client, headers, branch["id"], model["id"])

    page1 = client.get("/api/v1/requirements?page=1&page_size=2", headers=headers)
    assert page1.status_code == 200
    assert page1.json()["total"] == 3
    assert len(page1.json()["items"]) == 2

    page2 = client.get("/api/v1/requirements?page=2&page_size=2", headers=headers)
    assert len(page2.json()["items"]) == 1


def test_filters_work(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl21@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl21@example.com", "Password123!")

    _create_requirement(client, headers, branch["id"], model["id"], priority="HIGH")
    _create_requirement(client, headers, branch["id"], model["id"], priority="LOW")

    response = client.get("/api/v1/requirements?priority=HIGH", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["priority"] == "HIGH"


def test_search_works(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl22@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl22@example.com", "Password123!")

    _create_requirement(client, headers, branch["id"], model["id"], reason="Urgent ICU ventilator shortage")
    _create_requirement(client, headers, branch["id"], model["id"], reason="Routine consumable restock")

    response = client.get("/api/v1/requirements?search=ventilator", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_hospital_unit_manager_only_sees_own_requirements(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl23@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "tl24@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers1 = auth_headers(client, "tl23@example.com", "Password123!")
    headers2 = auth_headers(client, "tl24@example.com", "Password123!")

    _create_requirement(client, headers1, branch["id"], model["id"])
    _create_requirement(client, headers2, branch["id"], model["id"])

    listing = client.get("/api/v1/requirements", headers=headers1)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


def test_inventory_manager_has_read_only_access(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl25@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "inv1@example.com", "INVENTORY_MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl25@example.com", "Password123!")
    inv_headers = auth_headers(client, "inv1@example.com", "Password123!")

    created = _create_requirement(client, tl_headers, branch["id"], model["id"]).json()

    listing = client.get("/api/v1/requirements", headers=inv_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1  # broad read visibility, not scoped to own

    create_attempt = _create_requirement(client, inv_headers, branch["id"], model["id"])
    assert create_attempt.status_code == 403  # cannot execute fulfillment / create in 16A

    approve_attempt = client.post(f"/api/v1/requirements/{created['id']}/approve", json={}, headers=inv_headers)
    assert approve_attempt.status_code == 403


def test_doctor_cannot_create_requirement(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")

    response = _create_requirement(client, headers, branch["id"], model["id"])
    assert response.status_code == 403
