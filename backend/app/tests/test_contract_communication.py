"""Contract communication: events, context rendering, recipients, auto-send, expiry scan.

Contracts previously fired no notification events. These tests pin the Phase 1 event
family end to end: the business action fires the right event, the template renders real
database values (including physical Asset numbers), recipients resolve only from stored
records, auto-send drives the existing senders, and a delivery failure never touches the
contract itself.

Deliberately NOT asserted here: that a message actually reached WhatsApp or a mailbox.
That needs a linked WhatsApp Web session and a configured SMTP account, neither of which
a test can honestly stand in for — see the separate external-delivery reporting.
"""
from datetime import date, timedelta

from app.models.notifications import Notification, NotificationTemplate
from app.schemas.notification import (
    CONTRACT_EVENTS,
    EVENT_AMC_RENEWED,
    EVENT_CONTRACT_ASSET_ADDED,
    EVENT_CONTRACT_ASSET_REMOVED,
    EVENT_CONTRACT_CREATED,
    EVENT_CONTRACT_EXPIRED,
    EVENT_CONTRACT_EXPIRING,
    EVENT_CONTRACT_STATUS_CHANGED,
    EVENT_WARRANTY_RENEWED,
)
from app.services import contract_service

TODAY = date.today()


def _iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


# ---- fixtures ----

def _branch(admin_client, name):
    r = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"})
    assert r.status_code == 201, r.text
    return r.json()


def _model(admin_client, name="Comms Monitor"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


def _asset(admin_client, model_id, branch_id, asset_number=None, status="OPERATIONAL"):
    payload = {"device_model_id": model_id, "branch_id": branch_id, "status": status}
    if asset_number is not None:
        payload["asset_number"] = asset_number
    r = admin_client.post("/api/v1/assets", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _vendor(admin_client, name="Comms Vendor", email="vendor@example.com", phone="+919000000123"):
    r = admin_client.post(
        "/api/v1/vendors",
        json={"name": name, "vendor_type": "SERVICE", "status": "ACTIVE", "email": email, "phone": phone},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _rule(db_session, *, event, channel="WHATSAPP", auto_send=False, body=None, code=None,
          receiver_whatsapp="{{vendor_phone}}", receiver_email="{{vendor_email}}"):
    rule = NotificationTemplate(
        code=code or f"T_{event}_{channel}_{auto_send}",
        channel=channel,
        event=event,
        subject_template="{{contract_type}} {{contract_number}}" if channel == "EMAIL" else None,
        body_template=body or (
            "{{contract_type}} {{contract_number}} | {{device_name}} | {{asset_number}} | "
            "{{branch_name}} | {{vendor_name}} | {{contract_end}} | {{expiry_days}}"
        ),
        is_active=True,
        auto_send=auto_send,
        receiver_email=receiver_email,
        receiver_whatsapp_number=receiver_whatsapp,
    )
    db_session.add(rule)
    db_session.commit()
    return rule


def _contract(admin_client, vendor_id, asset_ids, *, ctype="AMC", end=None, number="AMC-001"):
    r = admin_client.post("/api/v1/contracts", json={
        "contract_type": ctype, "vendor_id": vendor_id, "asset_ids": asset_ids,
        "contract_number": number, "start_date": _iso(-30), "end_date": end or _iso(365),
        "status": "ACTIVE",
    })
    assert r.status_code == 201, r.text
    return r.json()


def _notifications(db_session, event):
    return db_session.query(Notification).filter(Notification.event == event).all()


# ---- catalog ----

def test_all_contract_events_are_configurable(admin_client):
    """Every contract event must be offered to admins in the rule editor, otherwise it
    can never be turned on."""
    events = admin_client.get("/api/v1/notifications/events").json()
    codes = {e["code"] for e in events}
    for event in CONTRACT_EVENTS:
        assert event in codes, f"{event} missing from the configurable event list"


# ---- creation ----

def test_contract_created_fires_and_renders_live_data(admin_client, db_session):
    branch = _branch(admin_client, "Comms Branch A")
    model = _model(admin_client, "Ventilator X")
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-AST-001")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_CREATED)

    _contract(admin_client, vendor["id"], [asset["id"]], end=_iso(200))

    rows = _notifications(db_session, EVENT_CONTRACT_CREATED)
    assert len(rows) == 1
    body = rows[0].rendered_body
    assert "Ventilator X" in body, body
    assert "COMM-AST-001" in body, body
    assert "Comms Branch A" in body, body
    assert "Comms Vendor" in body, body
    assert _iso(200) in body, body
    assert rows[0].recipient == "+919000000123", "recipient must come from the vendor record"


def test_no_rule_means_no_notification(admin_client, db_session):
    """A contract event with nothing configured is silent — no rule, no message."""
    branch = _branch(admin_client, "Comms Silent")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-SILENT-1")
    vendor = _vendor(admin_client, "Silent Vendor")

    _contract(admin_client, vendor["id"], [asset["id"]])
    assert _notifications(db_session, EVENT_CONTRACT_CREATED) == []


def test_asset_number_absent_renders_readable_text(admin_client, db_session):
    """asset_number is nullable — the message must say so, not leave a gap."""
    branch = _branch(admin_client, "Comms NoNum")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"])
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_CREATED)

    _contract(admin_client, vendor["id"], [asset["id"]])
    body = _notifications(db_session, EVENT_CONTRACT_CREATED)[0].rendered_body
    assert "Asset number not assigned" in body, body


# ---- renewal ----

def test_extending_end_date_fires_the_type_specific_renewal(admin_client, db_session):
    branch = _branch(admin_client, "Comms Renew")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-RENEW-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_AMC_RENEWED)

    contract = _contract(admin_client, vendor["id"], [asset["id"]], end=_iso(10))
    r = admin_client.patch(f"/api/v1/contracts/{contract['id']}", json={"end_date": _iso(400)})
    assert r.status_code == 200, r.text

    rows = _notifications(db_session, EVENT_AMC_RENEWED)
    assert len(rows) == 1, "extending coverage is a renewal"
    assert _iso(400) in rows[0].rendered_body


def test_warranty_renewal_uses_the_warranty_event(admin_client, db_session):
    branch = _branch(admin_client, "Comms War")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-WAR-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_WARRANTY_RENEWED)

    contract = _contract(admin_client, vendor["id"], [asset["id"]], ctype="WARRANTY",
                          end=_iso(5), number="WAR-001")
    admin_client.patch(f"/api/v1/contracts/{contract['id']}", json={"end_date": _iso(300)})
    assert len(_notifications(db_session, EVENT_WARRANTY_RENEWED)) == 1


def test_trivial_edit_is_not_a_renewal(admin_client, db_session):
    """Changing a contact name has no commercial meaning and must notify nobody."""
    branch = _branch(admin_client, "Comms Trivial")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-TRIV-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_AMC_RENEWED)

    contract = _contract(admin_client, vendor["id"], [asset["id"]], end=_iso(100))
    admin_client.patch(f"/api/v1/contracts/{contract['id']}", json={"contact_person": "Someone Else"})
    assert _notifications(db_session, EVENT_AMC_RENEWED) == []


def test_shortening_coverage_is_not_a_renewal(admin_client, db_session):
    branch = _branch(admin_client, "Comms Shorten")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-SHORT-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_AMC_RENEWED)

    contract = _contract(admin_client, vendor["id"], [asset["id"]], end=_iso(300))
    admin_client.patch(f"/api/v1/contracts/{contract['id']}", json={"end_date": _iso(100)})
    assert _notifications(db_session, EVENT_AMC_RENEWED) == []


def test_status_change_fires_its_own_event(admin_client, db_session):
    branch = _branch(admin_client, "Comms Status")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-ST-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_STATUS_CHANGED)

    contract = _contract(admin_client, vendor["id"], [asset["id"]])
    r = admin_client.post(f"/api/v1/contracts/{contract['id']}/deactivate")
    assert r.status_code == 200, r.text
    assert len(_notifications(db_session, EVENT_CONTRACT_STATUS_CHANGED)) == 1


# ---- covered asset add / remove ----

def test_adding_a_covered_asset_fires_and_names_only_that_asset(admin_client, db_session):
    branch = _branch(admin_client, "Comms Add")
    model = _model(admin_client, "Syringe Pump")
    first = _asset(admin_client, model["id"], branch["id"], "COMM-ADD-1")
    second = _asset(admin_client, model["id"], branch["id"], "COMM-ADD-2")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_ASSET_ADDED)

    contract = _contract(admin_client, vendor["id"], [first["id"]])
    r = admin_client.post(f"/api/v1/contracts/{contract['id']}/assets", json={"asset_ids": [second["id"]]})
    assert r.status_code == 200, r.text
    assert set(r.json()["asset_ids"]) == {first["id"], second["id"]}

    body = _notifications(db_session, EVENT_CONTRACT_ASSET_ADDED)[0].rendered_body
    assert "COMM-ADD-2" in body, body
    assert "COMM-ADD-1" not in body, "the message should name the asset that changed"


def test_removing_a_covered_asset_fires(admin_client, db_session):
    branch = _branch(admin_client, "Comms Rem")
    model = _model(admin_client)
    a1 = _asset(admin_client, model["id"], branch["id"], "COMM-REM-1")
    a2 = _asset(admin_client, model["id"], branch["id"], "COMM-REM-2")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_ASSET_REMOVED)

    contract = _contract(admin_client, vendor["id"], [a1["id"], a2["id"]])
    r = admin_client.post(f"/api/v1/contracts/{contract['id']}/assets/remove", json={"asset_ids": [a2["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["asset_ids"] == [a1["id"]]

    body = _notifications(db_session, EVENT_CONTRACT_ASSET_REMOVED)[0].rendered_body
    assert "COMM-REM-2" in body, body


def test_cannot_add_a_condemned_asset_to_an_existing_contract(admin_client, db_session):
    """The creation-time eligibility rule applies equally to later additions."""
    branch = _branch(admin_client, "Comms AddBad")
    model = _model(admin_client)
    good = _asset(admin_client, model["id"], branch["id"], "COMM-AB-1")
    bad = _asset(admin_client, model["id"], branch["id"], "COMM-AB-2", status="CONDEMNED")
    vendor = _vendor(admin_client)

    contract = _contract(admin_client, vendor["id"], [good["id"]])
    r = admin_client.post(f"/api/v1/contracts/{contract['id']}/assets", json={"asset_ids": [bad["id"]]})
    assert r.status_code == 422, r.text
    assert "CONDEMNED" in r.text


def test_warranty_cannot_be_extended_with_more_assets(admin_client):
    branch = _branch(admin_client, "Comms WarAdd")
    model = _model(admin_client)
    a1 = _asset(admin_client, model["id"], branch["id"], "COMM-WA-1")
    a2 = _asset(admin_client, model["id"], branch["id"], "COMM-WA-2")
    vendor = _vendor(admin_client)

    contract = _contract(admin_client, vendor["id"], [a1["id"]], ctype="WARRANTY", number="WAR-XX")
    r = admin_client.post(f"/api/v1/contracts/{contract['id']}/assets", json={"asset_ids": [a2["id"]]})
    assert r.status_code == 422, r.text


# ---- recipients ----

def test_vendor_without_contact_fails_instead_of_sending_anywhere(admin_client, db_session):
    """No recipient resolved must FAIL the notification — never fall back to some other
    address."""
    branch = _branch(admin_client, "Comms NoContact")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-NC-1")
    vendor = _vendor(admin_client, "No Contact Vendor", email=None, phone=None)
    _rule(db_session, event=EVENT_CONTRACT_CREATED)

    _contract(admin_client, vendor["id"], [asset["id"]])
    rows = _notifications(db_session, EVENT_CONTRACT_CREATED)
    assert rows[0].status == "FAILED"
    assert "No recipient address resolved" in rows[0].failure_reason


def test_email_rule_resolves_the_vendor_email(admin_client, db_session):
    branch = _branch(admin_client, "Comms Mail")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-MAIL-1")
    vendor = _vendor(admin_client, "Mail Vendor", email="ops@vendor.example")
    _rule(db_session, event=EVENT_CONTRACT_CREATED, channel="EMAIL", code="MAIL_CREATED")

    _contract(admin_client, vendor["id"], [asset["id"]])
    rows = _notifications(db_session, EVENT_CONTRACT_CREATED)
    assert rows[0].recipient == "ops@vendor.example"
    assert rows[0].rendered_subject is not None


# ---- auto-send ----

def test_auto_send_off_keeps_the_approval_workflow(admin_client, db_session, monkeypatch):
    branch = _branch(admin_client, "Comms AsOff")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-AO-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_CREATED, auto_send=False)

    import app.services.whatsapp_service as ws

    attempts = []
    monkeypatch.setattr(ws, "attempt_automation_send", lambda db, n: attempts.append(n))

    _contract(admin_client, vendor["id"], [asset["id"]])
    rows = _notifications(db_session, EVENT_CONTRACT_CREATED)
    assert rows[0].status == "PENDING_APPROVAL"
    assert attempts == []


def test_auto_send_on_invokes_the_existing_whatsapp_sender(admin_client, db_session, monkeypatch):
    branch = _branch(admin_client, "Comms AsOn")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-AN-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_CREATED, auto_send=True)

    import app.services.whatsapp_service as ws

    approved, attempted = [], []
    monkeypatch.setattr(ws, "approve_whatsapp_notification",
                        lambda db, n: (approved.append(n), n)[1])
    monkeypatch.setattr(ws, "attempt_automation_send",
                        lambda db, n: (attempted.append(n), setattr(n, "status", "SENT"), n)[2])

    _contract(admin_client, vendor["id"], [asset["id"]])
    assert len(approved) == 1 and len(attempted) == 1
    assert _notifications(db_session, EVENT_CONTRACT_CREATED)[0].status == "SENT"


def test_auto_send_email_calls_the_smtp_provider(admin_client, db_session, monkeypatch):
    branch = _branch(admin_client, "Comms AsMail")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-AM-1")
    vendor = _vendor(admin_client, "SMTP Vendor", email="smtp@vendor.example")
    _rule(db_session, event=EVENT_CONTRACT_CREATED, channel="EMAIL", auto_send=True, code="MAIL_AUTO")

    import app.services.notification_service as ns

    sent = []
    monkeypatch.setattr(ns, "send_email_smtp", lambda **kw: sent.append(kw))

    _contract(admin_client, vendor["id"], [asset["id"]])
    assert len(sent) == 1, "auto-send EMAIL must go through the existing SMTP provider"
    assert sent[0]["to_email"] == "smtp@vendor.example"
    assert "AMC-001" in sent[0]["subject"]
    assert _notifications(db_session, EVENT_CONTRACT_CREATED)[0].status == "SENT"


def test_delivery_failure_does_not_roll_back_the_contract(admin_client, db_session, monkeypatch):
    """The contract really was created. A disconnected WhatsApp session is a delivery
    problem, not a reason to undo it."""
    branch = _branch(admin_client, "Comms Fail")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-F-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_CREATED, auto_send=True)

    import app.services.whatsapp_service as ws

    def boom(db, n):
        raise RuntimeError("WhatsApp Web session is DISCONNECTED")

    monkeypatch.setattr(ws, "approve_whatsapp_notification", boom)

    contract = _contract(admin_client, vendor["id"], [asset["id"]])
    assert admin_client.get(f"/api/v1/contracts/{contract['id']}").status_code == 200

    rows = _notifications(db_session, EVENT_CONTRACT_CREATED)
    assert rows[0].status == "FAILED"
    assert "DISCONNECTED" in rows[0].failure_reason


def test_no_false_sent_when_evidence_is_missing(admin_client, db_session, monkeypatch):
    """The honesty contract: without DOM evidence the status must not become SENT."""
    branch = _branch(admin_client, "Comms Eviden")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-E-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_CREATED, auto_send=True)

    import app.services.whatsapp_service as ws

    def unconfirmed(db, n):
        n.whatsapp_status = "REQUIRES_USER_CONFIRMATION"
        return n

    monkeypatch.setattr(ws, "approve_whatsapp_notification", lambda db, n: n)
    monkeypatch.setattr(ws, "attempt_automation_send", unconfirmed)

    _contract(admin_client, vendor["id"], [asset["id"]])
    row = _notifications(db_session, EVENT_CONTRACT_CREATED)[0]
    assert row.status != "SENT", "no evidence must never be reported as delivered"


# ---- expiry scan ----

def test_scan_fires_expiring_for_a_contract_inside_the_window(admin_client, db_session):
    branch = _branch(admin_client, "Comms Exp")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-EXP-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_EXPIRING)

    _contract(admin_client, vendor["id"], [asset["id"]], end=_iso(5))

    summary = contract_service.scan_contract_expiry(db_session)
    assert summary["expiring_events"] == 1, summary
    rows = _notifications(db_session, EVENT_CONTRACT_EXPIRING)
    assert len(rows) == 1
    assert "5" in rows[0].rendered_body, rows[0].rendered_body


def test_scan_is_idempotent_across_repeated_runs(admin_client, db_session):
    """The whole point of the dedup key: Task Scheduler runs this daily and the same
    contract in the same reminder window must not message anyone twice."""
    branch = _branch(admin_client, "Comms Idem")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-ID-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_EXPIRING)

    _contract(admin_client, vendor["id"], [asset["id"]], end=_iso(3))

    first = contract_service.scan_contract_expiry(db_session)
    second = contract_service.scan_contract_expiry(db_session)
    third = contract_service.scan_contract_expiry(db_session)

    assert first["notifications_created"] == 1
    assert second["notifications_created"] == 0
    assert third["notifications_created"] == 0
    assert len(_notifications(db_session, EVENT_CONTRACT_EXPIRING)) == 1


def test_scan_advances_through_the_reminder_ladder(admin_client, db_session):
    """A separate reminder is legitimate once a tighter window is crossed — 30 days out
    and 7 days out are different messages, not a duplicate."""
    branch = _branch(admin_client, "Comms Ladder")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-LAD-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_EXPIRING)

    _contract(admin_client, vendor["id"], [asset["id"]], end=_iso(20))

    # 20 days out -> crosses the 30-day window.
    contract_service.scan_contract_expiry(db_session, today=TODAY)
    assert len(_notifications(db_session, EVENT_CONTRACT_EXPIRING)) == 1

    # Same window a week later -> still nothing new.
    contract_service.scan_contract_expiry(db_session, today=TODAY + timedelta(days=7))
    assert len(_notifications(db_session, EVENT_CONTRACT_EXPIRING)) == 1

    # 5 days out -> crosses the 7-day window, a genuinely new reminder.
    contract_service.scan_contract_expiry(db_session, today=TODAY + timedelta(days=15))
    assert len(_notifications(db_session, EVENT_CONTRACT_EXPIRING)) == 2


def test_scan_fires_expired_once_past_the_end_date(admin_client, db_session):
    branch = _branch(admin_client, "Comms Gone")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-G-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_EXPIRED)

    _contract(admin_client, vendor["id"], [asset["id"]], end=_iso(-2))

    summary = contract_service.scan_contract_expiry(db_session)
    assert summary["expired_events"] == 1, summary
    contract_service.scan_contract_expiry(db_session)
    assert len(_notifications(db_session, EVENT_CONTRACT_EXPIRED)) == 1, "expired must not repeat daily"


def test_scan_ignores_contracts_far_from_expiry(admin_client, db_session):
    branch = _branch(admin_client, "Comms Far")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-FAR-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_EXPIRING)

    _contract(admin_client, vendor["id"], [asset["id"]], end=_iso(300))
    summary = contract_service.scan_contract_expiry(db_session)
    assert summary["expiring_events"] == 0
    assert _notifications(db_session, EVENT_CONTRACT_EXPIRING) == []


def test_scan_skips_cancelled_contracts(admin_client, db_session):
    branch = _branch(admin_client, "Comms Cancelled")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "COMM-C-1")
    vendor = _vendor(admin_client)
    _rule(db_session, event=EVENT_CONTRACT_EXPIRING)

    contract = _contract(admin_client, vendor["id"], [asset["id"]], end=_iso(3))
    admin_client.patch(f"/api/v1/contracts/{contract['id']}", json={"status": "CANCELLED"})

    contract_service.scan_contract_expiry(db_session)
    assert _notifications(db_session, EVENT_CONTRACT_EXPIRING) == []


def test_scan_endpoint_requires_permission_and_returns_a_summary(admin_client):
    r = admin_client.post("/api/v1/contracts/run-expiry-scan")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("scanned", "expiring_events", "expired_events", "notifications_created"):
        assert key in body, body


def test_scan_endpoint_is_rejected_without_contract_write(client, db_session):
    from .conftest import auth_headers, create_user

    create_user(db_session, "scan.viewer@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "scan.viewer@example.com", "Password123!")
    assert client.post("/api/v1/contracts/run-expiry-scan", headers=headers).status_code == 403


# ---- seeded templates ----

def test_seeding_is_idempotent_and_ships_rules_disabled(admin_client, db_session):
    first = admin_client.post("/api/v1/notifications/templates/seed-defaults")
    assert first.status_code == 200, first.text
    created = first.json()["created"]
    assert created > 0

    second = admin_client.post("/api/v1/notifications/templates/seed-defaults")
    assert second.json()["created"] == 0, "re-seeding must not duplicate rules"

    seeded = db_session.query(NotificationTemplate).filter(
        NotificationTemplate.code.like("CONTRACT_%")
    ).all()
    assert seeded, "contract defaults should exist after seeding"
    for rule in seeded:
        assert rule.is_active is False, f"{rule.code} must ship disabled"
        assert rule.auto_send is False, f"{rule.code} must ship non-auto-send"
