from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _make_asset(admin_client, asset_number="AST-BI-001", category="Ventilator", branch_name=None):
    branch = admin_client.post(
        "/api/v1/branches", json={"name": branch_name or f"Branch-{asset_number}", "status": "ACTIVE"}
    ).json()
    model = admin_client.post("/api/v1/device-models", json={"name": f"Model-{asset_number}", "category": category}).json()
    asset = admin_client.post(
        "/api/v1/assets", json={"asset_number": asset_number, "device_model_id": model["id"], "branch_id": branch["id"]}
    ).json()
    return branch, model, asset


def _make_incident(admin_client, branch_id, asset_id=None, downtime_hours=None):
    payload = {"branch_id": branch_id, "description": "Device malfunction", "severity": "HIGH"}
    if asset_id:
        payload["asset_id"] = asset_id
    incident = admin_client.post("/api/v1/incidents", json=payload).json()
    if downtime_hours is not None:
        admin_client.patch(f"/api/v1/incidents/{incident['id']}", json={"downtime_hours": downtime_hours})
        incident = admin_client.get(f"/api/v1/incidents/{incident['id']}").json()
    return incident


def _engineer_headers(client, db_session, email="eng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _hospital_unit_manager_headers(client, db_session, email="mgr@example.com"):
    create_user(db_session, email, "HOSPITAL_UNIT_MANAGER", "Password123!")
    return auth_headers(client, email, "Password123!")


# ---- Assumptions CRUD ----

def test_create_assumption(admin_client):
    response = admin_client.post(
        "/api/v1/business-impact/assumptions",
        json={"name": "Ventilator downtime rate", "device_category": "Ventilator", "revenue_per_hour": 500},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["revenue_per_hour"] == 500
    assert body["currency"] == "INR"
    assert body["is_active"] is True


def test_blank_name_rejected(admin_client):
    response = admin_client.post("/api/v1/business-impact/assumptions", json={"name": "   "})
    assert response.status_code == 422


def test_negative_revenue_per_hour_rejected(admin_client):
    response = admin_client.post(
        "/api/v1/business-impact/assumptions", json={"name": "Bad rate", "revenue_per_hour": -10}
    )
    assert response.status_code == 422


def test_assumption_validates_branch_fk(admin_client):
    response = admin_client.post(
        "/api/v1/business-impact/assumptions", json={"name": "Bad branch", "branch_id": "does-not-exist"}
    )
    assert response.status_code == 404


def test_get_assumption_404(admin_client):
    response = admin_client.get("/api/v1/business-impact/assumptions/does-not-exist")
    assert response.status_code == 404


def test_update_assumption(admin_client):
    created = admin_client.post("/api/v1/business-impact/assumptions", json={"name": "Rate A", "revenue_per_hour": 100}).json()
    response = admin_client.patch(
        f"/api/v1/business-impact/assumptions/{created['id']}", json={"name": "Rate A", "revenue_per_hour": 200}
    )
    assert response.status_code == 200
    assert response.json()["revenue_per_hour"] == 200


def test_engineer_cannot_write_assumption(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post("/api/v1/business-impact/assumptions", json={"name": "Eng rate"}, headers=headers)
    assert response.status_code == 403


def test_engineer_cannot_read_assumptions(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.get("/api/v1/business-impact/assumptions", headers=headers)
    assert response.status_code == 403


def test_hospital_unit_manager_can_read_assumptions(admin_client, client, db_session):
    admin_client.post("/api/v1/business-impact/assumptions", json={"name": "Rate A"})
    headers = _hospital_unit_manager_headers(client, db_session)
    response = client.get("/api/v1/business-impact/assumptions", headers=headers)
    assert response.status_code == 200


def test_hospital_unit_manager_cannot_write_assumptions(admin_client, client, db_session):
    headers = _hospital_unit_manager_headers(client, db_session)
    response = client.post("/api/v1/business-impact/assumptions", json={"name": "Mgr rate"}, headers=headers)
    assert response.status_code == 403


def test_assumption_create_records_audit_log(admin_client, db_session):
    admin_client.post("/api/v1/business-impact/assumptions", json={"name": "Rate A"})
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "business_impact_assumption").all()
    assert any(log.action == "CREATE" for log in logs)


def test_list_assumptions_filters_by_device_category(admin_client):
    admin_client.post("/api/v1/business-impact/assumptions", json={"name": "Vent rate", "device_category": "Ventilator"})
    admin_client.post("/api/v1/business-impact/assumptions", json={"name": "Pump rate", "device_category": "Infusion Pump"})
    response = admin_client.get("/api/v1/business-impact/assumptions", params={"device_category": "Ventilator"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Vent rate"


# ---- Incident impact estimate ----

def test_incident_estimate_no_downtime_recorded(admin_client):
    branch, _, asset = _make_asset(admin_client)
    incident = _make_incident(admin_client, branch["id"], asset["id"])
    response = admin_client.get(f"/api/v1/business-impact/incidents/{incident['id']}/estimate")
    assert response.status_code == 200
    body = response.json()
    assert body["estimated_impact"] is None
    assert body["downtime_hours"] is None


def test_incident_estimate_no_matching_assumption(admin_client):
    branch, _, asset = _make_asset(admin_client, category="MRI")
    incident = _make_incident(admin_client, branch["id"], asset["id"], downtime_hours=4)
    response = admin_client.get(f"/api/v1/business-impact/incidents/{incident['id']}/estimate")
    body = response.json()
    assert body["estimated_impact"] is None
    assert body["assumption_id"] is None


def test_incident_estimate_computes_impact_from_device_category_assumption(admin_client):
    branch, _, asset = _make_asset(admin_client, category="Ventilator")
    admin_client.post("/api/v1/business-impact/assumptions", json={"name": "Vent rate", "device_category": "Ventilator", "revenue_per_hour": 500})
    incident = _make_incident(admin_client, branch["id"], asset["id"], downtime_hours=3)

    response = admin_client.get(f"/api/v1/business-impact/incidents/{incident['id']}/estimate")
    body = response.json()
    assert body["estimated_impact"] == 1500
    assert body["assumption_id"] is not None


def test_incident_estimate_prefers_branch_specific_assumption(admin_client):
    branch, _, asset = _make_asset(admin_client, category="Ventilator")
    admin_client.post("/api/v1/business-impact/assumptions", json={"name": "Generic vent rate", "device_category": "Ventilator", "revenue_per_hour": 500})
    admin_client.post("/api/v1/business-impact/assumptions", json={"name": "Branch vent rate", "branch_id": branch["id"], "device_category": "Ventilator", "revenue_per_hour": 900})
    incident = _make_incident(admin_client, branch["id"], asset["id"], downtime_hours=2)

    response = admin_client.get(f"/api/v1/business-impact/incidents/{incident['id']}/estimate")
    body = response.json()
    assert body["estimated_impact"] == 1800


def test_incident_estimate_requires_existing_incident(admin_client):
    response = admin_client.get("/api/v1/business-impact/incidents/does-not-exist/estimate")
    assert response.status_code == 404


def test_engineer_cannot_read_incident_estimate(admin_client, client, db_session):
    branch, _, asset = _make_asset(admin_client)
    incident = _make_incident(admin_client, branch["id"], asset["id"], downtime_hours=1)
    headers = _engineer_headers(client, db_session)
    response = client.get(f"/api/v1/business-impact/incidents/{incident['id']}/estimate", headers=headers)
    assert response.status_code == 403


# ---- Failure analytics ----

def test_failure_summary_insufficient_data_for_new_asset(admin_client):
    _, _, asset = _make_asset(admin_client)
    response = admin_client.get(f"/api/v1/analytics/failure-summary/{asset['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["incident_count"] == 0
    assert body["mtbf_hours"] is None
    assert body["mttr_hours"] is None
    assert body["repeat_failure"] is False


def test_failure_summary_computes_mttr(admin_client):
    branch, _, asset = _make_asset(admin_client)
    _make_incident(admin_client, branch["id"], asset["id"], downtime_hours=2)
    _make_incident(admin_client, branch["id"], asset["id"], downtime_hours=4)

    response = admin_client.get(f"/api/v1/analytics/failure-summary/{asset['id']}")
    body = response.json()
    assert body["incident_count"] == 2
    assert body["mttr_hours"] == 3.0
    assert body["total_downtime_hours"] == 6.0


def test_failure_summary_repeat_failure_flag(admin_client):
    branch, _, asset = _make_asset(admin_client)
    for _ in range(3):
        _make_incident(admin_client, branch["id"], asset["id"])
    response = admin_client.get(f"/api/v1/analytics/failure-summary/{asset['id']}")
    assert response.json()["repeat_failure"] is True


def test_failure_summary_requires_existing_asset(admin_client):
    response = admin_client.get("/api/v1/analytics/failure-summary/does-not-exist")
    assert response.status_code == 404


def test_failure_summary_list_sorted_by_incident_count(admin_client):
    branch_a, _, asset_a = _make_asset(admin_client, "AST-BI-A")
    branch_b, _, asset_b = _make_asset(admin_client, "AST-BI-B")
    _make_incident(admin_client, branch_a["id"], asset_a["id"])
    for _ in range(2):
        _make_incident(admin_client, branch_b["id"], asset_b["id"])

    response = admin_client.get("/api/v1/analytics/failure-summary")
    body = response.json()
    assert body["total"] == 2
    assert body["items"][0]["asset_id"] == asset_b["id"]


def test_failure_summary_list_repeat_failure_filter(admin_client):
    branch_a, _, asset_a = _make_asset(admin_client, "AST-BI-C")
    branch_b, _, asset_b = _make_asset(admin_client, "AST-BI-D")
    _make_incident(admin_client, branch_a["id"], asset_a["id"])
    for _ in range(3):
        _make_incident(admin_client, branch_b["id"], asset_b["id"])

    response = admin_client.get("/api/v1/analytics/failure-summary", params={"repeat_failure_only": True})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["asset_id"] == asset_b["id"]


def test_engineer_cannot_read_failure_summary(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.get(f"/api/v1/analytics/failure-summary/{asset['id']}", headers=headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Security hardening pass — branch isolation for UNIT_HEAD/TL
# ---------------------------------------------------------------------------

def _scoped_unit_head_headers(client, db_session, admin_client, *, branch_id, email="uh.bi@example.com"):
    user = create_user(db_session, email, "UNIT_HEAD", "Password123!")
    user.hospital_scope_id = branch_id
    db_session.add(user)
    db_session.commit()
    return auth_headers(client, email, "Password123!")


def test_unit_head_cannot_read_another_branchs_assumption(admin_client, client, db_session):
    branch_a, _, _ = _make_asset(admin_client, "AST-BI-SCOPE-A", branch_name="BI Branch A")
    branch_b, _, _ = _make_asset(admin_client, "AST-BI-SCOPE-B", branch_name="BI Branch B")
    assumption = admin_client.post(
        "/api/v1/business-impact/assumptions",
        json={"name": "Branch A policy", "branch_id": branch_a["id"], "revenue_per_hour": 5000},
    ).json()

    other_branch_headers = _scoped_unit_head_headers(client, db_session, admin_client, branch_id=branch_b["id"])
    response = client.get(f"/api/v1/business-impact/assumptions/{assumption['id']}", headers=other_branch_headers)
    assert response.status_code == 404

    own_branch_headers = _scoped_unit_head_headers(
        client, db_session, admin_client, branch_id=branch_a["id"], email="uh.bi.own@example.com"
    )
    ok = client.get(f"/api/v1/business-impact/assumptions/{assumption['id']}", headers=own_branch_headers)
    assert ok.status_code == 200


def test_unit_head_assumption_list_excludes_other_branches(admin_client, client, db_session):
    branch_a, _, _ = _make_asset(admin_client, "AST-BI-LIST-A", branch_name="BI List Branch A")
    branch_b, _, _ = _make_asset(admin_client, "AST-BI-LIST-B", branch_name="BI List Branch B")
    admin_client.post(
        "/api/v1/business-impact/assumptions",
        json={"name": "Branch A only", "branch_id": branch_a["id"], "revenue_per_hour": 1000},
    )
    admin_client.post(
        "/api/v1/business-impact/assumptions",
        json={"name": "Branch B only", "branch_id": branch_b["id"], "revenue_per_hour": 2000},
    )

    headers = _scoped_unit_head_headers(client, db_session, admin_client, branch_id=branch_b["id"])
    response = client.get("/api/v1/business-impact/assumptions", headers=headers)
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["items"]}
    assert "Branch B only" in names
    assert "Branch A only" not in names


def test_unit_head_cannot_read_another_branchs_incident_impact_estimate(admin_client, client, db_session):
    branch_a, _, asset_a = _make_asset(admin_client, "AST-BI-EST-A", category="Ventilator", branch_name="BI Est Branch A")
    branch_b, _, _ = _make_asset(admin_client, "AST-BI-EST-B", branch_name="BI Est Branch B")
    incident = _make_incident(admin_client, branch_a["id"], asset_a["id"])

    other_branch_headers = _scoped_unit_head_headers(client, db_session, admin_client, branch_id=branch_b["id"])
    response = client.get(f"/api/v1/business-impact/incidents/{incident['id']}/estimate", headers=other_branch_headers)
    assert response.status_code == 404

    own_branch_headers = _scoped_unit_head_headers(
        client, db_session, admin_client, branch_id=branch_a["id"], email="uh.bi.est.own@example.com"
    )
    ok = client.get(f"/api/v1/business-impact/incidents/{incident['id']}/estimate", headers=own_branch_headers)
    assert ok.status_code == 200


def test_unit_head_cannot_read_another_branchs_failure_summary(admin_client, client, db_session):
    branch_a, _, asset_a = _make_asset(admin_client, "AST-BI-FS-A", branch_name="BI FS Branch A")
    branch_b, _, _ = _make_asset(admin_client, "AST-BI-FS-B", branch_name="BI FS Branch B")

    other_branch_headers = _scoped_unit_head_headers(client, db_session, admin_client, branch_id=branch_b["id"])
    response = client.get(f"/api/v1/analytics/failure-summary/{asset_a['id']}", headers=other_branch_headers)
    assert response.status_code == 404


def test_unit_head_failure_summary_list_excludes_other_branches(admin_client, client, db_session):
    branch_a, _, asset_a = _make_asset(admin_client, "AST-BI-FSL-A", branch_name="BI FSL Branch A")
    branch_b, _, asset_b = _make_asset(admin_client, "AST-BI-FSL-B", branch_name="BI FSL Branch B")
    _make_incident(admin_client, branch_a["id"], asset_a["id"])
    _make_incident(admin_client, branch_b["id"], asset_b["id"])

    headers = _scoped_unit_head_headers(client, db_session, admin_client, branch_id=branch_b["id"])
    response = client.get("/api/v1/analytics/failure-summary", headers=headers)
    assert response.status_code == 200
    asset_ids = {item["asset_id"] for item in response.json()["items"]}
    assert asset_b["id"] in asset_ids
    assert asset_a["id"] not in asset_ids
