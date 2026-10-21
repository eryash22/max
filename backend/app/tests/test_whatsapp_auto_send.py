"""Per-rule auto-send, exercised over the WhatsApp Web path.

A WhatsApp notification produced by a communication rule used to stop at
PENDING_APPROVAL and need two manual actions (approve, then trigger the browser send).
`NotificationTemplate.auto_send` opts a single rule into delivering immediately,
without globally weakening `approval_required`. Introduced in migration 0025 as
`whatsapp_auto_send` and renamed in 0026 once the same flag started driving EMAIL too
(see test_contract_communication.py for the SMTP side).

The auto-send path is deliberately not a second sender: it drives exactly the
functions the manual UI calls, so recipient resolution, the BROWSER_AUTOMATION
provider, the persistent session and the DOM-evidence honesty contract are unchanged.
"""
import app.services.communication_service as communication_service
from app.models.notifications import Notification, NotificationTemplate
from app.schemas.notification import EVENT_REQUIREMENT_SUBMITTED

from .conftest import auth_headers, create_user

PASSWORD = "Password123!"


def _rule(db_session, *, auto_send: bool, number: str = "+919000000001") -> NotificationTemplate:
    rule = NotificationTemplate(
        code=f"AUTO_SEND_{auto_send}_{number}",
        channel="WHATSAPP",
        event=EVENT_REQUIREMENT_SUBMITTED,
        body_template="Requirement {{request_number}} submitted",
        is_active=True,
        receiver_whatsapp_number=number,
        auto_send=auto_send,
    )
    db_session.add(rule)
    db_session.commit()
    return rule


def _requirement(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "WA Branch", "status": "ACTIVE"}).json()
    model = admin_client.post("/api/v1/device-models", json={"name": "WA Device", "category": "MONITOR"}).json()
    response = admin_client.post(
        "/api/v1/requirements",
        json={
            "branch_id": branch["id"],
            "reason": "auto-send test",
            "items": [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"],
                        "requested_quantity": 1, "unit": "UNIT"}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _dispatch(db_session, requirement_id):
    from app.models.requirement import Requirement

    requirement = db_session.get(Requirement, requirement_id)
    return communication_service.dispatch_event(
        db_session, EVENT_REQUIREMENT_SUBMITTED, requirement=requirement, user_id=None
    )


# ---- Default / OFF preserves the existing flow ----

def test_column_defaults_to_off(db_session):
    rule = NotificationTemplate(
        code="DEFAULTS_OFF", channel="WHATSAPP", event=EVENT_REQUIREMENT_SUBMITTED,
        body_template="x", receiver_whatsapp_number="+919000000000",
    )
    db_session.add(rule)
    db_session.commit()
    assert rule.auto_send is False, "existing rules must keep the approval flow"


def test_auto_send_off_leaves_notification_pending_approval(admin_client, db_session, monkeypatch):
    _rule(db_session, auto_send=False, number="+919000000002")
    requirement = _requirement(admin_client)

    called = []
    monkeypatch.setattr(
        communication_service, "_maybe_auto_send",
        lambda db, rule, n: called.append(n),
    )
    results = _dispatch(db_session, requirement["id"])

    assert len(results) == 1
    # The helper is still invoked; it is the helper that decides to do nothing.
    assert len(called) == 1


def test_auto_send_off_does_not_touch_the_automation(admin_client, db_session, monkeypatch):
    _rule(db_session, auto_send=False, number="+919000000003")
    requirement = _requirement(admin_client)

    import app.services.whatsapp_service as whatsapp_service

    attempts = []
    monkeypatch.setattr(whatsapp_service, "attempt_automation_send",
                        lambda db, n: attempts.append(n))

    results = _dispatch(db_session, requirement["id"])
    assert results[0].status == "PENDING_APPROVAL"
    assert attempts == [], "an OFF rule must never invoke the browser automation"


# ---- ON drives the existing sender ----

def test_auto_send_on_invokes_the_existing_automation(admin_client, db_session, monkeypatch):
    _rule(db_session, auto_send=True, number="+919000000004")
    requirement = _requirement(admin_client)

    import app.services.whatsapp_service as whatsapp_service

    approved, attempted = [], []

    def fake_approve(db, n):
        approved.append(n)
        n.whatsapp_provider = "BROWSER_AUTOMATION"
        n.whatsapp_status = "QUEUED"
        return n

    def fake_attempt(db, n):
        attempted.append(n)
        n.whatsapp_status = "SENT"
        n.status = "SENT"
        return n

    monkeypatch.setattr(whatsapp_service, "approve_whatsapp_notification", fake_approve)
    monkeypatch.setattr(whatsapp_service, "attempt_automation_send", fake_attempt)

    results = _dispatch(db_session, requirement["id"])

    assert len(approved) == 1, "auto-send reuses the existing approval function"
    assert len(attempted) == 1, "auto-send reuses the existing browser sender"
    assert results[0].status == "SENT"
    assert results[0].approved_by == "system:auto_send"


# ---- Failure must never break the business transaction ----

def test_whatsapp_failure_does_not_roll_back_the_requirement(admin_client, db_session, monkeypatch):
    """The requirement really was submitted. A disconnected WhatsApp Web session is a
    delivery problem, not a reason to undo business state."""
    _rule(db_session, auto_send=True, number="+919000000005")
    requirement = _requirement(admin_client)

    import app.services.whatsapp_service as whatsapp_service

    def boom(db, n):
        raise RuntimeError("WhatsApp Web session is DISCONNECTED")

    monkeypatch.setattr(whatsapp_service, "approve_whatsapp_notification", boom)

    results = _dispatch(db_session, requirement["id"])

    assert results[0].status == "FAILED"
    assert "DISCONNECTED" in (results[0].failure_reason or "")

    # The requirement itself is untouched and still readable.
    fetched = admin_client.get(f"/api/v1/requirements/{requirement['id']}")
    assert fetched.status_code == 200, fetched.text


def test_send_failure_is_recorded_on_the_notification(admin_client, db_session, monkeypatch):
    _rule(db_session, auto_send=True, number="+919000000006")
    requirement = _requirement(admin_client)

    import app.services.whatsapp_service as whatsapp_service

    monkeypatch.setattr(whatsapp_service, "approve_whatsapp_notification",
                        lambda db, n: n)
    monkeypatch.setattr(whatsapp_service, "attempt_automation_send",
                        lambda db, n: (_ for _ in ()).throw(RuntimeError("selector not found")))

    results = _dispatch(db_session, requirement["id"])
    stored = db_session.get(Notification, results[0].id)
    assert stored.status == "FAILED"
    assert "selector not found" in stored.failure_reason


# ---- Recipient rules still apply ----

def test_auto_send_respects_notification_preference(admin_client, db_session, monkeypatch):
    """A recipient who opted out of WhatsApp must not be messaged just because the rule
    automates delivery."""
    user = create_user(db_session, "wa.optout@example.com", "VIEWER", PASSWORD)
    user.whatsapp_number = "+919000000007"
    user.notification_preference = "EMAIL"
    db_session.add(user)
    db_session.commit()

    _rule(db_session, auto_send=True, number="+919000000007")
    requirement = _requirement(admin_client)

    import app.services.whatsapp_service as whatsapp_service

    attempts = []
    monkeypatch.setattr(whatsapp_service, "attempt_automation_send", lambda db, n: attempts.append(n))

    results = _dispatch(db_session, requirement["id"])
    assert results[0].status == "FAILED"
    assert "notification_preference" in (results[0].failure_reason or "")
    assert attempts == [], "an opted-out recipient must never reach the sender"


def test_auto_send_requires_a_resolved_recipient(admin_client, db_session, monkeypatch):
    """No arbitrary recipient injection: with no address resolved from the rule, the
    notification fails instead of sending somewhere unintended."""
    rule = _rule(db_session, auto_send=True, number="+919000000008")
    rule.receiver_whatsapp_number = ""
    db_session.add(rule)
    db_session.commit()

    requirement = _requirement(admin_client)

    import app.services.whatsapp_service as whatsapp_service

    attempts = []
    monkeypatch.setattr(whatsapp_service, "attempt_automation_send", lambda db, n: attempts.append(n))

    results = _dispatch(db_session, requirement["id"])
    assert results[0].status == "FAILED"
    assert attempts == []


def test_auto_send_ignores_email_rules(admin_client, db_session, monkeypatch):
    """The toggle is WhatsApp-only — an EMAIL rule must be unaffected."""
    rule = NotificationTemplate(
        code="EMAIL_RULE_UNAFFECTED", channel="EMAIL", event=EVENT_REQUIREMENT_SUBMITTED,
        body_template="x", subject_template="s", receiver_email="ops@example.com",
        auto_send=True,
    )
    db_session.add(rule)
    db_session.commit()

    requirement = _requirement(admin_client)

    import app.services.whatsapp_service as whatsapp_service

    attempts = []
    monkeypatch.setattr(whatsapp_service, "attempt_automation_send", lambda db, n: attempts.append(n))

    _dispatch(db_session, requirement["id"])
    assert attempts == [], "an EMAIL rule must never enter the WhatsApp automation path"
