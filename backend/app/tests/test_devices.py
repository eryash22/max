def test_create_device_model(admin_client):
    response = admin_client.post(
        "/api/v1/device-models",
        json={"name": "Chattanooga Intelect Advanced", "manufacturer": "Chattanooga", "category": "Combination Therapy"},
    )
    assert response.status_code == 201
    assert response.json()["environment"] == "DEVELOPMENT / DEMO DATA"


def test_asset_requires_existing_device_model_and_branch(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    response = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": "AST-001", "device_model_id": "missing", "branch_id": branch["id"]},
    )
    assert response.status_code == 404


def test_asset_number_must_be_unique(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post(
        "/api/v1/device-models", json={"name": "Test Device", "category": "Other"}
    ).json()
    payload = {"asset_number": "AST-100", "device_model_id": model["id"], "branch_id": branch["id"]}
    first = admin_client.post("/api/v1/assets", json=payload)
    assert first.status_code == 201
    second = admin_client.post("/api/v1/assets", json=payload)
    assert second.status_code == 409


def test_branch_device_assignment_preserves_null_model_number(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    response = admin_client.post(
        "/api/v1/branch-device-assignments",
        json={
            "branch_id": branch["id"],
            "source_name": "Hot Bags",
            "assignment_status": "REVIEW_QUEUE",
            "data_quality_flag": "UNRESOLVED_SOURCE_NAME",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["device_model_id"] is None
    assert body["data_quality_flag"] == "UNRESOLVED_SOURCE_NAME"
