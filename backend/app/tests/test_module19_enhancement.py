"""Module 19 Enhancement — email retry and notification_preference honoring.

See services/notification_service.retry_email_notification and
services/communication_service._preference_blocks_channel.
"""
from .conftest import auth_headers, create_user


def _make_branch(admin_client, name="Enh Branch"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_device_model(admin_client, name="Enh Monitor"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


def _create_draft_requirement(admin_client, client, db_session, branch_id, model_id, *, tl_email):
    create_user(db_session, tl_email, "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, tl_email, "Password123!")
    created = client.post(
        "/api/v1/requirements",
        json={
            "branch_id": branch_id, "reason": "Module 19 enhancement test",
            "items": [{"item_type": "DEVICE_MODEL", "device_model_id": model_id, "requested_quantity": 1}],
        },
        headers=tl_headers,
    )
    assert created.status_code == 201, created.text
    return created.json(), tl_headers


def _make_rule(admin_client, *, event, channel="EMAIL", receiver_email=None, receiver_whatsapp_number=None, code=None):
    payload = {
        "code": code or f"{event}_{channel}_enh",
        "channel": channel,
        "event": event,
        "body_template": "Update on {{requirement_number}}: {{status}}",
        "is_active": True,
        "receiver_email": receiver_email,
        "receiver_whatsapp_number": receiver_whatsapp_number,
        "sender_name": "Biomedical Dept",
        "sender_email": "biomed@example.com",
    }
    response = admin_client.post("/api/v1/notifications/templates", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Email retry
# ---------------------------------------------------------------------------

def _failed_email_notification(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Retry Branch")
    model = _make_device_model(admin_client, "Retry Monitor")
    _make_rule(admin_client, event="REQUIREMENT_SUBMITTED", receiver_email="real@example.com", code="RETRY_RULE")
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="retry.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)
    notifications = admin_client.get("/api/v1/notifications", params={"related_requirement_id": req["id"]}).json()
    notification = notifications["items"][0]
    approved = admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    assert approved.json()["status"] == "FAILED"
    return approved.json()


def test_retry_reflects_real_smtp_outcome(admin_client, client, db_session):
    failed = _failed_email_notification(admin_client, client, db_session)
    retried = admin_client.post(f"/api/v1/notifications/{failed['id']}/retry")
    assert retried.status_code == 200, retried.text
    body = retried.json()
    # SMTP is still unconfigured in the test environment — retry must reflect that
    # honestly, not fake a SENT.
    assert body["status"] == "FAILED"
    assert "SMTP" in body["failure_reason"]


def test_retry_rejected_for_non_failed_notification(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Retry Branch 2")
    model = _make_device_model(admin_client, "Retry Monitor 2")
    _make_rule(admin_client, event="REQUIREMENT_SUBMITTED", receiver_email="", code="RETRY_RULE_2")
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="retry2.tl@example.com")
    # No rule matches (blank receiver) so create a manual, still-PENDING_APPROVAL EMAIL notification instead.
    created = admin_client.post("/api/v1/notifications", json={"recipient": "x@example.com", "channel": "EMAIL", "event": "PM_DUE"})
    notif = created.json()
    admin_client.post(f"/api/v1/notifications/{notif['id']}/submit")
    response = admin_client.post(f"/api/v1/notifications/{notif['id']}/retry")
    assert response.status_code == 409


def test_retry_rejected_for_whatsapp_channel(admin_client):
    created = admin_client.post("/api/v1/notifications", json={"recipient": "+91 90000 00001", "channel": "WHATSAPP", "event": "PM_DUE"})
    notif = created.json()
    admin_client.post(f"/api/v1/notifications/{notif['id']}/submit")
    admin_client.post(f"/api/v1/notifications/{notif['id']}/approve")
    admin_client.post(f"/api/v1/notifications/{notif['id']}/whatsapp/mark-failed", json={"failure_reason": "x"})
    response = admin_client.post(f"/api/v1/notifications/{notif['id']}/retry")
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# notification_preference honoring
# ---------------------------------------------------------------------------

def test_whatsapp_dispatch_skipped_when_user_prefers_email_only(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Pref Branch")
    model = _make_device_model(admin_client, "Pref Monitor")
    create_user(db_session, "pref.tl@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, "pref.tl@example.com", "Password123!")

    # Give the requester a WhatsApp number but leave notification_preference at its
    # default (EMAIL) — a rule targeting their WhatsApp number should now be skipped.
    client.patch("/api/v1/auth/me", json={"whatsapp_number": "+91 91234 56789"}, headers=tl_headers)

    # receiver_whatsapp_number must resolve to the literal number for the match to
    # apply — use a rule with a fixed value equal to the requester's stored number.
    _make_rule(admin_client, event="REQUIREMENT_APPROVED", channel="WHATSAPP",
               receiver_whatsapp_number="+91 91234 56789", code="PREF_WA_RULE")

    created = client.post(
        "/api/v1/requirements",
        json={"branch_id": branch["id"], "reason": "Preference test",
              "items": [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}]},
        headers=tl_headers,
    )
    req = created.json()
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl_headers)
    approved = admin_client.post(f"/api/v1/requirements/{req['id']}/approve", json={})
    assert approved.status_code == 200, approved.text

    notifications = admin_client.get(
        "/api/v1/notifications", params={"related_requirement_id": req["id"], "channel": "WHATSAPP"}
    ).json()
    matching = [n for n in notifications["items"] if n["recipient"] == "+91 91234 56789"]
    assert matching, "expected a notification row even though the send was skipped (visibility over silence)"
    assert matching[0]["status"] == "FAILED"
    assert "notification_preference" in matching[0]["failure_reason"]


def test_email_dispatch_unaffected_for_default_preference(admin_client, client, db_session):
    """Regression guard: the default notification_preference (EMAIL) must not block
    EMAIL dispatch for the common case."""
    branch = _make_branch(admin_client, "Pref Branch 2")
    model = _make_device_model(admin_client, "Pref Monitor 2")
    _make_rule(admin_client, event="REQUIREMENT_SUBMITTED", channel="EMAIL",
               receiver_email="{{requester_email}}", code="PREF_EMAIL_RULE")
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="pref2.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)

    notifications = admin_client.get(
        "/api/v1/notifications", params={"related_requirement_id": req["id"], "channel": "EMAIL"}
    ).json()
    matching = [n for n in notifications["items"] if n["recipient"] == "pref2.tl@example.com"]
    assert matching
    assert matching[0]["status"] == "PENDING_APPROVAL"
