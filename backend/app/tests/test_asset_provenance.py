"""Asset Digital Passport — Phase 2A: read-only Requirement/Fulfillment provenance
for an asset (Asset -> FulfillmentItemAsset -> FulfillmentItem -> FulfillmentRequest
-> Requirement), plus the MANAGER ASSET_READ grant needed to view the passport."""
from .conftest import auth_headers, create_user


def _make_branch(admin_client, name):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_device_model(admin_client, name):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


def _run_full_fulfillment_chain(admin_client, client, db_session, *, suffix):
    central_branch = _make_branch(admin_client, f"Provenance Central {suffix}")
    requesting_branch = _make_branch(admin_client, f"Provenance Requesting {suffix}")
    model = _make_device_model(admin_client, f"Provenance Monitor {suffix}")
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": f"PROV-{suffix}", "device_model_id": model["id"], "branch_id": central_branch["id"]},
    ).json()

    create_user(db_session, f"tl.prov{suffix}@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, f"tl.prov{suffix}@example.com", "Password123!")
    create_user(db_session, f"mgr.prov{suffix}@example.com", "MANAGER", "Password123!")
    mgr_headers = auth_headers(client, f"mgr.prov{suffix}@example.com", "Password123!")
    create_user(db_session, f"inv.prov{suffix}@example.com", "INVENTORY_MANAGER", "Password123!")
    inv_headers = auth_headers(client, f"inv.prov{suffix}@example.com", "Password123!")

    requirement = client.post(
        "/api/v1/requirements",
        json={
            "branch_id": requesting_branch["id"], "reason": "Provenance test",
            "items": [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        },
        headers=tl_headers,
    ).json()
    client.post(f"/api/v1/requirements/{requirement['id']}/submit", headers=tl_headers)
    client.post(f"/api/v1/requirements/{requirement['id']}/approve", json={}, headers=mgr_headers)

    fulfillment = client.post(
        "/api/v1/fulfillments", json={"requirement_id": requirement["id"]}, headers=inv_headers
    ).json()
    requirement_item_id = fulfillment["items"][0]["requirement_item_id"]

    client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": requirement_item_id, "asset_ids": [asset["id"]]}]},
        headers=inv_headers,
    )
    client.post(f"/api/v1/fulfillments/{fulfillment['id']}/process", headers=inv_headers)
    client.post(f"/api/v1/fulfillments/{fulfillment['id']}/pack", headers=inv_headers)
    client.post(f"/api/v1/fulfillments/{fulfillment['id']}/out-for-delivery", headers=inv_headers)
    client.post(f"/api/v1/fulfillments/{fulfillment['id']}/deliver", json={}, headers=inv_headers)
    complete = client.post(f"/api/v1/fulfillments/{fulfillment['id']}/complete", headers=inv_headers)
    assert complete.status_code == 200, complete.text

    return asset, requirement, fulfillment, requesting_branch, mgr_headers


def test_asset_provenance_reflects_completed_fulfillment(admin_client, client, db_session):
    asset, requirement, fulfillment, requesting_branch, _ = _run_full_fulfillment_chain(
        admin_client, client, db_session, suffix="a"
    )

    response = admin_client.get(f"/api/v1/assets/{asset['id']}/provenance")
    assert response.status_code == 200, response.text
    entries = response.json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["requirement_id"] == requirement["id"]
    assert entry["requirement_number"] == requirement["request_number"]
    assert entry["requirement_branch_id"] == requesting_branch["id"]
    assert entry["requirement_status"] == "APPROVED"
    assert entry["fulfillment_id"] == fulfillment["id"]
    assert entry["fulfillment_number"] == fulfillment["fulfillment_number"]
    assert entry["fulfillment_status"] == "COMPLETED"
    assert entry["delivered"] is True


def test_asset_provenance_empty_for_directly_created_asset(admin_client):
    branch = _make_branch(admin_client, "No Provenance Branch")
    model = _make_device_model(admin_client, "No Provenance Monitor")
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": "NOPROV-001", "device_model_id": model["id"], "branch_id": branch["id"]},
    ).json()

    response = admin_client.get(f"/api/v1/assets/{asset['id']}/provenance")
    assert response.status_code == 200
    assert response.json() == []


def test_asset_provenance_404_for_missing_asset(admin_client):
    response = admin_client.get("/api/v1/assets/does-not-exist/provenance")
    assert response.status_code == 404


def test_manager_can_read_asset_provenance_and_lifecycle(admin_client, client, db_session):
    """MANAGER gained ASSET_READ specifically so it can view the Digital Passport
    (Phase 2 decision #2) — verify both the base asset read and the new provenance
    endpoint work for this role, without granting ASSET_WRITE."""
    asset, _, _, _, mgr_headers = _run_full_fulfillment_chain(admin_client, client, db_session, suffix="b")

    get_asset = client.get(f"/api/v1/assets/{asset['id']}", headers=mgr_headers)
    assert get_asset.status_code == 200, get_asset.text

    get_lifecycle = client.get(f"/api/v1/assets/{asset['id']}/lifecycle", headers=mgr_headers)
    assert get_lifecycle.status_code == 200, get_lifecycle.text

    get_provenance = client.get(f"/api/v1/assets/{asset['id']}/provenance", headers=mgr_headers)
    assert get_provenance.status_code == 200, get_provenance.text
    assert len(get_provenance.json()) == 1

    # Still no write access — MANAGER's bundle stays read-only for assets.
    patch_attempt = client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"device_model_id": asset["device_model_id"], "branch_id": asset["branch_id"], "status": "RETIRED"},
        headers=mgr_headers,
    )
    assert patch_attempt.status_code == 403


def test_asset_provenance_requires_authentication(admin_client, client):
    """RBAC still gates the new endpoint the same way as every other asset endpoint —
    an unauthenticated request is rejected, not accidentally open."""
    branch = _make_branch(admin_client, "RBAC Provenance Branch")
    model = _make_device_model(admin_client, "RBAC Provenance Monitor")
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": "RBAC-PROV-001", "device_model_id": model["id"], "branch_id": branch["id"]},
    ).json()

    # `client` carries the admin_client fixture's default Authorization header, so an
    # explicit empty override is needed to simulate a truly unauthenticated request.
    response = client.get(f"/api/v1/assets/{asset['id']}/provenance", headers={"Authorization": ""})
    assert response.status_code in (401, 403)
