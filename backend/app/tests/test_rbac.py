from .conftest import auth_headers, create_user


def test_viewer_cannot_write_hospital(client, db_session):
    create_user(db_session, "viewer@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "viewer@example.com", "Password123!")
    response = client.post("/api/v1/hospital-networks", json={"code": None, "name": "Network A"}, headers=headers)
    assert response.status_code == 403


def test_viewer_can_read_hospital(client, db_session):
    create_user(db_session, "viewer@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "viewer@example.com", "Password123!")
    response = client.get("/api/v1/branches", headers=headers)
    assert response.status_code == 200


def test_biomedical_admin_can_manage_devices(client, db_session):
    create_user(db_session, "bmadmin@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    headers = auth_headers(client, "bmadmin@example.com", "Password123!")
    response = client.post("/api/v1/device-categories", json={"code": "US", "name": "Ultrasound"}, headers=headers)
    assert response.status_code == 201


def test_unknown_permission_is_rejected_at_role_creation(admin_client):
    response = admin_client.post(
        "/api/v1/roles",
        json={"code": "CUSTOM", "name": "Custom", "permission_codes": ["not.a.real.permission"]},
    )
    assert response.status_code == 400
