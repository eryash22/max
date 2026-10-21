from datetime import date, timedelta

from .conftest import auth_headers, create_user

TODAY = date.today()


def _iso(days_offset: int) -> str:
    return (TODAY + timedelta(days=days_offset)).isoformat()


def _make_branch(admin_client, name="Lifecycle Branch"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_device_model(admin_client, name="Lifecycle Monitor"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


def _make_asset(admin_client, device_model_id, branch_id, asset_number, **extra):
    payload = {"asset_number": asset_number, "device_model_id": device_model_id, "branch_id": branch_id, **extra}
    response = admin_client.post("/api/v1/assets", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _make_vendor(admin_client, name="Lifecycle Vendor"):
    return admin_client.post("/api/v1/vendors", json={"name": name, "vendor_type": "SERVICE", "status": "ACTIVE"}).json()


def _make_warranty(admin_client, asset_id, vendor_id, start_date, end_date):
    response = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "WARRANTY", "vendor_id": vendor_id, "asset_ids": [asset_id],
              "start_date": start_date, "end_date": end_date, "status": "ACTIVE"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_amc(admin_client, asset_id, vendor_id, start_date, end_date):
    response = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "vendor_id": vendor_id, "asset_ids": [asset_id],
              "start_date": start_date, "end_date": end_date, "status": "ACTIVE"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _engineer_headers(client, db_session, email="lc.eng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _doctor_headers(client, db_session, email="lc.doc@example.com"):
    create_user(db_session, email, "DOCTOR", "Password123!")
    return auth_headers(client, email, "Password123!")


# ---------------------------------------------------------------------------
# Asset fields
# ---------------------------------------------------------------------------

def test_asset_persists_operational_since(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "OPS-001", operational_since="2026-01-15")
    assert asset["operational_since"] == "2026-01-15"

    fetched = admin_client.get(f"/api/v1/assets/{asset['id']}")
    assert fetched.json()["operational_since"] == "2026-01-15"


def test_lifecycle_statuses_endpoint(admin_client):
    response = admin_client.get("/api/v1/assets/lifecycle-statuses")
    assert response.status_code == 200
    statuses = response.json()
    for expected in ["PROCUREMENT", "OPERATIONAL", "UNDER_MAINTENANCE", "UNDER_REPAIR", "AMC", "CMC", "WARRANTY", "CONDEMNED", "RETIRED"]:
        assert expected in statuses


def test_legacy_status_values_still_accepted(admin_client):
    """Asset.status has no enforced vocabulary — pre-18A free-text values must keep working."""
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "LEGACY-001", status="In Service")
    assert asset["status"] == "In Service"


# ---------------------------------------------------------------------------
# Lifecycle summary — derived AMC/CMC/Warranty coverage
# ---------------------------------------------------------------------------

def test_lifecycle_summary_derives_active_warranty_and_amc(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "COV-001", operational_since=_iso(-100))
    vendor = _make_vendor(admin_client, "Coverage Vendor")
    _make_warranty(admin_client, asset["id"], vendor["id"], _iso(-200), _iso(100))
    _make_amc(admin_client, asset["id"], vendor["id"], _iso(-30), _iso(300))

    summary = admin_client.get(f"/api/v1/assets/{asset['id']}/lifecycle")
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["warranty"]["expiry_status"] == "ACTIVE"
    assert body["amc"]["expiry_status"] == "ACTIVE"
    assert body["cmc"] is None
    assert body["current_vendor_id"] == vendor["id"]
    assert body["operational_since"] == _iso(-100)


def test_lifecycle_summary_reports_expired_contract(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "COV-002")
    vendor = _make_vendor(admin_client, "Expired Vendor")
    _make_warranty(admin_client, asset["id"], vendor["id"], _iso(-400), _iso(-10))

    summary = admin_client.get(f"/api/v1/assets/{asset['id']}/lifecycle").json()
    assert summary["warranty"]["expiry_status"] == "EXPIRED"


def test_lifecycle_summary_no_coverage_configured(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "COV-003")

    summary = admin_client.get(f"/api/v1/assets/{asset['id']}/lifecycle").json()
    assert summary["warranty"] is None
    assert summary["amc"] is None
    assert summary["cmc"] is None
    assert summary["current_vendor_id"] is None


def test_lifecycle_summary_requires_existing_asset(admin_client):
    response = admin_client.get("/api/v1/assets/does-not-exist/lifecycle")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Maintenance history — unified PM/Calibration/ServiceVisit view
# ---------------------------------------------------------------------------

def test_maintenance_history_unifies_pm_calibration_and_service_visit(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "MH-001")
    vendor = _make_vendor(admin_client, "MH Vendor")
    # Use the current admin as the PM engineer for simplicity — a real User id is all
    # the FK validation on PMRecord.engineer_id requires.
    admin_id = admin_client.get("/api/v1/auth/me").json()["id"]

    pm = admin_client.post(
        "/api/v1/pm/records",
        json={"asset_id": asset["id"], "engineer_id": admin_id, "performed_date": _iso(-10),
              "parts_replaced": "Filter", "functional_check_result": "PASS", "next_due_date": _iso(80)},
    )
    assert pm.status_code == 201, pm.text

    cal = admin_client.post(
        "/api/v1/calibration/records",
        json={"asset_id": asset["id"], "performed_date": _iso(-5), "calibration_agency": "Cal Co",
              "pass_fail": "PASS", "next_due_date": _iso(175)},
    )
    assert cal.status_code == 201, cal.text

    visit = admin_client.post(
        "/api/v1/service-visits",
        json={"asset_id": asset["id"], "vendor_id": vendor["id"], "visit_type": "BREAKDOWN",
              "visit_date": _iso(-2), "issue": "Screen flickering", "summary": "Replaced display cable",
              "downtime_hours": 3.5, "next_action": "Monitor for recurrence"},
    )
    assert visit.status_code == 201, visit.text

    history = admin_client.get(f"/api/v1/assets/{asset['id']}/maintenance-history")
    assert history.status_code == 200, history.text
    body = history.json()
    assert body["total"] == 3
    source_types = {item["source_type"] for item in body["items"]}
    assert source_types == {"PM", "CALIBRATION", "SERVICE_VISIT"}

    # Sorted newest-first by service_date.
    dates = [item["service_date"] for item in body["items"]]
    assert dates == sorted(dates, reverse=True)

    visit_entry = next(item for item in body["items"] if item["source_type"] == "SERVICE_VISIT")
    assert visit_entry["issue"] == "Screen flickering"
    assert visit_entry["downtime_hours"] == 3.5
    assert visit_entry["action_taken"] == "Replaced display cable"


def test_service_visit_issue_and_downtime_persist_via_direct_endpoint(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "MH-002")
    response = admin_client.post(
        "/api/v1/service-visits",
        json={"asset_id": asset["id"], "visit_type": "OTHER", "issue": "Unusual noise", "downtime_hours": 1.25},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["issue"] == "Unusual noise"
    assert body["downtime_hours"] == 1.25


def test_maintenance_history_pagination(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "MH-003")
    for i in range(3):
        admin_client.post(
            "/api/v1/service-visits",
            json={"asset_id": asset["id"], "visit_type": "OTHER", "visit_date": _iso(-i), "summary": f"Visit {i}"},
        )
    response = admin_client.get(f"/api/v1/assets/{asset['id']}/maintenance-history", params={"page": 1, "page_size": 2})
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


# ---------------------------------------------------------------------------
# Dashboard data foundation
# ---------------------------------------------------------------------------

def test_dashboard_summary_counts_by_status_and_expiry(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    vendor = _make_vendor(admin_client, "Dashboard Vendor")

    operational = _make_asset(admin_client, model["id"], branch["id"], "DASH-OP", status="OPERATIONAL")
    maintenance = _make_asset(admin_client, model["id"], branch["id"], "DASH-MAINT", status="UNDER_MAINTENANCE")
    repair = _make_asset(admin_client, model["id"], branch["id"], "DASH-REPAIR", status="UNDER_REPAIR")
    condemned = _make_asset(admin_client, model["id"], branch["id"], "DASH-COND", status="CONDEMNED")
    retired = _make_asset(admin_client, model["id"], branch["id"], "DASH-RET", status="RETIRED")

    _make_amc(admin_client, operational["id"], vendor["id"], _iso(-10), _iso(20))  # active, expiring within 30d window
    _make_amc(admin_client, maintenance["id"], vendor["id"], _iso(-400), _iso(-10))  # expired

    response = admin_client.get("/api/v1/assets/dashboard-summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_operational"] >= 1
    assert body["under_maintenance"] >= 1
    assert body["under_repair"] >= 1
    assert body["condemned"] >= 1
    assert body["retired"] >= 1
    assert body["amc_active"] >= 1
    assert body["amc_expired"] >= 1
    assert body["upcoming_amc_expiry"] >= 1


def test_dashboard_summary_includes_recent_maintenance(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "DASH-RECENT")
    admin_client.post(
        "/api/v1/service-visits",
        json={"asset_id": asset["id"], "visit_type": "OTHER", "visit_date": _iso(0), "summary": "Recent visit"},
    )
    response = admin_client.get("/api/v1/assets/dashboard-summary")
    body = response.json()
    assert any(item["asset_id"] == asset["id"] for item in body["recent_maintenance"])


# ---------------------------------------------------------------------------
# Calendar data foundation
# ---------------------------------------------------------------------------

def test_calendar_events_include_pm_calibration_and_expiry_dates(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "CAL-001")
    vendor = _make_vendor(admin_client, "Calendar Vendor")

    admin_client.post("/api/v1/pm/schedules", json={"asset_id": asset["id"], "frequency": "MONTHLY", "next_due_date": _iso(15)})
    admin_client.post("/api/v1/calibration/schedules", json={"asset_id": asset["id"], "frequency": "YEARLY", "next_due_date": _iso(45)})
    _make_amc(admin_client, asset["id"], vendor["id"], _iso(-10), _iso(60))

    response = admin_client.get("/api/v1/assets/calendar-events")
    assert response.status_code == 200, response.text
    event_types = {e["event_type"] for e in response.json()}
    assert "PM_DUE" in event_types
    assert "CALIBRATION_DUE" in event_types
    assert "AMC_EXPIRY" in event_types


def test_calendar_events_filtered_by_date_range(admin_client):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "CAL-002")
    admin_client.post("/api/v1/pm/schedules", json={"asset_id": asset["id"], "frequency": "MONTHLY", "next_due_date": _iso(200)})

    response = admin_client.get(
        "/api/v1/assets/calendar-events", params={"start_date": _iso(0), "end_date": _iso(10)}
    )
    dates = [e["date"] for e in response.json()]
    assert _iso(200) not in dates


# ---------------------------------------------------------------------------
# Module 19 — EXPECTED_DELIVERY calendar events + OPERATIONAL status fix
# ---------------------------------------------------------------------------

def test_calendar_events_include_in_flight_expected_delivery(admin_client, client, db_session):
    from .test_fulfillments import _create_approved_requirement

    branch = _make_branch(admin_client, "Delivery Cal Branch")
    model = _make_device_model(admin_client, "Delivery Cal Monitor")
    asset = _make_asset(admin_client, model["id"], branch["id"], "CAL-DELIVERY")
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="calfulfill.tl@example.com",
    )
    fulfillment = admin_client.post(
        "/api/v1/fulfillments",
        json={"requirement_id": requirement["id"], "expected_delivery_date": _iso(5)},
    ).json()
    requirement_item_id = fulfillment["items"][0]["requirement_item_id"]
    allocate = admin_client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": requirement_item_id, "asset_ids": [asset["id"]]}]},
    )
    assert allocate.status_code == 200, allocate.text

    response = admin_client.get("/api/v1/assets/calendar-events")
    assert response.status_code == 200, response.text
    delivery_events = [e for e in response.json() if e["event_type"] == "EXPECTED_DELIVERY"]
    assert any(e["reference_id"] == fulfillment["id"] and e["date"] == _iso(5) for e in delivery_events)


def test_calendar_excludes_expected_delivery_for_completed_and_cancelled(admin_client, client, db_session):
    from .test_fulfillments import _create_approved_requirement

    branch = _make_branch(admin_client, "Delivery Cal Cancel Branch")
    model = _make_device_model(admin_client, "Delivery Cal Cancel Monitor")
    spare = admin_client.post(
        "/api/v1/inventory/items", json={"item_code": "CAL-FILTER", "name": "Cal Filter", "is_consumable": False}
    ).json()
    admin_client.post(
        "/api/v1/inventory/transactions",
        json={"item_id": spare["id"], "transaction_type": "STOCK_IN", "quantity": 10},
    )
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "SPARE_PART", "inventory_item_id": spare["id"], "requested_quantity": 1}],
        tl_email="calcancel.tl@example.com",
    )
    fulfillment = admin_client.post(
        "/api/v1/fulfillments",
        json={"requirement_id": requirement["id"], "expected_delivery_date": _iso(6)},
    ).json()
    cancelled = admin_client.post(f"/api/v1/fulfillments/{fulfillment['id']}/cancel", json={})
    assert cancelled.status_code == 200, cancelled.text

    response = admin_client.get("/api/v1/assets/calendar-events")
    delivery_events = [e for e in response.json() if e["event_type"] == "EXPECTED_DELIVERY"]
    assert not any(e["reference_id"] == fulfillment["id"] for e in delivery_events)


def test_fulfillment_completion_sets_operational_status_counted_in_dashboard(admin_client, client, db_session):
    from .test_fulfillments import _create_approved_requirement

    branch = _make_branch(admin_client, "Operational Branch")
    model = _make_device_model(admin_client, "Operational Monitor")
    asset = _make_asset(admin_client, model["id"], branch["id"], "OP-FIX-001")
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="opfix.tl@example.com",
    )
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()
    requirement_item_id = fulfillment["items"][0]["requirement_item_id"]
    admin_client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": requirement_item_id, "asset_ids": [asset["id"]]}]},
    )
    admin_client.post(f"/api/v1/fulfillments/{fulfillment['id']}/process")
    admin_client.post(f"/api/v1/fulfillments/{fulfillment['id']}/pack")
    admin_client.post(f"/api/v1/fulfillments/{fulfillment['id']}/out-for-delivery")
    admin_client.post(f"/api/v1/fulfillments/{fulfillment['id']}/deliver", json={})
    completed = admin_client.post(f"/api/v1/fulfillments/{fulfillment['id']}/complete")
    assert completed.status_code == 200, completed.text

    before = admin_client.get("/api/v1/assets/dashboard-summary").json()
    updated_asset = admin_client.get(f"/api/v1/assets/{asset['id']}").json()
    assert updated_asset["status"] == "OPERATIONAL"
    assert before["total_operational"] >= 1


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def test_doctor_can_read_lifecycle_and_dashboard(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "RBAC-001")
    headers = _doctor_headers(client, db_session)

    assert client.get(f"/api/v1/assets/{asset['id']}/lifecycle", headers=headers).status_code == 200
    assert client.get("/api/v1/assets/dashboard-summary", headers=headers).status_code == 200
    assert client.get("/api/v1/assets/calendar-events", headers=headers).status_code == 200


def test_doctor_cannot_write_asset(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "RBAC-002")
    headers = _doctor_headers(client, db_session, "rbac2.doc@example.com")

    response = client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"asset_number": asset["asset_number"], "device_model_id": model["id"], "branch_id": branch["id"],
              "status": "OPERATIONAL", "operational_since": _iso(0)},
        headers=headers,
    )
    assert response.status_code == 403


def test_engineer_can_update_operational_since(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = _make_device_model(admin_client)
    asset = _make_asset(admin_client, model["id"], branch["id"], "RBAC-003")
    headers = _engineer_headers(client, db_session, "rbac3.eng@example.com")

    response = client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"asset_number": asset["asset_number"], "device_model_id": model["id"], "branch_id": branch["id"],
              "status": "OPERATIONAL", "operational_since": _iso(0)},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["operational_since"] == _iso(0)
