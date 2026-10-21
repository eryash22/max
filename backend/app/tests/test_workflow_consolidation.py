"""Workflow consolidation pass — end-to-end integration coverage.

Every other test file in this suite exercises one module's own boundary (Requirement,
Fulfillment, Incident, PM, Calibration each in isolation). This file walks the full
chains the brief describes: Branch -> Requirement -> Manager approval -> Inventory
Manager -> Fulfillment -> Delivery -> Branch/Asset status; Branch -> Incident (device
issue) -> Biomedical Engineer -> resolution; Branch -> Incident (calibration request)
-> Biomedical Engineer -> linked CalibrationRecord -> branch hub visibility.
"""
from app.models.device import Asset

from .conftest import auth_headers, create_user


def _make_branch(admin_client, name):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_device_model(admin_client, name):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


def test_device_requirement_full_chain_to_operational_branch_device(admin_client, client, db_session):
    """Branch -> Requirement (device) -> Manager approval -> Inventory Manager ->
    allocate/process/pack/dispatch/deliver/complete -> device shows under the
    REQUESTING branch as OPERATIONAL, not the branch it started at."""
    central_branch = _make_branch(admin_client, "Central Warehouse Branch")
    requesting_branch = _make_branch(admin_client, "Requesting Branch")
    model = _make_device_model(admin_client, "Consolidation Monitor")
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": "CONS-001", "device_model_id": model["id"], "branch_id": central_branch["id"]},
    ).json()

    create_user(db_session, "tl.consolidation@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl.consolidation@example.com", "Password123!")
    create_user(db_session, "mgr.consolidation@example.com", "MANAGER", "Password123!")
    mgr_headers = auth_headers(client, "mgr.consolidation@example.com", "Password123!")
    create_user(db_session, "inv.consolidation@example.com", "INVENTORY_MANAGER", "Password123!")
    inv_headers = auth_headers(client, "inv.consolidation@example.com", "Password123!")

    created = client.post(
        "/api/v1/requirements",
        json={
            "branch_id": requesting_branch["id"], "reason": "Consolidation E2E test",
            "items": [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        },
        headers=tl_headers,
    )
    assert created.status_code == 201, created.text
    requirement = created.json()

    submitted = client.post(f"/api/v1/requirements/{requirement['id']}/submit", headers=tl_headers)
    assert submitted.status_code == 200, submitted.text

    # A Manager who did not raise the requirement approves it — self-approval is
    # separately blocked (test_manager_role.py), this exercises the real handoff.
    approved = client.post(f"/api/v1/requirements/{requirement['id']}/approve", json={}, headers=mgr_headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"

    fulfillment = client.post(
        "/api/v1/fulfillments", json={"requirement_id": requirement["id"]}, headers=inv_headers
    ).json()
    requirement_item_id = fulfillment["items"][0]["requirement_item_id"]

    # Before delivery completes, the branch must NOT show the device as its own yet.
    pending_check = admin_client.get(f"/api/v1/assets?branch_id={requesting_branch['id']}")
    assert pending_check.json()["total"] == 0

    assert client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": requirement_item_id, "asset_ids": [asset["id"]]}]},
        headers=inv_headers,
    ).status_code == 200
    assert client.post(f"/api/v1/fulfillments/{fulfillment['id']}/process", headers=inv_headers).status_code == 200
    assert client.post(f"/api/v1/fulfillments/{fulfillment['id']}/pack", headers=inv_headers).status_code == 200
    assert client.post(f"/api/v1/fulfillments/{fulfillment['id']}/out-for-delivery", headers=inv_headers).status_code == 200
    assert client.post(f"/api/v1/fulfillments/{fulfillment['id']}/deliver", json={}, headers=inv_headers).status_code == 200

    complete = client.post(f"/api/v1/fulfillments/{fulfillment['id']}/complete", headers=inv_headers)
    assert complete.status_code == 200, complete.text

    moved_asset = db_session.get(Asset, asset["id"])
    db_session.refresh(moved_asset)
    assert moved_asset.branch_id == requesting_branch["id"]
    assert moved_asset.status == "OPERATIONAL"

    final_check = admin_client.get(f"/api/v1/assets?branch_id={requesting_branch['id']}")
    assert final_check.json()["total"] == 1
    assert final_check.json()["items"][0]["id"] == asset["id"]

    still_at_central = admin_client.get(f"/api/v1/assets?branch_id={central_branch['id']}")
    assert still_at_central.json()["total"] == 0


def test_device_issue_incident_full_chain_to_resolution(admin_client, client, db_session):
    """Branch -> Incident (device issue) -> Biomedical Engineer assigned (fires
    INCIDENT_ASSIGNED) -> diagnosed -> resolved/closed (fires INCIDENT_RESOLVED)."""
    branch = _make_branch(admin_client, "Incident Chain Branch")
    model = _make_device_model(admin_client, "Incident Chain Ventilator")
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": "INC-CHAIN-001", "device_model_id": model["id"], "branch_id": branch["id"]},
    ).json()

    create_user(db_session, "tl.incchain@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl.incchain@example.com", "Password123!")
    engineer = create_user(db_session, "eng.incchain@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    eng_headers = auth_headers(client, "eng.incchain@example.com", "Password123!")

    admin_client.post(
        "/api/v1/notifications/templates",
        json={
            "code": "INCIDENT_CHAIN_ASSIGNED", "channel": "EMAIL", "event": "INCIDENT_ASSIGNED",
            "body_template": "Assigned: {{description}}", "receiver_email": "{{assigned_engineer_email}}",
            "is_active": True,
        },
    )

    # This would have 403'd before the RBAC fix (HOSPITAL_UNIT_MANAGER missing
    # incident.create) even though /raise already offered TL this exact tile.
    incident = client.post(
        "/api/v1/incidents",
        json={"branch_id": branch["id"], "asset_id": asset["id"], "description": "Ventilator alarm won't clear",
              "severity": "CRITICAL", "problem_category": "Device Issue"},
        headers=tl_headers,
    )
    assert incident.status_code == 201, incident.text
    incident = incident.json()

    assigned = client.post(
        f"/api/v1/incidents/{incident['id']}/assign", json={"assigned_engineer_id": engineer.id}, headers=eng_headers
    )
    assert assigned.status_code == 200
    assert assigned.json()["status"] == "WORK_IN_PROGRESS"

    from app.models.notifications import Notification

    assigned_notifications = db_session.query(Notification).filter(Notification.event == "INCIDENT_ASSIGNED").all()
    assert len(assigned_notifications) == 1
    assert assigned_notifications[0].recipient == engineer.email

    condemned = client.post(
        f"/api/v1/incidents/{incident['id']}/status", json={"to_status": "TO_BE_CONDEMNED"}, headers=eng_headers
    )
    assert condemned.status_code == 200

    # An illegal jump (skipping straight back to OPEN) must now be rejected.
    illegal = client.post(f"/api/v1/incidents/{incident['id']}/status", json={"to_status": "OPEN"}, headers=eng_headers)
    assert illegal.status_code == 409

    closed = client.post(f"/api/v1/incidents/{incident['id']}/status", json={"to_status": "OPERATIONAL"}, headers=eng_headers)
    assert closed.status_code == 200
    assert closed.json()["closed_by"] == engineer.id


def test_calibration_incident_full_chain_to_branch_hub_visibility(admin_client, client, db_session):
    """Branch -> Incident (calibration request) -> Biomedical Engineer creates a
    linked CalibrationRecord -> branch hub's calibration-due filter reflects it."""
    branch = _make_branch(admin_client, "Calibration Chain Branch")
    model = _make_device_model(admin_client, "Calibration Chain Monitor")
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": "CAL-CHAIN-001", "device_model_id": model["id"], "branch_id": branch["id"]},
    ).json()

    create_user(db_session, "tl.calchain@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl.calchain@example.com", "Password123!")
    create_user(db_session, "eng.calchain@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    eng_headers = auth_headers(client, "eng.calchain@example.com", "Password123!")

    incident = client.post(
        "/api/v1/incidents",
        json={"branch_id": branch["id"], "asset_id": asset["id"], "description": "Annual calibration due",
              "problem_category": "Calibration Request"},
        headers=tl_headers,
    ).json()

    # Before the hand-off, the branch hub has nothing to show.
    before = admin_client.get(f"/api/v1/calibration/schedules?branch_id={branch['id']}")
    assert before.json()["total"] == 0

    schedule = client.post(
        "/api/v1/calibration/schedules", json={"asset_id": asset["id"], "frequency": "YEARLY"}, headers=eng_headers
    ).json()
    record = client.post(
        "/api/v1/calibration/records",
        json={"schedule_id": schedule["id"], "asset_id": asset["id"], "incident_id": incident["id"],
              "pass_fail": "PASS"},
        headers=eng_headers,
    )
    assert record.status_code == 201, record.text
    assert record.json()["incident_id"] == incident["id"]

    after = admin_client.get(f"/api/v1/calibration/schedules?branch_id={branch['id']}")
    assert after.json()["total"] == 1
    assert after.json()["items"][0]["asset_id"] == asset["id"]

    branch_records = admin_client.get(f"/api/v1/calibration/records?branch_id={branch['id']}")
    assert branch_records.json()["total"] == 1
    assert branch_records.json()["items"][0]["incident_id"] == incident["id"]
