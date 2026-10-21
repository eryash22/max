from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _make_asset(admin_client, asset_number="VENT-001"):
    branch = admin_client.post("/api/v1/branches", json={"name": f"Branch-{asset_number}", "status": "ACTIVE"}).json()
    model = admin_client.post(
        "/api/v1/device-models", json={"name": f"Ventilator-{asset_number}", "category": "Ventilator"}
    ).json()
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": model["id"], "branch_id": branch["id"]},
    ).json()
    return branch, model, asset


def _make_vendor(admin_client, name="Acme Service"):
    return admin_client.post("/api/v1/vendors", json={"name": name, "vendor_type": "SERVICE"}).json()


def _make_incident(admin_client, branch_id, asset_id=None):
    payload = {"branch_id": branch_id, "description": "Device not powering on", "severity": "HIGH"}
    if asset_id is not None:
        payload["asset_id"] = asset_id
    return admin_client.post("/api/v1/incidents", json=payload).json()


def _engineer_headers(client, db_session, email="eng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _doctor_headers(client, db_session, email="doc@example.com"):
    create_user(db_session, email, "DOCTOR", "Password123!")
    return auth_headers(client, email, "Password123!")


# ---- Create / validation ----

def test_engineer_can_create_service_visit(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)

    response = client.post(
        "/api/v1/service-visits",
        json={"asset_id": asset["id"], "visit_type": "BREAKDOWN", "summary": "Replaced fuse", "cost": 150.0},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["asset_id"] == asset["id"]
    assert body["visit_type"] == "BREAKDOWN"
    assert body["cost"] == 150.0


def test_doctor_cannot_create_service_visit(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _doctor_headers(client, db_session)

    response = client.post("/api/v1/service-visits", json={"asset_id": asset["id"]}, headers=headers)
    assert response.status_code == 403


def test_doctor_can_read_service_visits(admin_client, client, db_session):
    headers = _doctor_headers(client, db_session)
    response = client.get("/api/v1/service-visits", headers=headers)
    assert response.status_code == 200


def test_visit_requires_valid_asset(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post("/api/v1/service-visits", json={"asset_id": "does-not-exist"}, headers=headers)
    assert response.status_code == 404


def test_visit_validates_incident_fk(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/service-visits", json={"asset_id": asset["id"], "incident_id": "nope"}, headers=headers
    )
    assert response.status_code == 404


def test_visit_validates_vendor_fk(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/service-visits", json={"asset_id": asset["id"], "vendor_id": "nope"}, headers=headers
    )
    assert response.status_code == 404


def test_visit_validates_engineer_fk(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/service-visits", json={"asset_id": asset["id"], "engineer_id": "nope"}, headers=headers
    )
    assert response.status_code == 404


def test_invalid_visit_type_rejected(admin_client, client, db_session):
    # Batch E, Part 20 — visit_type is now validated against active ServiceVisitType
    # rows at the API layer (400), not a static pydantic choice list (422).
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/service-visits", json={"asset_id": asset["id"], "visit_type": "MAGIC"}, headers=headers
    )
    assert response.status_code == 400


def test_negative_cost_rejected(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/service-visits", json={"asset_id": asset["id"], "cost": -10}, headers=headers
    )
    assert response.status_code == 422


def test_invalid_visit_date_rejected(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/service-visits", json={"asset_id": asset["id"], "visit_date": "not-a-date"}, headers=headers
    )
    assert response.status_code == 422


def test_visit_can_link_incident_and_vendor(admin_client, client, db_session):
    branch, _, asset = _make_asset(admin_client)
    vendor = _make_vendor(admin_client)
    incident = _make_incident(admin_client, branch["id"], asset["id"])
    headers = _engineer_headers(client, db_session)

    response = client.post(
        "/api/v1/service-visits",
        json={
            "asset_id": asset["id"], "incident_id": incident["id"], "vendor_id": vendor["id"],
            "visit_type": "BREAKDOWN", "summary": "Vendor called on-site",
        },
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["incident_id"] == incident["id"]
    assert body["vendor_id"] == vendor["id"]


# ---- Read / list / filters ----

def test_get_visit_by_id(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/service-visits", json={"asset_id": asset["id"]}, headers=headers).json()

    response = client.get(f"/api/v1/service-visits/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_missing_visit_404(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.get("/api/v1/service-visits/does-not-exist", headers=headers)
    assert response.status_code == 404


def test_list_filters_by_asset(admin_client, client, db_session):
    _, _, asset1 = _make_asset(admin_client, "VENT-101")
    _, _, asset2 = _make_asset(admin_client, "VENT-102")
    headers = _engineer_headers(client, db_session)
    client.post("/api/v1/service-visits", json={"asset_id": asset1["id"]}, headers=headers)
    client.post("/api/v1/service-visits", json={"asset_id": asset2["id"]}, headers=headers)

    response = client.get(f"/api/v1/service-visits?asset_id={asset1['id']}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["asset_id"] == asset1["id"]


def test_list_filters_by_visit_type(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    client.post("/api/v1/service-visits", json={"asset_id": asset["id"], "visit_type": "PM"}, headers=headers)
    client.post("/api/v1/service-visits", json={"asset_id": asset["id"], "visit_type": "BREAKDOWN"}, headers=headers)

    response = client.get("/api/v1/service-visits?visit_type=BREAKDOWN", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["visit_type"] == "BREAKDOWN"


def test_list_unknown_visit_type_filter_returns_empty(client, db_session):
    # Batch E, Part 20 — the list filter is no longer restricted to a static/known
    # set (it must still work for a deactivated or legacy free-text value), so an
    # unrecognized visit_type simply yields zero matches rather than a 400.
    headers = _engineer_headers(client, db_session)
    response = client.get("/api/v1/service-visits?visit_type=MAGIC", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_list_search_by_summary(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    client.post("/api/v1/service-visits", json={"asset_id": asset["id"], "summary": "Replaced fuse"}, headers=headers)
    client.post("/api/v1/service-visits", json={"asset_id": asset["id"], "summary": "Calibrated sensor"}, headers=headers)

    response = client.get("/api/v1/service-visits?search=fuse", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert "fuse" in body["items"][0]["summary"].lower()


def test_list_filters_by_visit_date_range(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    client.post("/api/v1/service-visits", json={"asset_id": asset["id"], "visit_date": "2026-01-05"}, headers=headers)
    client.post("/api/v1/service-visits", json={"asset_id": asset["id"], "visit_date": "2026-06-05"}, headers=headers)

    response = client.get(
        "/api/v1/service-visits?visit_date_after=2026-05-01&visit_date_before=2026-07-01", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["visit_date"] == "2026-06-05"


def test_list_pagination(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    for _ in range(3):
        client.post("/api/v1/service-visits", json={"asset_id": asset["id"]}, headers=headers)

    response = client.get("/api/v1/service-visits?page=1&page_size=2", headers=headers)
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


# ---- Update / delete ----

def test_update_visit(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/service-visits", json={"asset_id": asset["id"], "summary": "Initial"}, headers=headers).json()

    response = client.patch(
        f"/api/v1/service-visits/{created['id']}", json={"summary": "Updated", "cost": 75.5}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "Updated"
    assert body["cost"] == 75.5


def test_update_missing_visit_404(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.patch("/api/v1/service-visits/does-not-exist", json={"summary": "x"}, headers=headers)
    assert response.status_code == 404


def test_doctor_cannot_update_visit(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    eng_headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/service-visits", json={"asset_id": asset["id"]}, headers=eng_headers).json()

    doc_headers = _doctor_headers(client, db_session)
    response = client.patch(f"/api/v1/service-visits/{created['id']}", json={"summary": "x"}, headers=doc_headers)
    assert response.status_code == 403


def test_delete_visit(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/service-visits", json={"asset_id": asset["id"]}, headers=headers).json()

    response = client.delete(f"/api/v1/service-visits/{created['id']}", headers=headers)
    assert response.status_code == 204
    assert client.get(f"/api/v1/service-visits/{created['id']}", headers=headers).status_code == 404


def test_delete_missing_visit_404(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.delete("/api/v1/service-visits/does-not-exist", headers=headers)
    assert response.status_code == 404


# ---- Audit logging ----

def test_create_writes_audit_log(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/service-visits", json={"asset_id": asset["id"]}, headers=headers).json()

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "service_visit", AuditLog.entity_id == created["id"], AuditLog.action == "CREATE"
    ).first()
    assert log is not None


def test_update_writes_audit_log(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/service-visits", json={"asset_id": asset["id"]}, headers=headers).json()
    client.patch(f"/api/v1/service-visits/{created['id']}", json={"summary": "Updated"}, headers=headers)

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "service_visit", AuditLog.entity_id == created["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None


def test_delete_writes_audit_log(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/service-visits", json={"asset_id": asset["id"]}, headers=headers).json()
    client.delete(f"/api/v1/service-visits/{created['id']}", headers=headers)

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "service_visit", AuditLog.entity_id == created["id"], AuditLog.action == "DELETE"
    ).first()
    assert log is not None


# ---------------------------------------------------------------------------
# Security hardening pass — branch isolation for UNIT_HEAD/TL
# ---------------------------------------------------------------------------

def _scoped_unit_head_headers(client, db_session, *, branch_id, email="uh.sv@example.com"):
    user = create_user(db_session, email, "UNIT_HEAD", "Password123!")
    user.hospital_scope_id = branch_id
    db_session.add(user)
    db_session.commit()
    return auth_headers(client, email, "Password123!")


def test_unit_head_cannot_read_another_branchs_service_visit(admin_client, client, db_session):
    branch_a, _, asset_a = _make_asset(admin_client, "SV-SCOPE-A")
    branch_b, _, _ = _make_asset(admin_client, "SV-SCOPE-B")
    visit = admin_client.post("/api/v1/service-visits", json={"asset_id": asset_a["id"]}).json()

    other_branch_headers = _scoped_unit_head_headers(client, db_session, branch_id=branch_b["id"])
    response = client.get(f"/api/v1/service-visits/{visit['id']}", headers=other_branch_headers)
    assert response.status_code == 404

    own_branch_headers = _scoped_unit_head_headers(
        client, db_session, branch_id=branch_a["id"], email="uh.sv.own@example.com"
    )
    ok = client.get(f"/api/v1/service-visits/{visit['id']}", headers=own_branch_headers)
    assert ok.status_code == 200


def test_unit_head_service_visit_list_excludes_other_branches(admin_client, client, db_session):
    branch_a, _, asset_a = _make_asset(admin_client, "SV-LIST-A")
    branch_b, _, asset_b = _make_asset(admin_client, "SV-LIST-B")
    admin_client.post("/api/v1/service-visits", json={"asset_id": asset_a["id"]})
    visit_b = admin_client.post("/api/v1/service-visits", json={"asset_id": asset_b["id"]}).json()

    headers = _scoped_unit_head_headers(client, db_session, branch_id=branch_b["id"])
    response = client.get("/api/v1/service-visits", headers=headers)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert visit_b["id"] in ids
    assert all(item["asset_id"] == asset_b["id"] for item in response.json()["items"])

    # An explicit cross-branch branch_id filter can't widen a scoped caller — the
    # server forces it back to their own branch rather than honoring branch_a here.
    escape_attempt = client.get(
        f"/api/v1/service-visits?branch_id={branch_a['id']}", headers=headers
    )
    assert escape_attempt.status_code == 200
    escape_ids = {item["id"] for item in escape_attempt.json()["items"]}
    assert escape_ids == {visit_b["id"]}
