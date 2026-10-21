"""Inventory/Asset unification pass.

Covers: org-wide (case-insensitive) Asset Number uniqueness, the dedicated
'Allocate Device to Branch' action (updates the SAME Asset row, never inserts a
second one; preserves QR identity/history; rejects CONDEMNED/RETIRED assets;
enforces the existing branch-scope/RBAC boundary), and that this workflow leaves
QR/contract/lifecycle behavior untouched.
"""
from app.models.device import Asset

from .conftest import auth_headers, create_user


def _branch(admin_client, name, **extra):
    r = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _model(admin_client, name="Unify Test Ventilator", manufacturer="Acme"):
    r = admin_client.post("/api/v1/device-models",
                          json={"name": name, "category": "VENTILATOR", "manufacturer": manufacturer})
    assert r.status_code == 201, r.text
    return r.json()


def _asset(admin_client, model_id, branch_id, asset_number=None, status="OPERATIONAL", **extra):
    payload = {"device_model_id": model_id, "branch_id": branch_id, "status": status, **extra}
    if asset_number is not None:
        payload["asset_number"] = asset_number
    r = admin_client.post("/api/v1/assets", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ---- org-wide, case-insensitive Asset Number uniqueness ----

def test_duplicate_asset_number_rejected_case_insensitively_across_branches(admin_client):
    branch_a = _branch(admin_client, "Unify Branch A")
    branch_b = _branch(admin_client, "Unify Branch B")
    model = _model(admin_client)
    _asset(admin_client, model["id"], branch_a["id"], asset_number="MAX-DEV-UNIFY-001")

    r = admin_client.post("/api/v1/assets", json={
        "device_model_id": model["id"], "branch_id": branch_b["id"], "status": "OPERATIONAL",
        "asset_number": "max-dev-unify-001",
    })
    assert r.status_code == 409, r.text


def test_asset_number_whitespace_is_trimmed(admin_client):
    branch = _branch(admin_client, "Unify Branch Trim")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], asset_number="  MAX-DEV-TRIM-1  ")
    assert asset["asset_number"] == "MAX-DEV-TRIM-1"


def test_same_device_model_supports_many_distinct_physical_assets(admin_client):
    branch = _branch(admin_client, "Unify Branch Many")
    model = _model(admin_client, name="ResMed Stellar 150")
    a1 = _asset(admin_client, model["id"], branch["id"], asset_number="MAX-DEV-010")
    a2 = _asset(admin_client, model["id"], branch["id"], asset_number="MAX-DEV-011")
    assert a1["id"] != a2["id"]
    assert a1["device_model_id"] == a2["device_model_id"] == model["id"]


# ---- Allocate Device to Branch ----

def test_allocate_updates_same_asset_row_not_a_new_one(admin_client, db_session):
    central = _branch(admin_client, "Unify Central Warehouse", is_central_warehouse=True)
    destination = _branch(admin_client, "Unify Destination Branch")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], central["id"], asset_number="MAX-DEV-ALLOC-1", status="RECEIVED")
    original_id = asset["id"]
    original_qr_token = db_session.get(Asset, original_id).qr_token

    before_count = db_session.query(Asset).count()
    r = admin_client.post(f"/api/v1/assets/{original_id}/allocate", json={"branch_id": destination["id"]})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["id"] == original_id
    assert body["asset_number"] == "MAX-DEV-ALLOC-1"
    assert body["branch_id"] == destination["id"]
    after_count = db_session.query(Asset).count()
    assert after_count == before_count  # no second Asset row created

    row = db_session.get(Asset, original_id)
    assert row.qr_token == original_qr_token  # QR identity untouched by allocation
    assert row.branch_id == destination["id"]


def test_allocate_removes_asset_from_central_stock_and_shows_in_destination(admin_client):
    central = _branch(admin_client, "Unify Central Warehouse 2", is_central_warehouse=True)
    destination = _branch(admin_client, "Unify Destination Branch 2")
    model = _model(admin_client, name="Unify Central Stock Model")
    asset = _asset(admin_client, model["id"], central["id"], asset_number="MAX-DEV-ALLOC-2", status="RECEIVED")

    before = admin_client.get("/api/v1/assets/central-device-stock").json()
    before_count = next((row["available_count"] for row in before if row["device_model_id"] == model["id"]), 0)
    assert before_count >= 1

    r = admin_client.post(f"/api/v1/assets/{asset['id']}/allocate", json={"branch_id": destination["id"]})
    assert r.status_code == 200, r.text

    after = admin_client.get("/api/v1/assets/central-device-stock").json()
    after_count = next((row["available_count"] for row in after if row["device_model_id"] == model["id"]), 0)
    assert after_count == before_count - 1

    dest_assets = admin_client.get("/api/v1/assets", params={"branch_id": destination["id"]}).json()
    assert any(a["id"] == asset["id"] for a in dest_assets["items"])


def test_allocate_preserves_asset_history(admin_client):
    central = _branch(admin_client, "Unify Central Warehouse 3", is_central_warehouse=True)
    destination = _branch(admin_client, "Unify Destination Branch 3")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], central["id"], asset_number="MAX-DEV-ALLOC-3", status="RECEIVED")

    r = admin_client.post(f"/api/v1/assets/{asset['id']}/allocate", json={"branch_id": destination["id"]})
    assert r.status_code == 200, r.text

    history = admin_client.get(f"/api/v1/assets/{asset['id']}/history")
    assert history.status_code == 200, history.text
    events = [h["event_type"] for h in history.json()["items"]]
    assert "BRANCH_TRANSFER" in events


def test_condemned_or_retired_asset_cannot_be_allocated(admin_client):
    central = _branch(admin_client, "Unify Central Warehouse 4", is_central_warehouse=True)
    destination = _branch(admin_client, "Unify Destination Branch 4")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], central["id"], asset_number="MAX-DEV-ALLOC-4", status="RETIRED")

    r = admin_client.post(f"/api/v1/assets/{asset['id']}/allocate", json={"branch_id": destination["id"]})
    assert r.status_code == 422


def test_allocation_picker_searches_by_device_model_name(admin_client):
    central = _branch(admin_client, "Unify Central Warehouse 5b", is_central_warehouse=True)
    model = _model(admin_client, name="ResMed Stellar 150 Unify Search")
    asset = _asset(admin_client, model["id"], central["id"], asset_number="MAX-DEV-SEARCH-1", status="RECEIVED")

    r = admin_client.get("/api/v1/assets", params={"available_for_allocation": "true", "search": "Stellar 150 Unify"})
    assert r.status_code == 200, r.text
    ids = [a["id"] for a in r.json()["items"]]
    assert asset["id"] in ids


def test_allocate_rejects_unknown_branch(admin_client):
    central = _branch(admin_client, "Unify Central Warehouse 5", is_central_warehouse=True)
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], central["id"], asset_number="MAX-DEV-ALLOC-5", status="RECEIVED")

    r = admin_client.post(f"/api/v1/assets/{asset['id']}/allocate", json={"branch_id": "does-not-exist"})
    assert r.status_code == 404


def test_available_for_allocation_filter_excludes_condemned_and_retired(admin_client):
    central = _branch(admin_client, "Unify Central Warehouse 6", is_central_warehouse=True)
    model = _model(admin_client)
    eligible = _asset(admin_client, model["id"], central["id"], asset_number="MAX-DEV-ALLOC-6-OK", status="RECEIVED")
    retired = _asset(admin_client, model["id"], central["id"], asset_number="MAX-DEV-ALLOC-6-RETIRED", status="RETIRED")

    r = admin_client.get("/api/v1/assets", params={"available_for_allocation": "true", "branch_id": central["id"]})
    assert r.status_code == 200, r.text
    ids = [a["id"] for a in r.json()["items"]]
    assert eligible["id"] in ids
    assert retired["id"] not in ids


# ---- RBAC / branch isolation ----

def test_unit_head_without_asset_write_cannot_allocate(client, db_session, admin_client):
    central = _branch(admin_client, "Unify Central Warehouse 7", is_central_warehouse=True)
    destination = _branch(admin_client, "Unify Destination Branch 7")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], central["id"], asset_number="MAX-DEV-ALLOC-7", status="RECEIVED")

    create_user(db_session, "unithead@example.com", "UNIT_HEAD", "Password123!")
    headers = auth_headers(client, "unithead@example.com", "Password123!")

    r = client.post(f"/api/v1/assets/{asset['id']}/allocate", json={"branch_id": destination["id"]}, headers=headers)
    assert r.status_code == 403


def test_qr_public_page_reflects_new_branch_after_allocation(admin_client, db_session):
    central = _branch(admin_client, "Unify Central Warehouse 8", is_central_warehouse=True)
    destination = _branch(admin_client, "Unify Destination Branch 8", status="ACTIVE")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], central["id"], asset_number="MAX-DEV-ALLOC-8", status="RECEIVED")
    token = db_session.get(Asset, asset["id"]).qr_token

    before = admin_client.get(f"/api/v1/public/qr/{token}")
    before_branch = next(f["value"] for f in before.json()["fields"] if f["key"] == "branch_name")
    assert before_branch == central["name"]

    r = admin_client.post(f"/api/v1/assets/{asset['id']}/allocate", json={"branch_id": destination["id"]})
    assert r.status_code == 200, r.text

    after = admin_client.get(f"/api/v1/public/qr/{token}")
    after_branch = next(f["value"] for f in after.json()["fields"] if f["key"] == "branch_name")
    assert after_branch == destination["name"]
