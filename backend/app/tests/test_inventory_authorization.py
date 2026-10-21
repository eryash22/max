import pytest

from .conftest import auth_headers, create_user

NO_INVENTORY_ROLES = ["DOCTOR", "STAFF_OPERATOR", "VIEWER"]


@pytest.mark.parametrize("role_code", NO_INVENTORY_ROLES)
def test_roles_without_inventory_grant_cannot_read_inventory(client, db_session, role_code):
    email = f"{role_code.lower()}@example.com"
    create_user(db_session, email, role_code, "Password123!")
    headers = auth_headers(client, email, "Password123!")
    response = client.get("/api/v1/inventory/items", headers=headers)
    assert response.status_code == 403


@pytest.mark.parametrize("role_code", NO_INVENTORY_ROLES)
def test_roles_without_inventory_grant_cannot_write_inventory(client, db_session, role_code):
    email = f"{role_code.lower()}@example.com"
    create_user(db_session, email, role_code, "Password123!")
    headers = auth_headers(client, email, "Password123!")
    response = client.post(
        "/api/v1/inventory/items", json={"item_code": "ITM-1", "name": "Gauze"}, headers=headers
    )
    assert response.status_code == 403


def test_inventory_manager_has_full_inventory_access(client, db_session):
    create_user(db_session, "inv@example.com", "INVENTORY_MANAGER", "Password123!")
    headers = auth_headers(client, "inv@example.com", "Password123!")
    response = client.post(
        "/api/v1/inventory/items", json={"item_code": "ITM-2", "name": "Cold Pack"}, headers=headers
    )
    assert response.status_code == 201


def test_biomedical_engineer_has_read_only_inventory_access(client, db_session):
    create_user(db_session, "eng@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    headers = auth_headers(client, "eng@example.com", "Password123!")
    read_response = client.get("/api/v1/inventory/items", headers=headers)
    assert read_response.status_code == 200
    write_response = client.post(
        "/api/v1/inventory/items", json={"item_code": "ITM-3", "name": "Electrode"}, headers=headers
    )
    assert write_response.status_code == 403
