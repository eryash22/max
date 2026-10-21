from app.models.documents import Document

from .conftest import auth_headers, create_user


def _make_branch(admin_client):
    return admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()


def test_doctor_can_create_incident(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.post(
        "/api/v1/incidents",
        json={"branch_id": branch["id"], "description": "Ventilator is not working.", "severity": "CRITICAL"},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "NEW"
    assert body["severity"] == "CRITICAL"


def test_viewer_cannot_create_incident(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "viewer@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "viewer@example.com", "Password123!")
    response = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "test"}, headers=headers
    )
    assert response.status_code == 403


def test_doctor_only_sees_own_incidents(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc1@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "doc2@example.com", "DOCTOR", "Password123!")
    headers1 = auth_headers(client, "doc1@example.com", "Password123!")
    headers2 = auth_headers(client, "doc2@example.com", "Password123!")

    client.post("/api/v1/incidents", json={"branch_id": branch["id"], "description": "Doc1 report"}, headers=headers1)
    client.post("/api/v1/incidents", json={"branch_id": branch["id"], "description": "Doc2 report"}, headers=headers2)

    listing = client.get("/api/v1/incidents", headers=headers1)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["description"] == "Doc1 report"


def test_doctor_cannot_view_other_doctors_incident_by_id(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc1@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "doc2@example.com", "DOCTOR", "Password123!")
    headers1 = auth_headers(client, "doc1@example.com", "Password123!")
    headers2 = auth_headers(client, "doc2@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Doc1 report"}, headers=headers1
    ).json()

    response = client.get(f"/api/v1/incidents/{created['id']}", headers=headers2)
    assert response.status_code == 404


def test_engineer_sees_all_incidents_and_can_manage(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "eng@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken pump"}, headers=doc_headers
    ).json()

    listing = client.get("/api/v1/incidents", headers=eng_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    patched = client.patch(
        f"/api/v1/incidents/{created['id']}", json={"root_cause": "Worn seal"}, headers=eng_headers
    )
    assert patched.status_code == 200
    assert patched.json()["root_cause"] == "Worn seal"


def test_status_transition_creates_history_and_closes_incident(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "eng@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken pump"}, headers=doc_headers
    ).json()

    ack = client.post(
        f"/api/v1/incidents/{created['id']}/status", json={"to_status": "ACKNOWLEDGED"}, headers=eng_headers
    )
    assert ack.status_code == 200
    assert ack.json()["status"] == "ACKNOWLEDGED"

    closed = client.post(
        f"/api/v1/incidents/{created['id']}/status",
        json={"to_status": "CLOSED", "notes": "Fixed and verified"},
        headers=eng_headers,
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED"
    assert closed.json()["closed_by"] is not None
    assert closed.json()["closed_at"] is not None

    history = client.get(f"/api/v1/incidents/{created['id']}/history", headers=eng_headers)
    assert history.status_code == 200
    to_statuses = [h["to_status"] for h in history.json()]
    assert to_statuses == ["NEW", "ACKNOWLEDGED", "CLOSED"]


def test_invalid_status_rejected(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "eng@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken pump"}, headers=doc_headers
    ).json()

    response = client.post(
        f"/api/v1/incidents/{created['id']}/status", json={"to_status": "NOT_A_REAL_STATUS"}, headers=eng_headers
    )
    assert response.status_code == 400


def test_doctor_cannot_manage_incident_status(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken pump"}, headers=doc_headers
    ).json()

    response = client.post(
        f"/api/v1/incidents/{created['id']}/status", json={"to_status": "ACKNOWLEDGED"}, headers=doc_headers
    )
    assert response.status_code == 403


def test_create_requires_branch(admin_client, client, db_session):
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.post(
        "/api/v1/incidents", json={"branch_id": "does-not-exist", "description": "Broken pump"}, headers=doc_headers
    )
    assert response.status_code == 404


def test_create_rejects_missing_description(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.post("/api/v1/incidents", json={"branch_id": branch["id"]}, headers=doc_headers)
    assert response.status_code == 422


def test_asset_linkage_and_severity_recorded(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = admin_client.post("/api/v1/device-models", json={"name": "Ventilator X", "category": "Ventilator"}).json()
    asset = admin_client.post(
        "/api/v1/assets", json={"asset_number": "VENT-001", "device_model_id": model["id"], "branch_id": branch["id"]}
    ).json()
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents",
        json={"branch_id": branch["id"], "asset_id": asset["id"], "description": "Ventilator alarm stuck",
              "severity": "CRITICAL", "problem_category": "ALARM"},
        headers=doc_headers,
    ).json()
    assert created["asset_id"] == asset["id"]
    assert created["severity"] == "CRITICAL"

    listing = admin_client.get(f"/api/v1/incidents?asset_id={asset['id']}")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


def test_search_matches_description(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Infusion pump beeping"},
        headers=doc_headers,
    )
    client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Monitor screen blank"},
        headers=doc_headers,
    )

    results = admin_client.get("/api/v1/incidents?search=pump")
    assert results.status_code == 200
    assert results.json()["total"] == 1
    assert "pump" in results.json()["items"][0]["description"].lower()


def test_assign_sets_engineer_and_advances_status(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    doc = create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    engineer = create_user(db_session, "eng@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken pump"}, headers=doc_headers
    ).json()
    assert created["status"] == "NEW"

    assigned = client.post(
        f"/api/v1/incidents/{created['id']}/assign",
        json={"assigned_engineer_id": engineer.id, "notes": "Assigning to on-call engineer"},
        headers=eng_headers,
    )
    assert assigned.status_code == 200
    body = assigned.json()
    assert body["assigned_engineer_id"] == engineer.id
    assert body["status"] == "ASSIGNED"

    history = client.get(f"/api/v1/incidents/{created['id']}/history", headers=eng_headers).json()
    assert [h["to_status"] for h in history] == ["NEW", "ASSIGNED"]


def test_doctor_cannot_assign_incident(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken pump"}, headers=doc_headers
    ).json()
    response = client.post(
        f"/api/v1/incidents/{created['id']}/assign", json={"assigned_engineer_id": "someone"}, headers=doc_headers
    )
    assert response.status_code == 403


def test_attachment_linkage(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "eng@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken pump"}, headers=doc_headers
    ).json()

    document = Document(title="Photo of broken pump", document_type="INCIDENT_PHOTOGRAPH")
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    link = client.post(
        f"/api/v1/incidents/{created['id']}/attachments", json={"document_id": document.id}, headers=eng_headers
    )
    assert link.status_code == 201

    listing = client.get(f"/api/v1/incidents/{created['id']}/attachments", headers=eng_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["document_id"] == document.id


# ---------------------------------------------------------------------------
# Workflow consolidation pass — RBAC fix, transition graph, incident linkage
# ---------------------------------------------------------------------------

def test_hospital_unit_manager_can_create_incident(admin_client, client, db_session):
    """Previously HOSPITAL_UNIT_MANAGER (TL/Unit Head) held incident.read but not
    incident.create, even though /raise already showed Incident tiles to anyone with
    either permission — a TL submitting would 403. Fixed in security/permissions.py."""
    branch = _make_branch(admin_client)
    create_user(db_session, "tl.raise@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    headers = auth_headers(client, "tl.raise@example.com", "Password123!")
    response = client.post(
        "/api/v1/incidents",
        json={"branch_id": branch["id"], "description": "Infusion pump beeping at the nursing station"},
        headers=headers,
    )
    assert response.status_code == 201, response.text


def test_illegal_status_transition_rejected(admin_client, client, db_session):
    """CLOSED is terminal; a previously-closed incident cannot be reopened by jumping
    straight back to NEW, unlike before this pass (any status was reachable from any
    other status)."""
    branch = _make_branch(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "eng@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken pump"}, headers=doc_headers
    ).json()

    closed = client.post(
        f"/api/v1/incidents/{created['id']}/status", json={"to_status": "CLOSED"}, headers=eng_headers
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED"

    reopen = client.post(
        f"/api/v1/incidents/{created['id']}/status", json={"to_status": "NEW"}, headers=eng_headers
    )
    assert reopen.status_code == 409


def test_legal_diagnosis_chain_accepted(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "eng@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken pump"}, headers=doc_headers
    ).json()

    for to_status in ("ASSIGNED", "UNDER_DIAGNOSIS", "RESOLVED", "PENDING_VERIFICATION", "CLOSED"):
        response = client.post(
            f"/api/v1/incidents/{created['id']}/status", json={"to_status": to_status}, headers=eng_headers
        )
        assert response.status_code == 200, (to_status, response.text)
        assert response.json()["status"] == to_status


def test_assign_fires_incident_assigned_notification(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    doc = create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    engineer = create_user(db_session, "eng.notify@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng.notify@example.com", "Password123!")

    admin_client.post(
        "/api/v1/notifications/templates",
        json={
            "code": "INCIDENT_ASSIGNED_EMAIL", "channel": "EMAIL", "event": "INCIDENT_ASSIGNED",
            "body_template": "Incident assigned to {{assigned_engineer_name}} ({{assigned_engineer_email}}): {{description}}",
            "receiver_email": "{{assigned_engineer_email}}", "is_active": True,
        },
    )

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken pump"}, headers=doc_headers
    ).json()

    client.post(
        f"/api/v1/incidents/{created['id']}/assign",
        json={"assigned_engineer_id": engineer.id},
        headers=eng_headers,
    )

    from app.models.notifications import Notification

    notifications = db_session.query(Notification).filter(Notification.event == "INCIDENT_ASSIGNED").all()
    assert len(notifications) == 1
    assert notifications[0].recipient == engineer.email
    assert engineer.email in notifications[0].rendered_body


def test_resolve_fires_incident_resolved_notification(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc.resolve@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "eng.resolve@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc.resolve@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng.resolve@example.com", "Password123!")

    admin_client.post(
        "/api/v1/notifications/templates",
        json={
            "code": "INCIDENT_RESOLVED_EMAIL", "channel": "EMAIL", "event": "INCIDENT_RESOLVED",
            "body_template": "Incident resolved: {{description}}",
            "receiver_email": "{{reported_by_email}}", "is_active": True,
        },
    )

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken pump"}, headers=doc_headers
    ).json()
    client.post(f"/api/v1/incidents/{created['id']}/status", json={"to_status": "ACKNOWLEDGED"}, headers=eng_headers)
    client.post(f"/api/v1/incidents/{created['id']}/status", json={"to_status": "CLOSED"}, headers=eng_headers)

    from app.models.notifications import Notification

    notifications = db_session.query(Notification).filter(Notification.event == "INCIDENT_RESOLVED").all()
    assert len(notifications) == 1
    assert notifications[0].recipient == "doc.resolve@example.com"


def test_pm_record_can_link_back_to_incident(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = admin_client.post("/api/v1/device-models", json={"name": "PM Ventilator", "category": "Ventilator"}).json()
    asset = admin_client.post(
        "/api/v1/assets", json={"asset_number": "PM-001", "device_model_id": model["id"], "branch_id": branch["id"]}
    ).json()
    create_user(db_session, "tl.pm@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, "tl.pm@example.com", "Password123!")
    create_user(db_session, "eng.pm@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    eng_headers = auth_headers(client, "eng.pm@example.com", "Password123!")

    incident = client.post(
        "/api/v1/incidents",
        json={"branch_id": branch["id"], "asset_id": asset["id"], "description": "Due for maintenance",
              "problem_category": "Maintenance Request"},
        headers=tl_headers,
    ).json()

    schedule = client.post(
        "/api/v1/pm/schedules", json={"asset_id": asset["id"], "frequency": "MONTHLY"}, headers=eng_headers
    ).json()
    record = client.post(
        "/api/v1/pm/records",
        json={"schedule_id": schedule["id"], "asset_id": asset["id"], "incident_id": incident["id"]},
        headers=eng_headers,
    )
    assert record.status_code == 201, record.text
    assert record.json()["incident_id"] == incident["id"]

    branch_records = client.get(f"/api/v1/pm/records?branch_id={branch['id']}", headers=eng_headers)
    assert branch_records.status_code == 200
    assert branch_records.json()["total"] == 1


def test_calibration_record_can_link_back_to_incident(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    model = admin_client.post("/api/v1/device-models", json={"name": "Cal Monitor", "category": "Monitor"}).json()
    asset = admin_client.post(
        "/api/v1/assets", json={"asset_number": "CAL-001", "device_model_id": model["id"], "branch_id": branch["id"]}
    ).json()
    create_user(db_session, "eng.cal@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    eng_headers = auth_headers(client, "eng.cal@example.com", "Password123!")

    incident = client.post(
        "/api/v1/incidents",
        json={"branch_id": branch["id"], "asset_id": asset["id"], "description": "Due for calibration",
              "problem_category": "Calibration Request"},
        headers=eng_headers,
    ).json()

    schedule = client.post(
        "/api/v1/calibration/schedules", json={"asset_id": asset["id"], "frequency": "YEARLY"}, headers=eng_headers
    ).json()
    record = client.post(
        "/api/v1/calibration/records",
        json={"schedule_id": schedule["id"], "asset_id": asset["id"], "incident_id": incident["id"]},
        headers=eng_headers,
    )
    assert record.status_code == 201, record.text
    assert record.json()["incident_id"] == incident["id"]

    branch_records = client.get(f"/api/v1/calibration/records?branch_id={branch['id']}", headers=eng_headers)
    assert branch_records.status_code == 200
    assert branch_records.json()["total"] == 1


def test_service_visit_branch_filter(admin_client, client, db_session):
    branch = admin_client.post("/api/v1/branches", json={"name": "SV Branch A", "status": "ACTIVE"}).json()
    model = admin_client.post("/api/v1/device-models", json={"name": "SV Bed", "category": "Bed"}).json()
    asset = admin_client.post(
        "/api/v1/assets", json={"asset_number": "SV-001", "device_model_id": model["id"], "branch_id": branch["id"]}
    ).json()
    other_branch = admin_client.post("/api/v1/branches", json={"name": "SV Branch B", "status": "ACTIVE"}).json()
    other_model = admin_client.post("/api/v1/device-models", json={"name": "Other Bed", "category": "Bed"}).json()
    other_asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": "SV-002", "device_model_id": other_model["id"], "branch_id": other_branch["id"]},
    ).json()

    admin_client.post("/api/v1/service-visits", json={"asset_id": asset["id"], "visit_type": "BREAKDOWN"})
    admin_client.post("/api/v1/service-visits", json={"asset_id": other_asset["id"], "visit_type": "BREAKDOWN"})

    response = admin_client.get(f"/api/v1/service-visits?branch_id={branch['id']}")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["asset_id"] == asset["id"]


def test_attachment_requires_existing_document(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "eng@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken pump"}, headers=doc_headers
    ).json()

    response = client.post(
        f"/api/v1/incidents/{created['id']}/attachments", json={"document_id": "missing"}, headers=eng_headers
    )
    assert response.status_code == 404
