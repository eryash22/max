"""Module 19 Enhancement — WhatsApp Web browser-automation provider
(BrowserAutomationProvider) and its admin-only session-management endpoints.

These tests never launch a real browser or touch WhatsApp Web — they replace the
process-wide `whatsapp_automation_service.WhatsAppAutomationSession` singleton with a
fake that mimics its public contract (get_status/is_connected/connect/disconnect/
send_message), matching how the rest of this codebase avoids real SMTP/network calls
in tests. What's under test is the *provider/service/router* wiring and the honest
state-machine transitions, not Playwright itself.
"""
import pytest

from app.services import whatsapp_automation_service
from app.services.whatsapp_automation_service import SendResult

from .conftest import auth_headers, create_user


class FakeSession:
    def __init__(self, connected: bool = False):
        self._connected = connected
        self.sent_calls = []
        self.next_result = SendResult(state="SENT", detail="ok", evidence="dom_outgoing_bubble_match")

    def is_connected(self) -> bool:
        return self._connected

    def get_status(self) -> dict:
        return {
            "state": "CONNECTED" if self._connected else "DISCONNECTED",
            "qr_code_base64": None,
            "connected_at": None,
            "last_error": None,
            "last_checked_at": None,
        }

    def connect(self) -> dict:
        self._connected = False
        return {"state": "AWAITING_QR_SCAN", "qr_code_base64": "Zm9v", "connected_at": None,
                "last_error": None, "last_checked_at": None}

    def disconnect(self) -> dict:
        self._connected = False
        return self.get_status()

    def send_message(self, *, recipient: str, text: str) -> SendResult:
        self.sent_calls.append((recipient, text))
        if not self._connected:
            return SendResult(state="REQUIRES_USER_CONFIRMATION", detail="not connected")
        return self.next_result


@pytest.fixture
def fake_session(monkeypatch):
    fake = FakeSession(connected=True)
    monkeypatch.setattr(whatsapp_automation_service, "_session", fake)
    return fake


def _engineer_headers(client, db_session, email="wa.auto.eng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _create_and_submit_whatsapp_notification(admin_client, recipient="+91 90000 00099"):
    created = admin_client.post(
        "/api/v1/notifications",
        json={"recipient": recipient, "channel": "WHATSAPP", "event": "REQUIREMENT_SUBMITTED"},
    )
    assert created.status_code == 201, created.text
    notification = created.json()
    submitted = admin_client.post(f"/api/v1/notifications/{notification['id']}/submit")
    assert submitted.status_code == 200, submitted.text
    return submitted.json()


# ---------------------------------------------------------------------------
# Provider behavior
# ---------------------------------------------------------------------------

def test_automation_provider_not_configured_when_disconnected(admin_client, monkeypatch):
    monkeypatch.setattr(whatsapp_automation_service, "_session", FakeSession(connected=False))
    admin_client.patch("/api/v1/notifications/provider-config", json={"active_provider": "BROWSER_AUTOMATION"})
    notification = _create_and_submit_whatsapp_notification(admin_client)
    approved = admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["whatsapp_status"] == "REQUIRES_USER_CONFIRMATION"
    assert body["whatsapp_provider"] == "BROWSER_AUTOMATION"


def test_automation_provider_queues_when_connected(admin_client, fake_session):
    admin_client.patch("/api/v1/notifications/provider-config", json={"active_provider": "BROWSER_AUTOMATION"})
    notification = _create_and_submit_whatsapp_notification(admin_client)
    approved = admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert body["whatsapp_status"] == "QUEUED"
    assert body["whatsapp_provider"] == "BROWSER_AUTOMATION"


def test_attempt_send_marks_sent_on_dom_evidence(admin_client, fake_session):
    admin_client.patch("/api/v1/notifications/provider-config", json={"active_provider": "BROWSER_AUTOMATION"})
    notification = _create_and_submit_whatsapp_notification(admin_client)
    admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")

    fake_session.next_result = SendResult(state="SENT", detail="matched", evidence="dom_outgoing_bubble_match")
    sent = admin_client.post(f"/api/v1/notifications/{notification['id']}/whatsapp/automation/attempt-send")
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["status"] == "SENT"
    assert body["whatsapp_status"] == "SENT"
    assert body["sent_at"] is not None
    assert fake_session.sent_calls, "automation session should have been asked to send"


def test_attempt_send_requires_confirmation_without_evidence(admin_client, fake_session):
    admin_client.patch("/api/v1/notifications/provider-config", json={"active_provider": "BROWSER_AUTOMATION"})
    notification = _create_and_submit_whatsapp_notification(admin_client)
    admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")

    fake_session.next_result = SendResult(state="REQUIRES_USER_CONFIRMATION", detail="no matching bubble found")
    result = admin_client.post(f"/api/v1/notifications/{notification['id']}/whatsapp/automation/attempt-send")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert body["whatsapp_status"] == "REQUIRES_USER_CONFIRMATION"
    assert body["failure_reason"] == "no matching bubble found"


def test_attempt_send_marks_failed_on_error(admin_client, fake_session):
    admin_client.patch("/api/v1/notifications/provider-config", json={"active_provider": "BROWSER_AUTOMATION"})
    notification = _create_and_submit_whatsapp_notification(admin_client)
    admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")

    fake_session.next_result = SendResult(state="FAILED", detail="navigation error")
    result = admin_client.post(f"/api/v1/notifications/{notification['id']}/whatsapp/automation/attempt-send")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "FAILED"
    assert body["whatsapp_status"] == "FAILED"
    assert body["failure_reason"] == "navigation error"


def test_attempt_send_rejected_for_wrong_provider(admin_client, fake_session):
    # Default provider is BROWSER_LINK — never queued on BROWSER_AUTOMATION.
    notification = _create_and_submit_whatsapp_notification(admin_client)
    admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    result = admin_client.post(f"/api/v1/notifications/{notification['id']}/whatsapp/automation/attempt-send")
    assert result.status_code == 409


# ---------------------------------------------------------------------------
# Session-management endpoints (admin-only)
# ---------------------------------------------------------------------------

def test_admin_can_read_automation_status(admin_client, fake_session):
    response = admin_client.get("/api/v1/notifications/whatsapp-automation/status")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "CONNECTED"


def test_engineer_cannot_read_automation_status(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.get("/api/v1/notifications/whatsapp-automation/status", headers=headers)
    assert response.status_code == 403


def test_admin_can_connect_and_disconnect(admin_client, fake_session):
    connected = admin_client.post("/api/v1/notifications/whatsapp-automation/connect")
    assert connected.status_code == 200, connected.text
    assert connected.json()["state"] == "AWAITING_QR_SCAN"
    assert connected.json()["qr_code_base64"] is not None

    disconnected = admin_client.post("/api/v1/notifications/whatsapp-automation/disconnect")
    assert disconnected.status_code == 200, disconnected.text
    assert disconnected.json()["state"] == "DISCONNECTED"


def test_engineer_cannot_connect_session(client, db_session, fake_session):
    headers = _engineer_headers(client, db_session, "wa.auto.eng2@example.com")
    response = client.post("/api/v1/notifications/whatsapp-automation/connect", headers=headers)
    assert response.status_code == 403


def test_test_message_endpoint_uses_session(admin_client, fake_session):
    fake_session.next_result = SendResult(state="SENT", detail="ok", evidence="dom_outgoing_bubble_match")
    response = admin_client.post(
        "/api/v1/notifications/whatsapp-automation/test-message",
        json={"recipient": "+91 90000 00001", "message": "Test ping"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "SENT"
    assert fake_session.sent_calls == [("+91 90000 00001", "Test ping")]


def test_test_message_requires_non_blank_fields(admin_client, fake_session):
    response = admin_client.post(
        "/api/v1/notifications/whatsapp-automation/test-message",
        json={"recipient": "  ", "message": "hi"},
    )
    assert response.status_code == 422
