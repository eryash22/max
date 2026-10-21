from .conftest import auth_headers, create_user


def _make_branch(admin_client, name="Saket"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_department(admin_client, branch_id, name="ICU"):
    master = admin_client.post("/api/v1/department-masters", json={"name": name}).json()
    return admin_client.post(
        "/api/v1/departments", json={"branch_id": branch_id, "department_master_id": master["id"]}
    ).json()


def _make_device_model(admin_client, name="Patient Monitor"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


def _make_asset(admin_client, device_model_id, branch_id, asset_number):
    response = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": device_model_id, "branch_id": branch_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_inventory_item(admin_client, code="SP-100"):
    return admin_client.post(
        "/api/v1/inventory/items", json={"item_code": code, "name": "Sensor Cable", "is_consumable": False}
    ).json()


def _stock_in(admin_client, item_id, quantity, location_id=None):
    response = admin_client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": item_id, "location_id": location_id, "transaction_type": "STOCK_IN", "quantity": quantity},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_consumable(admin_client, code="CON-100", opening_quantity=20):
    return admin_client.post(
        "/api/v1/consumables",
        json={"item_code": code, "name": "Syringe", "unit": "PCS", "opening_quantity": opening_quantity},
    ).json()


def _make_inventory_location(admin_client, branch_id, name="Main Warehouse"):
    return admin_client.post(
        "/api/v1/inventory/locations", json={"branch_id": branch_id, "name": name, "kind": "WAREHOUSE"}
    ).json()


def _inventory_manager_headers(client, db_session, email="invmgr@example.com"):
    create_user(db_session, email, "INVENTORY_MANAGER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _create_approved_requirement(admin_client, client, db_session, branch_id, items, *, tl_email, department_id=None):
    create_user(db_session, tl_email, "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, tl_email, "Password123!")
    payload = {"branch_id": branch_id, "reason": "Fulfillment test", "items": items}
    if department_id:
        payload["department_id"] = department_id
    created = client.post("/api/v1/requirements", json=payload, headers=tl_headers)
    assert created.status_code == 201, created.text
    created = created.json()
    submitted = client.post(f"/api/v1/requirements/{created['id']}/submit", headers=tl_headers)
    assert submitted.status_code == 200, submitted.text
    approved = admin_client.post(f"/api/v1/requirements/{created['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    return approved.json(), tl_headers


# ---------------------------------------------------------------------------
# Entry into fulfillment
# ---------------------------------------------------------------------------

def test_only_approved_requirement_can_enter_fulfillment(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl1@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl1@example.com", "Password123!")
    created = client.post(
        "/api/v1/requirements",
        json={"branch_id": branch["id"], "reason": "x", "items": [
            {"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1},
        ]},
        headers=tl_headers,
    ).json()

    response = admin_client.post("/api/v1/fulfillments", json={"requirement_id": created["id"]})
    assert response.status_code == 409, response.text


def test_create_fulfillment_from_approved_requirement(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 2}],
        tl_email="tl2@example.com",
    )

    response = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PENDING_ALLOCATION"
    assert body["fulfillment_number"].startswith("FUL-")
    assert len(body["items"]) == 1
    assert body["items"][0]["requested_quantity"] == 2
    assert body["items"][0]["allocated_quantity"] == 0
    assert body["items"][0]["pending_quantity"] == 2


def test_cannot_create_duplicate_fulfillment_for_same_requirement(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl3@example.com",
    )
    first = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]})
    assert first.status_code == 201

    second = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]})
    assert second.status_code == 409


def test_hospital_unit_manager_cannot_create_fulfillment(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement, tl_headers = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl4@example.com",
    )

    response = client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}, headers=tl_headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Allocation — spare parts / consumables (quantity-based)
# ---------------------------------------------------------------------------

def test_inventory_manager_can_allocate_within_stock(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    item = _make_inventory_item(admin_client, "SP-200")
    _stock_in(admin_client, item["id"], 10)
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "SPARE_PART", "inventory_item_id": item["id"], "requested_quantity": 5}],
        tl_email="tl5@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()

    response = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": fulfillment["items"][0]["requirement_item_id"], "quantity": 5}]},
        headers=inv_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ALLOCATED"
    assert body["items"][0]["allocated_quantity"] == 5
    # available_quantity excludes this fulfillment's own reservation (shown separately
    # as allocated_quantity) — it reports what's free from every OTHER fulfillment's
    # perspective, which stays the full 10 since nothing else is competing for this item.
    assert body["items"][0]["available_quantity"] == 10


# ---------------------------------------------------------------------------
# Security hardening pass — inventory.picker_read callers never see central stock
# ---------------------------------------------------------------------------

def test_picker_read_caller_does_not_see_available_quantity_on_own_fulfillment(admin_client, client, db_session):
    """TL/Unit-Head/Hospital-Unit-Manager hold inventory.picker_read, not
    inventory.read (security/permissions.py) — the /inventory endpoints already null
    stock fields for them (strip_stock_fields), but available_quantity on a
    fulfillment they can see (their own requirement) used to leak the same central
    stock number through a second, unmasked path."""
    branch = _make_branch(admin_client)
    item = _make_inventory_item(admin_client, "SP-300")
    _stock_in(admin_client, item["id"], 10)
    requirement, tl_headers = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "SPARE_PART", "inventory_item_id": item["id"], "requested_quantity": 5}],
        tl_email="picker.mask@example.com",
    )
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()

    detail = client.get(f"/api/v1/fulfillments/{fulfillment['id']}", headers=tl_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["items"][0]["available_quantity"] is None

    availability = client.get(f"/api/v1/fulfillments/{fulfillment['id']}/availability", headers=tl_headers)
    assert availability.status_code == 200, availability.text
    assert availability.json()["items"][0]["available_quantity"] is None

    listing = client.get("/api/v1/fulfillments", headers=tl_headers)
    assert listing.status_code == 200, listing.text
    listed = next(f for f in listing.json()["items"] if f["id"] == fulfillment["id"])
    assert listed["items"][0]["available_quantity"] is None

    # An Inventory Manager (full inventory.read) looking at the exact same
    # fulfillment still sees the real number — this is masking, not data loss.
    inv_headers = _inventory_manager_headers(client, db_session, email="unmasked.invmgr@example.com")
    unmasked = client.get(f"/api/v1/fulfillments/{fulfillment['id']}", headers=inv_headers)
    assert unmasked.status_code == 200, unmasked.text
    assert unmasked.json()["items"][0]["available_quantity"] == 10


def test_allocate_more_than_available_stock_fails(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    item = _make_inventory_item(admin_client, "SP-201")
    _stock_in(admin_client, item["id"], 3)
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "SPARE_PART", "inventory_item_id": item["id"], "requested_quantity": 5}],
        tl_email="tl6@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()

    response = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": fulfillment["items"][0]["requirement_item_id"], "quantity": 5}]},
        headers=inv_headers,
    )
    assert response.status_code == 409


def test_partial_fulfillment_allows_less_than_requested(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    item = _make_inventory_item(admin_client, "SP-202")
    _stock_in(admin_client, item["id"], 10)
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "SPARE_PART", "inventory_item_id": item["id"], "requested_quantity": 10}],
        tl_email="tl7@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()

    response = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": fulfillment["items"][0]["requirement_item_id"], "quantity": 4}]},
        headers=inv_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"][0]["allocated_quantity"] == 4
    assert body["items"][0]["pending_quantity"] == 6


def test_two_fulfillments_cannot_over_allocate_shared_stock(admin_client, client, db_session):
    """Requirement A and B both want the same spare part; only 6 in stock. A takes 5,
    leaving 1 — B trying to take 3 must fail (reservation, not raw per-item math)."""
    branch = _make_branch(admin_client)
    item = _make_inventory_item(admin_client, "SP-203")
    _stock_in(admin_client, item["id"], 6)
    req_a, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "SPARE_PART", "inventory_item_id": item["id"], "requested_quantity": 5}],
        tl_email="tl8a@example.com",
    )
    req_b, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "SPARE_PART", "inventory_item_id": item["id"], "requested_quantity": 5}],
        tl_email="tl8b@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    ful_a = admin_client.post("/api/v1/fulfillments", json={"requirement_id": req_a["id"]}).json()
    ful_b = admin_client.post("/api/v1/fulfillments", json={"requirement_id": req_b["id"]}).json()

    ok = client.post(
        f"/api/v1/fulfillments/{ful_a['id']}/allocate",
        json={"items": [{"requirement_item_id": ful_a["items"][0]["requirement_item_id"], "quantity": 5}]},
        headers=inv_headers,
    )
    assert ok.status_code == 200, ok.text

    blocked = client.post(
        f"/api/v1/fulfillments/{ful_b['id']}/allocate",
        json={"items": [{"requirement_item_id": ful_b["items"][0]["requirement_item_id"], "quantity": 3}]},
        headers=inv_headers,
    )
    assert blocked.status_code == 409


def test_consumable_stock_not_deducted_until_complete(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    consumable = _make_consumable(admin_client, "CON-200", opening_quantity=10)
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "CONSUMABLE", "consumable_id": consumable["id"], "requested_quantity": 4}],
        tl_email="tl9@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()
    client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": fulfillment["items"][0]["requirement_item_id"], "quantity": 4}]},
        headers=inv_headers,
    )

    unchanged = admin_client.get(f"/api/v1/consumables?page=1&page_size=25").json()
    row = next(c for c in unchanged["items"] if c["id"] == consumable["id"])
    assert row["opening_quantity"] == 10  # not deducted yet — only reserved


# ---------------------------------------------------------------------------
# Allocation — serialized devices/assets
# ---------------------------------------------------------------------------

def test_allocate_serialized_device_links_specific_assets(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client, "Ventilator")
    asset1 = _make_asset(admin_client, model["id"], branch["id"], "AST-1001")
    asset2 = _make_asset(admin_client, model["id"], branch["id"], "AST-1002")
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 2}],
        tl_email="tl10@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()

    response = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{
            "requirement_item_id": fulfillment["items"][0]["requirement_item_id"],
            "asset_ids": [asset1["id"], asset2["id"]],
        }]},
        headers=inv_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"][0]["allocated_quantity"] == 2
    linked_asset_ids = {a["asset_id"] for a in body["items"][0]["assets"]}
    assert linked_asset_ids == {asset1["id"], asset2["id"]}


def test_cannot_double_allocate_same_asset_to_two_fulfillments(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client, "Infusion Pump")
    asset = _make_asset(admin_client, model["id"], branch["id"], "AST-2001")
    req_a, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl11a@example.com",
    )
    req_b, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl11b@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    ful_a = admin_client.post("/api/v1/fulfillments", json={"requirement_id": req_a["id"]}).json()
    ful_b = admin_client.post("/api/v1/fulfillments", json={"requirement_id": req_b["id"]}).json()

    ok = client.post(
        f"/api/v1/fulfillments/{ful_a['id']}/allocate",
        json={"items": [{"requirement_item_id": ful_a["items"][0]["requirement_item_id"], "asset_ids": [asset["id"]]}]},
        headers=inv_headers,
    )
    assert ok.status_code == 200, ok.text

    blocked = client.post(
        f"/api/v1/fulfillments/{ful_b['id']}/allocate",
        json={"items": [{"requirement_item_id": ful_b["items"][0]["requirement_item_id"], "asset_ids": [asset["id"]]}]},
        headers=inv_headers,
    )
    assert blocked.status_code == 409


def test_asset_not_matching_device_model_rejected(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model_a = _make_device_model(admin_client, "Ventilator A")
    model_b = _make_device_model(admin_client, "Ventilator B")
    wrong_asset = _make_asset(admin_client, model_b["id"], branch["id"], "AST-3001")
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model_a["id"], "requested_quantity": 1}],
        tl_email="tl12@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()

    response = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{
            "requirement_item_id": fulfillment["items"][0]["requirement_item_id"],
            "asset_ids": [wrong_asset["id"]],
        }]},
        headers=inv_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

def test_cannot_skip_status_transition(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl13@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()

    response = client.post(f"/api/v1/fulfillments/{fulfillment['id']}/pack", headers=inv_headers)
    assert response.status_code == 409


def test_process_requires_at_least_one_allocated_item(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    item = _make_inventory_item(admin_client, "SP-300")
    _stock_in(admin_client, item["id"], 5)
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "SPARE_PART", "inventory_item_id": item["id"], "requested_quantity": 5}],
        tl_email="tl14@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()
    client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": fulfillment["items"][0]["requirement_item_id"], "quantity": 0}]},
        headers=inv_headers,
    )

    response = client.post(f"/api/v1/fulfillments/{fulfillment['id']}/process", headers=inv_headers)
    assert response.status_code == 409


def test_cancel_allowed_before_dispatch(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl15@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()

    response = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/cancel", json={"remarks": "no longer needed"}, headers=inv_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_cancel_blocked_after_out_for_delivery(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "AST-4001")
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl16@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()
    req_item_id = fulfillment["items"][0]["requirement_item_id"]
    client.post(f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
                json={"items": [{"requirement_item_id": req_item_id, "asset_ids": [asset["id"]]}]}, headers=inv_headers)
    client.post(f"/api/v1/fulfillments/{fulfillment['id']}/process", headers=inv_headers)
    client.post(f"/api/v1/fulfillments/{fulfillment['id']}/pack", headers=inv_headers)
    client.post(f"/api/v1/fulfillments/{fulfillment['id']}/out-for-delivery", headers=inv_headers)

    response = client.post(f"/api/v1/fulfillments/{fulfillment['id']}/cancel", json={}, headers=inv_headers)
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Full lifecycle + inventory/asset side effects at completion
# ---------------------------------------------------------------------------

def test_full_lifecycle_updates_inventory_and_preserves_asset(admin_client, client, db_session):
    from app.models.audit import AuditLog
    from app.models.asset_history import AssetHistory
    from app.models.inventory import InventoryTransaction

    branch = _make_branch(admin_client)
    department = _make_department(admin_client, branch["id"])
    model = _make_device_model(admin_client, "Syringe Pump")
    asset = _make_asset(admin_client, model["id"], branch["id"], "AST-5001")
    spare = _make_inventory_item(admin_client, "SP-400")
    _stock_in(admin_client, spare["id"], 10)
    consumable = _make_consumable(admin_client, "CON-400", opening_quantity=10)
    location = _make_inventory_location(admin_client, branch["id"])

    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [
            {"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1},
            {"item_type": "SPARE_PART", "inventory_item_id": spare["id"], "requested_quantity": 3},
            {"item_type": "CONSUMABLE", "consumable_id": consumable["id"], "requested_quantity": 2},
        ],
        tl_email="tl17@example.com", department_id=department["id"],
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post(
        "/api/v1/fulfillments",
        json={"requirement_id": requirement["id"], "source_location_id": location["id"], "expected_delivery_date": "2026-09-01"},
    ).json()
    items_by_type = {i["item_type"]: i for i in fulfillment["items"]}

    allocate = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [
            {"requirement_item_id": items_by_type["DEVICE_MODEL"]["requirement_item_id"], "asset_ids": [asset["id"]]},
            {"requirement_item_id": items_by_type["SPARE_PART"]["requirement_item_id"], "quantity": 3},
            {"requirement_item_id": items_by_type["CONSUMABLE"]["requirement_item_id"], "quantity": 2},
        ]},
        headers=inv_headers,
    )
    assert allocate.status_code == 200, allocate.text

    assert client.post(f"/api/v1/fulfillments/{fulfillment['id']}/process", headers=inv_headers).status_code == 200
    assert client.post(f"/api/v1/fulfillments/{fulfillment['id']}/pack", headers=inv_headers).status_code == 200
    assert client.post(f"/api/v1/fulfillments/{fulfillment['id']}/out-for-delivery", headers=inv_headers).status_code == 200

    deliver = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/deliver",
        json={"actual_delivery_date": "2026-08-25", "delivery_remarks": "Handed to ward nurse"},
        headers=inv_headers,
    )
    assert deliver.status_code == 200, deliver.text
    assert deliver.json()["status"] == "DELIVERED"
    assert deliver.json()["delivered_by_id"] is not None

    complete = client.post(f"/api/v1/fulfillments/{fulfillment['id']}/complete", headers=inv_headers)
    assert complete.status_code == 200, complete.text
    body = complete.json()
    assert body["status"] == "COMPLETED"
    for item in body["items"]:
        assert item["delivered_quantity"] == item["allocated_quantity"]

    # Spare part: a real STOCK_OUT transaction referencing the fulfillment number.
    txns = db_session.query(InventoryTransaction).filter(
        InventoryTransaction.item_id == spare["id"], InventoryTransaction.transaction_type == "STOCK_OUT",
    ).all()
    assert len(txns) == 1
    assert txns[0].quantity == 3
    assert txns[0].reference == fulfillment["fulfillment_number"]

    # Consumable: opening_quantity deducted only now, not at allocation time.
    updated_consumable = admin_client.get("/api/v1/consumables?page=1&page_size=25").json()
    row = next(c for c in updated_consumable["items"] if c["id"] == consumable["id"])
    assert row["opening_quantity"] == 8

    # Serialized asset: relocated to the requirement's branch/department, not deleted,
    # status updated, and a history trail preserved.
    updated_asset = admin_client.get(f"/api/v1/assets/{asset['id']}").json()
    assert updated_asset["department_id"] == department["id"]
    # Module 19 fix: was hardcoded "Active", which is not a member of
    # LIFECYCLE_STATUSES (schemas/asset_lifecycle.py) and so silently dropped out of
    # the dashboard's operational count — see test_asset_lifecycle.py's
    # test_fulfillment_completion_sets_operational_status_counted_in_dashboard.
    assert updated_asset["status"] == "OPERATIONAL"
    history = db_session.query(AssetHistory).filter(AssetHistory.asset_id == asset["id"]).all()
    assert any(h.event_type == "DEPARTMENT_CHANGE" for h in history)
    assert any(h.event_type == "STATUS_CHANGE" for h in history)

    logs = db_session.query(AuditLog).filter(AuditLog.entity == "fulfillment", AuditLog.entity_id == fulfillment["id"]).all()
    actions = {log.action for log in logs}
    assert {"CREATE", "ALLOCATE", "PROCESS", "PACK", "OUT_FOR_DELIVERY", "DELIVER", "COMPLETE"}.issubset(actions)

    history_response = client.get(f"/api/v1/fulfillments/{fulfillment['id']}/history", headers=inv_headers)
    to_statuses = [h["to_status"] for h in history_response.json()]
    assert to_statuses == [
        "PENDING_ALLOCATION", "ALLOCATED", "UNDER_PROCESS", "PACKED", "OUT_FOR_DELIVERY", "DELIVERED", "COMPLETED",
    ]


# ---------------------------------------------------------------------------
# RBAC / visibility
# ---------------------------------------------------------------------------

def test_hospital_unit_manager_can_view_own_fulfillment_but_not_act(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement, tl_headers = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl18@example.com",
    )
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()

    view = client.get(f"/api/v1/fulfillments/{fulfillment['id']}", headers=tl_headers)
    assert view.status_code == 200

    act = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": fulfillment["items"][0]["requirement_item_id"], "asset_ids": []}]},
        headers=tl_headers,
    )
    assert act.status_code == 403


def test_other_hospital_unit_manager_cannot_view_someone_elses_fulfillment(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl19a@example.com",
    )
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()
    create_user(db_session, "tl19b@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    other_headers = auth_headers(client, "tl19b@example.com", "Password123!")

    response = client.get(f"/api/v1/fulfillments/{fulfillment['id']}", headers=other_headers)
    assert response.status_code == 404


def test_inventory_manager_has_broad_list_visibility(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl20@example.com",
    )
    admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]})
    inv_headers = _inventory_manager_headers(client, db_session)

    listing = client.get("/api/v1/fulfillments", headers=inv_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


# ---------------------------------------------------------------------------
# List filters / search
# ---------------------------------------------------------------------------

def test_list_filter_by_status_and_search(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    req_a, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl21a@example.com",
    )
    req_b, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl21b@example.com",
    )
    ful_a = admin_client.post("/api/v1/fulfillments", json={"requirement_id": req_a["id"]}).json()
    admin_client.post("/api/v1/fulfillments", json={"requirement_id": req_b["id"]})
    inv_headers = _inventory_manager_headers(client, db_session)
    client.post(f"/api/v1/fulfillments/{ful_a['id']}/cancel", json={}, headers=inv_headers)

    by_status = client.get("/api/v1/fulfillments?status_filter=CANCELLED", headers=inv_headers)
    assert by_status.json()["total"] == 1

    by_search = client.get(f"/api/v1/fulfillments?search={req_a['request_number']}", headers=inv_headers)
    assert by_search.json()["total"] == 1
    assert by_search.json()["items"][0]["fulfillment_number"] == ful_a["fulfillment_number"]


# ---------------------------------------------------------------------------
# Part B — central warehouse device stock + CONDEMNED/RETIRED exclusion
# ---------------------------------------------------------------------------

def _make_central_branch(admin_client, name="Central Warehouse QA"):
    return admin_client.post(
        "/api/v1/branches", json={"name": name, "status": "ACTIVE", "is_central_warehouse": True}
    ).json()


def test_central_device_stock_counts_physical_assets_not_models(admin_client):
    central = _make_central_branch(admin_client)
    model_a = _make_device_model(admin_client, "Central TENS/EMS Combo")
    model_b = _make_device_model(admin_client, "Central Multiparameter Monitor")
    _make_asset(admin_client, model_a["id"], central["id"], "QA-CENTRAL-001")
    _make_asset(admin_client, model_a["id"], central["id"], "QA-CENTRAL-002")
    _make_asset(admin_client, model_b["id"], central["id"], "QA-CENTRAL-003")

    response = admin_client.get("/api/v1/assets/central-device-stock")
    assert response.status_code == 200, response.text
    rows = {r["device_model_id"]: r["available_count"] for r in response.json()}
    assert rows[model_a["id"]] == 2
    assert rows[model_b["id"]] == 1


def _condemn_asset(admin_client, client, db_session, branch_id, asset_id, *, tl_email, mgr_email):
    """Drives the real condemnation workflow (raise CONDEMNATION requirement -> submit
    -> Manager approve) — a direct PATCH status=CONDEMNED is deliberately rejected
    without an approved condemnation requirement (see routers/devices.py:update_asset),
    so tests must go through the same path a real user would."""
    create_user(db_session, tl_email, "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, tl_email, "Password123!")
    create_user(db_session, mgr_email, "MANAGER", "Password123!")
    mgr_headers = auth_headers(client, mgr_email, "Password123!")
    created = client.post(
        "/api/v1/requirements",
        json={"branch_id": branch_id, "reason": "QA condemnation", "items": [
            {"item_type": "CONDEMNATION", "asset_id": asset_id, "requested_quantity": 1},
        ]},
        headers=tl_headers,
    ).json()
    client.post(f"/api/v1/requirements/{created['id']}/submit", headers=tl_headers)
    approved = client.post(f"/api/v1/requirements/{created['id']}/approve", json={}, headers=mgr_headers)
    assert approved.status_code == 200, approved.text


def test_central_device_stock_excludes_condemned_and_retired(admin_client, client, db_session):
    central = _make_central_branch(admin_client, "Central Warehouse QA Condemned")
    model = _make_device_model(admin_client, "Central Oxygen Concentrator")
    ok_asset = _make_asset(admin_client, model["id"], central["id"], "QA-CENTRAL-010")
    condemned_asset = _make_asset(admin_client, model["id"], central["id"], "QA-CENTRAL-011")
    retired_asset = _make_asset(admin_client, model["id"], central["id"], "QA-CENTRAL-012")
    _condemn_asset(
        admin_client, client, db_session, central["id"], condemned_asset["id"],
        tl_email="tl-central-condemn@example.com", mgr_email="mgr-central-condemn@example.com",
    )
    archived = admin_client.post(f"/api/v1/assets/{retired_asset['id']}/archive")
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "RETIRED"

    response = admin_client.get("/api/v1/assets/central-device-stock")
    rows = {r["device_model_id"]: r["available_count"] for r in response.json()}
    assert rows[model["id"]] == 1


def test_central_device_stock_excludes_other_branch_assets(admin_client):
    central = _make_central_branch(admin_client, "Central Warehouse QA Isolation")
    other_branch = _make_branch(admin_client, "Non-Central Branch QA")
    model = _make_device_model(admin_client, "Central Isolation Model")
    _make_asset(admin_client, model["id"], central["id"], "QA-CENTRAL-020")
    _make_asset(admin_client, model["id"], other_branch["id"], "QA-BRANCH-020")

    response = admin_client.get("/api/v1/assets/central-device-stock")
    rows = {r["device_model_id"]: r["available_count"] for r in response.json()}
    assert rows[model["id"]] == 1


def test_central_device_stock_changes_after_fulfillment_moves_asset(admin_client, client, db_session):
    central = _make_central_branch(admin_client, "Central Warehouse QA Fulfillment")
    requesting_branch = _make_branch(admin_client, "Requesting Branch QA")
    model = _make_device_model(admin_client, "Central Fulfillment Model")
    asset = _make_asset(admin_client, model["id"], central["id"], "QA-CENTRAL-030")

    before = admin_client.get("/api/v1/assets/central-device-stock").json()
    before_count = next((r["available_count"] for r in before if r["device_model_id"] == model["id"]), 0)
    assert before_count == 1

    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, requesting_branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl-central-stock@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session, "invmgr-central-stock@example.com")
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()
    client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": fulfillment["items"][0]["requirement_item_id"], "asset_ids": [asset["id"]]}]},
        headers=inv_headers,
    )
    client.post(f"/api/v1/fulfillments/{fulfillment['id']}/process", json={}, headers=inv_headers)
    client.post(f"/api/v1/fulfillments/{fulfillment['id']}/pack", json={}, headers=inv_headers)
    client.post(f"/api/v1/fulfillments/{fulfillment['id']}/out-for-delivery", json={}, headers=inv_headers)
    client.post(f"/api/v1/fulfillments/{fulfillment['id']}/deliver", json={}, headers=inv_headers)
    completed = client.post(f"/api/v1/fulfillments/{fulfillment['id']}/complete", json={}, headers=inv_headers)
    assert completed.status_code == 200, completed.text

    after = admin_client.get("/api/v1/assets/central-device-stock").json()
    after_count = next((r["available_count"] for r in after if r["device_model_id"] == model["id"]), 0)
    assert after_count == 0

    moved_asset = admin_client.get(f"/api/v1/assets/{asset['id']}").json()
    assert moved_asset["branch_id"] == requesting_branch["id"]


def test_central_device_stock_requires_inventory_read(client, db_session):
    create_user(db_session, "central-stock-viewer@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "central-stock-viewer@example.com", "Password123!")
    response = client.get("/api/v1/assets/central-device-stock", headers=headers)
    assert response.status_code == 403


def test_condemned_asset_never_offered_for_allocation(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Condemned Allocation QA Branch")
    model = _make_device_model(admin_client, "Condemned Allocation Model")
    good_asset = _make_asset(admin_client, model["id"], branch["id"], "QA-COND-ALLOC-001")
    condemned_asset = _make_asset(admin_client, model["id"], branch["id"], "QA-COND-ALLOC-002")
    _condemn_asset(
        admin_client, client, db_session, branch["id"], condemned_asset["id"],
        tl_email="tl-alloc-condemn@example.com", mgr_email="mgr-alloc-condemn@example.com",
    )

    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="tl-condemned-alloc@example.com",
    )
    inv_headers = _inventory_manager_headers(client, db_session, "invmgr-condemned-alloc@example.com")
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()

    blocked = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{
            "requirement_item_id": fulfillment["items"][0]["requirement_item_id"],
            "asset_ids": [condemned_asset["id"]],
        }]},
        headers=inv_headers,
    )
    assert blocked.status_code == 409, blocked.text

    allowed = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{
            "requirement_item_id": fulfillment["items"][0]["requirement_item_id"],
            "asset_ids": [good_asset["id"]],
        }]},
        headers=inv_headers,
    )
    assert allowed.status_code == 200, allowed.text
