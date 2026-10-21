from datetime import datetime, timedelta, timezone

from app.models.audit import AuditLog

from .conftest import auth_headers, create_user

# inventory_service._today() computes expiry status from UTC, not local time, so
# TODAY must be pinned to UTC to avoid a local/UTC day-boundary mismatch.
TODAY = datetime.now(timezone.utc).date()


def _iso(delta_days: int) -> str:
    return (TODAY + timedelta(days=delta_days)).isoformat()


def _engineer_headers(client, db_session, email="invtesteng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _doctor_headers(client, db_session, email="invtestdoc@example.com"):
    create_user(db_session, email, "DOCTOR", "Password123!")
    return auth_headers(client, email, "Password123!")


def _make_item(admin_client, item_code="ITM-100", **overrides):
    payload = {"item_code": item_code, "name": f"Item {item_code}", "unit": "EA"}
    payload.update(overrides)
    response = admin_client.post("/api/v1/inventory/items", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _make_device_model(admin_client, name="Infusion-Pump-Model"):
    response = admin_client.post("/api/v1/device-models", json={"name": name, "category": "Infusion"})
    assert response.status_code == 201, response.text
    return response.json()


def _stock_in(admin_client, item_id, quantity, **overrides):
    payload = {"item_id": item_id, "transaction_type": "STOCK_IN", "quantity": quantity}
    payload.update(overrides)
    return admin_client.post("/api/v1/inventory/transactions", json=payload)


def _stock_out(admin_client, item_id, quantity, **overrides):
    payload = {"item_id": item_id, "transaction_type": "STOCK_OUT", "quantity": quantity}
    payload.update(overrides)
    return admin_client.post("/api/v1/inventory/transactions", json=payload)


# ---- Item CRUD ----

def test_create_item_with_full_fields(admin_client):
    item = _make_item(
        admin_client, item_code="ITM-FULL", category="Consumable", unit="BOX",
        min_level=5, max_level=100, reorder_level=10, unit_cost=12.5, supplier="Acme Supplies",
    )
    assert item["item_code"] == "ITM-FULL"
    assert item["max_level"] == 100
    assert item["unit_cost"] == 12.5
    assert item["current_stock"] == 0
    assert item["stock_status"] == "OUT_OF_STOCK"


def test_create_item_duplicate_code_conflicts(admin_client):
    _make_item(admin_client, item_code="ITM-DUP")
    response = admin_client.post("/api/v1/inventory/items", json={"item_code": "ITM-DUP", "name": "Other"})
    assert response.status_code == 409


def test_create_item_blank_name_rejected(admin_client):
    response = admin_client.post("/api/v1/inventory/items", json={"item_code": "ITM-BLANK", "name": "   "})
    assert response.status_code == 422


def test_create_item_negative_level_rejected(admin_client):
    response = admin_client.post(
        "/api/v1/inventory/items", json={"item_code": "ITM-NEG", "name": "Test", "min_level": -1}
    )
    assert response.status_code == 422


def test_get_item_by_id(admin_client):
    item = _make_item(admin_client, item_code="ITM-GET")
    response = admin_client.get(f"/api/v1/inventory/items/{item['id']}")
    assert response.status_code == 200
    assert response.json()["item_code"] == "ITM-GET"


def test_get_item_missing_404(admin_client):
    response = admin_client.get("/api/v1/inventory/items/does-not-exist")
    assert response.status_code == 404


def test_update_item(admin_client):
    item = _make_item(admin_client, item_code="ITM-UPD")
    response = admin_client.patch(f"/api/v1/inventory/items/{item['id']}", json={"unit_cost": 42.0, "supplier": "New Co"})
    assert response.status_code == 200
    body = response.json()
    assert body["unit_cost"] == 42.0
    assert body["supplier"] == "New Co"


def test_list_items_search_and_category_filter(admin_client):
    _make_item(admin_client, item_code="ITM-SRCH-1", category="Electrode")
    _make_item(admin_client, item_code="ITM-SRCH-2", category="Tubing")
    response = admin_client.get("/api/v1/inventory/items", params={"category": "Electrode"})
    assert response.status_code == 200
    body = response.json()
    assert all(i["category"] == "Electrode" for i in body["items"])
    assert any(i["item_code"] == "ITM-SRCH-1" for i in body["items"])

    search_response = admin_client.get("/api/v1/inventory/items", params={"search": "ITM-SRCH-2"})
    codes = [i["item_code"] for i in search_response.json()["items"]]
    assert "ITM-SRCH-2" in codes


def test_list_items_pagination(admin_client):
    for i in range(3):
        _make_item(admin_client, item_code=f"ITM-PAGE-{i}")
    response = admin_client.get("/api/v1/inventory/items", params={"page": 1, "page_size": 2})
    body = response.json()
    assert len(body["items"]) == 2
    assert body["page"] == 1
    assert body["page_size"] == 2


# ---- Transactions: stock in / out / adjustment ----

def test_stock_in_increases_current_stock(admin_client):
    item = _make_item(admin_client, item_code="ITM-IN")
    response = _stock_in(admin_client, item["id"], 50)
    assert response.status_code == 201
    stock = admin_client.get(f"/api/v1/inventory/items/{item['id']}/stock").json()
    assert stock["current_stock"] == 50


def test_stock_out_decreases_current_stock(admin_client):
    item = _make_item(admin_client, item_code="ITM-OUT")
    _stock_in(admin_client, item["id"], 50)
    response = _stock_out(admin_client, item["id"], 20)
    assert response.status_code == 201
    stock = admin_client.get(f"/api/v1/inventory/items/{item['id']}/stock").json()
    assert stock["current_stock"] == 30


def test_stock_out_insufficient_stock_rejected(admin_client):
    item = _make_item(admin_client, item_code="ITM-INSUFF")
    _stock_in(admin_client, item["id"], 5)
    response = _stock_out(admin_client, item["id"], 10)
    assert response.status_code == 409


def test_adjustment_can_be_negative(admin_client):
    item = _make_item(admin_client, item_code="ITM-ADJ")
    _stock_in(admin_client, item["id"], 50)
    response = admin_client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": item["id"], "transaction_type": "ADJUSTMENT", "quantity": -5},
    )
    assert response.status_code == 201
    stock = admin_client.get(f"/api/v1/inventory/items/{item['id']}/stock").json()
    assert stock["current_stock"] == 45


def test_adjustment_below_zero_rejected(admin_client):
    item = _make_item(admin_client, item_code="ITM-ADJNEG")
    _stock_in(admin_client, item["id"], 5)
    response = admin_client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": item["id"], "transaction_type": "ADJUSTMENT", "quantity": -10},
    )
    assert response.status_code == 409


def test_transfer_does_not_change_net_stock(admin_client):
    item = _make_item(admin_client, item_code="ITM-XFER")
    _stock_in(admin_client, item["id"], 50)
    response = admin_client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": item["id"], "transaction_type": "TRANSFER", "quantity": 10},
    )
    assert response.status_code == 201
    stock = admin_client.get(f"/api/v1/inventory/items/{item['id']}/stock").json()
    assert stock["current_stock"] == 50


def test_transaction_zero_quantity_rejected(admin_client):
    item = _make_item(admin_client, item_code="ITM-ZERO")
    response = admin_client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": item["id"], "transaction_type": "STOCK_IN", "quantity": 0},
    )
    assert response.status_code == 422


def test_transaction_negative_stock_in_rejected(admin_client):
    item = _make_item(admin_client, item_code="ITM-NEGIN")
    response = admin_client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": item["id"], "transaction_type": "STOCK_IN", "quantity": -5},
    )
    assert response.status_code == 422


def test_transaction_invalid_type_rejected(admin_client):
    item = _make_item(admin_client, item_code="ITM-BADTYPE")
    response = admin_client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": item["id"], "transaction_type": "TELEPORT", "quantity": 5},
    )
    assert response.status_code == 422


def test_transaction_invalid_item_fk_rejected(admin_client):
    response = _stock_in(admin_client, "does-not-exist", 5)
    assert response.status_code == 422


def test_transaction_history_and_get_by_id(admin_client):
    item = _make_item(admin_client, item_code="ITM-HIST")
    create_response = _stock_in(admin_client, item["id"], 15, reference="PO-1")
    txn_id = create_response.json()["id"]

    get_response = admin_client.get(f"/api/v1/inventory/transactions/{txn_id}")
    assert get_response.status_code == 200
    assert get_response.json()["reference"] == "PO-1"

    list_response = admin_client.get("/api/v1/inventory/transactions", params={"item_id": item["id"]})
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1


def test_transaction_missing_404(admin_client):
    response = admin_client.get("/api/v1/inventory/transactions/does-not-exist")
    assert response.status_code == 404


def test_transaction_list_filter_by_type(admin_client):
    item = _make_item(admin_client, item_code="ITM-TYPEFILTER")
    _stock_in(admin_client, item["id"], 10)
    _stock_out(admin_client, item["id"], 2)
    response = admin_client.get(
        "/api/v1/inventory/transactions", params={"item_id": item["id"], "transaction_type": "STOCK_OUT"}
    )
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["transaction_type"] == "STOCK_OUT"


def test_transaction_list_pagination(admin_client):
    item = _make_item(admin_client, item_code="ITM-TXNPAGE")
    for _ in range(3):
        _stock_in(admin_client, item["id"], 1)
    response = admin_client.get(
        "/api/v1/inventory/transactions", params={"item_id": item["id"], "page": 1, "page_size": 2}
    )
    body = response.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3


# ---- Low-stock / out-of-stock status ----

def test_low_stock_status(admin_client):
    item = _make_item(admin_client, item_code="ITM-LOW", reorder_level=10)
    _stock_in(admin_client, item["id"], 5)
    stock = admin_client.get(f"/api/v1/inventory/items/{item['id']}/stock").json()
    assert stock["stock_status"] == "LOW_STOCK"


def test_out_of_stock_status(admin_client):
    item = _make_item(admin_client, item_code="ITM-OOS")
    stock = admin_client.get(f"/api/v1/inventory/items/{item['id']}/stock").json()
    assert stock["current_stock"] == 0
    assert stock["stock_status"] == "OUT_OF_STOCK"


def test_normal_stock_status(admin_client):
    item = _make_item(admin_client, item_code="ITM-NORMAL", reorder_level=10, max_level=100)
    _stock_in(admin_client, item["id"], 50)
    stock = admin_client.get(f"/api/v1/inventory/items/{item['id']}/stock").json()
    assert stock["stock_status"] == "NORMAL"


def test_overstock_status(admin_client):
    item = _make_item(admin_client, item_code="ITM-OVER", max_level=20)
    _stock_in(admin_client, item["id"], 50)
    stock = admin_client.get(f"/api/v1/inventory/items/{item['id']}/stock").json()
    assert stock["stock_status"] == "OVERSTOCK"


def test_list_items_filter_by_stock_status(admin_client):
    low = _make_item(admin_client, item_code="ITM-FILTLOW", reorder_level=10)
    _stock_in(admin_client, low["id"], 1)
    normal = _make_item(admin_client, item_code="ITM-FILTNORM", reorder_level=10)
    _stock_in(admin_client, normal["id"], 50)

    response = admin_client.get("/api/v1/inventory/items", params={"stock_status": "LOW_STOCK"})
    codes = [i["item_code"] for i in response.json()["items"]]
    assert "ITM-FILTLOW" in codes
    assert "ITM-FILTNORM" not in codes


# ---- Batch / expiry ----

def test_stock_in_with_batch_and_expiry(admin_client):
    item = _make_item(admin_client, item_code="ITM-BATCH")
    response = _stock_in(admin_client, item["id"], 10, batch_number="B-001", expiry_date=_iso(90))
    assert response.status_code == 201
    body = response.json()
    assert body["batch_number"] == "B-001"
    assert body["expiry_status"] == "OK"


def test_near_expiry_status(admin_client):
    item = _make_item(admin_client, item_code="ITM-NEAREXP")
    response = _stock_in(admin_client, item["id"], 10, batch_number="B-002", expiry_date=_iso(10))
    assert response.json()["expiry_status"] == "NEAR_EXPIRY"


def test_expired_status(admin_client):
    item = _make_item(admin_client, item_code="ITM-EXPIRED")
    response = _stock_in(admin_client, item["id"], 10, batch_number="B-003", expiry_date=_iso(-5))
    assert response.json()["expiry_status"] == "EXPIRED"


def test_no_expiry_date_is_not_set(admin_client):
    item = _make_item(admin_client, item_code="ITM-NOEXP")
    response = _stock_in(admin_client, item["id"], 10)
    assert response.json()["expiry_status"] == "NOT_SET"


def test_invalid_expiry_date_rejected(admin_client):
    item = _make_item(admin_client, item_code="ITM-BADEXP")
    response = _stock_in(admin_client, item["id"], 10, expiry_date="not-a-date")
    assert response.status_code == 422


def test_filter_transactions_by_expiry_status(admin_client):
    item = _make_item(admin_client, item_code="ITM-EXPFILTER")
    _stock_in(admin_client, item["id"], 5, batch_number="B-OK", expiry_date=_iso(200))
    _stock_in(admin_client, item["id"], 5, batch_number="B-EXP", expiry_date=_iso(-1))
    response = admin_client.get(
        "/api/v1/inventory/transactions", params={"item_id": item["id"], "expiry_status": "EXPIRED"}
    )
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["batch_number"] == "B-EXP"


def test_filter_transactions_by_batch_number(admin_client):
    item = _make_item(admin_client, item_code="ITM-BATCHFILTER")
    _stock_in(admin_client, item["id"], 5, batch_number="B-XYZ")
    _stock_in(admin_client, item["id"], 5, batch_number="B-OTHER")
    response = admin_client.get(
        "/api/v1/inventory/transactions", params={"item_id": item["id"], "batch_number": "B-XYZ"}
    )
    body = response.json()
    assert body["total"] == 1


# ---- Spare-part compatibility ----

def test_add_and_list_compatibility(admin_client):
    item = _make_item(admin_client, item_code="ITM-COMPAT")
    model = _make_device_model(admin_client)
    response = admin_client.post(
        f"/api/v1/inventory/items/{item['id']}/compatibility",
        json={"device_model_id": model["id"], "notes": "Fits all revisions"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["device_model_id"] == model["id"]

    list_response = admin_client.get(f"/api/v1/inventory/items/{item['id']}/compatibility")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_compatibility_duplicate_rejected(admin_client):
    item = _make_item(admin_client, item_code="ITM-COMPATDUP")
    model = _make_device_model(admin_client, name="Model-Dup")
    admin_client.post(f"/api/v1/inventory/items/{item['id']}/compatibility", json={"device_model_id": model["id"]})
    response = admin_client.post(f"/api/v1/inventory/items/{item['id']}/compatibility", json={"device_model_id": model["id"]})
    assert response.status_code == 409


def test_compatibility_invalid_device_model_rejected(admin_client):
    item = _make_item(admin_client, item_code="ITM-COMPATBAD")
    response = admin_client.post(
        f"/api/v1/inventory/items/{item['id']}/compatibility", json={"device_model_id": "does-not-exist"}
    )
    assert response.status_code == 422


def test_compatibility_invalid_item_rejected(admin_client):
    model = _make_device_model(admin_client, name="Model-Orphan")
    response = admin_client.post(
        "/api/v1/inventory/items/does-not-exist/compatibility", json={"device_model_id": model["id"]}
    )
    assert response.status_code == 422


def test_remove_compatibility(admin_client):
    item = _make_item(admin_client, item_code="ITM-COMPATDEL")
    model = _make_device_model(admin_client, name="Model-Del")
    add_response = admin_client.post(
        f"/api/v1/inventory/items/{item['id']}/compatibility", json={"device_model_id": model["id"]}
    )
    compat_id = add_response.json()["id"]
    delete_response = admin_client.delete(f"/api/v1/inventory/items/{item['id']}/compatibility/{compat_id}")
    assert delete_response.status_code == 204
    list_response = admin_client.get(f"/api/v1/inventory/items/{item['id']}/compatibility")
    assert list_response.json() == []


# ---- RBAC ----

def test_doctor_cannot_read_inventory(client, db_session):
    headers = _doctor_headers(client, db_session)
    response = client.get("/api/v1/inventory/items", headers=headers)
    assert response.status_code == 403


def test_doctor_cannot_write_inventory(client, db_session):
    headers = _doctor_headers(client, db_session)
    response = client.post("/api/v1/inventory/items", json={"item_code": "ITM-RBAC", "name": "X"}, headers=headers)
    assert response.status_code == 403


def test_engineer_has_read_only_inventory_access(client, db_session):
    headers = _engineer_headers(client, db_session)
    read_response = client.get("/api/v1/inventory/items", headers=headers)
    assert read_response.status_code == 200
    write_response = client.post("/api/v1/inventory/items", json={"item_code": "ITM-ENGWRITE", "name": "X"}, headers=headers)
    assert write_response.status_code == 403


def test_inventory_manager_has_full_access(client, db_session):
    create_user(db_session, "invmgr@example.com", "INVENTORY_MANAGER", "Password123!")
    headers = auth_headers(client, "invmgr@example.com", "Password123!")
    response = client.post("/api/v1/inventory/items", json={"item_code": "ITM-MGRFULL", "name": "X"}, headers=headers)
    assert response.status_code == 201
    txn_response = client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": response.json()["id"], "transaction_type": "STOCK_IN", "quantity": 5},
        headers=headers,
    )
    assert txn_response.status_code == 201


# ---- Audit logging ----

def test_item_create_and_update_are_audited(admin_client, db_session):
    item = _make_item(admin_client, item_code="ITM-AUDIT")
    admin_client.patch(f"/api/v1/inventory/items/{item['id']}", json={"unit_cost": 5.0})

    logs = db_session.query(AuditLog).filter(AuditLog.entity_id == item["id"]).all()
    actions = {log.action for log in logs}
    assert "CREATE" in actions
    assert "UPDATE" in actions


def test_transaction_create_is_audited(admin_client, db_session):
    item = _make_item(admin_client, item_code="ITM-TXNAUDIT")
    txn = _stock_in(admin_client, item["id"], 10).json()
    log = db_session.query(AuditLog).filter(AuditLog.entity_id == txn["id"]).first()
    assert log is not None
    assert log.entity == "inventory_transaction"
    assert log.action == "CREATE"


def test_compatibility_create_is_audited(admin_client, db_session):
    item = _make_item(admin_client, item_code="ITM-COMPATAUDIT")
    model = _make_device_model(admin_client, name="Model-Audit")
    link = admin_client.post(
        f"/api/v1/inventory/items/{item['id']}/compatibility", json={"device_model_id": model["id"]}
    ).json()
    log = db_session.query(AuditLog).filter(AuditLog.entity_id == link["id"]).first()
    assert log is not None
    assert log.entity == "spare_part_compatibility"
