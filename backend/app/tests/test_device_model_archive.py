"""Final admin/inventory/vendor/dashboard polish pass — Part 2: device catalog
archive/delete."""

from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _make_device_model(admin_client, name="Archive-Model-1"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "Imaging"}).json()


def _make_branch(admin_client, name="Archive-Branch"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_asset(admin_client, device_model_id, branch_id, asset_number="ARCH-AST-1"):
    return admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": device_model_id, "branch_id": branch_id},
    ).json()


def _make_requirement_referencing_device_model(admin_client, device_model_id, branch_id, reason="Need new stock"):
    response = admin_client.post(
        "/api/v1/requirements",
        json={
            "branch_id": branch_id,
            "reason": reason,
            "items": [
                {"item_type": "DEVICE_MODEL", "device_model_id": device_model_id, "requested_quantity": 1},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---- Delete ----

def test_delete_unreferenced_device_model_succeeds(admin_client):
    model = _make_device_model(admin_client)
    response = admin_client.delete(f"/api/v1/device-models/{model['id']}")
    assert response.status_code == 204

    get_response = admin_client.get(f"/api/v1/device-models/{model['id']}")
    assert get_response.status_code == 404


def test_delete_referenced_device_model_blocked(admin_client):
    model = _make_device_model(admin_client, "Referenced Model")
    branch = _make_branch(admin_client)
    _make_asset(admin_client, model["id"], branch["id"])

    response = admin_client.delete(f"/api/v1/device-models/{model['id']}")
    assert response.status_code == 409
    assert "1" in response.json()["detail"]

    still_there = admin_client.get(f"/api/v1/device-models/{model['id']}")
    assert still_there.status_code == 200


def test_delete_device_model_referenced_only_by_requirement_item_blocked(admin_client):
    """DEV Data Cleanup safety fix (2026-08-31): a DeviceModel with zero Asset rows
    but still referenced by a RequirementItem must be blocked with a clean 409, not
    reach an unhandled 500 from a Postgres FK violation on the actual DELETE."""
    model = _make_device_model(admin_client, "Requirement-Referenced Model")
    branch = _make_branch(admin_client, "Requirement-Referenced Branch")
    _make_requirement_referencing_device_model(admin_client, model["id"], branch["id"])

    response = admin_client.delete(f"/api/v1/device-models/{model['id']}")
    assert response.status_code == 409
    assert "1" in response.json()["detail"]

    still_there = admin_client.get(f"/api/v1/device-models/{model['id']}")
    assert still_there.status_code == 200


def test_delete_device_model_with_multiple_reference_types_blocked(admin_client):
    """Both an Asset and a RequirementItem reference the same model — the guard must
    sum every reference type, not just report on the first one it finds."""
    model = _make_device_model(admin_client, "Multi-Referenced Model")
    branch = _make_branch(admin_client, "Multi-Referenced Branch")
    _make_asset(admin_client, model["id"], branch["id"], asset_number="MULTI-AST-1")
    _make_requirement_referencing_device_model(admin_client, model["id"], branch["id"])

    response = admin_client.delete(f"/api/v1/device-models/{model['id']}")
    assert response.status_code == 409
    assert "2" in response.json()["detail"]

    still_there = admin_client.get(f"/api/v1/device-models/{model['id']}")
    assert still_there.status_code == 200


def test_delete_device_model_not_found(admin_client):
    response = admin_client.delete("/api/v1/device-models/does-not-exist")
    assert response.status_code == 404


def test_delete_device_model_requires_device_write(client, db_session):
    create_user(db_session, "viewer@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "viewer@example.com", "Password123!")
    response = client.delete("/api/v1/device-models/does-not-exist", headers=headers)
    assert response.status_code == 403


# ---- Archive / activate ----

def test_archive_and_activate_device_model(admin_client, db_session):
    model = _make_device_model(admin_client, "Toggle Model")
    assert model["is_active"] is True

    archived = admin_client.post(f"/api/v1/device-models/{model['id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["is_active"] is False

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "device_model", AuditLog.entity_id == model["id"], AuditLog.action == "ARCHIVE"
    ).first()
    assert log is not None

    activated = admin_client.post(f"/api/v1/device-models/{model['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True

    activate_log = db_session.query(AuditLog).filter(
        AuditLog.entity == "device_model", AuditLog.entity_id == model["id"], AuditLog.action == "ACTIVATE"
    ).first()
    assert activate_log is not None


def test_archive_referenced_device_model_allowed(admin_client):
    """Archiving (soft toggle) is always allowed even when referenced — only the hard
    DELETE is blocked."""
    model = _make_device_model(admin_client, "Archivable Referenced Model")
    branch = _make_branch(admin_client, "Archivable Branch")
    _make_asset(admin_client, model["id"], branch["id"], asset_number="ARCH-AST-2")

    response = admin_client.post(f"/api/v1/device-models/{model['id']}/archive")
    assert response.status_code == 200
    assert response.json()["is_active"] is False


# ---- List filtering ----

def test_list_device_models_excludes_archived_by_default(admin_client):
    active = _make_device_model(admin_client, "Active Listed Model")
    archived = _make_device_model(admin_client, "Archived Listed Model")
    admin_client.post(f"/api/v1/device-models/{archived['id']}/archive")

    response = admin_client.get("/api/v1/device-models")
    ids = [item["id"] for item in response.json()["items"]]
    assert active["id"] in ids
    assert archived["id"] not in ids


def test_list_device_models_can_request_archived(admin_client):
    archived = _make_device_model(admin_client, "Explicit Archived Model")
    admin_client.post(f"/api/v1/device-models/{archived['id']}/archive")

    response = admin_client.get("/api/v1/device-models?is_active=false")
    ids = [item["id"] for item in response.json()["items"]]
    assert archived["id"] in ids
