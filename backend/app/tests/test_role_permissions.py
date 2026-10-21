"""Centralized Role & Permissions system — the canonical source-of-truth checks.

routers/roles.py's PATCH /api/v1/roles/{id} already does a full replace (delete every
existing role_permission row, reinsert exactly the given list) rather than an
additive-only merge, and dependencies/auth.py's get_current_user() recomputes
role/permission codes from the DB on every request (no cache) — these tests pin down
that behavior so it can't silently regress into "turning a permission off doesn't
actually remove it" or "the permissions screen and actual access drift apart."
"""
from app.models.identity import Role, RolePermission

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, auth_headers, create_user


def test_super_admin_can_read_role_permissions(admin_client):
    response = admin_client.get("/api/v1/roles")
    assert response.status_code == 200
    codes = {role["code"] for role in response.json()}
    assert "TL" in codes and "UNIT_HEAD" in codes and "SUPER_ADMIN" in codes


def test_super_admin_can_update_role_permissions(admin_client, db_session):
    role = db_session.query(Role).filter(Role.code == "TL").first()
    new_codes = sorted({rp.permission.code for rp in role.permissions} | {"inventory.picker_read"})

    response = admin_client.patch(f"/api/v1/roles/{role.id}", json={"permission_codes": new_codes})
    assert response.status_code == 200, response.text
    assert set(response.json()["permissions"]) == set(new_codes)


def test_unauthorized_user_cannot_update_role_permissions(client, db_session):
    role = db_session.query(Role).filter(Role.code == "TL").first()
    create_user(db_session, "viewer.roles@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "viewer.roles@example.com", "Password123!")

    response = client.patch(f"/api/v1/roles/{role.id}", json={"permission_codes": []}, headers=headers)
    assert response.status_code == 403


def test_turning_a_permission_off_actually_removes_it(admin_client, db_session):
    role = db_session.query(Role).filter(Role.code == "TL").first()
    current_codes = {rp.permission.code for rp in role.permissions}
    assert "requirement.create" in current_codes
    reduced_codes = sorted(current_codes - {"requirement.create"})

    response = admin_client.patch(f"/api/v1/roles/{role.id}", json={"permission_codes": reduced_codes})
    assert response.status_code == 200, response.text
    assert "requirement.create" not in response.json()["permissions"]

    db_session.expire_all()
    remaining = {rp.permission.code for rp in db_session.query(Role).filter(Role.id == role.id).first().permissions}
    assert "requirement.create" not in remaining


def test_turning_a_permission_on_actually_grants_it(admin_client, db_session):
    role = db_session.query(Role).filter(Role.code == "VIEWER").first()
    current_codes = {rp.permission.code for rp in role.permissions}
    assert "notification.approve" not in current_codes
    added_codes = sorted(current_codes | {"notification.approve"})

    response = admin_client.patch(f"/api/v1/roles/{role.id}", json={"permission_codes": added_codes})
    assert response.status_code == 200, response.text
    assert "notification.approve" in response.json()["permissions"]


def test_repeated_saves_never_create_duplicate_role_permission_rows(admin_client, db_session):
    role = db_session.query(Role).filter(Role.code == "TL").first()
    codes = sorted({rp.permission.code for rp in role.permissions})

    for _ in range(3):
        response = admin_client.patch(f"/api/v1/roles/{role.id}", json={"permission_codes": codes})
        assert response.status_code == 200, response.text

    db_session.expire_all()
    rows = db_session.query(RolePermission).filter(RolePermission.role_id == role.id).all()
    permission_ids = [r.permission_id for r in rows]
    assert len(permission_ids) == len(set(permission_ids))
    assert len(permission_ids) == len(codes)


def test_role_assignment_gives_correct_effective_permissions(admin_client, client, db_session):
    role = db_session.query(Role).filter(Role.code == "UNIT_HEAD").first()
    unit_head_codes = {rp.permission.code for rp in role.permissions}

    user = create_user(db_session, "effective.uh@example.com", "UNIT_HEAD", "Password123!")
    headers = auth_headers(client, "effective.uh@example.com", "Password123!")
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert set(me["permissions"]) == unit_head_codes


def test_changing_users_role_changes_effective_permissions_with_no_leakage(admin_client, client, db_session):
    user = create_user(db_session, "reassign@example.com", "UNIT_HEAD", "Password123!")
    unit_head_role = db_session.query(Role).filter(Role.code == "UNIT_HEAD").first()
    unit_head_codes = {rp.permission.code for rp in unit_head_role.permissions}

    headers = auth_headers(client, "reassign@example.com", "Password123!")
    me_before = client.get("/api/v1/auth/me", headers=headers).json()
    assert set(me_before["permissions"]) == unit_head_codes

    update = admin_client.patch(f"/api/v1/users/{user.id}", json={"role_codes": ["INVENTORY_MANAGER"]})
    assert update.status_code == 200, update.text

    inventory_manager_role = db_session.query(Role).filter(Role.code == "INVENTORY_MANAGER").first()
    inventory_manager_codes = {rp.permission.code for rp in inventory_manager_role.permissions}

    headers_after = auth_headers(client, "reassign@example.com", "Password123!")
    me_after = client.get("/api/v1/auth/me", headers=headers_after).json()
    assert set(me_after["permissions"]) == inventory_manager_codes
    # No stale UNIT_HEAD-only permission should remain once the role changed.
    unit_head_only = unit_head_codes - inventory_manager_codes
    assert not (unit_head_only & set(me_after["permissions"]))


def test_direct_api_reflects_updated_role_permission_without_relogin(admin_client, client, db_session):
    """No permission cache exists (dependencies/auth.py recomputes from the DB on
    every request) — a role edit must take effect on the very next API call made
    with an already-issued token, no re-login required."""
    user = create_user(db_session, "nocache.viewer@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "nocache.viewer@example.com", "Password123!")

    denied = client.get("/api/v1/roles", headers=headers)
    assert denied.status_code == 403

    role = db_session.query(Role).filter(Role.code == "VIEWER").first()
    codes = sorted({rp.permission.code for rp in role.permissions} | {"roles.manage"})
    admin_client.patch(f"/api/v1/roles/{role.id}", json={"permission_codes": codes})

    allowed = client.get("/api/v1/roles", headers=headers)
    assert allowed.status_code == 200
