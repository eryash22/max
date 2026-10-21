"""F1 — the canonical "open incident" definition.

The Dashboard used to derive open incident counts client-side as
`total - RESOLVED - CLOSED`, which silently counted every CANCELLED incident as
open. CANCELLED is a terminal archive state (incident_service.ALLOWED_TRANSITIONS
maps both CLOSED and CANCELLED to an empty transition set), so it must never count
as open.

These tests pin the shared vocabulary in schemas/incident.py so the partition can't
drift back into a subtraction, and prove the partition end-to-end through
GET /api/v1/dashboard/summary — the endpoint the redesigned dashboard actually reads.
"""
from app.schemas.incident import NON_OPEN_STATUSES, OPEN_STATUSES, VALID_STATUSES

from .conftest import auth_headers, create_user


# ---- The canonical definition itself ----

def test_open_and_non_open_partition_every_valid_status():
    """Every status is classified exactly once — a status can't be added to
    VALID_STATUSES and silently fall outside both buckets."""
    assert set(OPEN_STATUSES) | set(NON_OPEN_STATUSES) == set(VALID_STATUSES)
    assert set(OPEN_STATUSES) & set(NON_OPEN_STATUSES) == set()


def test_terminal_statuses_are_not_open():
    for status in ("RESOLVED", "CLOSED", "CANCELLED"):
        assert status in NON_OPEN_STATUSES, f"{status} must not count as open"
        assert status not in OPEN_STATUSES


def test_active_statuses_are_open():
    """NEW/ASSIGNED/etc. are open, and PENDING_VERIFICATION deliberately stays open —
    someone still has to verify it."""
    for status in ("NEW", "ACKNOWLEDGED", "ASSIGNED", "UNDER_DIAGNOSIS", "ESCALATED",
                    "WAITING_FOR_VENDOR", "WAITING_FOR_PART", "WAITING_FOR_USER",
                    "PENDING_VERIFICATION"):
        assert status in OPEN_STATUSES, f"{status} must count as open"


def test_cancelled_is_the_regression_case():
    """The specific bug: CANCELLED counted as open because the old formula only
    subtracted RESOLVED and CLOSED."""
    assert "CANCELLED" in NON_OPEN_STATUSES
    assert "CANCELLED" not in OPEN_STATUSES


# ---- The same partition, proven end-to-end through the dashboard endpoint ----

def _branch(admin_client, name):
    response = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"})
    assert response.status_code == 201, response.text
    return response.json()


def _incident(admin_client, branch_id, description, severity="HIGH"):
    response = admin_client.post(
        "/api/v1/incidents",
        json={"branch_id": branch_id, "description": description, "severity": severity},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _transition(admin_client, incident_id, to_status):
    response = admin_client.post(
        f"/api/v1/incidents/{incident_id}/status", json={"to_status": to_status}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_dashboard_open_count_excludes_every_terminal_status(admin_client):
    """One incident driven to each terminal state plus one left open — the open count
    must be exactly 1. The old subtraction would have returned 2 by counting the
    cancelled one."""
    branch = _branch(admin_client, "F1 Branch")

    _incident(admin_client, branch["id"], "still open")

    resolved = _incident(admin_client, branch["id"], "resolved incident")
    _transition(admin_client, resolved["id"], "ASSIGNED")
    _transition(admin_client, resolved["id"], "UNDER_DIAGNOSIS")
    _transition(admin_client, resolved["id"], "RESOLVED")

    closed = _incident(admin_client, branch["id"], "closed incident")
    _transition(admin_client, closed["id"], "CLOSED")

    cancelled = _incident(admin_client, branch["id"], "cancelled incident")
    _transition(admin_client, cancelled["id"], "CANCELLED")

    response = admin_client.get(f"/api/v1/dashboard/summary?branch_id={branch['id']}")
    assert response.status_code == 200, response.text
    incidents = response.json()["incidents"]

    assert incidents["total"] == 4
    assert incidents["open"] == 1, f"only the untouched incident is open, got {incidents}"
    assert incidents["by_status"]["CANCELLED"] == 1
    assert incidents["by_status"]["RESOLVED"] == 1
    assert incidents["by_status"]["CLOSED"] == 1
    assert incidents["by_status"]["NEW"] == 1


def test_dashboard_critical_open_excludes_cancelled(admin_client):
    """The critical-open KPI used the same broken subtraction, so it gets its own
    regression test."""
    branch = _branch(admin_client, "F1 Critical Branch")

    _incident(admin_client, branch["id"], "critical open", severity="CRITICAL")
    cancelled = _incident(admin_client, branch["id"], "critical cancelled", severity="CRITICAL")
    _transition(admin_client, cancelled["id"], "CANCELLED")

    incidents = admin_client.get(f"/api/v1/dashboard/summary?branch_id={branch['id']}").json()["incidents"]
    assert incidents["critical_open"] == 1, f"cancelled critical must not count, got {incidents}"


def test_open_statuses_are_reported_so_the_frontend_never_re_derives_them(admin_client):
    """The response carries the canonical list, so a chart or drill-down link filters
    on the same definition the count used."""
    branch = _branch(admin_client, "F1 Vocabulary Branch")
    incidents = admin_client.get(f"/api/v1/dashboard/summary?branch_id={branch['id']}").json()["incidents"]
    assert set(incidents["open_statuses"]) == set(OPEN_STATUSES)
    assert "CANCELLED" not in incidents["open_statuses"]


def test_dashboard_incident_counts_are_branch_scoped_for_unit_head(admin_client, client, db_session):
    """The summary feeds a branch-scoped dashboard, so it obeys the same isolation as
    every other incident read — including against a forged branch_id."""
    mine = _branch(admin_client, "F1 Mine")
    theirs = _branch(admin_client, "F1 Theirs")

    _incident(admin_client, mine["id"], "mine open")
    _incident(admin_client, theirs["id"], "theirs open")
    _incident(admin_client, theirs["id"], "theirs open 2")

    user = create_user(db_session, "f1.unithead@example.com", "UNIT_HEAD", "Password123!")
    user.hospital_scope_id = mine["id"]
    db_session.add(user)
    db_session.commit()
    headers = auth_headers(client, "f1.unithead@example.com", "Password123!")

    body = client.get("/api/v1/dashboard/summary", headers=headers).json()
    assert body["scope"] == "BRANCH"
    assert body["branch_id"] == mine["id"]
    assert body["incidents"]["total"] == 1, f"unit head sees only their branch, got {body['incidents']}"

    forged = client.get(f"/api/v1/dashboard/summary?branch_id={theirs['id']}", headers=headers).json()
    assert forged["branch_id"] == mine["id"], "a forged branch_id must not repoint the scope"
    assert forged["incidents"]["total"] == 1
