from app.models.audit import AuditLog


def test_login_creates_audit_log(client, db_session):
    from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, create_user

    create_user(db_session, ADMIN_EMAIL, "SUPER_ADMIN", ADMIN_PASSWORD)
    client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})

    logs = db_session.query(AuditLog).filter(AuditLog.action == "LOGIN").all()
    assert len(logs) == 1


def test_branch_creation_is_audited(admin_client, db_session):
    response = admin_client.post("/api/v1/branches", json={"name": "Vaishali", "status": "ACTIVE"})
    branch_id = response.json()["id"]

    log = db_session.query(AuditLog).filter(AuditLog.entity == "branch", AuditLog.entity_id == branch_id).first()
    assert log is not None
    assert log.action == "CREATE"
    assert log.new_value["name"] == "Vaishali"


def test_audit_logs_endpoint_requires_permission(client, db_session):
    from .conftest import auth_headers, create_user

    create_user(db_session, "viewer@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "viewer@example.com", "Password123!")
    response = client.get("/api/v1/audit-logs", headers=headers)
    assert response.status_code == 403


def test_audit_logs_endpoint_lists_entries(admin_client):
    admin_client.post("/api/v1/branches", json={"name": "Gurugram", "status": "ACTIVE"})
    response = admin_client.get("/api/v1/audit-logs")
    assert response.status_code == 200
    assert response.json()["total"] >= 1
