"""Business-workflow audit — device condemnation workflow.

UNIT_HEAD/TL raise a CONDEMNATION requirement item referencing a real Asset ->
MANAGER approves/rejects (existing requirement approval architecture, no new
model) -> on approval, the Asset auto-transitions to CONDEMNED with an
AssetHistory entry and an audit log record. A direct PATCH to status=CONDEMNED
with no approved condemnation requirement is rejected.
"""
from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _make_branch(admin_client, name="Condemnation Test Branch"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_asset(admin_client, asset_number="AST-COND-001", status="OPERATIONAL"):
    branch = _make_branch(admin_client, f"Branch-{asset_number}")
    model = admin_client.post(
        "/api/v1/device-models", json={"name": f"Model-{asset_number}", "category": "Ventilator"}
    ).json()
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": model["id"], "branch_id": branch["id"], "status": status},
    ).json()
    return branch, model, asset


def _tl_headers(client, db_session, email="tl-cond@example.com"):
    create_user(db_session, email, "HOSPITAL_UNIT_MANAGER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _manager_headers(client, db_session, email="mgr-cond@example.com"):
    create_user(db_session, email, "MANAGER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _raise_condemnation(client, headers, branch_id, asset_id, reason="Device beyond economical repair"):
    payload = {
        "branch_id": branch_id,
        "reason": reason,
        "items": [{"item_type": "CONDEMNATION", "asset_id": asset_id, "requested_quantity": 1}],
    }
    return client.post("/api/v1/requirements", json=payload, headers=headers)


def _submit(client, headers, requirement_id):
    return client.post(f"/api/v1/requirements/{requirement_id}/submit", headers=headers)


# ---- 1. TL can create a condemnation requirement ----

def test_tl_can_create_condemnation_requirement(admin_client, client, db_session):
    branch, _, asset = _make_asset(admin_client)
    headers = _tl_headers(client, db_session)

    response = _raise_condemnation(client, headers, branch["id"], asset["id"])
    assert response.status_code == 201
    body = response.json()
    assert body["items"][0]["item_type"] == "CONDEMNATION"
    assert body["items"][0]["asset_id"] == asset["id"]
    assert body["items"][0]["device_model_id"] is None


# ---- 2. Condemnation requires an Asset, not a DeviceModel ----

def test_condemnation_rejects_device_model_reference(admin_client, client, db_session):
    branch, model, _ = _make_asset(admin_client)
    headers = _tl_headers(client, db_session, "tl-cond2@example.com")

    payload = {
        "branch_id": branch["id"],
        "reason": "Wrong reference type",
        "items": [{"item_type": "CONDEMNATION", "device_model_id": model["id"], "requested_quantity": 1}],
    }
    response = client.post("/api/v1/requirements", json=payload, headers=headers)
    assert response.status_code == 422


def test_condemnation_requires_asset_id(admin_client, client, db_session):
    branch, _, _ = _make_asset(admin_client, asset_number="AST-COND-NOREF")
    headers = _tl_headers(client, db_session, "tl-cond3@example.com")

    payload = {
        "branch_id": branch["id"],
        "reason": "Missing asset reference",
        "items": [{"item_type": "CONDEMNATION", "requested_quantity": 1}],
    }
    response = client.post("/api/v1/requirements", json=payload, headers=headers)
    assert response.status_code == 422


# ---- 3. TL cannot self-approve ----

def test_tl_cannot_self_approve_condemnation(admin_client, client, db_session):
    branch, _, asset = _make_asset(admin_client, asset_number="AST-COND-SELF")
    headers = _tl_headers(client, db_session, "tl-cond4@example.com")

    created = _raise_condemnation(client, headers, branch["id"], asset["id"]).json()
    _submit(client, headers, created["id"])

    response = client.post(f"/api/v1/requirements/{created['id']}/approve", json={}, headers=headers)
    assert response.status_code == 403


# ---- 4/5/6/7/8. Manager approves -> asset condemned; reject -> not condemned ----

def test_manager_can_approve_condemnation_and_asset_becomes_condemned(admin_client, client, db_session):
    branch, _, asset = _make_asset(admin_client, asset_number="AST-COND-APPROVE")
    tl_headers = _tl_headers(client, db_session, "tl-cond5@example.com")
    mgr_headers = _manager_headers(client, db_session, "mgr-cond5@example.com")

    created = _raise_condemnation(client, tl_headers, branch["id"], asset["id"]).json()
    _submit(client, tl_headers, created["id"])

    approved = client.post(
        f"/api/v1/requirements/{created['id']}/approve", json={"remarks": "Confirmed beyond repair"}, headers=mgr_headers
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"

    asset_after = admin_client.get(f"/api/v1/assets/{asset['id']}").json()
    assert asset_after["status"] == "CONDEMNED"

    # 7. AssetHistory created
    history = admin_client.get(f"/api/v1/assets/{asset['id']}/history").json()
    condemn_entries = [h for h in history["items"] if h["to_value"] == "CONDEMNED"]
    assert len(condemn_entries) == 1
    assert condemn_entries[0]["from_value"] == "OPERATIONAL"
    assert condemn_entries[0]["event_type"] == "STATUS_CHANGE"

    # 8. AuditLog created
    audit_entry = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity == "asset", AuditLog.entity_id == asset["id"], AuditLog.action == "CONDEMN")
        .first()
    )
    assert audit_entry is not None


def test_manager_rejection_does_not_condemn_asset(admin_client, client, db_session):
    branch, _, asset = _make_asset(admin_client, asset_number="AST-COND-REJECT")
    tl_headers = _tl_headers(client, db_session, "tl-cond6@example.com")
    mgr_headers = _manager_headers(client, db_session, "mgr-cond6@example.com")

    created = _raise_condemnation(client, tl_headers, branch["id"], asset["id"]).json()
    _submit(client, tl_headers, created["id"])

    rejected = client.post(
        f"/api/v1/requirements/{created['id']}/reject", json={"remarks": "Repair is cheaper"}, headers=mgr_headers
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"

    asset_after = admin_client.get(f"/api/v1/assets/{asset['id']}").json()
    assert asset_after["status"] == "OPERATIONAL"


# ---- 9. Direct PATCH bypass is blocked ----

def test_direct_patch_to_condemned_without_approval_is_rejected(admin_client, db_session):
    _, _, asset = _make_asset(admin_client, asset_number="AST-COND-BYPASS")

    response = admin_client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={
            "asset_number": asset["asset_number"], "device_model_id": asset["device_model_id"],
            "branch_id": asset["branch_id"], "status": "CONDEMNED",
        },
    )
    assert response.status_code == 422
    assert "condemnation" in response.json()["detail"].lower()

    asset_after = admin_client.get(f"/api/v1/assets/{asset['id']}").json()
    assert asset_after["status"] == "OPERATIONAL"


def test_direct_patch_to_condemned_allowed_after_approval(admin_client, client, db_session):
    """Defensive path: if an approved condemnation requirement exists for this asset,
    the generic PATCH is not blocked (covers a manual re-application scenario) —
    distinct from the primary auto-transition-on-approval path already covered above."""
    branch, _, asset = _make_asset(admin_client, asset_number="AST-COND-DEFENSIVE")
    tl_headers = _tl_headers(client, db_session, "tl-cond7@example.com")
    mgr_headers = _manager_headers(client, db_session, "mgr-cond7@example.com")

    created = _raise_condemnation(client, tl_headers, branch["id"], asset["id"]).json()
    _submit(client, tl_headers, created["id"])
    client.post(f"/api/v1/requirements/{created['id']}/approve", json={}, headers=mgr_headers)

    # Asset is already CONDEMNED (auto-transitioned) — a redundant direct PATCH with
    # status=CONDEMNED must not error just because it's already in that state.
    response = admin_client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={
            "asset_number": asset["asset_number"], "device_model_id": asset["device_model_id"],
            "branch_id": asset["branch_id"], "status": "CONDEMNED",
        },
    )
    assert response.status_code == 200


# ---- 10. Existing normal Asset PATCH behavior remains valid ----

def test_normal_asset_patch_still_works(admin_client):
    _, _, asset = _make_asset(admin_client, asset_number="AST-COND-NORMAL")
    response = admin_client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={
            "asset_number": asset["asset_number"], "device_model_id": asset["device_model_id"],
            "branch_id": asset["branch_id"], "status": "UNDER_MAINTENANCE",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "UNDER_MAINTENANCE"


# ---- 11. Existing (non-condemnation) requirement workflow remains valid ----

def test_regular_device_requirement_workflow_unaffected(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Regular Req Branch")
    model = admin_client.post(
        "/api/v1/device-models", json={"name": "Regular Monitor", "category": "MONITOR"}
    ).json()
    tl_headers = _tl_headers(client, db_session, "tl-cond8@example.com")
    mgr_headers = _manager_headers(client, db_session, "mgr-cond8@example.com")

    payload = {
        "branch_id": branch["id"],
        "reason": "Need another monitor",
        "items": [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
    }
    created = client.post("/api/v1/requirements", json=payload, headers=tl_headers).json()
    _submit(client, tl_headers, created["id"])
    approved = client.post(f"/api/v1/requirements/{created['id']}/approve", json={}, headers=mgr_headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"


# ---- 12. Existing fulfillment workflow remains valid ----

def test_fulfillment_workflow_unaffected_by_condemnation_changes(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Fulfillment Regression Branch")
    item = admin_client.post(
        "/api/v1/inventory/items", json={"item_code": "SP-COND-1", "name": "Sensor Cable", "is_consumable": False}
    ).json()
    admin_client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": item["id"], "transaction_type": "STOCK_IN", "quantity": 10},
    )
    tl_headers = _tl_headers(client, db_session, "tl-cond9@example.com")
    mgr_headers = _manager_headers(client, db_session, "mgr-cond9@example.com")
    create_user(db_session, "invmgr-cond9@example.com", "INVENTORY_MANAGER", "Password123!")
    inv_headers = auth_headers(client, "invmgr-cond9@example.com", "Password123!")

    payload = {
        "branch_id": branch["id"],
        "reason": "Need spare part",
        "items": [{"item_type": "SPARE_PART", "inventory_item_id": item["id"], "requested_quantity": 2}],
    }
    created = client.post("/api/v1/requirements", json=payload, headers=tl_headers).json()
    _submit(client, tl_headers, created["id"])
    client.post(f"/api/v1/requirements/{created['id']}/approve", json={}, headers=mgr_headers)

    fulfillment = client.post(
        "/api/v1/fulfillments", json={"requirement_id": created["id"]}, headers=inv_headers
    )
    assert fulfillment.status_code == 201
    assert fulfillment.json()["status"] == "PENDING_ALLOCATION"


# ---- Condemnation eligibility — an already-condemned/retired asset can't be re-condemned ----

def test_condemnation_rejects_already_condemned_asset(admin_client, client, db_session):
    branch, _, asset = _make_asset(admin_client, "AST-COND-ALREADY", status="CONDEMNED")
    headers = _tl_headers(client, db_session, "tl-cond10@example.com")

    response = _raise_condemnation(client, headers, branch["id"], asset["id"])
    assert response.status_code == 404, response.text


def test_condemnation_rejects_retired_asset(admin_client, client, db_session):
    branch, _, asset = _make_asset(admin_client, "AST-COND-RETIRED", status="RETIRED")
    headers = _tl_headers(client, db_session, "tl-cond11@example.com")

    response = _raise_condemnation(client, headers, branch["id"], asset["id"])
    assert response.status_code == 404, response.text
