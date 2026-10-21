from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _make_incident(admin_client, branch_name="Escalation Branch"):
    branch = admin_client.post("/api/v1/branches", json={"name": branch_name, "status": "ACTIVE"}).json()
    incident = admin_client.post(
        "/api/v1/incidents",
        json={"branch_id": branch["id"], "description": "Ventilator alarm not silencing", "severity": "HIGH"},
    ).json()
    return branch, incident


def _engineer_headers(client, db_session, email="eng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _make_policy(admin_client, name="Critical ventilator escalation"):
    return admin_client.post(
        "/api/v1/escalation/policies",
        json={"name": name, "trigger_type": "SEVERITY_BASED", "severity_threshold": "CRITICAL"},
    ).json()


# ---- Policy CRUD ----

def test_create_policy(admin_client):
    policy = _make_policy(admin_client)
    assert policy["name"] == "Critical ventilator escalation"
    assert policy["trigger_type"] == "SEVERITY_BASED"
    assert policy["is_active"] is True


def test_duplicate_policy_name_rejected(admin_client):
    _make_policy(admin_client, "Dup Policy")
    response = admin_client.post(
        "/api/v1/escalation/policies",
        json={"name": "Dup Policy", "trigger_type": "TIME_BASED"},
    )
    assert response.status_code == 409


def test_invalid_trigger_type_rejected(admin_client):
    response = admin_client.post(
        "/api/v1/escalation/policies", json={"name": "Bad Policy", "trigger_type": "NOT_REAL"}
    )
    assert response.status_code == 422


def test_invalid_severity_threshold_rejected(admin_client):
    response = admin_client.post(
        "/api/v1/escalation/policies",
        json={"name": "Bad Severity", "trigger_type": "SEVERITY_BASED", "severity_threshold": "URGENT"},
    )
    assert response.status_code == 422


def test_blank_name_rejected(admin_client):
    response = admin_client.post(
        "/api/v1/escalation/policies", json={"name": "   ", "trigger_type": "TIME_BASED"}
    )
    assert response.status_code == 422


def test_get_policy_404(admin_client):
    response = admin_client.get("/api/v1/escalation/policies/does-not-exist")
    assert response.status_code == 404


def test_update_policy(admin_client):
    policy = _make_policy(admin_client)
    response = admin_client.patch(
        f"/api/v1/escalation/policies/{policy['id']}",
        json={"name": policy["name"], "trigger_type": "TIME_BASED", "is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["trigger_type"] == "TIME_BASED"
    assert response.json()["is_active"] is False


def test_update_policy_duplicate_name_rejected(admin_client):
    _make_policy(admin_client, "Policy A")
    policy_b = _make_policy(admin_client, "Policy B")
    response = admin_client.patch(
        f"/api/v1/escalation/policies/{policy_b['id']}",
        json={"name": "Policy A", "trigger_type": "TIME_BASED"},
    )
    assert response.status_code == 409


def test_list_policies_filters_by_trigger_type_and_search(admin_client):
    admin_client.post("/api/v1/escalation/policies", json={"name": "Time Policy", "trigger_type": "TIME_BASED"})
    p2 = admin_client.post(
        "/api/v1/escalation/policies", json={"name": "Severity Policy", "trigger_type": "SEVERITY_BASED"}
    ).json()

    response = admin_client.get("/api/v1/escalation/policies", params={"trigger_type": "SEVERITY_BASED"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == p2["id"]

    response = admin_client.get("/api/v1/escalation/policies", params={"search": "Time"})
    assert response.json()["total"] == 1


def test_policy_create_records_audit_log(admin_client, db_session):
    _make_policy(admin_client)
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "escalation_policy").all()
    assert any(log.action == "CREATE" for log in logs)


# ---- Levels ----

def test_create_level(admin_client):
    policy = _make_policy(admin_client)
    response = admin_client.post(
        f"/api/v1/escalation/policies/{policy['id']}/levels",
        json={"level_number": 1, "role_code": "BIOMEDICAL_ENGINEER", "wait_minutes": 15},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["level_number"] == 1
    assert body["policy_id"] == policy["id"]


def test_create_level_requires_existing_policy(admin_client):
    response = admin_client.post(
        "/api/v1/escalation/policies/does-not-exist/levels", json={"level_number": 1}
    )
    assert response.status_code == 404


def test_duplicate_level_number_rejected(admin_client):
    policy = _make_policy(admin_client)
    admin_client.post(f"/api/v1/escalation/policies/{policy['id']}/levels", json={"level_number": 1})
    response = admin_client.post(f"/api/v1/escalation/policies/{policy['id']}/levels", json={"level_number": 1})
    assert response.status_code == 409


def test_invalid_level_number_rejected(admin_client):
    policy = _make_policy(admin_client)
    response = admin_client.post(f"/api/v1/escalation/policies/{policy['id']}/levels", json={"level_number": 0})
    assert response.status_code == 422


def test_list_levels_ordered(admin_client):
    policy = _make_policy(admin_client)
    admin_client.post(f"/api/v1/escalation/policies/{policy['id']}/levels", json={"level_number": 2})
    admin_client.post(f"/api/v1/escalation/policies/{policy['id']}/levels", json={"level_number": 1})
    response = admin_client.get(f"/api/v1/escalation/policies/{policy['id']}/levels")
    levels = response.json()
    assert [lvl["level_number"] for lvl in levels] == [1, 2]


def test_delete_level(admin_client, db_session):
    policy = _make_policy(admin_client)
    level = admin_client.post(f"/api/v1/escalation/policies/{policy['id']}/levels", json={"level_number": 1}).json()
    response = admin_client.delete(f"/api/v1/escalation/policies/{policy['id']}/levels/{level['id']}")
    assert response.status_code == 204
    remaining = admin_client.get(f"/api/v1/escalation/policies/{policy['id']}/levels").json()
    assert remaining == []
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "escalation_level", AuditLog.action == "DELETE").all()
    assert len(logs) == 1


def test_delete_level_404(admin_client):
    policy = _make_policy(admin_client)
    response = admin_client.delete(f"/api/v1/escalation/policies/{policy['id']}/levels/does-not-exist")
    assert response.status_code == 404


# ---- Events ----

def test_create_event(admin_client):
    _, incident = _make_incident(admin_client)
    policy = _make_policy(admin_client)
    admin_client.post(f"/api/v1/escalation/policies/{policy['id']}/levels", json={"level_number": 1})

    response = admin_client.post(
        "/api/v1/escalation/events",
        json={"incident_id": incident["id"], "policy_id": policy["id"], "level_number": 1, "notes": "Paged on-call engineer"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["incident_id"] == incident["id"]
    assert body["level_number"] == 1


def test_create_event_requires_existing_incident(admin_client):
    policy = _make_policy(admin_client)
    admin_client.post(f"/api/v1/escalation/policies/{policy['id']}/levels", json={"level_number": 1})
    response = admin_client.post(
        "/api/v1/escalation/events",
        json={"incident_id": "does-not-exist", "policy_id": policy["id"], "level_number": 1},
    )
    assert response.status_code == 404


def test_create_event_requires_existing_policy(admin_client):
    _, incident = _make_incident(admin_client)
    response = admin_client.post(
        "/api/v1/escalation/events",
        json={"incident_id": incident["id"], "policy_id": "does-not-exist", "level_number": 1},
    )
    assert response.status_code == 404


def test_create_event_requires_configured_level(admin_client):
    _, incident = _make_incident(admin_client)
    policy = _make_policy(admin_client)
    response = admin_client.post(
        "/api/v1/escalation/events",
        json={"incident_id": incident["id"], "policy_id": policy["id"], "level_number": 99},
    )
    assert response.status_code == 422


def test_create_event_validates_notified_user(admin_client):
    _, incident = _make_incident(admin_client)
    policy = _make_policy(admin_client)
    admin_client.post(f"/api/v1/escalation/policies/{policy['id']}/levels", json={"level_number": 1})
    response = admin_client.post(
        "/api/v1/escalation/events",
        json={"incident_id": incident["id"], "policy_id": policy["id"], "level_number": 1, "notified_user_id": "does-not-exist"},
    )
    assert response.status_code == 404


def test_list_events_filters_by_incident(admin_client):
    _, incident_a = _make_incident(admin_client, "Branch A")
    _, incident_b = _make_incident(admin_client, "Branch B")
    policy = _make_policy(admin_client)
    admin_client.post(f"/api/v1/escalation/policies/{policy['id']}/levels", json={"level_number": 1})
    admin_client.post(
        "/api/v1/escalation/events",
        json={"incident_id": incident_a["id"], "policy_id": policy["id"], "level_number": 1},
    )

    response = admin_client.get("/api/v1/escalation/events", params={"incident_id": incident_a["id"]})
    assert response.json()["total"] == 1
    response = admin_client.get("/api/v1/escalation/events", params={"incident_id": incident_b["id"]})
    assert response.json()["total"] == 0


def test_event_create_records_audit_log(admin_client, db_session):
    _, incident = _make_incident(admin_client)
    policy = _make_policy(admin_client)
    admin_client.post(f"/api/v1/escalation/policies/{policy['id']}/levels", json={"level_number": 1})
    admin_client.post(
        "/api/v1/escalation/events",
        json={"incident_id": incident["id"], "policy_id": policy["id"], "level_number": 1},
    )
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "escalation_event").all()
    assert len(logs) == 1


def test_events_pagination(admin_client):
    _, incident = _make_incident(admin_client)
    policy = _make_policy(admin_client)
    admin_client.post(f"/api/v1/escalation/policies/{policy['id']}/levels", json={"level_number": 1})
    for _ in range(3):
        admin_client.post(
            "/api/v1/escalation/events",
            json={"incident_id": incident["id"], "policy_id": policy["id"], "level_number": 1},
        )
    response = admin_client.get("/api/v1/escalation/events", params={"page": 1, "page_size": 2})
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


# ---- RBAC ----

def test_engineer_can_read_and_write_escalation(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/escalation/policies", json={"name": "Engineer Policy", "trigger_type": "TIME_BASED"}, headers=headers
    )
    assert response.status_code == 201
    response = client.get("/api/v1/escalation/policies", headers=headers)
    assert response.status_code == 200


def test_doctor_cannot_write_escalation_policy(admin_client, client, db_session):
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.post(
        "/api/v1/escalation/policies", json={"name": "Doctor Policy", "trigger_type": "TIME_BASED"}, headers=headers
    )
    assert response.status_code == 403


def test_doctor_can_read_escalation_policies(admin_client, client, db_session):
    _make_policy(admin_client)
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.get("/api/v1/escalation/policies", headers=headers)
    assert response.status_code == 200


def test_inventory_manager_cannot_write_escalation_policy(admin_client, client, db_session):
    # INVENTORY_MANAGER holds incident.read (reused for escalation reads) but not
    # incident.manage, matching the read/write split every other role gets.
    create_user(db_session, "inv@example.com", "INVENTORY_MANAGER", "Password123!")
    headers = auth_headers(client, "inv@example.com", "Password123!")
    response = client.post(
        "/api/v1/escalation/policies", json={"name": "Inventory Policy", "trigger_type": "TIME_BASED"}, headers=headers
    )
    assert response.status_code == 403
