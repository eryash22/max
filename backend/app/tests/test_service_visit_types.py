"""Batch E, Part 20 — Service Visit Type configuration."""
from app.models.incident import ServiceVisitType

from .conftest import auth_headers, create_user


def _make_asset(admin_client, asset_number="VENT-001"):
    branch = admin_client.post("/api/v1/branches", json={"name": f"Branch-{asset_number}", "status": "ACTIVE"}).json()
    model = admin_client.post(
        "/api/v1/device-models", json={"name": f"Ventilator-{asset_number}", "category": "Ventilator"}
    ).json()
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": model["id"], "branch_id": branch["id"]},
    ).json()
    return branch, model, asset


def _engineer_headers(client, db_session, email="eng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _doctor_headers(client, db_session, email="doc@example.com"):
    create_user(db_session, email, "DOCTOR", "Password123!")
    return auth_headers(client, email, "Password123!")


# ---- Seeding ----

def test_default_visit_types_seeded(client, db_session):
    # ensure_default_service_visit_types runs as part of the `client` fixture,
    # mirroring migration 0018's seed of PM/CALIBRATION/BREAKDOWN/INSTALLATION/OTHER.
    names = {t.name for t in db_session.query(ServiceVisitType).all()}
    assert names == {"PM", "CALIBRATION", "BREAKDOWN", "INSTALLATION", "OTHER"}
    assert all(t.is_active for t in db_session.query(ServiceVisitType).all())


# ---- List ----

def test_any_authenticated_user_can_list_visit_types(client, db_session):
    headers = _doctor_headers(client, db_session)
    response = client.get("/api/v1/service-visits/types", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    names = {t["name"] for t in body["items"]}
    assert names == {"PM", "CALIBRATION", "BREAKDOWN", "INSTALLATION", "OTHER"}


def test_list_excludes_inactive_by_default(admin_client, client, db_session):
    to_deactivate = db_session.query(ServiceVisitType).filter(ServiceVisitType.name == "OTHER").first()
    admin_client.post(f"/api/v1/service-visits/types/{to_deactivate.id}/deactivate")

    response = admin_client.get("/api/v1/service-visits/types")
    names = {t["name"] for t in response.json()["items"]}
    assert "OTHER" not in names

    response = admin_client.get("/api/v1/service-visits/types?include_inactive=true")
    names = {t["name"] for t in response.json()["items"]}
    assert "OTHER" in names


# ---- Create (Super Admin only) ----

def test_super_admin_can_create_visit_type(admin_client):
    response = admin_client.post("/api/v1/service-visits/types", json={"name": "UPGRADE"})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "UPGRADE"
    assert body["is_active"] is True


def test_engineer_cannot_create_visit_type(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post("/api/v1/service-visits/types", json={"name": "UPGRADE"}, headers=headers)
    assert response.status_code == 403


def test_duplicate_visit_type_name_rejected(admin_client):
    response = admin_client.post("/api/v1/service-visits/types", json={"name": "PM"})
    assert response.status_code == 409


def test_blank_visit_type_name_rejected(admin_client):
    response = admin_client.post("/api/v1/service-visits/types", json={"name": "   "})
    assert response.status_code == 400


# ---- Deactivate / activate (Super Admin only, never hard-deleted) ----

def test_super_admin_can_deactivate_and_reactivate_visit_type(admin_client, db_session):
    other = db_session.query(ServiceVisitType).filter(ServiceVisitType.name == "OTHER").first()

    response = admin_client.post(f"/api/v1/service-visits/types/{other.id}/deactivate")
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    response = admin_client.post(f"/api/v1/service-visits/types/{other.id}/activate")
    assert response.status_code == 200
    assert response.json()["is_active"] is True


def test_engineer_cannot_deactivate_visit_type(client, db_session):
    other = db_session.query(ServiceVisitType).filter(ServiceVisitType.name == "OTHER").first()
    headers = _engineer_headers(client, db_session)
    response = client.post(f"/api/v1/service-visits/types/{other.id}/deactivate", headers=headers)
    assert response.status_code == 403


def test_deactivate_missing_visit_type_404(admin_client):
    response = admin_client.post("/api/v1/service-visits/types/does-not-exist/deactivate")
    assert response.status_code == 404


def test_deactivating_a_type_in_use_does_not_touch_historical_visits(admin_client, db_session):
    """A type already referenced by historical ServiceVisit rows must never be
    removable — deactivating it must not alter or null out those rows."""
    _, _, asset = _make_asset(admin_client)
    visit = admin_client.post(
        "/api/v1/service-visits", json={"asset_id": asset["id"], "visit_type": "OTHER"}
    ).json()

    other = db_session.query(ServiceVisitType).filter(ServiceVisitType.name == "OTHER").first()
    response = admin_client.post(f"/api/v1/service-visits/types/{other.id}/deactivate")
    assert response.status_code == 200

    fetched = admin_client.get(f"/api/v1/service-visits/{visit['id']}").json()
    assert fetched["visit_type"] == "OTHER"


# ---- visit_type acceptance on ServiceVisit create/update ----

def test_create_visit_rejects_inactive_visit_type(admin_client, db_session):
    other = db_session.query(ServiceVisitType).filter(ServiceVisitType.name == "OTHER").first()
    admin_client.post(f"/api/v1/service-visits/types/{other.id}/deactivate")
    _, _, asset = _make_asset(admin_client)

    response = admin_client.post(
        "/api/v1/service-visits", json={"asset_id": asset["id"], "visit_type": "OTHER"}
    )
    assert response.status_code == 400


def test_create_visit_rejects_unknown_visit_type(admin_client):
    _, _, asset = _make_asset(admin_client)
    response = admin_client.post(
        "/api/v1/service-visits", json={"asset_id": asset["id"], "visit_type": "NOT_A_TYPE"}
    )
    assert response.status_code == 400


def test_create_visit_accepts_new_active_visit_type(admin_client):
    admin_client.post("/api/v1/service-visits/types", json={"name": "UPGRADE"})
    _, _, asset = _make_asset(admin_client)
    response = admin_client.post(
        "/api/v1/service-visits", json={"asset_id": asset["id"], "visit_type": "UPGRADE"}
    )
    assert response.status_code == 201
    assert response.json()["visit_type"] == "UPGRADE"


def test_update_visit_rejects_inactive_visit_type(admin_client, db_session):
    _, _, asset = _make_asset(admin_client)
    visit = admin_client.post(
        "/api/v1/service-visits", json={"asset_id": asset["id"], "visit_type": "PM"}
    ).json()

    other = db_session.query(ServiceVisitType).filter(ServiceVisitType.name == "OTHER").first()
    admin_client.post(f"/api/v1/service-visits/types/{other.id}/deactivate")

    response = admin_client.patch(f"/api/v1/service-visits/{visit['id']}", json={"visit_type": "OTHER"})
    assert response.status_code == 400
