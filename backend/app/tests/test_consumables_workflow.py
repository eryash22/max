"""Consumables workflow — central<->branch stock transfer, ledger, and the
`stock_status`/update endpoints added alongside it. The Requirement -> Manager ->
Inventory Manager -> Fulfillment pipeline itself (self-approval blocked, RBAC,
partial fulfillment, negative-stock prevention, audit logging) is Module 16A/17A/
20, already covered by test_requirements.py/test_fulfillments.py/
test_manager_role.py — this file only exercises what's new: the central pool
decreasing, the requesting branch's own stock appearing, the transaction ledger
recording it, and that a pre-existing device/spare-part requirement is unaffected.
"""
from app.models.audit import AuditLog
from app.models.identity import User

from .conftest import auth_headers, create_user


def _make_branch(admin_client, name="Saket"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _get_or_create_user(db_session, email, role_code):
    """`create_user` always inserts — safe to call once per email per test, but
    `_fulfill_to_completion` (and other per-call helpers below) may run more than
    once within a single test against the same default email, which would violate
    `users.email`'s unique constraint. This makes that reuse safe."""
    existing = db_session.query(User).filter(User.email == email).first()
    if existing is not None:
        return existing
    return create_user(db_session, email, role_code, "Password123!")


def _make_consumable(admin_client, code="CON-100", opening_quantity=20, branch_id=None, **extra):
    payload = {"item_code": code, "name": "ECG Electrodes", "unit": "PCS", "opening_quantity": opening_quantity}
    if branch_id is not None:
        payload["branch_id"] = branch_id
    payload.update(extra)
    response = admin_client.post("/api/v1/consumables", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _inventory_manager_headers(client, db_session, email="invmgr-cw@example.com"):
    _get_or_create_user(db_session, email, "INVENTORY_MANAGER")
    return auth_headers(client, email, "Password123!")


def _manager_headers(client, db_session, email="manager-cw@example.com"):
    _get_or_create_user(db_session, email, "MANAGER")
    return auth_headers(client, email, "Password123!")


def _raise_and_approve_consumable_requirement(
    admin_client, client, db_session, branch_id, consumable_id, quantity, *,
    tl_email, manager_headers,
):
    _get_or_create_user(db_session, tl_email, "HOSPITAL_UNIT_MANAGER")
    tl_headers = auth_headers(client, tl_email, "Password123!")

    created = client.post(
        "/api/v1/requirements",
        json={
            "branch_id": branch_id, "reason": "Consumables workflow test",
            "items": [{"item_type": "CONSUMABLE", "consumable_id": consumable_id, "requested_quantity": quantity}],
        },
        headers=tl_headers,
    )
    assert created.status_code == 201, created.text
    requirement = created.json()

    submitted = client.post(f"/api/v1/requirements/{requirement['id']}/submit", headers=tl_headers)
    assert submitted.status_code == 200, submitted.text

    approved = client.post(f"/api/v1/requirements/{requirement['id']}/approve", json={}, headers=manager_headers)
    assert approved.status_code == 200, approved.text
    return approved.json(), tl_headers


def _fulfill_to_completion(admin_client, client, db_session, requirement, allocate_quantity):
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()
    requirement_item_id = fulfillment["items"][0]["requirement_item_id"]

    allocate = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": requirement_item_id, "quantity": allocate_quantity}]},
        headers=inv_headers,
    )
    assert allocate.status_code == 200, allocate.text

    for action in ("process", "pack", "out-for-delivery"):
        response = client.post(f"/api/v1/fulfillments/{fulfillment['id']}/{action}", headers=inv_headers)
        assert response.status_code == 200, response.text

    deliver = client.post(f"/api/v1/fulfillments/{fulfillment['id']}/deliver", json={}, headers=inv_headers)
    assert deliver.status_code == 200, deliver.text

    complete = client.post(f"/api/v1/fulfillments/{fulfillment['id']}/complete", headers=inv_headers)
    assert complete.status_code == 200, complete.text
    return complete.json(), inv_headers


# ---------------------------------------------------------------------------
# Consumable model / category / branch scoping
# ---------------------------------------------------------------------------

def test_same_item_code_allowed_for_central_and_branch_rows(admin_client):
    branch = _make_branch(admin_client)
    central = _make_consumable(admin_client, "CON-DUP", opening_quantity=50)
    branch_row = _make_consumable(admin_client, "CON-DUP", opening_quantity=0, branch_id=branch["id"])
    assert central["id"] != branch_row["id"]
    assert central["branch_id"] is None
    assert branch_row["branch_id"] == branch["id"]


def test_duplicate_item_code_within_same_branch_rejected(admin_client):
    branch = _make_branch(admin_client)
    _make_consumable(admin_client, "CON-DUP2", opening_quantity=10, branch_id=branch["id"])
    response = admin_client.post(
        "/api/v1/consumables",
        json={"item_code": "CON-DUP2", "name": "Duplicate", "branch_id": branch["id"], "opening_quantity": 5},
    )
    assert response.status_code == 409


def test_consumable_stock_status_computed(admin_client):
    out_of_stock = _make_consumable(admin_client, "CON-OOS", opening_quantity=0)
    low = _make_consumable(admin_client, "CON-LOW", opening_quantity=5, reorder_level=10)
    normal = _make_consumable(admin_client, "CON-OK", opening_quantity=50, reorder_level=10)
    assert out_of_stock["stock_status"] == "OUT_OF_STOCK"
    assert low["stock_status"] == "LOW_STOCK"
    assert normal["stock_status"] == "NORMAL"


def test_update_consumable_endpoint(admin_client):
    consumable = _make_consumable(admin_client, "CON-UPD", opening_quantity=10)
    response = admin_client.patch(f"/api/v1/consumables/{consumable['id']}", json={"reorder_level": 3, "supplier": "Acme"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reorder_level"] == 3
    assert body["supplier"] == "Acme"
    assert body["opening_quantity"] == 10  # untouched


# ---------------------------------------------------------------------------
# Delete — Batch F: hard delete only when zero references, else 409/deactivate
# ---------------------------------------------------------------------------

def test_delete_consumable_with_no_references_hard_deletes(admin_client, db_session):
    consumable = _make_consumable(admin_client, "CON-DEL-CLEAN", opening_quantity=5)
    response = admin_client.delete(f"/api/v1/consumables/{consumable['id']}")
    assert response.status_code == 204, response.text

    get_response = admin_client.get(f"/api/v1/consumables/{consumable['id']}")
    assert get_response.status_code == 404

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "consumable", AuditLog.entity_id == consumable["id"], AuditLog.action == "DELETE"
    ).first()
    assert log is not None


def test_delete_consumable_referenced_by_transaction_is_blocked(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Malviya Nagar")
    central = _make_consumable(admin_client, "CON-DEL-TXN", opening_quantity=100)

    requirement, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, branch["id"], central["id"], 20,
        tl_email="tl-del-txn@example.com", manager_headers=_manager_headers(client, db_session, "mgr-del-txn@example.com"),
    )
    _fulfill_to_completion(admin_client, client, db_session, requirement, 20)

    # `central` now has a ConsumableTransaction (TRANSFER) and a RequirementItem
    # referencing it — both should block a hard delete.
    response = admin_client.delete(f"/api/v1/consumables/{central['id']}")
    assert response.status_code == 409, response.text

    still_there = admin_client.get(f"/api/v1/consumables/{central['id']}")
    assert still_there.status_code == 200
    assert still_there.json()["status"] == "ACTIVE"


def test_delete_blocked_consumable_can_instead_be_deactivated(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Pitampura")
    central = _make_consumable(admin_client, "CON-DEL-DEACT", opening_quantity=100)

    requirement, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, branch["id"], central["id"], 10,
        tl_email="tl-del-deact@example.com", manager_headers=_manager_headers(client, db_session, "mgr-del-deact@example.com"),
    )
    _fulfill_to_completion(admin_client, client, db_session, requirement, 10)

    blocked = admin_client.delete(f"/api/v1/consumables/{central['id']}")
    assert blocked.status_code == 409

    deactivated = admin_client.patch(f"/api/v1/consumables/{central['id']}", json={"status": "INACTIVE"})
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["status"] == "INACTIVE"

    still_exists = admin_client.get(f"/api/v1/consumables/{central['id']}")
    assert still_exists.status_code == 200
    assert still_exists.json()["status"] == "INACTIVE"

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "consumable", AuditLog.entity_id == central["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None


def test_delete_missing_consumable_404(admin_client):
    response = admin_client.delete("/api/v1/consumables/does-not-exist")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Central -> branch stock transfer on fulfillment completion
# ---------------------------------------------------------------------------

def test_central_stock_decreases_and_branch_stock_appears_on_completion(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Vikaspuri")
    central = _make_consumable(admin_client, "CON-XFER", opening_quantity=250)

    requirement, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, branch["id"], central["id"], 100,
        tl_email="tl-xfer@example.com", manager_headers=_manager_headers(client, db_session, "mgr-xfer@example.com"),
    )
    _fulfill_to_completion(admin_client, client, db_session, requirement, 100)

    updated_central = admin_client.get(f"/api/v1/consumables/{central['id']}").json()
    assert updated_central["opening_quantity"] == 150

    branch_rows = admin_client.get(f"/api/v1/consumables?branch_id={branch['id']}").json()["items"]
    branch_row = next(r for r in branch_rows if r["item_code"] == "CON-XFER")
    assert branch_row["opening_quantity"] == 100
    assert branch_row["branch_id"] == branch["id"]


def test_second_transfer_credits_existing_branch_row_not_a_new_one(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Patparganj")
    central = _make_consumable(admin_client, "CON-XFER2", opening_quantity=250)

    manager_headers = _manager_headers(client, db_session, "mgr-xfer2@example.com")
    req1, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, branch["id"], central["id"], 40,
        tl_email="tl-xfer2a@example.com", manager_headers=manager_headers,
    )
    _fulfill_to_completion(admin_client, client, db_session, req1, 40)

    req2, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, branch["id"], central["id"], 25,
        tl_email="tl-xfer2b@example.com", manager_headers=manager_headers,
    )
    _fulfill_to_completion(admin_client, client, db_session, req2, 25)

    branch_rows = admin_client.get(f"/api/v1/consumables?branch_id={branch['id']}").json()["items"]
    matching = [r for r in branch_rows if r["item_code"] == "CON-XFER2"]
    assert len(matching) == 1
    assert matching[0]["opening_quantity"] == 65

    central_updated = admin_client.get(f"/api/v1/consumables/{central['id']}").json()
    assert central_updated["opening_quantity"] == 250 - 65


def test_partial_fulfillment_transfers_only_allocated_quantity(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Shalimar Bagh")
    central = _make_consumable(admin_client, "CON-PARTIAL", opening_quantity=60)

    requirement, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, branch["id"], central["id"], 100,
        tl_email="tl-partial@example.com", manager_headers=_manager_headers(client, db_session, "mgr-partial@example.com"),
    )
    completed, _ = _fulfill_to_completion(admin_client, client, db_session, requirement, 60)
    item = completed["items"][0]
    assert item["requested_quantity"] == 100
    assert item["allocated_quantity"] == 60
    assert item["delivered_quantity"] == 60
    assert item["pending_quantity"] == 40

    central_updated = admin_client.get(f"/api/v1/consumables/{central['id']}").json()
    assert central_updated["opening_quantity"] == 0
    assert central_updated["stock_status"] == "OUT_OF_STOCK"

    branch_rows = admin_client.get(f"/api/v1/consumables?branch_id={branch['id']}").json()["items"]
    branch_row = next(r for r in branch_rows if r["item_code"] == "CON-PARTIAL")
    assert branch_row["opening_quantity"] == 60


def test_branch_requesting_its_own_directly_owned_stock_does_not_duplicate(admin_client, client, db_session):
    """A Consumable already scoped to the requesting branch (no central row involved)
    is just consumed in place — no second row, no phantom transfer-in."""
    branch = _make_branch(admin_client, "Dwarka")
    own_stock = _make_consumable(admin_client, "CON-OWN", opening_quantity=30, branch_id=branch["id"])

    requirement, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, branch["id"], own_stock["id"], 10,
        tl_email="tl-own@example.com", manager_headers=_manager_headers(client, db_session, "mgr-own@example.com"),
    )
    _fulfill_to_completion(admin_client, client, db_session, requirement, 10)

    branch_rows = admin_client.get(f"/api/v1/consumables?branch_id={branch['id']}").json()["items"]
    matching = [r for r in branch_rows if r["item_code"] == "CON-OWN"]
    assert len(matching) == 1
    assert matching[0]["id"] == own_stock["id"]
    assert matching[0]["opening_quantity"] == 20


# ---------------------------------------------------------------------------
# Transaction ledger
# ---------------------------------------------------------------------------

def test_transfer_recorded_in_ledger_and_visible_from_both_rows(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Panchsheel Park")
    central = _make_consumable(admin_client, "CON-LEDGER", opening_quantity=80)

    requirement, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, branch["id"], central["id"], 30,
        tl_email="tl-ledger@example.com", manager_headers=_manager_headers(client, db_session, "mgr-ledger@example.com"),
    )
    completed, inv_headers = _fulfill_to_completion(admin_client, client, db_session, requirement, 30)

    central_txns = admin_client.get(f"/api/v1/consumables/{central['id']}/transactions").json()["items"]
    assert len(central_txns) == 1
    txn = central_txns[0]
    assert txn["transaction_type"] == "TRANSFER"
    assert txn["quantity"] == 30
    assert txn["source_branch_id"] is None
    assert txn["destination_branch_id"] == branch["id"]
    assert txn["requirement_id"] == requirement["id"]
    assert txn["reference"] == completed["fulfillment_number"]
    assert txn["performed_by"] is not None

    branch_rows = admin_client.get(f"/api/v1/consumables?branch_id={branch['id']}").json()["items"]
    branch_row = next(r for r in branch_rows if r["item_code"] == "CON-LEDGER")
    branch_txns = admin_client.get(f"/api/v1/consumables/{branch_row['id']}/transactions").json()["items"]
    assert len(branch_txns) == 1
    assert branch_txns[0]["id"] == txn["id"]


def test_own_stock_consumption_recorded_as_stock_out(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Greater Kailash")
    own_stock = _make_consumable(admin_client, "CON-OUT", opening_quantity=15, branch_id=branch["id"])

    requirement, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, branch["id"], own_stock["id"], 5,
        tl_email="tl-out@example.com", manager_headers=_manager_headers(client, db_session, "mgr-out@example.com"),
    )
    _fulfill_to_completion(admin_client, client, db_session, requirement, 5)

    txns = admin_client.get(f"/api/v1/consumables/{own_stock['id']}/transactions").json()["items"]
    assert len(txns) == 1
    assert txns[0]["transaction_type"] == "STOCK_OUT"
    assert txns[0]["quantity"] == 5
    assert txns[0]["source_branch_id"] == branch["id"]
    assert txns[0]["destination_branch_id"] is None


# ---------------------------------------------------------------------------
# Negative stock prevention still applies through the transfer path
# ---------------------------------------------------------------------------

def test_over_allocation_beyond_central_stock_rejected(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Rohini")
    central = _make_consumable(admin_client, "CON-LIMIT", opening_quantity=10)

    requirement, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, branch["id"], central["id"], 50,
        tl_email="tl-limit@example.com", manager_headers=_manager_headers(client, db_session, "mgr-limit@example.com"),
    )
    inv_headers = _inventory_manager_headers(client, db_session)
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()
    requirement_item_id = fulfillment["items"][0]["requirement_item_id"]

    blocked = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": requirement_item_id, "quantity": 20}]},
        headers=inv_headers,
    )
    assert blocked.status_code == 409

    central_unchanged = admin_client.get(f"/api/v1/consumables/{central['id']}").json()
    assert central_unchanged["opening_quantity"] == 10


# ---------------------------------------------------------------------------
# Pre-existing device workflow unaffected
# ---------------------------------------------------------------------------

def test_device_requirement_workflow_unaffected_by_consumable_changes(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Nagpur")
    model = admin_client.post("/api/v1/device-models", json={"name": "Infusion Pump", "category": "PUMP"}).json()
    asset = admin_client.post(
        "/api/v1/assets", json={"asset_number": "AST-CW-1", "device_model_id": model["id"], "branch_id": branch["id"]},
    )
    assert asset.status_code == 201, asset.text
    asset = asset.json()

    create_user(db_session, "tl-device-cw@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl-device-cw@example.com", "Password123!")
    created = client.post(
        "/api/v1/requirements",
        json={
            "branch_id": branch["id"], "reason": "Device workflow regression check",
            "items": [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        },
        headers=tl_headers,
    )
    assert created.status_code == 201, created.text
    requirement = created.json()
    assert client.post(f"/api/v1/requirements/{requirement['id']}/submit", headers=tl_headers).status_code == 200
    approved = admin_client.post(f"/api/v1/requirements/{requirement['id']}/approve", json={})
    assert approved.status_code == 200, approved.text

    inv_headers = _inventory_manager_headers(client, db_session, "invmgr-device-cw@example.com")
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": approved.json()["id"]}).json()
    requirement_item_id = fulfillment["items"][0]["requirement_item_id"]
    allocate = client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": requirement_item_id, "asset_ids": [asset["id"]]}]},
        headers=inv_headers,
    )
    assert allocate.status_code == 200, allocate.text
    for action in ("process", "pack", "out-for-delivery"):
        assert client.post(f"/api/v1/fulfillments/{fulfillment['id']}/{action}", headers=inv_headers).status_code == 200
    assert client.post(f"/api/v1/fulfillments/{fulfillment['id']}/deliver", json={}, headers=inv_headers).status_code == 200
    complete = client.post(f"/api/v1/fulfillments/{fulfillment['id']}/complete", headers=inv_headers)
    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# Business-rule correction — consumables are fulfilled from CENTRAL STOCK only,
# never branch-to-branch. See services/requirement_service.py's
# _validate_item_reference CONSUMABLE check and routers/inventory.py's
# list_consumables central_only filter.
# ---------------------------------------------------------------------------

def test_saket_requirement_fulfilled_only_from_central_stock(admin_client, client, db_session):
    saket = _make_branch(admin_client, "Saket Central Rule")
    central = _make_consumable(admin_client, "CON-CENTRAL-SAKET", opening_quantity=250)

    requirement, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, saket["id"], central["id"], 100,
        tl_email="tl-saket-central@example.com", manager_headers=_manager_headers(client, db_session, "mgr-saket-central@example.com"),
    )
    _fulfill_to_completion(admin_client, client, db_session, requirement, 100)

    central_after = admin_client.get(f"/api/v1/consumables/{central['id']}").json()
    assert central_after["opening_quantity"] == 150

    saket_rows = admin_client.get(f"/api/v1/consumables?branch_id={saket['id']}").json()["items"]
    matching = [r for r in saket_rows if r["item_code"] == "CON-CENTRAL-SAKET"]
    assert len(matching) == 1
    assert matching[0]["opening_quantity"] == 100


def test_max_blk_requirement_fulfilled_only_from_central_stock(admin_client, client, db_session):
    max_blk = _make_branch(admin_client, "Max BLK Central Rule")
    central = _make_consumable(admin_client, "CON-CENTRAL-MAXBLK", opening_quantity=60)

    requirement, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, max_blk["id"], central["id"], 20,
        tl_email="tl-maxblk-central@example.com", manager_headers=_manager_headers(client, db_session, "mgr-maxblk-central@example.com"),
    )
    _fulfill_to_completion(admin_client, client, db_session, requirement, 20)

    central_after = admin_client.get(f"/api/v1/consumables/{central['id']}").json()
    assert central_after["opening_quantity"] == 40

    max_blk_rows = admin_client.get(f"/api/v1/consumables?branch_id={max_blk['id']}").json()["items"]
    matching = [r for r in max_blk_rows if r["item_code"] == "CON-CENTRAL-MAXBLK"]
    assert matching[0]["opening_quantity"] == 20


def test_saket_stock_never_used_to_fulfill_max_blk(admin_client, client, db_session):
    saket = _make_branch(admin_client, "Saket Isolation")
    max_blk = _make_branch(admin_client, "Max BLK Isolation")
    saket_stock = _make_consumable(admin_client, "CON-ISOLATION", opening_quantity=40, branch_id=saket["id"])

    _get_or_create_user(db_session, "tl-isolation@example.com", "HOSPITAL_UNIT_MANAGER")
    headers = auth_headers(client, "tl-isolation@example.com", "Password123!")
    response = client.post(
        "/api/v1/requirements",
        json={
            "branch_id": max_blk["id"], "reason": "Cross-branch attempt",
            "items": [{"item_type": "CONSUMABLE", "consumable_id": saket_stock["id"], "requested_quantity": 10}],
        },
        headers=headers,
    )
    assert response.status_code == 404, response.text
    assert "another branch" in response.json()["detail"]

    unchanged = admin_client.get(f"/api/v1/consumables/{saket_stock['id']}").json()
    assert unchanged["opening_quantity"] == 40


def test_max_blk_stock_never_used_to_fulfill_saket(admin_client, client, db_session):
    saket = _make_branch(admin_client, "Saket Isolation 2")
    max_blk = _make_branch(admin_client, "Max BLK Isolation 2")
    max_blk_stock = _make_consumable(admin_client, "CON-ISOLATION-2", opening_quantity=25, branch_id=max_blk["id"])

    _get_or_create_user(db_session, "tl-isolation2@example.com", "HOSPITAL_UNIT_MANAGER")
    headers = auth_headers(client, "tl-isolation2@example.com", "Password123!")
    response = client.post(
        "/api/v1/requirements",
        json={
            "branch_id": saket["id"], "reason": "Cross-branch attempt",
            "items": [{"item_type": "CONSUMABLE", "consumable_id": max_blk_stock["id"], "requested_quantity": 5}],
        },
        headers=headers,
    )
    assert response.status_code == 404, response.text

    unchanged = admin_client.get(f"/api/v1/consumables/{max_blk_stock['id']}").json()
    assert unchanged["opening_quantity"] == 25


def test_ledger_records_central_to_requesting_branch(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Ledger Rule Branch")
    central = _make_consumable(admin_client, "CON-LEDGER-RULE", opening_quantity=90)

    requirement, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, branch["id"], central["id"], 15,
        tl_email="tl-ledger-rule@example.com", manager_headers=_manager_headers(client, db_session, "mgr-ledger-rule@example.com"),
    )
    _fulfill_to_completion(admin_client, client, db_session, requirement, 15)

    txns = admin_client.get(f"/api/v1/consumables/{central['id']}/transactions").json()["items"]
    transfer = next(t for t in txns if t["transaction_type"] == "TRANSFER")
    assert transfer["source_branch_id"] is None
    assert transfer["destination_branch_id"] == branch["id"]
    assert transfer["quantity"] == 15


def test_direct_api_cannot_move_stock_between_two_other_branches(admin_client, client, db_session):
    """Direct API tampering: even with a crafted payload, a requirement raised
    for Branch B can never reference Branch A's own stock row."""
    branch_a = _make_branch(admin_client, "Tamper Branch A")
    branch_b = _make_branch(admin_client, "Tamper Branch B")
    branch_a_stock = _make_consumable(admin_client, "CON-TAMPER", opening_quantity=70, branch_id=branch_a["id"])

    tamper_attempt = admin_client.post(
        "/api/v1/requirements",
        json={
            "branch_id": branch_b["id"], "reason": "Tamper attempt",
            "items": [{"item_type": "CONSUMABLE", "consumable_id": branch_a_stock["id"], "requested_quantity": 30}],
        },
    )
    assert tamper_attempt.status_code == 404
    unchanged = admin_client.get(f"/api/v1/consumables/{branch_a_stock['id']}").json()
    assert unchanged["opening_quantity"] == 70


def test_consumable_picker_central_only_excludes_branch_stock(admin_client):
    branch = _make_branch(admin_client, "Picker Exclusion Branch")
    central = _make_consumable(admin_client, "CON-PICKER", opening_quantity=50)
    _make_consumable(admin_client, "CON-PICKER", opening_quantity=0, branch_id=branch["id"])

    picker = admin_client.get("/api/v1/consumables?central_only=true&page_size=200").json()
    item_codes_and_branches = [(r["item_code"], r["branch_id"]) for r in picker["items"]]
    assert (central["item_code"], None) in item_codes_and_branches
    assert all(branch_id is None for _, branch_id in item_codes_and_branches)


def test_existing_inventory_manager_fulfillment_flow_unaffected(admin_client, client, db_session):
    """Inventory Manager still allocates/completes exactly as before — this rule
    only changes which consumable_id a Requirement is allowed to reference."""
    branch = _make_branch(admin_client, "InvMgr Unaffected Branch")
    central = _make_consumable(admin_client, "CON-INVMGR", opening_quantity=200)

    requirement, _ = _raise_and_approve_consumable_requirement(
        admin_client, client, db_session, branch["id"], central["id"], 50,
        tl_email="tl-invmgr-unaffected@example.com", manager_headers=_manager_headers(client, db_session, "mgr-invmgr-unaffected@example.com"),
    )
    completed, _ = _fulfill_to_completion(admin_client, client, db_session, requirement, 50)
    assert completed["status"] == "COMPLETED"
