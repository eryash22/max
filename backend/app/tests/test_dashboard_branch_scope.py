"""Branch-scoping hardening pass — Dashboard leak fix.

GET /assets/dashboard-summary and GET /assets/calendar-events previously ran
fully unscoped queries regardless of caller, so a UNIT_HEAD/TL's Dashboard showed
organization-wide asset/PM/calibration/contract counts. Both endpoints now reuse
branch_scope_id() (same mechanism as Requirements/Incidents/Assets) to scope
every count/event to the caller's own branch when they hold UNIT_HEAD/TL.
"""
from datetime import date, timedelta

from .conftest import auth_headers, create_user

TODAY = date.today()


def _iso(days_offset: int) -> str:
    return (TODAY + timedelta(days=days_offset)).isoformat()


def _make_branch(admin_client, name):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_device_model(admin_client, name):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


def _make_asset(admin_client, device_model_id, branch_id, asset_number, **extra):
    payload = {"asset_number": asset_number, "device_model_id": device_model_id, "branch_id": branch_id, **extra}
    response = admin_client.post("/api/v1/assets", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _make_vendor(admin_client, name):
    return admin_client.post("/api/v1/vendors", json={"name": name, "vendor_type": "SERVICE", "status": "ACTIVE"}).json()


def _make_amc(admin_client, asset_id, vendor_id, start_date, end_date):
    response = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "vendor_id": vendor_id, "asset_ids": [asset_id],
              "start_date": start_date, "end_date": end_date, "status": "ACTIVE"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _scoped_headers(client, db_session, *, email, role_code, branch_id):
    user = create_user(db_session, email, role_code, "Password123!")
    user.hospital_scope_id = branch_id
    db_session.add(user)
    db_session.commit()
    return auth_headers(client, email, "Password123!")


def test_unit_head_dashboard_summary_scoped_to_own_branch(admin_client, client, db_session):
    branch_a = _make_branch(admin_client, "Dash Scope Branch A")
    branch_b = _make_branch(admin_client, "Dash Scope Branch B")
    model = _make_device_model(admin_client, "Dash Scope Monitor")
    _make_asset(admin_client, model["id"], branch_a["id"], "DASH-SCOPE-A1", status="OPERATIONAL")
    _make_asset(admin_client, model["id"], branch_b["id"], "DASH-SCOPE-B1", status="CONDEMNED")

    headers = _scoped_headers(client, db_session, email="uh.dash@example.com", role_code="UNIT_HEAD", branch_id=branch_a["id"])
    response = client.get("/api/v1/assets/dashboard-summary", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_operational"] == 1
    assert body["condemned"] == 0


def test_tl_calendar_events_scoped_to_own_branch(admin_client, client, db_session):
    branch_a = _make_branch(admin_client, "Cal Scope Branch A")
    branch_b = _make_branch(admin_client, "Cal Scope Branch B")
    model = _make_device_model(admin_client, "Cal Scope Monitor")
    asset_a = _make_asset(admin_client, model["id"], branch_a["id"], "CAL-SCOPE-A1")
    asset_b = _make_asset(admin_client, model["id"], branch_b["id"], "CAL-SCOPE-B1")

    admin_client.post("/api/v1/pm/schedules", json={"asset_id": asset_a["id"], "frequency": "MONTHLY", "next_due_date": _iso(10)})
    admin_client.post("/api/v1/pm/schedules", json={"asset_id": asset_b["id"], "frequency": "MONTHLY", "next_due_date": _iso(10)})

    headers = _scoped_headers(client, db_session, email="tl.cal@example.com", role_code="TL", branch_id=branch_a["id"])
    response = client.get("/api/v1/assets/calendar-events", headers=headers)
    assert response.status_code == 200, response.text
    events = response.json()
    scoped_asset_ids = {e["asset_id"] for e in events if e["event_type"] == "PM_DUE"}
    assert asset_a["id"] in scoped_asset_ids
    assert asset_b["id"] not in scoped_asset_ids


def test_unit_head_dashboard_amc_counts_scoped_to_own_branch(admin_client, client, db_session):
    branch_a = _make_branch(admin_client, "Dash AMC Branch A")
    branch_b = _make_branch(admin_client, "Dash AMC Branch B")
    model = _make_device_model(admin_client, "Dash AMC Monitor")
    vendor = _make_vendor(admin_client, "Dash AMC Vendor")
    asset_a = _make_asset(admin_client, model["id"], branch_a["id"], "DASH-AMC-A1")
    asset_b = _make_asset(admin_client, model["id"], branch_b["id"], "DASH-AMC-B1")

    _make_amc(admin_client, asset_a["id"], vendor["id"], _iso(-10), _iso(20))
    _make_amc(admin_client, asset_b["id"], vendor["id"], _iso(-10), _iso(20))

    headers = _scoped_headers(client, db_session, email="uh.amc@example.com", role_code="UNIT_HEAD", branch_id=branch_a["id"])
    response = client.get("/api/v1/assets/dashboard-summary", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["amc_active"] == 1


def test_super_admin_dashboard_and_calendar_stay_unscoped(admin_client):
    """Sanity check — the branch-scoping fix must not affect roles with broad
    visibility (SUPER_ADMIN and every other non-UNIT_HEAD/TL role)."""
    branch_a = admin_client.post("/api/v1/branches", json={"name": "Unscoped Branch A", "status": "ACTIVE"}).json()
    branch_b = admin_client.post("/api/v1/branches", json={"name": "Unscoped Branch B", "status": "ACTIVE"}).json()
    model = _make_device_model(admin_client, "Unscoped Monitor")
    _make_asset(admin_client, model["id"], branch_a["id"], "UNSCOPE-A1", status="OPERATIONAL")
    _make_asset(admin_client, model["id"], branch_b["id"], "UNSCOPE-B1", status="OPERATIONAL")

    response = admin_client.get("/api/v1/assets/dashboard-summary")
    assert response.status_code == 200, response.text
    assert response.json()["total_operational"] >= 2
