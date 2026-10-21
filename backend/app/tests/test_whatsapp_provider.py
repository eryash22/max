"""Module 19 — WhatsApp honest state machine (QUEUED -> OPENED ->
REQUIRES_USER_CONFIRMATION -> SENT / FAILED) and the BROWSER_LINK/BUSINESS_API
provider abstraction. See services/whatsapp_provider.py and
services/whatsapp_service.py.
"""
from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _engineer_headers(client, db_session, email="wa.eng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _create_and_submit_whatsapp_notification(admin_client, recipient="+91 90000 00001"):
    created = admin_client.post(
        "/api/v1/notifications",
        json={"recipient": recipient, "channel": "WHATSAPP", "event": "REQUIREMENT_SUBMITTED"},
    )
    assert created.status_code == 201, created.text
    notification = created.json()
    submitted = admin_client.post(f"/api/v1/notifications/{notification['id']}/submit")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "PENDING_APPROVAL"
    return submitted.json()


# ---------------------------------------------------------------------------
# BROWSER_LINK provider (default, real) — honest state machine
# ---------------------------------------------------------------------------

def test_approve_whatsapp_queues_instead_of_sending(admin_client):
    notification = _create_and_submit_whatsapp_notification(admin_client)
    approved = admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert body["whatsapp_status"] == "QUEUED"
    assert body["whatsapp_provider"] == "BROWSER_LINK"


def test_whatsapp_opened_is_best_effort_telemetry(admin_client):
    notification = _create_and_submit_whatsapp_notification(admin_client)
    admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    opened = admin_client.post(f"/api/v1/notifications/{notification['id']}/whatsapp/opened")
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["whatsapp_status"] == "OPENED"
    assert body["whatsapp_opened_at"] is not None


def test_confirm_sent_from_queued(admin_client):
    notification = _create_and_submit_whatsapp_notification(admin_client)
    admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    confirmed = admin_client.post(f"/api/v1/notifications/{notification['id']}/whatsapp/confirm-sent")
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "SENT"
    assert body["whatsapp_status"] == "SENT"
    assert body["whatsapp_confirmed_by"] is not None
    assert body["sent_at"] is not None


def test_cannot_confirm_sent_twice(admin_client):
    notification = _create_and_submit_whatsapp_notification(admin_client)
    admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    first = admin_client.post(f"/api/v1/notifications/{notification['id']}/whatsapp/confirm-sent")
    assert first.status_code == 200
    second = admin_client.post(f"/api/v1/notifications/{notification['id']}/whatsapp/confirm-sent")
    assert second.status_code == 409


def test_mark_failed_from_queued(admin_client):
    notification = _create_and_submit_whatsapp_notification(admin_client)
    admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    failed = admin_client.post(
        f"/api/v1/notifications/{notification['id']}/whatsapp/mark-failed",
        json={"failure_reason": "Number no longer valid"},
    )
    assert failed.status_code == 200, failed.text
    body = failed.json()
    assert body["status"] == "FAILED"
    assert body["whatsapp_status"] == "FAILED"
    assert body["failure_reason"] == "Number no longer valid"


def test_mark_failed_requires_non_blank_reason(admin_client):
    notification = _create_and_submit_whatsapp_notification(admin_client)
    admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    response = admin_client.post(
        f"/api/v1/notifications/{notification['id']}/whatsapp/mark-failed", json={"failure_reason": "   "}
    )
    assert response.status_code == 422


def test_email_approval_semantics_unaffected_by_module_19(admin_client):
    """Regression guard: EMAIL's approve-now-sends-or-fails behavior (Module 17B) must
    stay byte-identical after adding the WHATSAPP state machine.

    This manually-created notification carries no rendered_body (the original
    Module 10 path — no rule ever rendered it), so approve_notification's own
    documented pre-17B behavior applies: it goes straight to SENT with no real SMTP
    attempt. The SMTP-failure path for a rule-generated (rendered_body-carrying)
    EMAIL notification is already covered by
    test_communication.py::test_email_send_failure_when_smtp_not_configured — this
    test only needs to confirm that path, and the untouched manual-create path,
    still behave exactly as before Module 19."""
    created = admin_client.post(
        "/api/v1/notifications", json={"recipient": "unaffected@example.com", "channel": "EMAIL", "event": "PM_DUE"}
    )
    notification = created.json()
    admin_client.post(f"/api/v1/notifications/{notification['id']}/submit")
    approved = admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "SENT"
    assert body["whatsapp_status"] is None


# ---------------------------------------------------------------------------
# BUSINESS_API provider (stub — no credentials)
# ---------------------------------------------------------------------------

def test_business_api_provider_requires_user_confirmation(admin_client):
    switched = admin_client.patch("/api/v1/notifications/provider-config", json={"active_provider": "BUSINESS_API"})
    assert switched.status_code == 200, switched.text
    assert switched.json()["is_configured"] is False

    notification = _create_and_submit_whatsapp_notification(admin_client)
    approved = admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert body["whatsapp_status"] == "REQUIRES_USER_CONFIRMATION"
    assert body["whatsapp_provider"] == "BUSINESS_API"

    # Even unconfigured, a human can still confirm they sent it manually.
    confirmed = admin_client.post(f"/api/v1/notifications/{notification['id']}/whatsapp/confirm-sent")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "SENT"


def test_unknown_provider_rejected(admin_client):
    response = admin_client.patch("/api/v1/notifications/provider-config", json={"active_provider": "CARRIER_PIGEON"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def test_engineer_cannot_read_provider_config(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.get("/api/v1/notifications/provider-config", headers=headers)
    assert response.status_code == 403


def test_engineer_cannot_change_provider_config(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session, "wa.eng2@example.com")
    response = client.patch(
        "/api/v1/notifications/provider-config", json={"active_provider": "BUSINESS_API"}, headers=headers
    )
    assert response.status_code == 403


def test_admin_can_read_default_provider_config(admin_client):
    response = admin_client.get("/api/v1/notifications/provider-config")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["channel"] == "WHATSAPP"
    assert body["active_provider"] == "BROWSER_LINK"
    assert body["is_configured"] is True


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def test_provider_config_change_writes_audit_log(admin_client, db_session):
    admin_client.patch("/api/v1/notifications/provider-config", json={"active_provider": "BUSINESS_API"})
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "communication_provider_config").all()
    assert any(log.action == "UPDATE" for log in logs)


def test_whatsapp_confirm_sent_writes_audit_log(admin_client, db_session):
    notification = _create_and_submit_whatsapp_notification(admin_client)
    admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    admin_client.post(f"/api/v1/notifications/{notification['id']}/whatsapp/confirm-sent")
    logs = db_session.query(AuditLog).filter(
        AuditLog.entity == "notification", AuditLog.entity_id == notification["id"]
    ).all()
    assert any(log.action == "UPDATE" for log in logs)
