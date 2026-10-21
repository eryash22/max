from datetime import date, datetime, timedelta, timezone

from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _make_asset(admin_client, asset_number="XRAY-001"):
    branch = admin_client.post("/api/v1/branches", json={"name": f"Branch-{asset_number}", "status": "ACTIVE"}).json()
    model = admin_client.post(
        "/api/v1/device-models", json={"name": f"XRay-{asset_number}", "category": "Imaging"}
    ).json()
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": model["id"], "branch_id": branch["id"]},
    ).json()
    return branch, model, asset


def _engineer_headers(client, db_session, email="caleng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


# ---- Schedule CRUD ----

def test_engineer_can_create_calibration_schedule(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)

    response = client.post(
        "/api/v1/calibration/schedules",
        json={"asset_id": asset["id"], "frequency": "YEARLY"},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["asset_id"] == asset["id"]
    assert body["frequency"] == "YEARLY"
    assert body["is_active"] is True
    assert body["next_due_date"] is not None
    assert body["due_status"] in ("DUE", "UPCOMING")


def test_doctor_cannot_create_calibration_schedule(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")

    response = client.post(
        "/api/v1/calibration/schedules", json={"asset_id": asset["id"], "frequency": "YEARLY"}, headers=headers
    )
    assert response.status_code == 403


def test_doctor_cannot_read_calibration_schedules(admin_client, client, db_session):
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.get("/api/v1/calibration/schedules", headers=headers)
    assert response.status_code == 403


def test_schedule_requires_valid_asset(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/calibration/schedules", json={"asset_id": "does-not-exist", "frequency": "YEARLY"}, headers=headers
    )
    assert response.status_code == 404


def test_custom_frequency_requires_interval_days(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/calibration/schedules", json={"asset_id": asset["id"], "frequency": "CUSTOM"}, headers=headers
    )
    assert response.status_code == 422


def test_invalid_frequency_rejected(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/calibration/schedules", json={"asset_id": asset["id"], "frequency": "WEEKLY"}, headers=headers
    )
    assert response.status_code == 422


def test_update_schedule_toggle_active(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    created = client.post(
        "/api/v1/calibration/schedules", json={"asset_id": asset["id"], "frequency": "YEARLY"}, headers=headers
    ).json()

    updated = client.patch(f"/api/v1/calibration/schedules/{created['id']}", json={"is_active": False}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["is_active"] is False


def test_delete_schedule(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    created = client.post(
        "/api/v1/calibration/schedules", json={"asset_id": asset["id"], "frequency": "YEARLY"}, headers=headers
    ).json()

    deleted = client.delete(f"/api/v1/calibration/schedules/{created['id']}", headers=headers)
    assert deleted.status_code == 204

    fetched = client.get(f"/api/v1/calibration/schedules/{created['id']}", headers=headers)
    assert fetched.status_code == 404


# ---- Due status ----

def test_due_status_overdue(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    past = (date.today() - timedelta(days=5)).isoformat()
    created = client.post(
        "/api/v1/calibration/schedules",
        json={"asset_id": asset["id"], "frequency": "YEARLY", "next_due_date": past},
        headers=headers,
    ).json()
    assert created["due_status"] == "OVERDUE"


def test_due_status_upcoming(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    future = (date.today() + timedelta(days=200)).isoformat()
    created = client.post(
        "/api/v1/calibration/schedules",
        json={"asset_id": asset["id"], "frequency": "YEARLY", "next_due_date": future},
        headers=headers,
    ).json()
    assert created["due_status"] == "UPCOMING"


def test_due_status_not_scheduled_without_frequency(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/calibration/schedules", json={"asset_id": asset["id"]}, headers=headers).json()
    assert created["next_due_date"] is None
    assert created["due_status"] == "NOT_SCHEDULED"


def test_list_schedules_filter_by_due_status(admin_client, client, db_session):
    _, _, asset1 = _make_asset(admin_client, "XRAY-001")
    _, _, asset2 = _make_asset(admin_client, "XRAY-002")
    headers = _engineer_headers(client, db_session)

    past = (date.today() - timedelta(days=5)).isoformat()
    future = (date.today() + timedelta(days=200)).isoformat()
    client.post("/api/v1/calibration/schedules", json={"asset_id": asset1["id"], "frequency": "YEARLY", "next_due_date": past}, headers=headers)
    client.post("/api/v1/calibration/schedules", json={"asset_id": asset2["id"], "frequency": "YEARLY", "next_due_date": future}, headers=headers)

    overdue = client.get("/api/v1/calibration/schedules?due_status=OVERDUE", headers=headers)
    assert overdue.status_code == 200
    assert overdue.json()["total"] == 1
    assert overdue.json()["items"][0]["asset_id"] == asset1["id"]

    upcoming = client.get("/api/v1/calibration/schedules?due_status=UPCOMING", headers=headers)
    assert upcoming.json()["total"] == 1
    assert upcoming.json()["items"][0]["asset_id"] == asset2["id"]


def test_invalid_due_status_filter_rejected(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.get("/api/v1/calibration/schedules?due_status=NOT_A_STATE", headers=headers)
    assert response.status_code == 400


def test_due_status_filter_uses_utc_today_not_local(admin_client, client, db_session, monkeypatch):
    # Regression guard for B1: the schedules-list due_status filter (calibration.py)
    # and the per-item due_status (calibration_service.compute_due_status) must both
    # key off datetime.now(timezone.utc).date(), the same convention
    # calibration_service._today() already uses everywhere else, and never off
    # local-timezone date.today().
    #
    # Simulate a moment where local time (e.g. IST, UTC+5:30 — the timezone this
    # suite has hit this exact bug in before, per PROJECT_STATUS.md) has already
    # rolled over to the next calendar day while UTC has not: 22:30 UTC becomes
    # 04:00 the next day in IST. If any code path fell back to local date.today(),
    # a schedule due exactly "today" (UTC) would be misclassified as OVERDUE
    # instead of DUE, and the OVERDUE/DUE filters would disagree with each other.
    import app.services.calibration_service as calibration_service_module

    fixed_instant = datetime(2027, 6, 15, 22, 30, tzinfo=timezone.utc)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_instant

    monkeypatch.setattr(calibration_service_module, "datetime", _FrozenDateTime)

    utc_today = fixed_instant.date()
    _, _, overdue_asset = _make_asset(admin_client, "UTC-OVERDUE")
    _, _, due_asset = _make_asset(admin_client, "UTC-DUE")
    headers = _engineer_headers(client, db_session)

    overdue_date = (utc_today - timedelta(days=1)).isoformat()
    due_today_date = utc_today.isoformat()

    overdue_created = client.post(
        "/api/v1/calibration/schedules",
        json={"asset_id": overdue_asset["id"], "frequency": "YEARLY", "next_due_date": overdue_date},
        headers=headers,
    ).json()
    due_created = client.post(
        "/api/v1/calibration/schedules",
        json={"asset_id": due_asset["id"], "frequency": "YEARLY", "next_due_date": due_today_date},
        headers=headers,
    ).json()

    # Per-item due_status (service layer) is UTC-anchored.
    assert overdue_created["due_status"] == "OVERDUE"
    assert due_created["due_status"] == "DUE"

    # List filters (router layer) agree with the per-item classification above —
    # a schedule due exactly "today" in UTC must never appear under OVERDUE.
    overdue_listing = client.get("/api/v1/calibration/schedules?due_status=OVERDUE", headers=headers)
    assert overdue_listing.json()["total"] == 1
    assert overdue_listing.json()["items"][0]["asset_id"] == overdue_asset["id"]

    due_listing = client.get("/api/v1/calibration/schedules?due_status=DUE", headers=headers)
    assert due_listing.json()["total"] == 1
    assert due_listing.json()["items"][0]["asset_id"] == due_asset["id"]


# ---- Calibration records ----

def test_create_calibration_record_with_certificate_and_result(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)

    schedule = client.post(
        "/api/v1/calibration/schedules", json={"asset_id": asset["id"], "frequency": "YEARLY"}, headers=headers
    ).json()

    response = client.post(
        "/api/v1/calibration/records",
        json={
            "schedule_id": schedule["id"],
            "asset_id": asset["id"],
            "certificate_number": "CAL-CERT-0001",
            "calibration_agency": "Fluke Biomedical",
            "reference_standard": "IEC 60601-1",
            "result": "Within tolerance",
            "pass_fail": "PASS",
            "notes": "Annual calibration completed",
        },
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["certificate_number"] == "CAL-CERT-0001"
    assert body["pass_fail"] == "PASS"
    assert body["performed_date"] is not None
    assert body["next_due_date"] is not None

    # completing a calibration record tied to a schedule should sync the schedule's due date
    refreshed_schedule = client.get(f"/api/v1/calibration/schedules/{schedule['id']}", headers=headers).json()
    assert refreshed_schedule["next_due_date"] == body["next_due_date"]


def test_calibration_record_requires_valid_asset(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post("/api/v1/calibration/records", json={"asset_id": "does-not-exist"}, headers=headers)
    assert response.status_code == 404


def test_calibration_record_requires_valid_schedule(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/calibration/records", json={"asset_id": asset["id"], "schedule_id": "does-not-exist"}, headers=headers
    )
    assert response.status_code == 404


def test_calibration_record_invalid_pass_fail_rejected(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/calibration/records", json={"asset_id": asset["id"], "pass_fail": "MAYBE"}, headers=headers
    )
    assert response.status_code == 422


def test_calibration_record_invalid_performed_date_rejected(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/calibration/records", json={"asset_id": asset["id"], "performed_date": "not-a-date"}, headers=headers
    )
    assert response.status_code == 422


def test_doctor_cannot_create_calibration_record(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.post("/api/v1/calibration/records", json={"asset_id": asset["id"]}, headers=headers)
    assert response.status_code == 403


def test_calibration_history_filter_by_asset(admin_client, client, db_session):
    _, _, asset1 = _make_asset(admin_client, "XRAY-001")
    _, _, asset2 = _make_asset(admin_client, "XRAY-002")
    headers = _engineer_headers(client, db_session)

    client.post("/api/v1/calibration/records", json={"asset_id": asset1["id"], "notes": "Cal 1"}, headers=headers)
    client.post("/api/v1/calibration/records", json={"asset_id": asset2["id"], "notes": "Cal 2"}, headers=headers)

    history = client.get(f"/api/v1/calibration/records?asset_id={asset1['id']}", headers=headers)
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["notes"] == "Cal 1"


def test_calibration_records_filter_by_pass_fail(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)

    client.post("/api/v1/calibration/records", json={"asset_id": asset["id"], "pass_fail": "PASS"}, headers=headers)
    client.post("/api/v1/calibration/records", json={"asset_id": asset["id"], "pass_fail": "FAIL"}, headers=headers)

    failed = client.get(f"/api/v1/calibration/records?asset_id={asset['id']}&pass_fail=FAIL", headers=headers)
    assert failed.status_code == 200
    assert failed.json()["total"] == 1
    assert failed.json()["items"][0]["pass_fail"] == "FAIL"


def test_calibration_records_pagination(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    for i in range(3):
        client.post("/api/v1/calibration/records", json={"asset_id": asset["id"], "notes": f"Cal {i}"}, headers=headers)

    page1 = client.get(f"/api/v1/calibration/records?asset_id={asset['id']}&page=1&page_size=2", headers=headers)
    assert page1.status_code == 200
    assert page1.json()["total"] == 3
    assert len(page1.json()["items"]) == 2

    page2 = client.get(f"/api/v1/calibration/records?asset_id={asset['id']}&page=2&page_size=2", headers=headers)
    assert len(page2.json()["items"]) == 1


def test_get_calibration_record_by_id(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/calibration/records", json={"asset_id": asset["id"], "notes": "Cal"}, headers=headers).json()

    fetched = client.get(f"/api/v1/calibration/records/{created['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]


# ---- Audit logging ----

def test_calibration_schedule_creation_is_audited(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    created = client.post(
        "/api/v1/calibration/schedules", json={"asset_id": asset["id"], "frequency": "YEARLY"}, headers=headers
    ).json()

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "calibration_schedule", AuditLog.entity_id == created["id"]
    ).first()
    assert log is not None
    assert log.action == "CREATE"


def test_calibration_record_creation_is_audited(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/calibration/records", json={"asset_id": asset["id"]}, headers=headers).json()

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "calibration_record", AuditLog.entity_id == created["id"]
    ).first()
    assert log is not None
    assert log.action == "CREATE"
