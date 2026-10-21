"""Device Catalog / Allocate-to-Branch unification pass.

Covers the new backend surface this pass adds on top of the existing
Inventory/Asset unification work (see test_inventory_asset_unification.py for the
pre-existing allocate/QR/branch-scope coverage this does NOT duplicate):

- /api/v1/device-models now reports live total/available/allocated unit counts per
  model, including models with zero physical stock.
- /api/v1/assets?available_for_allocation=true&central_only=true only returns stock
  actually sitting at a central-warehouse branch.
- Multiple physical units of the same DeviceModel, registered into the same branch,
  each get their own distinct Asset Number and QR identity.
- The new Review Queue delete endpoint: success, 404, permission, and branch-scope.
"""
from app.models.device import Asset

from .conftest import auth_headers, create_user


def _branch(admin_client, name, **extra):
    r = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _model(admin_client, name="Catalog Unify Model", manufacturer="Acme"):
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


# ---- Device Catalog with live stock counts ----

def test_zero_stock_model_is_still_listed_with_zero_counts(admin_client):
    model = _model(admin_client, name="Catalog Unify Zero Stock")

    r = admin_client.get("/api/v1/device-models", params={"search": "Catalog Unify Zero Stock"})
    assert r.status_code == 200, r.text
    row = next(m for m in r.json()["items"] if m["id"] == model["id"])
    assert row["total_units"] == 0
    assert row["available_units"] == 0
    assert row["allocated_units"] == 0


def test_model_stock_counts_split_central_vs_allocated(admin_client):
    central = _branch(admin_client, "Catalog Unify Central", is_central_warehouse=True)
    branch = _branch(admin_client, "Catalog Unify Branch")
    model = _model(admin_client, name="Catalog Unify Counted Model")

    _asset(admin_client, model["id"], central["id"], asset_number="CAT-UNIFY-C1", status="RECEIVED")
    _asset(admin_client, model["id"], central["id"], asset_number="CAT-UNIFY-C2", status="RETIRED")
    _asset(admin_client, model["id"], branch["id"], asset_number="CAT-UNIFY-B1", status="OPERATIONAL")

    r = admin_client.get("/api/v1/device-models", params={"search": "Catalog Unify Counted Model"})
    assert r.status_code == 200, r.text
    row = next(m for m in r.json()["items"] if m["id"] == model["id"])
    assert row["total_units"] == 3
    assert row["available_units"] == 1  # only the RECEIVED one at the central branch
    assert row["allocated_units"] == 1  # only the one sitting at the non-central branch


def test_unit_head_picker_still_strips_admin_fields_but_keeps_counts(admin_client, client, db_session):
    model = _model(admin_client, name="Catalog Unify Picker Model")
    create_user(db_session, "pickerunify@example.com", "UNIT_HEAD", "Password123!")
    headers = auth_headers(client, "pickerunify@example.com", "Password123!")

    r = client.get("/api/v1/device-models", params={"search": "Catalog Unify Picker Model"}, headers=headers)
    assert r.status_code == 200, r.text
    row = next(m for m in r.json()["items"] if m["id"] == model["id"])
    assert row["known_quantity"] is None  # admin-only field still stripped
    assert row["total_units"] == 0  # stock counts survive the strip


# ---- central_only allocation-candidate filter ----

def test_central_only_excludes_assets_already_at_a_branch(admin_client):
    central = _branch(admin_client, "Catalog Unify Central Only", is_central_warehouse=True)
    branch = _branch(admin_client, "Catalog Unify Non Central")
    model = _model(admin_client, name="Catalog Unify Central Filter Model")
    central_asset = _asset(admin_client, model["id"], central["id"], asset_number="CAT-UNIFY-CO-1", status="RECEIVED")
    branch_asset = _asset(admin_client, model["id"], branch["id"], asset_number="CAT-UNIFY-CO-2", status="OPERATIONAL")

    r = admin_client.get("/api/v1/assets", params={"available_for_allocation": "true", "central_only": "true"})
    assert r.status_code == 200, r.text
    ids = [a["id"] for a in r.json()["items"]]
    assert central_asset["id"] in ids
    assert branch_asset["id"] not in ids


def test_without_central_only_both_branches_are_candidates(admin_client):
    central = _branch(admin_client, "Catalog Unify No Filter Central", is_central_warehouse=True)
    branch = _branch(admin_client, "Catalog Unify No Filter Branch")
    model = _model(admin_client, name="Catalog Unify No Filter Model")
    central_asset = _asset(admin_client, model["id"], central["id"], asset_number="CAT-UNIFY-NF-1", status="RECEIVED")
    branch_asset = _asset(admin_client, model["id"], branch["id"], asset_number="CAT-UNIFY-NF-2", status="OPERATIONAL")

    r = admin_client.get("/api/v1/assets", params={"available_for_allocation": "true"})
    assert r.status_code == 200, r.text
    ids = [a["id"] for a in r.json()["items"]]
    assert central_asset["id"] in ids
    assert branch_asset["id"] in ids


# ---- Register multiple physical units of the same model into one branch ----

def test_multiple_units_of_same_model_get_distinct_numbers_and_qr_identities(admin_client, db_session):
    branch = _branch(admin_client, "Catalog Unify Saket")
    model = _model(admin_client, name="ResMed Stellar 150 Catalog Unify")

    created = [
        _asset(admin_client, model["id"], branch["id"], asset_number=f"MAX-SKT-UNIFY-{i:03d}", status="OPERATIONAL")
        for i in range(1, 6)
    ]

    asset_numbers = {a["asset_number"] for a in created}
    assert len(asset_numbers) == 5  # five distinct Asset Numbers

    ids = {a["id"] for a in created}
    assert len(ids) == 5  # five distinct Asset rows, not one shared row

    tokens = {db_session.get(Asset, a["id"]).qr_token for a in created}
    assert len(tokens) == 5  # five distinct QR identities
    assert all(t for t in tokens)  # every one actually provisioned, none left blank

    for a in created:
        assert a["device_model_id"] == model["id"]  # all reference the SAME catalog model
        assert a["branch_id"] == branch["id"]


def test_duplicate_asset_number_among_new_units_is_rejected_without_partial_creation(admin_client, db_session):
    branch = _branch(admin_client, "Catalog Unify Dup Branch")
    model = _model(admin_client, name="Catalog Unify Dup Model")

    first = _asset(admin_client, model["id"], branch["id"], asset_number="CAT-UNIFY-DUP-1")
    before_count = db_session.query(Asset).count()

    r = admin_client.post("/api/v1/assets", json={
        "device_model_id": model["id"], "branch_id": branch["id"], "status": "OPERATIONAL",
        "asset_number": "cat-unify-dup-1",
    })
    assert r.status_code == 409, r.text
    assert db_session.query(Asset).count() == before_count  # no row left behind by the rejected attempt


# ---- Review Queue delete ----

def test_review_queue_delete_removes_item(admin_client, db_session):
    from app.models.device import BranchDeviceAssignment
    from app.models.review import ReviewItem

    assignment = BranchDeviceAssignment(branch_id=_branch(admin_client, "Review Delete Branch")["id"],
                                         source_name="import-test", assignment_status="PENDING_ASSET_IDENTIFICATION")
    db_session.add(assignment)
    db_session.flush()
    item = ReviewItem(source_type="branch_device_assignment", source_id=assignment.id,
                       reason_code="LOCATION_ASSET_VERIFICATION_REQUIRED", status="OPEN")
    db_session.add(item)
    db_session.commit()

    r = admin_client.delete(f"/api/v1/review-queue/{item.id}")
    assert r.status_code == 204, r.text
    assert db_session.get(ReviewItem, item.id) is None
    # the underlying business record is untouched by deleting the queue entry
    assert db_session.get(BranchDeviceAssignment, assignment.id) is not None


def test_review_queue_delete_missing_item_404(admin_client):
    r = admin_client.delete("/api/v1/review-queue/does-not-exist")
    assert r.status_code == 404


def test_review_queue_delete_requires_permission(client, db_session, admin_client):
    from app.models.device import BranchDeviceAssignment
    from app.models.review import ReviewItem

    branch = _branch(admin_client, "Review Delete No Perm Branch")
    assignment = BranchDeviceAssignment(branch_id=branch["id"], source_name="import-test",
                                         assignment_status="PENDING_ASSET_IDENTIFICATION")
    db_session.add(assignment)
    db_session.flush()
    item = ReviewItem(source_type="branch_device_assignment", source_id=assignment.id,
                       reason_code="OTHER", status="OPEN")
    db_session.add(item)
    db_session.commit()

    create_user(db_session, "noperm-review@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "noperm-review@example.com", "Password123!")

    r = client.delete(f"/api/v1/review-queue/{item.id}", headers=headers)
    assert r.status_code == 403
