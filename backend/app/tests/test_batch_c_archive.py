"""Final cleanup pass — Batch C:
Part 1 — Branch safe-delete guard + deactivate (archive) fallback.
Part 3 — Asset archive (Assets are never hard-deleted; archiving is a status flip).
"""

from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _make_branch(admin_client, name="Batch-C-Branch"):
    response = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"})
    assert response.status_code == 201, response.text
    return response.json()


def _make_device_model(admin_client, name="Batch-C-Model"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "Imaging"}).json()


def _make_asset(admin_client, device_model_id, branch_id, asset_number="BC-AST-1"):
    response = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": device_model_id, "branch_id": branch_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---- Branch delete guard ----

def test_delete_unreferenced_branch_succeeds(admin_client):
    branch = _make_branch(admin_client)
    response = admin_client.delete(f"/api/v1/branches/{branch['id']}")
    assert response.status_code == 204

    get_response = admin_client.get(f"/api/v1/branches/{branch['id']}")
    assert get_response.status_code == 404


def test_delete_referenced_branch_blocked(admin_client):
    branch = _make_branch(admin_client, "Referenced Branch")
    model = _make_device_model(admin_client)
    _make_asset(admin_client, model["id"], branch["id"])

    response = admin_client.delete(f"/api/v1/branches/{branch['id']}")
    assert response.status_code == 409
    assert "1" in response.json()["detail"]

    still_there = admin_client.get(f"/api/v1/branches/{branch['id']}")
    assert still_there.status_code == 200


def test_delete_branch_not_found(admin_client):
    response = admin_client.delete("/api/v1/branches/does-not-exist")
    assert response.status_code == 404


def test_delete_branch_requires_hospital_write(client, db_session):
    create_user(db_session, "viewer@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "viewer@example.com", "Password123!")
    response = client.delete("/api/v1/branches/does-not-exist", headers=headers)
    assert response.status_code == 403


# ---- Branch activate / deactivate ----

def test_activate_and_deactivate_branch(admin_client, db_session):
    branch = _make_branch(admin_client, "Toggle Branch")
    assert branch["status"] == "ACTIVE"

    deactivated = admin_client.post(f"/api/v1/branches/{branch['id']}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "INACTIVE"

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "branch", AuditLog.entity_id == branch["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None

    activated = admin_client.post(f"/api/v1/branches/{branch['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"


def test_deactivate_referenced_branch_allowed(admin_client):
    """Deactivating (soft toggle) is always allowed even when referenced — only the
    hard DELETE is blocked."""
    branch = _make_branch(admin_client, "Deactivatable Referenced Branch")
    model = _make_device_model(admin_client, "Deactivatable Model")
    _make_asset(admin_client, model["id"], branch["id"], asset_number="BC-AST-2")

    response = admin_client.post(f"/api/v1/branches/{branch['id']}/deactivate")
    assert response.status_code == 200
    assert response.json()["status"] == "INACTIVE"


# ---- Asset archive ----

def test_archive_and_unarchive_asset(admin_client, db_session):
    branch = _make_branch(admin_client, "Asset Archive Branch")
    model = _make_device_model(admin_client, "Asset Archive Model")
    asset = _make_asset(admin_client, model["id"], branch["id"], asset_number="BC-AST-3")

    archived = admin_client.post(f"/api/v1/assets/{asset['id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "RETIRED"

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "asset", AuditLog.entity_id == asset["id"], AuditLog.action == "ARCHIVE"
    ).first()
    assert log is not None

    unarchived = admin_client.post(f"/api/v1/assets/{asset['id']}/unarchive")
    assert unarchived.status_code == 200
    assert unarchived.json()["status"] == "OPERATIONAL"


def test_archive_asset_allowed_even_with_history_references(admin_client):
    """Archiving an asset must always succeed — it is a status flip, not a delete, so
    it is never reference-blocked (unlike Branch/DeviceModel hard deletes)."""
    branch = _make_branch(admin_client, "History Branch")
    model = _make_device_model(admin_client, "History Model")
    asset = _make_asset(admin_client, model["id"], branch["id"], asset_number="BC-AST-4")

    response = admin_client.post(f"/api/v1/assets/{asset['id']}/archive")
    assert response.status_code == 200
    assert response.json()["status"] == "RETIRED"


def test_unarchive_non_retired_asset_rejected(admin_client):
    branch = _make_branch(admin_client, "Unarchive Guard Branch")
    model = _make_device_model(admin_client, "Unarchive Guard Model")
    asset = _make_asset(admin_client, model["id"], branch["id"], asset_number="BC-AST-5")

    response = admin_client.post(f"/api/v1/assets/{asset['id']}/unarchive")
    assert response.status_code == 409


def test_archive_asset_not_found(admin_client):
    response = admin_client.post("/api/v1/assets/does-not-exist/archive")
    assert response.status_code == 404


def test_archive_asset_requires_asset_write(client, db_session):
    create_user(db_session, "viewer2@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "viewer2@example.com", "Password123!")
    response = client.post("/api/v1/assets/does-not-exist/archive", headers=headers)
    assert response.status_code == 403


# ---- Stale/deleted master-data visibility audit — Branch picker (Final task) ----
# A deactivated branch must drop out of any "new record" branch picker
# (status_filter=ACTIVE, the same opt-in convention as Department/Vendor below)
# while the admin Branches list keeps seeing every status by default (unchanged).

def test_deactivated_branch_excluded_from_active_filtered_list(admin_client):
    branch = _make_branch(admin_client, "Stale Branch Picker QA")
    admin_client.post(f"/api/v1/branches/{branch['id']}/deactivate")

    active_only = admin_client.get("/api/v1/branches?status_filter=ACTIVE&page_size=200")
    assert all(b["id"] != branch["id"] for b in active_only.json()["items"])


def test_deactivated_branch_still_visible_unfiltered(admin_client):
    """Admin management views (Branches list) that don't pass status_filter must keep
    seeing inactive branches too, so they stay manageable/reactivatable."""
    branch = _make_branch(admin_client, "Stale Branch Admin QA")
    admin_client.post(f"/api/v1/branches/{branch['id']}/deactivate")

    unfiltered = admin_client.get("/api/v1/branches?page_size=200")
    assert any(b["id"] == branch["id"] for b in unfiltered.json()["items"])
