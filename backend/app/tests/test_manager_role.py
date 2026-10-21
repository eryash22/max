"""Module 20 — dedicated MANAGER role for requirement approval.

BIOMEDICAL_ADMIN previously stood in as the approver (documented limitation in
Module 16A/17A). These tests cover the new MANAGER role end to end: seeding,
permission bundle, existing-role non-regression, the approve/reject workflow,
self-approval blocking, fulfillment continuing to work after a MANAGER
approval, admin-assignable via the Users API, notification recipient
resolution, RBAC scope, and audit logging.
"""
from app.models.audit import AuditLog
from app.models.identity import Permission, Role
from app.models.notifications import Notification
from app.security.permissions import DEFAULT_ROLE_PERMISSIONS, MANAGER
from app.services import communication_service

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, auth_headers, create_user


def _make_branch(admin_client, name="Manager Test Branch"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_device_model(admin_client, name="Manager Test Monitor"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


def _create_and_submit(client, tl_headers, branch_id, model_id, reason="Needs manager review"):
    created = client.post(
        "/api/v1/requirements",
        json={
            "branch_id": branch_id, "reason": reason,
            "items": [{"item_type": "DEVICE_MODEL", "device_model_id": model_id, "requested_quantity": 1}],
        },
        headers=tl_headers,
    )
    assert created.status_code == 201, created.text
    created = created.json()
    submitted = client.post(f"/api/v1/requirements/{created['id']}/submit", headers=tl_headers)
    assert submitted.status_code == 200, submitted.text
    return submitted.json()


# ---------------------------------------------------------------------------
# 1-3: seeding, permission bundle, existing roles intact
# ---------------------------------------------------------------------------

def test_manager_role_is_seeded(client, db_session):
    role = db_session.query(Role).filter(Role.code == "MANAGER").first()
    assert role is not None
    assert role.name == "Manager"
    assert role.is_system is True


def test_manager_permissions_are_correct(client, db_session):
    role = db_session.query(Role).filter(Role.code == "MANAGER").first()
    codes = {rp.permission.code for rp in role.permissions}
    assert codes == set(DEFAULT_ROLE_PERMISSIONS[MANAGER])
    assert "requirement.approve" in codes
    assert "requirement.read" in codes
    # Least privilege: no unrestricted device/asset/inventory/vendor write access.
    assert "device.write" not in codes
    assert "asset.write" not in codes
    assert "inventory.write" not in codes
    assert "users.manage" not in codes
    assert "roles.manage" not in codes


def test_existing_roles_remain_intact(client, db_session):
    expected = {
        "SUPER_ADMIN", "BIOMEDICAL_ADMIN", "BIOMEDICAL_ENGINEER", "INVENTORY_MANAGER",
        "HOSPITAL_UNIT_MANAGER", "DOCTOR", "STAFF_OPERATOR", "VIEWER", "MANAGER",
    }
    codes = {r.code for r in db_session.query(Role).all()}
    assert expected <= codes

    admin_role = db_session.query(Role).filter(Role.code == "BIOMEDICAL_ADMIN").first()
    admin_codes = {rp.permission.code for rp in admin_role.permissions}
    assert "requirement.approve" in admin_codes
    assert "requirement.fulfill" in admin_codes


# ---------------------------------------------------------------------------
# 4-8: MANAGER can read/approve/reject; others cannot
# ---------------------------------------------------------------------------

def test_manager_can_read_allowed_requirements(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl.mgr1@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr.role1@example.com", "MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl.mgr1@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr.role1@example.com", "Password123!")

    requirement = _create_and_submit(client, tl_headers, branch["id"], model["id"])

    listing = client.get("/api/v1/requirements?status_filter=PENDING_MANAGER_APPROVAL", headers=mgr_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    detail = client.get(f"/api/v1/requirements/{requirement['id']}", headers=mgr_headers)
    assert detail.status_code == 200

    history = client.get(f"/api/v1/requirements/{requirement['id']}/history", headers=mgr_headers)
    assert history.status_code == 200


def test_manager_can_approve(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl.mgr2@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr.role2@example.com", "MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl.mgr2@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr.role2@example.com", "Password123!")

    requirement = _create_and_submit(client, tl_headers, branch["id"], model["id"])

    response = client.post(
        f"/api/v1/requirements/{requirement['id']}/approve", json={"remarks": "Approved by Manager"}, headers=mgr_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "APPROVED"
    assert body["manager_decision"] == "APPROVED"


def test_manager_can_reject(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl.mgr3@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr.role3@example.com", "MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl.mgr3@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr.role3@example.com", "Password123!")

    requirement = _create_and_submit(client, tl_headers, branch["id"], model["id"])

    response = client.post(
        f"/api/v1/requirements/{requirement['id']}/reject", json={"remarks": "Not justified"}, headers=mgr_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REJECTED"


def test_non_manager_cannot_approve(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl.mgr4@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "viewer.mgr4@example.com", "VIEWER", "Password123!")
    tl_headers = auth_headers(client, "tl.mgr4@example.com", "Password123!")
    viewer_headers = auth_headers(client, "viewer.mgr4@example.com", "Password123!")

    requirement = _create_and_submit(client, tl_headers, branch["id"], model["id"])

    response = client.post(f"/api/v1/requirements/{requirement['id']}/approve", json={}, headers=viewer_headers)
    assert response.status_code == 403


def test_requester_cannot_self_approve_as_manager(admin_client, client, db_session):
    """A user holding both HOSPITAL_UNIT_MANAGER (to raise) and MANAGER (to approve)
    still cannot approve their own request — the block is on requester identity, not
    role name (see requirement_service.approve_requirement)."""
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    user = create_user(db_session, "dual.role@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    manager_role = db_session.query(Role).filter(Role.code == "MANAGER").first()
    from app.models.identity import UserRole
    db_session.add(UserRole(user_id=user.id, role_id=manager_role.id))
    db_session.commit()
    headers = auth_headers(client, "dual.role@example.com", "Password123!")

    requirement = _create_and_submit(client, headers, branch["id"], model["id"])

    response = client.post(f"/api/v1/requirements/{requirement['id']}/approve", json={}, headers=headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 9-10: approval moves status correctly; fulfillment can still begin
# ---------------------------------------------------------------------------

def test_manager_approval_moves_to_correct_state_and_fulfillment_can_begin(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl.mgr5@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr.role5@example.com", "MANAGER", "Password123!")
    create_user(db_session, "invmgr.mgr5@example.com", "INVENTORY_MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl.mgr5@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr.role5@example.com", "Password123!")
    inv_headers = auth_headers(client, "invmgr.mgr5@example.com", "Password123!")

    requirement = _create_and_submit(client, tl_headers, branch["id"], model["id"])
    assert requirement["status"] == "PENDING_MANAGER_APPROVAL"

    approved = client.post(f"/api/v1/requirements/{requirement['id']}/approve", json={}, headers=mgr_headers).json()
    assert approved["status"] == "APPROVED"

    fulfillment = client.post(
        "/api/v1/fulfillments", json={"requirement_id": approved["id"]}, headers=inv_headers
    )
    assert fulfillment.status_code == 201, fulfillment.text
    assert fulfillment.json()["status"] == "PENDING_ALLOCATION"


# ---------------------------------------------------------------------------
# 11: BIOMEDICAL_ADMIN remains a valid approver
# ---------------------------------------------------------------------------

def test_biomedical_admin_can_still_approve(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl.mgr6@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "bmadmin.mgr6@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    tl_headers = auth_headers(client, "tl.mgr6@example.com", "Password123!")
    admin_headers = auth_headers(client, "bmadmin.mgr6@example.com", "Password123!")

    requirement = _create_and_submit(client, tl_headers, branch["id"], model["id"])
    response = client.post(f"/api/v1/requirements/{requirement['id']}/approve", json={}, headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "APPROVED"


# ---------------------------------------------------------------------------
# 12: user admin can assign MANAGER
# ---------------------------------------------------------------------------

def test_user_admin_can_assign_manager_role(admin_client):
    created = admin_client.post(
        "/api/v1/users",
        json={
            "email": "new.manager@example.com", "name": "New Manager",
            "password": "Password123!", "role_codes": ["MANAGER"],
        },
    )
    assert created.status_code == 201, created.text
    user = created.json()
    assert user["roles"] == ["MANAGER"]

    updated = admin_client.patch(f"/api/v1/users/{user['id']}", json={"role_codes": ["MANAGER"]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["roles"] == ["MANAGER"]


def test_user_admin_can_reassign_existing_user_to_manager(admin_client, db_session):
    existing = create_user(db_session, "reassign.me@example.com", "VIEWER", "Password123!")
    updated = admin_client.patch(f"/api/v1/users/{existing.id}", json={"role_codes": ["MANAGER"]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["roles"] == ["MANAGER"]


# ---------------------------------------------------------------------------
# 13: notification recipient resolution recognizes MANAGER
# ---------------------------------------------------------------------------

def test_notification_recipient_resolution_recognizes_manager(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl.mgr7@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr.role7@example.com", "MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl.mgr7@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr.role7@example.com", "Password123!")

    rule = admin_client.post(
        "/api/v1/notifications/templates",
        json={
            "code": "REQ_APPROVED_MGR_RULE", "channel": "EMAIL", "event": "REQUIREMENT_APPROVED",
            "body_template": "{{requirement_number}} approved by {{manager_name}} ({{manager_email}})",
            "is_active": True, "receiver_email": "{{requester_email}}",
            "sender_name": "Biomedical Dept", "sender_email": "biomed@example.com",
        },
    )
    assert rule.status_code == 201, rule.text

    requirement = _create_and_submit(client, tl_headers, branch["id"], model["id"])
    approved = client.post(f"/api/v1/requirements/{requirement['id']}/approve", json={}, headers=mgr_headers)
    assert approved.status_code == 200, approved.text

    notification = (
        db_session.query(Notification)
        .filter(Notification.event == "REQUIREMENT_APPROVED", Notification.related_requirement_id == requirement["id"])
        .first()
    )
    assert notification is not None
    assert "mgr.role7@example.com" in notification.rendered_body


# ---------------------------------------------------------------------------
# 14: RBAC scope preserved (Manager is organization-wide, like other
# requirement.approve holders today — see PROJECT_STATUS.md Phase 6 decision)
# ---------------------------------------------------------------------------

def test_manager_sees_requirements_across_branches(admin_client, client, db_session):
    branch_a = _make_branch(admin_client, "Branch A")
    branch_b = _make_branch(admin_client, "Branch B")
    model = _make_device_model(admin_client)
    create_user(db_session, "tl.a@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "tl.b@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr.rbac@example.com", "MANAGER", "Password123!")
    tl_a_headers = auth_headers(client, "tl.a@example.com", "Password123!")
    tl_b_headers = auth_headers(client, "tl.b@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr.rbac@example.com", "Password123!")

    _create_and_submit(client, tl_a_headers, branch_a["id"], model["id"])
    _create_and_submit(client, tl_b_headers, branch_b["id"], model["id"])

    listing = client.get("/api/v1/requirements?status_filter=PENDING_MANAGER_APPROVAL", headers=mgr_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 2


# ---------------------------------------------------------------------------
# 15: audit log is created for role assignment and approval
# ---------------------------------------------------------------------------

def test_audit_log_created_for_manager_role_assignment(admin_client, db_session):
    created = admin_client.post(
        "/api/v1/users",
        json={
            "email": "audited.manager@example.com", "name": "Audited Manager",
            "password": "Password123!", "role_codes": ["MANAGER"],
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]

    log = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity == "user", AuditLog.entity_id == user_id, AuditLog.action == "CREATE")
        .first()
    )
    assert log is not None
    assert log.new_value["role_codes"] == ["MANAGER"]


def test_audit_log_created_for_manager_approval(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    create_user(db_session, "tl.mgr8@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    create_user(db_session, "mgr.role8@example.com", "MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl.mgr8@example.com", "Password123!")
    mgr_headers = auth_headers(client, "mgr.role8@example.com", "Password123!")

    requirement = _create_and_submit(client, tl_headers, branch["id"], model["id"])
    approved = client.post(f"/api/v1/requirements/{requirement['id']}/approve", json={}, headers=mgr_headers)
    assert approved.status_code == 200, approved.text

    log = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity == "requirement", AuditLog.entity_id == requirement["id"], AuditLog.action == "APPROVE")
        .first()
    )
    assert log is not None
