"""Final Operational Gap Pass, Part 1 — Asset Number must be optional.

`assets.asset_number` is now nullable (migration 0009). NULL/absence — never an
empty string or an auto-generated placeholder — represents "no asset number yet".
Uniqueness is still enforced for every non-NULL value.

Item 8 from the task's test list ("Import behavior remains valid") is covered by
the existing `test_imports_module14.py` end-to-end workbook import, which already
imports a real `Asset Number*` value through the unmodified `_import_asset`
row-handler — that handler was verified (by reading it) to always run with a real
asset_number already present (the Data Import Center's Device Master sheet still
requires it as its business key, unchanged by this pass), so no new import test
was needed; the full regression run proves that path is unaffected.
"""


def test_asset_without_asset_number_can_be_created(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post("/api/v1/device-models", json={"name": "Test Device", "category": "Other"}).json()

    response = admin_client.post(
        "/api/v1/assets",
        json={"serial_number": "SN-NOASSETNUM", "device_model_id": model["id"], "branch_id": branch["id"]},
    )
    assert response.status_code == 201
    assert response.json()["asset_number"] is None


def test_asset_with_asset_number_can_be_created(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post("/api/v1/device-models", json={"name": "Test Device", "category": "Other"}).json()

    response = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": "AST-WITH-NUM", "device_model_id": model["id"], "branch_id": branch["id"]},
    )
    assert response.status_code == 201
    assert response.json()["asset_number"] == "AST-WITH-NUM"


def test_duplicate_non_null_asset_number_is_rejected(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post("/api/v1/device-models", json={"name": "Test Device", "category": "Other"}).json()
    payload = {"asset_number": "AST-DUP-X", "device_model_id": model["id"], "branch_id": branch["id"]}

    first = admin_client.post("/api/v1/assets", json=payload)
    assert first.status_code == 201
    second = admin_client.post("/api/v1/assets", json=payload)
    assert second.status_code == 409


def test_blank_asset_number_does_not_create_duplicate_violation(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post("/api/v1/device-models", json={"name": "Test Device", "category": "Other"}).json()

    # Two assets, both with no asset number — NULL != NULL, so this must not 409.
    first = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": "", "serial_number": "SN-A", "device_model_id": model["id"], "branch_id": branch["id"]},
    )
    second = admin_client.post(
        "/api/v1/assets",
        json={"serial_number": "SN-B", "device_model_id": model["id"], "branch_id": branch["id"]},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    # Blank string was normalized to NULL, never stored as "".
    assert first.json()["asset_number"] is None
    assert second.json()["asset_number"] is None


def test_asset_number_can_be_added_later_via_patch(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post("/api/v1/device-models", json={"name": "Test Device", "category": "Other"}).json()
    created = admin_client.post(
        "/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch["id"]}
    ).json()
    assert created["asset_number"] is None

    updated = admin_client.patch(
        f"/api/v1/assets/{created['id']}",
        json={"asset_number": "AST-ADDED-LATER", "device_model_id": model["id"], "branch_id": branch["id"]},
    )
    assert updated.status_code == 200
    assert updated.json()["asset_number"] == "AST-ADDED-LATER"


def test_patch_rejects_asset_number_that_collides_with_another_asset(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post("/api/v1/device-models", json={"name": "Test Device", "category": "Other"}).json()
    admin_client.post(
        "/api/v1/assets",
        json={"asset_number": "AST-TAKEN", "device_model_id": model["id"], "branch_id": branch["id"]},
    )
    other = admin_client.post(
        "/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch["id"]}
    ).json()

    response = admin_client.patch(
        f"/api/v1/assets/{other['id']}",
        json={"asset_number": "AST-TAKEN", "device_model_id": model["id"], "branch_id": branch["id"]},
    )
    assert response.status_code == 409


def test_patch_resending_same_asset_number_does_not_self_conflict(admin_client):
    """A full-replace PATCH that resends the asset's own current asset_number
    (e.g. editing an unrelated field) must not spuriously 409 against itself."""
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post("/api/v1/device-models", json={"name": "Test Device", "category": "Other"}).json()
    created = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": "AST-SELF", "device_model_id": model["id"], "branch_id": branch["id"]},
    ).json()

    response = admin_client.patch(
        f"/api/v1/assets/{created['id']}",
        json={
            "asset_number": "AST-SELF",
            "device_model_id": model["id"],
            "branch_id": branch["id"],
            "criticality": "HIGH",
        },
    )
    assert response.status_code == 200
    assert response.json()["criticality"] == "HIGH"
    assert response.json()["asset_number"] == "AST-SELF"


def test_existing_assets_still_load(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post("/api/v1/device-models", json={"name": "Test Device", "category": "Other"}).json()
    with_number = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": "AST-EXISTING", "device_model_id": model["id"], "branch_id": branch["id"]},
    ).json()
    without_number = admin_client.post(
        "/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch["id"]}
    ).json()

    listed = admin_client.get("/api/v1/assets").json()
    assert listed["total"] == 2

    fetched_with = admin_client.get(f"/api/v1/assets/{with_number['id']}")
    fetched_without = admin_client.get(f"/api/v1/assets/{without_number['id']}")
    assert fetched_with.status_code == 200
    assert fetched_without.status_code == 200
    assert fetched_with.json()["asset_number"] == "AST-EXISTING"
    assert fetched_without.json()["asset_number"] is None


def test_combined_device_creation_without_asset_number(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    response = admin_client.post(
        "/api/v1/devices",
        json={"model_name": "Blank Number Device", "category": "Other Biomedical Equipment", "branch_id": branch["id"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["asset"]["asset_number"] is None
    assert body["device_model"]["name"] == "Blank Number Device"


def test_asset_lifecycle_summary_works_without_asset_number(admin_client):
    """Regression test: AssetLifecycleSummaryOut originally declared asset_number as
    a required `str`, so GET /assets/{id}/lifecycle 500'd for any asset with no
    asset number — found live during this pass's own verification and fixed in
    schemas/asset_lifecycle.py."""
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post("/api/v1/device-models", json={"name": "Test Device", "category": "Other"}).json()
    asset = admin_client.post(
        "/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch["id"]}
    ).json()
    assert asset["asset_number"] is None

    response = admin_client.get(f"/api/v1/assets/{asset['id']}/lifecycle")
    assert response.status_code == 200
    assert response.json()["asset_number"] is None


def test_combined_device_creation_with_asset_number_still_enforces_uniqueness(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post(
        "/api/v1/device-models", json={"name": "Dup Check Device", "category": "Other"}
    ).json()
    payload = {"device_model_id": model["id"], "branch_id": branch["id"], "asset_number": "AST-COMBINED-DUP"}
    first = admin_client.post("/api/v1/devices", json=payload)
    assert first.status_code == 201
    second = admin_client.post("/api/v1/devices", json=payload)
    assert second.status_code == 409
