"""Final non-AI cleanup pass — Incident CANCELLED (safe "archive" equivalent).

There is deliberately no DELETE endpoint for incidents (they can be referenced by
asset history, repair, procurement, and audit). CANCELLED is a new terminal status
reachable from every still-open, not-yet-resolved status, preserving the full
record and its status history — nothing is deleted.
"""

from .conftest import auth_headers, create_user


def _make_branch(admin_client):
    return admin_client.post("/api/v1/branches", json={"name": "Cancel-Test-Branch", "status": "ACTIVE"}).json()


def test_new_incident_can_be_cancelled(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc-cancel@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "eng-cancel@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc-cancel@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng-cancel@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Duplicate report"}, headers=doc_headers
    ).json()

    cancelled = client.post(
        f"/api/v1/incidents/{created['id']}/status",
        json={"to_status": "CANCELLED", "notes": "Duplicate of another report"},
        headers=eng_headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    # Preserved, not deleted — still fetchable with full history.
    fetched = client.get(f"/api/v1/incidents/{created['id']}", headers=eng_headers)
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "CANCELLED"

    history = client.get(f"/api/v1/incidents/{created['id']}/history", headers=eng_headers).json()
    assert [h["to_status"] for h in history] == ["OPEN", "CANCELLED"]


def test_cancelled_incident_is_terminal(admin_client, client, db_session):
    branch = _make_branch(admin_client)
    create_user(db_session, "doc-cancel2@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "eng-cancel2@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc-cancel2@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng-cancel2@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "False alarm"}, headers=doc_headers
    ).json()
    client.post(f"/api/v1/incidents/{created['id']}/status", json={"to_status": "CANCELLED"}, headers=eng_headers)

    reopen = client.post(
        f"/api/v1/incidents/{created['id']}/status", json={"to_status": "WORK_IN_PROGRESS"}, headers=eng_headers
    )
    assert reopen.status_code == 409


def test_resolved_incident_cannot_be_cancelled(admin_client, client, db_session):
    """CANCELLED is only reachable from a still-open status — an incident already
    OPERATIONAL should stay closed, not cancelled."""
    branch = _make_branch(admin_client)
    create_user(db_session, "doc-cancel3@example.com", "DOCTOR", "Password123!")
    create_user(db_session, "eng-cancel3@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    doc_headers = auth_headers(client, "doc-cancel3@example.com", "Password123!")
    eng_headers = auth_headers(client, "eng-cancel3@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Pump issue"}, headers=doc_headers
    ).json()
    client.post(f"/api/v1/incidents/{created['id']}/status", json={"to_status": "WORK_IN_PROGRESS"}, headers=eng_headers)
    client.post(f"/api/v1/incidents/{created['id']}/status", json={"to_status": "OPERATIONAL"}, headers=eng_headers)

    response = client.post(
        f"/api/v1/incidents/{created['id']}/status", json={"to_status": "CANCELLED"}, headers=eng_headers
    )
    assert response.status_code == 409
