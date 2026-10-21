from app.models.audit import AuditLog
from app.models.notifications import Notification
from app.services import communication_service

from .conftest import auth_headers, create_user


def _make_branch(admin_client, name="Comm Branch"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_device_model(admin_client, name="Comm Monitor"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


def _create_draft_requirement(admin_client, client, db_session, branch_id, model_id, *, tl_email):
    create_user(db_session, tl_email, "HOSPITAL_UNIT_MANAGER", "Password123!")
    tl_headers = auth_headers(client, tl_email, "Password123!")
    created = client.post(
        "/api/v1/requirements",
        json={
            "branch_id": branch_id, "reason": "Communication automation test",
            "items": [{"item_type": "DEVICE_MODEL", "device_model_id": model_id, "requested_quantity": 1}],
        },
        headers=tl_headers,
    )
    assert created.status_code == 201, created.text
    return created.json(), tl_headers


def _make_rule(admin_client, *, event, channel="EMAIL", receiver_email=None, receiver_whatsapp_number=None,
                receiver_name=None, subject_template=None, body_template="Update on {{requirement_number}}: {{status}}",
                is_active=True, code=None):
    payload = {
        "code": code or f"{event}_{channel}",
        "channel": channel,
        "event": event,
        "body_template": body_template,
        "is_active": is_active,
        "receiver_name": receiver_name,
        "receiver_email": receiver_email,
        "receiver_whatsapp_number": receiver_whatsapp_number,
        "sender_name": "Biomedical Dept",
        "sender_email": "biomed@example.com",
    }
    if subject_template is not None:
        payload["subject_template"] = subject_template
    response = admin_client.post("/api/v1/notifications/templates", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _engineer_headers(client, db_session, email="comm.eng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def test_render_template_substitutes_known_variables():
    context = {"requirement_number": "REQ-000999", "status": "SUBMITTED"}
    rendered = communication_service.render_template("Request {{requirement_number}} is now {{status}}.", context)
    assert rendered == "Request REQ-000999 is now SUBMITTED."


def test_render_template_leaves_unknown_token_literal():
    rendered = communication_service.render_template("Hello {{not_a_real_variable}}", {"requirement_number": "REQ-1"})
    assert "{{not_a_real_variable}}" in rendered


def test_preview_with_sample_data(admin_client):
    response = admin_client.post(
        "/api/v1/notifications/templates/preview",
        json={
            "channel": "EMAIL", "event": "REQUIREMENT_SUBMITTED",
            "subject_template": "New request {{requirement_number}}",
            "body_template": "Hospital: {{hospital}}, Branch: {{branch}}, Priority: {{priority}}",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["used_sample_data"] is True
    assert "REQ-000123" in body["rendered_subject"]
    assert "Sample Hospital Network" in body["rendered_body"]


def test_preview_with_real_requirement_resolves_real_data(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Preview Branch")
    model = _make_device_model(admin_client, "Preview Monitor")
    requirement, _ = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="preview.tl@example.com")

    response = admin_client.post(
        "/api/v1/notifications/templates/preview",
        json={
            "channel": "EMAIL", "event": "REQUIREMENT_SUBMITTED",
            "body_template": "{{requirement_number}} from {{branch}}",
            "requirement_id": requirement["id"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["used_sample_data"] is False
    assert requirement["request_number"] in body["rendered_body"]
    assert "Preview Branch" in body["rendered_body"]


def test_preview_does_not_create_notification(admin_client, db_session):
    before = db_session.query(Notification).count()
    admin_client.post(
        "/api/v1/notifications/templates/preview",
        json={"channel": "EMAIL", "event": "REQUIREMENT_SUBMITTED", "body_template": "{{requirement_number}}"},
    )
    after = db_session.query(Notification).count()
    assert after == before


def test_preview_requires_notification_approve(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/notifications/templates/preview",
        json={"channel": "EMAIL", "event": "REQUIREMENT_SUBMITTED", "body_template": "x"},
        headers=headers,
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Event generation
# ---------------------------------------------------------------------------

def test_requirement_submitted_fires_configured_rule_with_dynamic_recipient(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Submit Branch")
    model = _make_device_model(admin_client, "Submit Monitor")
    _make_rule(
        admin_client, event="REQUIREMENT_SUBMITTED", receiver_email="{{requester_email}}",
        body_template="{{requirement_number}} submitted by {{requester_name}}",
        code="SUBMIT_RULE",
    )
    requirement, tl_headers = _create_draft_requirement(
        admin_client, client, db_session, branch["id"], model["id"], tl_email="submit.tl@example.com",
    )
    response = client.post(f"/api/v1/requirements/{requirement['id']}/submit", headers=tl_headers)
    assert response.status_code == 200, response.text

    notifications = admin_client.get(
        "/api/v1/notifications", params={"related_requirement_id": requirement["id"], "event": "REQUIREMENT_SUBMITTED"}
    ).json()
    assert notifications["total"] == 1
    notification = notifications["items"][0]
    assert notification["recipient"] == "submit.tl@example.com"
    assert notification["channel"] == "EMAIL"
    assert requirement["request_number"] in notification["rendered_body"]
    assert notification["status"] == "PENDING_APPROVAL"


def test_requirement_approved_and_rejected_fire_distinct_rules(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Decision Branch")
    model = _make_device_model(admin_client, "Decision Monitor")
    _make_rule(admin_client, event="REQUIREMENT_APPROVED", receiver_email="approved-notify@example.com", code="APPROVE_RULE")
    _make_rule(admin_client, event="REQUIREMENT_REJECTED", receiver_email="rejected-notify@example.com", code="REJECT_RULE")

    req1, tl1 = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="dec.tl1@example.com")
    client.post(f"/api/v1/requirements/{req1['id']}/submit", headers=tl1)
    admin_client.post(f"/api/v1/requirements/{req1['id']}/approve", json={})

    req2, tl2 = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="dec.tl2@example.com")
    client.post(f"/api/v1/requirements/{req2['id']}/submit", headers=tl2)
    admin_client.post(f"/api/v1/requirements/{req2['id']}/reject", json={"remarks": "no budget"})

    approved_notifs = admin_client.get("/api/v1/notifications", params={"related_requirement_id": req1["id"], "event": "REQUIREMENT_APPROVED"}).json()
    rejected_notifs = admin_client.get("/api/v1/notifications", params={"related_requirement_id": req2["id"], "event": "REQUIREMENT_REJECTED"}).json()
    assert approved_notifs["total"] == 1
    assert approved_notifs["items"][0]["recipient"] == "approved-notify@example.com"
    assert rejected_notifs["total"] == 1
    assert rejected_notifs["items"][0]["recipient"] == "rejected-notify@example.com"


def test_fulfillment_lifecycle_fires_every_stage_event(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Lifecycle Branch")
    model = _make_device_model(admin_client, "Lifecycle Monitor")
    for event in [
        "FULFILLMENT_CREATED", "FULFILLMENT_UNDER_PROCESS", "FULFILLMENT_PACKED",
        "FULFILLMENT_OUT_FOR_DELIVERY", "FULFILLMENT_DELIVERED", "FULFILLMENT_COMPLETED",
    ]:
        _make_rule(admin_client, event=event, receiver_email="ops@example.com", code=f"{event}_RULE")

    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="lc.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)
    admin_client.post(f"/api/v1/requirements/{req['id']}/approve", json={})

    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": req["id"]}).json()
    fid = fulfillment["id"]
    item_id = fulfillment["items"][0]["requirement_item_id"]
    # Serialized item — need an asset to allocate.
    asset = admin_client.post(
        "/api/v1/assets", json={"asset_number": "LC-AST-001", "device_model_id": model["id"], "branch_id": branch["id"]}
    ).json()
    admin_client.post(
        f"/api/v1/fulfillments/{fid}/allocate",
        json={"items": [{"requirement_item_id": item_id, "asset_ids": [asset["id"]]}]},
    )
    admin_client.post(f"/api/v1/fulfillments/{fid}/process")
    admin_client.post(f"/api/v1/fulfillments/{fid}/pack")
    admin_client.post(f"/api/v1/fulfillments/{fid}/out-for-delivery")
    admin_client.post(f"/api/v1/fulfillments/{fid}/deliver", json={})
    admin_client.post(f"/api/v1/fulfillments/{fid}/complete")

    history = admin_client.get("/api/v1/notifications", params={"related_fulfillment_id": fid, "page_size": 50}).json()
    fired_events = {n["event"] for n in history["items"]}
    assert fired_events == {
        "FULFILLMENT_CREATED", "FULFILLMENT_UNDER_PROCESS", "FULFILLMENT_PACKED",
        "FULFILLMENT_OUT_FOR_DELIVERY", "FULFILLMENT_DELIVERED", "FULFILLMENT_COMPLETED",
    }
    for n in history["items"]:
        assert n["related_requirement_id"] == req["id"]


def test_expected_delivery_date_change_fires_event(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Date Branch")
    model = _make_device_model(admin_client, "Date Monitor")
    _make_rule(
        admin_client, event="FULFILLMENT_DELIVERY_DATE_CHANGED", receiver_email="dates@example.com", code="DATE_RULE",
        body_template="Expected delivery for {{fulfillment_number}} is now {{expected_delivery_date}}",
    )

    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="date.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)
    admin_client.post(f"/api/v1/requirements/{req['id']}/approve", json={})
    fulfillment = admin_client.post(
        "/api/v1/fulfillments", json={"requirement_id": req["id"], "expected_delivery_date": "2026-09-01"}
    ).json()
    item_id = fulfillment["items"][0]["requirement_item_id"]
    asset = admin_client.post(
        "/api/v1/assets", json={"asset_number": "DATE-AST-001", "device_model_id": model["id"], "branch_id": branch["id"]}
    ).json()

    # First allocate keeps the same date — should not fire (no real change).
    admin_client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": item_id, "asset_ids": [asset["id"]]}], "expected_delivery_date": "2026-09-01"},
    )
    no_change = admin_client.get("/api/v1/notifications", params={"related_fulfillment_id": fulfillment["id"], "event": "FULFILLMENT_DELIVERY_DATE_CHANGED"}).json()
    assert no_change["total"] == 0

    # A real date change fires the event.
    admin_client.post(
        f"/api/v1/fulfillments/{fulfillment['id']}/allocate",
        json={"items": [{"requirement_item_id": item_id, "asset_ids": [asset["id"]]}], "expected_delivery_date": "2026-09-15"},
    )
    changed = admin_client.get("/api/v1/notifications", params={"related_fulfillment_id": fulfillment["id"], "event": "FULFILLMENT_DELIVERY_DATE_CHANGED"}).json()
    assert changed["total"] == 1
    assert "2026-09-15" in changed["items"][0]["rendered_body"]


def test_no_rule_configured_creates_no_notification(admin_client, client, db_session):
    branch = _make_branch(admin_client, "NoRule Branch")
    model = _make_device_model(admin_client, "NoRule Monitor")
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="norule.tl@example.com")
    response = client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)
    assert response.status_code == 200

    notifications = admin_client.get("/api/v1/notifications", params={"related_requirement_id": req["id"]}).json()
    assert notifications["total"] == 0


def test_disabled_rule_does_not_fire(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Disabled Branch")
    model = _make_device_model(admin_client, "Disabled Monitor")
    _make_rule(admin_client, event="REQUIREMENT_SUBMITTED", receiver_email="x@example.com", is_active=False, code="DISABLED_RULE")
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="disabled.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)

    notifications = admin_client.get("/api/v1/notifications", params={"related_requirement_id": req["id"]}).json()
    assert notifications["total"] == 0


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------

def test_static_receiver_email_used_as_is(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Static Branch")
    model = _make_device_model(admin_client, "Static Monitor")
    _make_rule(admin_client, event="REQUIREMENT_SUBMITTED", receiver_email="fixed-inbox@example.com", code="STATIC_RULE")
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="static.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)

    notifications = admin_client.get("/api/v1/notifications", params={"related_requirement_id": req["id"]}).json()
    assert notifications["items"][0]["recipient"] == "fixed-inbox@example.com"


def test_whatsapp_recipient_resolution_and_url(admin_client, client, db_session):
    branch = _make_branch(admin_client, "WA Branch")
    model = _make_device_model(admin_client, "WA Monitor")
    _make_rule(
        admin_client, event="REQUIREMENT_SUBMITTED", channel="WHATSAPP",
        receiver_whatsapp_number="+91 98765 43210", body_template="New request {{requirement_number}}",
        code="WA_RULE",
    )
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="wa.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)

    notifications = admin_client.get("/api/v1/notifications", params={"related_requirement_id": req["id"], "channel": "WHATSAPP"}).json()
    assert notifications["total"] == 1
    notification = notifications["items"][0]
    assert notification["recipient"] == "+91 98765 43210"
    assert notification["status"] == "PENDING_APPROVAL"

    # Module 19: approving a WHATSAPP notification no longer jumps straight to SENT —
    # there is no way to detect a real send without browser automation (deliberately
    # not built), so it only queues the wa.me link; `status` stays PENDING_APPROVAL
    # until an explicit confirm-sent action.
    approved = admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert body["whatsapp_status"] == "QUEUED"
    assert body["whatsapp_url"] is not None
    assert body["whatsapp_url"].startswith("https://wa.me/919876543210")
    assert req["request_number"] in body["whatsapp_url"]

    confirmed = admin_client.post(f"/api/v1/notifications/{notification['id']}/whatsapp/confirm-sent")
    assert confirmed.status_code == 200, confirmed.text
    confirmed_body = confirmed.json()
    assert confirmed_body["status"] == "SENT"
    assert confirmed_body["whatsapp_status"] == "SENT"
    assert confirmed_body["whatsapp_confirmed_by"]
    # Once SENT, the wa.me link is no longer offered (already handled).
    assert confirmed_body["whatsapp_url"] is None


# ---------------------------------------------------------------------------
# Failed communication
# ---------------------------------------------------------------------------

def test_blank_resolved_recipient_creates_failed_notification(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Blank Branch")
    model = _make_device_model(admin_client, "Blank Monitor")
    _make_rule(admin_client, event="REQUIREMENT_SUBMITTED", receiver_email="{{manager_email}}", code="BLANK_RULE")
    # manager_email is only known after approval — at submit time it's blank, so the
    # resolved recipient is empty and the notification should record a failure.
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="blank.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)

    notifications = admin_client.get("/api/v1/notifications", params={"related_requirement_id": req["id"]}).json()
    assert notifications["total"] == 1
    notification = notifications["items"][0]
    assert notification["status"] == "FAILED"
    assert notification["failure_reason"]


def test_email_send_failure_when_smtp_not_configured(admin_client, client, db_session):
    branch = _make_branch(admin_client, "SMTP Branch")
    model = _make_device_model(admin_client, "SMTP Monitor")
    _make_rule(admin_client, event="REQUIREMENT_SUBMITTED", receiver_email="real@example.com", code="SMTP_RULE")
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="smtp.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)

    notifications = admin_client.get("/api/v1/notifications", params={"related_requirement_id": req["id"]}).json()
    notification = notifications["items"][0]
    assert notification["status"] == "PENDING_APPROVAL"

    approved = admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    # No SMTP host is configured in the test environment (config.py defaults to "") —
    # approving must reflect the real failure, not silently claim SENT.
    assert body["status"] == "FAILED"
    assert "SMTP" in body["failure_reason"]


# ---------------------------------------------------------------------------
# Duplicate prevention
# ---------------------------------------------------------------------------

def test_dispatch_event_twice_does_not_duplicate(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Dup Branch")
    model = _make_device_model(admin_client, "Dup Monitor")
    _make_rule(admin_client, event="REQUIREMENT_SUBMITTED", receiver_email="dup@example.com", code="DUP_RULE")
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="dup.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)

    from app.models.requirement import Requirement
    requirement_row = db_session.get(Requirement, req["id"])
    before = db_session.query(Notification).filter(Notification.related_requirement_id == req["id"]).count()
    communication_service.dispatch_event(db_session, "REQUIREMENT_SUBMITTED", requirement=requirement_row, user_id=None)
    after = db_session.query(Notification).filter(Notification.related_requirement_id == req["id"]).count()
    assert before == 1
    assert after == 1


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def test_engineer_cannot_create_communication_rule(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/notifications/templates",
        json={"code": "ENG_RULE", "channel": "EMAIL", "event": "REQUIREMENT_SUBMITTED", "body_template": "x",
              "receiver_email": "x@example.com"},
        headers=headers,
    )
    assert response.status_code == 403


def test_engineer_can_read_communication_history(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.get("/api/v1/notifications", headers=headers)
    assert response.status_code == 200


def test_doctor_cannot_read_communication_history(admin_client, client, db_session):
    create_user(db_session, "comm.doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "comm.doc@example.com", "Password123!")
    response = client.get("/api/v1/notifications", headers=headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def test_event_dispatch_writes_audit_log(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Audit Branch")
    model = _make_device_model(admin_client, "Audit Monitor")
    _make_rule(admin_client, event="REQUIREMENT_SUBMITTED", receiver_email="audit@example.com", code="AUDIT_RULE")
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="audit.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)

    notification = admin_client.get("/api/v1/notifications", params={"related_requirement_id": req["id"]}).json()["items"][0]
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "notification", AuditLog.entity_id == notification["id"]).all()
    assert any(log.action == "CREATE" for log in logs)


# ---------------------------------------------------------------------------
# Communication history
# ---------------------------------------------------------------------------

def test_communication_history_traceable_to_requirement_and_fulfillment(admin_client, client, db_session):
    branch = _make_branch(admin_client, "History Branch")
    model = _make_device_model(admin_client, "History Monitor")
    _make_rule(admin_client, event="FULFILLMENT_CREATED", receiver_email="history@example.com", code="HISTORY_RULE")
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="history.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)
    admin_client.post(f"/api/v1/requirements/{req['id']}/approve", json={})
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": req["id"]}).json()

    history = admin_client.get("/api/v1/notifications", params={"related_fulfillment_id": fulfillment["id"]}).json()
    assert history["total"] == 1
    entry = history["items"][0]
    assert entry["related_requirement_id"] == req["id"]
    assert entry["related_fulfillment_id"] == fulfillment["id"]
    assert entry["event"] == "FULFILLMENT_CREATED"


# ---------------------------------------------------------------------------
# Module 19 — cancellation events (previously notified no one)
# ---------------------------------------------------------------------------

def test_requirement_cancellation_fires_event(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Cancel Branch")
    model = _make_device_model(admin_client, "Cancel Monitor")
    _make_rule(admin_client, event="REQUIREMENT_CANCELLED", receiver_email="cancel@example.com", code="CANCEL_RULE")
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="cancel.tl@example.com")

    cancelled = client.post(f"/api/v1/requirements/{req['id']}/cancel", json={}, headers=tl)
    assert cancelled.status_code == 200, cancelled.text

    notifications = admin_client.get(
        "/api/v1/notifications", params={"related_requirement_id": req["id"], "event": "REQUIREMENT_CANCELLED"}
    ).json()
    assert notifications["total"] == 1
    assert notifications["items"][0]["recipient"] == "cancel@example.com"


def test_fulfillment_cancellation_fires_event(admin_client, client, db_session):
    from .test_fulfillments import _create_approved_requirement

    branch = _make_branch(admin_client, "Fulfillment Cancel Branch")
    model = _make_device_model(admin_client, "Fulfillment Cancel Monitor")
    _make_rule(admin_client, event="FULFILLMENT_CANCELLED", receiver_email="fcancel@example.com", code="FCANCEL_RULE")
    requirement, _ = _create_approved_requirement(
        admin_client, client, db_session, branch["id"],
        [{"item_type": "DEVICE_MODEL", "device_model_id": model["id"], "requested_quantity": 1}],
        tl_email="fcancel.tl@example.com",
    )
    fulfillment = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]}).json()

    cancelled = admin_client.post(f"/api/v1/fulfillments/{fulfillment['id']}/cancel", json={})
    assert cancelled.status_code == 200, cancelled.text

    notifications = admin_client.get(
        "/api/v1/notifications", params={"related_fulfillment_id": fulfillment["id"], "event": "FULFILLMENT_CANCELLED"}
    ).json()
    assert notifications["total"] == 1
    assert notifications["items"][0]["recipient"] == "fcancel@example.com"


# ---------------------------------------------------------------------------
# Module 19 — idempotency tightened to include template_id
# ---------------------------------------------------------------------------

def test_two_active_rules_same_event_each_get_their_own_notification(admin_client, client, db_session):
    branch = _make_branch(admin_client, "Two Rules Branch")
    model = _make_device_model(admin_client, "Two Rules Monitor")
    _make_rule(admin_client, event="REQUIREMENT_SUBMITTED", receiver_email="first@example.com", code="TWO_RULE_A")
    _make_rule(admin_client, event="REQUIREMENT_SUBMITTED", receiver_email="second@example.com", code="TWO_RULE_B")
    req, tl = _create_draft_requirement(admin_client, client, db_session, branch["id"], model["id"], tl_email="tworules.tl@example.com")
    client.post(f"/api/v1/requirements/{req['id']}/submit", headers=tl)

    notifications = admin_client.get(
        "/api/v1/notifications", params={"related_requirement_id": req["id"], "event": "REQUIREMENT_SUBMITTED"}
    ).json()
    assert notifications["total"] == 2
    recipients = {n["recipient"] for n in notifications["items"]}
    assert recipients == {"first@example.com", "second@example.com"}
