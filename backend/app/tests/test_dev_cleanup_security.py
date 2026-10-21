"""Security hardening pass — DEV_DATA_CLEANUP (force=true) safety net.

force=true bypasses every entity's normal reference/status delete-guards via the
generic FK-graph cascade (services/force_delete.py::force_delete_row). The
DEV_DATA_CLEANUP permission check alone only proves the caller is a Super Admin —
it says nothing about whether this is a QA/demo deployment. These tests cover the
environment gate (services/force_delete.py::assert_dev_environment) added to close
that gap, plus the audit trail that was previously missing on most force-delete
paths.
"""
from app.config import settings
from app.models.audit import AuditLog

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, auth_headers, create_user


def _make_branch(admin_client, name="Cleanup Branch"):
    response = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"})
    assert response.status_code == 201, response.text
    return response.json()


def test_force_delete_branch_blocked_in_production(admin_client, monkeypatch):
    branch = _make_branch(admin_client)
    monkeypatch.setattr(settings, "environment", "production")

    response = admin_client.delete(f"/api/v1/branches/{branch['id']}?force=true")
    assert response.status_code == 403
    assert "production" in response.json()["detail"].lower()

    # The branch must still exist — a blocked force-delete is not a partial delete.
    still_there = admin_client.get(f"/api/v1/branches/{branch['id']}")
    assert still_there.status_code == 200


def test_force_delete_branch_allowed_outside_production(admin_client, monkeypatch):
    branch = _make_branch(admin_client)
    monkeypatch.setattr(settings, "environment", "development")

    response = admin_client.delete(f"/api/v1/branches/{branch['id']}?force=true")
    assert response.status_code == 204

    gone = admin_client.get(f"/api/v1/branches/{branch['id']}")
    assert gone.status_code == 404


def test_force_delete_branch_writes_audit_log(admin_client, db_session):
    branch = _make_branch(admin_client, name="Audited Cleanup Branch")
    response = admin_client.delete(f"/api/v1/branches/{branch['id']}?force=true")
    assert response.status_code == 204

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "branch", AuditLog.entity_id == branch["id"], AuditLog.action == "FORCE_DELETE"
    ).first()
    assert log is not None
    assert log.old_value["name"] == "Audited Cleanup Branch"


def test_force_delete_user_blocked_in_production(admin_client, client, db_session, monkeypatch):
    victim = client.post(
        "/api/v1/users",
        json={"email": "prod.protected@example.com", "name": "X", "password": "Password123!", "role_codes": ["VIEWER"]},
        headers=auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD),
    ).json()
    monkeypatch.setattr(settings, "environment", "production")

    response = admin_client.delete(f"/api/v1/users/{victim['id']}?force=true")
    assert response.status_code == 403


def test_force_delete_requirement_blocked_in_production(admin_client, client, db_session, monkeypatch):
    branch = _make_branch(admin_client, name="Requirement Cleanup Branch")
    model = admin_client.post("/api/v1/device-models", json={"name": "Cleanup Model", "category": "MONITOR"}).json()
    create_user(db_session, "tl.cleanup@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl.cleanup@example.com", "Password123!")
    requirement = client.post(
        "/api/v1/requirements",
        json={
            "branch_id": branch["id"], "reason": "cleanup test",
            "items": [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        },
        headers=tl_headers,
    )
    assert requirement.status_code == 201, requirement.text
    requirement = requirement.json()
    monkeypatch.setattr(settings, "environment", "production")

    response = admin_client.delete(f"/api/v1/requirements/{requirement['id']}?force=true")
    assert response.status_code == 403


def test_force_delete_still_requires_super_admin_even_outside_production(client, db_session, monkeypatch):
    """The environment gate is additive, not a replacement for the permission check —
    a non-Super-Admin must still 403 even in a dev/QA environment."""
    monkeypatch.setattr(settings, "environment", "development")
    create_user(db_session, ADMIN_EMAIL, "SUPER_ADMIN", ADMIN_PASSWORD)
    admin_headers = auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    branch = client.post(
        "/api/v1/branches", json={"name": "Non-Admin Cleanup Branch", "status": "ACTIVE"}, headers=admin_headers
    ).json()

    create_user(db_session, "bio.admin.cleanup@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    bio_admin_headers = auth_headers(client, "bio.admin.cleanup@example.com", "Password123!")
    response = client.delete(f"/api/v1/branches/{branch['id']}?force=true", headers=bio_admin_headers)
    assert response.status_code == 403
