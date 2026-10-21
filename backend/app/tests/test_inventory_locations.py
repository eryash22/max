from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _make_branch(admin_client, name="Loc-Branch"):
    response = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"})
    assert response.status_code == 201, response.text
    return response.json()


def _make_location(admin_client, branch_id, name="Main Store", **overrides):
    payload = {"branch_id": branch_id, "name": name}
    payload.update(overrides)
    response = admin_client.post("/api/v1/inventory/locations", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _make_item(admin_client, item_code="LOCITM-100"):
    response = admin_client.post("/api/v1/inventory/items", json={"item_code": item_code, "name": item_code})
    assert response.status_code == 201, response.text
    return response.json()


def _engineer_headers(client, db_session, email="invloceng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _inventory_manager_headers(client, db_session, email="invlocmgr@example.com"):
    create_user(db_session, email, "INVENTORY_MANAGER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _doctor_headers(client, db_session, email="invlocdoc@example.com"):
    create_user(db_session, email, "DOCTOR", "Password123!")
    return auth_headers(client, email, "Password123!")


# ---- Create / validation ----

def test_inventory_manager_can_create_location(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    headers = _inventory_manager_headers(client, db_session)

    response = client.post(
        "/api/v1/inventory/locations",
        json={"branch_id": branch["id"], "name": "Central Warehouse", "code": "WH-1", "kind": "WAREHOUSE"},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Central Warehouse"
    assert body["kind"] == "WAREHOUSE"
    assert body["branch_id"] == branch["id"]


def test_default_kind_is_warehouse(admin_client):
    branch = _make_branch(admin_client)
    location = _make_location(admin_client, branch["id"])
    assert location["kind"] == "WAREHOUSE"


def test_doctor_cannot_create_location(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    headers = _doctor_headers(client, db_session)
    response = client.post(
        "/api/v1/inventory/locations", json={"branch_id": branch["id"], "name": "X"}, headers=headers
    )
    assert response.status_code == 403


def test_engineer_can_read_but_not_write_locations(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.get("/api/v1/inventory/locations", headers=headers)
    assert response.status_code == 200

    response = client.post(
        "/api/v1/inventory/locations", json={"branch_id": branch["id"], "name": "X"}, headers=headers
    )
    assert response.status_code == 403


def test_doctor_cannot_read_locations(admin_client, client, db_session):
    # Doctor holds neither inventory.read nor inventory.write, per RBAC_MATRIX.md —
    # matches the existing exclusion already regression-tested for inventory items
    # in test_inventory_authorization.py.
    headers = _doctor_headers(client, db_session)
    response = client.get("/api/v1/inventory/locations", headers=headers)
    assert response.status_code == 403


def test_location_requires_valid_branch(client, db_session):
    headers = _inventory_manager_headers(client, db_session)
    response = client.post(
        "/api/v1/inventory/locations", json={"branch_id": "does-not-exist", "name": "X"}, headers=headers
    )
    assert response.status_code == 404


def test_blank_name_rejected(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    headers = _inventory_manager_headers(client, db_session)
    response = client.post(
        "/api/v1/inventory/locations", json={"branch_id": branch["id"], "name": "  "}, headers=headers
    )
    assert response.status_code == 422


def test_invalid_kind_rejected(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    headers = _inventory_manager_headers(client, db_session)
    response = client.post(
        "/api/v1/inventory/locations", json={"branch_id": branch["id"], "name": "X", "kind": "MAGIC"}, headers=headers
    )
    assert response.status_code == 422


# ---- Read / list / filters ----

def test_get_location_by_id(admin_client):
    branch = _make_branch(admin_client)
    location = _make_location(admin_client, branch["id"])
    response = admin_client.get(f"/api/v1/inventory/locations/{location['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == location["id"]


def test_get_missing_location_404(admin_client):
    response = admin_client.get("/api/v1/inventory/locations/does-not-exist")
    assert response.status_code == 404


def test_list_filters_by_branch(admin_client):
    branch1 = _make_branch(admin_client, "Branch-A")
    branch2 = _make_branch(admin_client, "Branch-B")
    _make_location(admin_client, branch1["id"], "Loc A")
    _make_location(admin_client, branch2["id"], "Loc B")

    response = admin_client.get(f"/api/v1/inventory/locations?branch_id={branch1['id']}")
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["branch_id"] == branch1["id"]


def test_list_filters_by_kind(admin_client):
    branch = _make_branch(admin_client)
    _make_location(admin_client, branch["id"], "Warehouse 1", kind="WAREHOUSE")
    _make_location(admin_client, branch["id"], "Bin 1", kind="BIN")

    response = admin_client.get("/api/v1/inventory/locations?kind=BIN")
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["kind"] == "BIN"


def test_list_search_by_name_or_code(admin_client):
    branch = _make_branch(admin_client)
    _make_location(admin_client, branch["id"], "Emergency Store", code="ER-1")
    _make_location(admin_client, branch["id"], "ICU Store", code="ICU-1")

    response = admin_client.get("/api/v1/inventory/locations?search=Emergency")
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Emergency Store"

    response = admin_client.get("/api/v1/inventory/locations?search=ICU-1")
    body = response.json()
    assert body["total"] == 1


def test_list_pagination(admin_client):
    branch = _make_branch(admin_client)
    for i in range(3):
        _make_location(admin_client, branch["id"], f"Loc-{i}")

    response = admin_client.get("/api/v1/inventory/locations?page=1&page_size=2")
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


# ---- Update / delete ----

def test_update_location(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    location = _make_location(admin_client, branch["id"], "Old Name")
    headers = _inventory_manager_headers(client, db_session)

    response = client.patch(
        f"/api/v1/inventory/locations/{location['id']}", json={"name": "New Name", "kind": "BIN"}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "New Name"
    assert body["kind"] == "BIN"


def test_update_missing_location_404(client, db_session):
    headers = _inventory_manager_headers(client, db_session)
    response = client.patch("/api/v1/inventory/locations/does-not-exist", json={"name": "X"}, headers=headers)
    assert response.status_code == 404


def test_update_validates_branch_fk(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    location = _make_location(admin_client, branch["id"])
    headers = _inventory_manager_headers(client, db_session)
    response = client.patch(
        f"/api/v1/inventory/locations/{location['id']}", json={"branch_id": "does-not-exist"}, headers=headers
    )
    assert response.status_code == 404


def test_doctor_cannot_update_location(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    location = _make_location(admin_client, branch["id"])
    headers = _doctor_headers(client, db_session)
    response = client.patch(f"/api/v1/inventory/locations/{location['id']}", json={"name": "X"}, headers=headers)
    assert response.status_code == 403


def test_delete_location(admin_client):
    branch = _make_branch(admin_client)
    location = _make_location(admin_client, branch["id"])
    response = admin_client.delete(f"/api/v1/inventory/locations/{location['id']}")
    assert response.status_code == 204
    assert admin_client.get(f"/api/v1/inventory/locations/{location['id']}").status_code == 404


def test_delete_missing_location_404(admin_client):
    response = admin_client.delete("/api/v1/inventory/locations/does-not-exist")
    assert response.status_code == 404


def test_delete_blocked_when_referenced_by_transaction(admin_client):
    branch = _make_branch(admin_client)
    location = _make_location(admin_client, branch["id"])
    item = _make_item(admin_client)
    txn = admin_client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": item["id"], "transaction_type": "STOCK_IN", "quantity": 5, "location_id": location["id"]},
    )
    assert txn.status_code == 201

    response = admin_client.delete(f"/api/v1/inventory/locations/{location['id']}")
    assert response.status_code == 409


# ---- Transaction location FK validation (Module 13 closes this gap) ----

def test_transaction_validates_location_fk(admin_client):
    item = _make_item(admin_client, "LOCITM-200")
    response = admin_client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": item["id"], "transaction_type": "STOCK_IN", "quantity": 5, "location_id": "does-not-exist"},
    )
    assert response.status_code == 422


def test_transaction_with_valid_location_succeeds(admin_client):
    branch = _make_branch(admin_client)
    location = _make_location(admin_client, branch["id"])
    item = _make_item(admin_client, "LOCITM-300")
    response = admin_client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": item["id"], "transaction_type": "STOCK_IN", "quantity": 5, "location_id": location["id"]},
    )
    assert response.status_code == 201
    assert response.json()["location_id"] == location["id"]


# ---- Audit logging ----

def test_create_writes_audit_log(admin_client, db_session):
    branch = _make_branch(admin_client)
    location = _make_location(admin_client, branch["id"])
    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "inventory_location", AuditLog.entity_id == location["id"], AuditLog.action == "CREATE"
    ).first()
    assert log is not None


def test_update_writes_audit_log(admin_client, db_session):
    branch = _make_branch(admin_client)
    location = _make_location(admin_client, branch["id"])
    admin_client.patch(f"/api/v1/inventory/locations/{location['id']}", json={"name": "Renamed"})

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "inventory_location", AuditLog.entity_id == location["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None


def test_delete_writes_audit_log(admin_client, db_session):
    branch = _make_branch(admin_client)
    location = _make_location(admin_client, branch["id"])
    admin_client.delete(f"/api/v1/inventory/locations/{location['id']}")

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "inventory_location", AuditLog.entity_id == location["id"], AuditLog.action == "DELETE"
    ).first()
    assert log is not None
