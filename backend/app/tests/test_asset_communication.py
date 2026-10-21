"""Phase 2 — physical asset / device event communications.

Pins the asset event family end to end: the real business action fires the right
event, the template renders real database values, recipients resolve only from stored
relationships, one business action produces one notification, trivial edits produce
none, branch isolation holds, auto-send drives the existing senders, and a delivery
failure never touches the asset change that triggered it.

Deliberately NOT asserted here: that a message actually reached WhatsApp or a mailbox.
That needs a linked WhatsApp Web session and a configured SMTP account, neither of
which a test can honestly stand in for — see the separate external-delivery reporting.
"""
from app.models.device import Asset
from app.models.hospital import Branch
from app.models.notifications import Notification, NotificationTemplate
from app.schemas.notification import (
    ASSET_EVENTS,
    EVENT_ASSET_ALLOCATED,
    EVENT_ASSET_CONDEMNED,
    EVENT_ASSET_CREATED,
    EVENT_ASSET_LOCATION_CHANGED,
    EVENT_ASSET_RETIRED,
    EVENT_ASSET_STATUS_CHANGED,
    EVENT_ASSET_TRANSFERRED,
)

from .conftest import auth_headers, create_user


# ---- fixtures ----

def _branch(admin_client, name, **extra):
    r = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _model(admin_client, name="Phase2 Ventilator", manufacturer="Acme"):
    r = admin_client.post("/api/v1/device-models",
                          json={"name": name, "category": "VENTILATOR", "manufacturer": manufacturer})
    assert r.status_code == 201, r.text
    return r.json()


def _asset(admin_client, model_id, branch_id, asset_number=None, status="OPERATIONAL", **extra):
    payload = {"device_model_id": model_id, "branch_id": branch_id, "status": status, **extra}
    if asset_number is not None:
        payload["asset_number"] = asset_number
    r = admin_client.post("/api/v1/assets", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _patch(admin_client, asset, **changes):
    payload = {
        "asset_number": asset.get("asset_number"),
        "serial_number": asset.get("serial_number"),
        "device_model_id": asset["device_model_id"],
        "branch_id": asset["branch_id"],
        "department_id": asset.get("department_id"),
        "location_id": asset.get("location_id"),
        "status": asset["status"],
        "criticality": asset.get("criticality"),
        "purchase_date": asset.get("purchase_date"),
        "installation_date": asset.get("installation_date"),
        "operational_since": asset.get("operational_since"),
    }
    payload.update(changes)
    r = admin_client.patch(f"/api/v1/assets/{asset['id']}", json=payload)
    return r


def _rule(db_session, *, event, channel="WHATSAPP", auto_send=False, body=None, code=None,
          receiver_whatsapp="{{unit_head_phone}}", receiver_email="{{unit_head_email}}",
          is_active=True):
    rule = NotificationTemplate(
        code=code or f"P2_{event}_{channel}_{auto_send}",
        channel=channel,
        event=event,
        subject_template="{{device_name}} {{asset_number}}" if channel == "EMAIL" else None,
        body_template=body or (
            "{{event}} | {{device_name}} | {{asset_number}} | {{branch_name}} | "
            "{{previous_status}} -> {{new_status}} | {{previous_branch}} -> {{new_branch}}"
        ),
        is_active=is_active,
        auto_send=auto_send,
        receiver_email=receiver_email,
        receiver_whatsapp_number=receiver_whatsapp,
    )
    db_session.add(rule)
    db_session.commit()
    return rule


def _unit_head(db_session, branch_id, *, email, phone="+919000000001", name=None):
    """A real Unit Head User attached to a real Branch — the relationship the
    {{unit_head_*}} recipient tokens resolve through."""
    user = create_user(db_session, email, "UNIT_HEAD")
    user.hospital_scope_id = branch_id
    user.whatsapp_number = phone
    user.notification_preference = "BOTH"
    if name:
        user.name = name
    branch = db_session.get(Branch, branch_id)
    branch.unit_head_user_id = user.id
    db_session.commit()
    return user


def _role_user(db_session, role_code, *, email, branch_id=None, phone=None):
    user = create_user(db_session, email, role_code)
    user.hospital_scope_id = branch_id
    user.whatsapp_number = phone
    user.notification_preference = "BOTH"
    db_session.commit()
    return user


def _notifications(db_session, event):
    return db_session.query(Notification).filter(Notification.event == event).all()


# ---- catalog / UI configurability ----

def test_all_asset_events_are_configurable(admin_client):
    """Every Phase 2 event must be offered in the rule editor, otherwise Super Admin
    can never turn it on."""
    codes = {e["code"] for e in admin_client.get("/api/v1/notifications/events").json()}
    for event in ASSET_EVENTS:
        assert event in codes, f"{event} missing from the configurable event list"


def test_every_asset_event_has_a_human_label(admin_client):
    events = {e["code"]: e["label"] for e in admin_client.get("/api/v1/notifications/events").json()}
    for event in ASSET_EVENTS:
        assert events[event] != event, f"{event} has no descriptive label"


# ---- ASSET_CREATED ----

def test_asset_created_fires_with_live_data_and_branch_recipient(admin_client, db_session):
    branch = _branch(admin_client, "P2 Created")
    head = _unit_head(db_session, branch["id"], email="head.created@example.com")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED)

    _asset(admin_client, model["id"], branch["id"], "P2-C-1")

    rows = _notifications(db_session, EVENT_ASSET_CREATED)
    assert len(rows) == 1
    assert rows[0].recipient == head.whatsapp_number
    assert "Phase2 Ventilator" in rows[0].rendered_body
    assert "P2-C-1" in rows[0].rendered_body
    assert "P2 Created" in rows[0].rendered_body


def test_asset_created_only_reaches_its_own_branch(admin_client, db_session):
    """The core isolation property: an asset added at one branch must never resolve a
    recipient belonging to another."""
    home = _branch(admin_client, "P2 Home")
    other = _branch(admin_client, "P2 Other")
    _unit_head(db_session, home["id"], email="head.home@example.com", phone="+919000000111")
    _unit_head(db_session, other["id"], email="head.other@example.com", phone="+919000000222")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED)

    _asset(admin_client, model["id"], home["id"], "P2-ISO-1")

    rows = _notifications(db_session, EVENT_ASSET_CREATED)
    assert [r.recipient for r in rows] == ["+919000000111"]
    assert "P2 Other" not in rows[0].rendered_body


def test_no_rule_means_no_notification(admin_client, db_session):
    branch = _branch(admin_client, "P2 NoRule")
    model = _model(admin_client)
    _asset(admin_client, model["id"], branch["id"], "P2-NR-1")
    assert _notifications(db_session, EVENT_ASSET_CREATED) == []


def test_disabled_rule_sends_nothing(admin_client, db_session):
    branch = _branch(admin_client, "P2 Disabled")
    _unit_head(db_session, branch["id"], email="head.disabled@example.com")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED, is_active=False)
    _asset(admin_client, model["id"], branch["id"], "P2-D-1")
    assert _notifications(db_session, EVENT_ASSET_CREATED) == []


def test_combined_device_endpoint_fires_one_created_event(admin_client, db_session):
    """POST /devices composes the same Asset creation — one device, one event."""
    branch = _branch(admin_client, "P2 Combined")
    _unit_head(db_session, branch["id"], email="head.combined@example.com")
    _rule(db_session, event=EVENT_ASSET_CREATED)

    r = admin_client.post("/api/v1/devices", json={
        "branch_id": branch["id"], "model_name": "Combined Pump", "category": "PUMP",
        "asset_number": "P2-CB-1", "status": "OPERATIONAL",
    })
    assert r.status_code == 201, r.text
    assert len(_notifications(db_session, EVENT_ASSET_CREATED)) == 1


# ---- template rendering ----

def test_null_asset_number_renders_readable_text(admin_client, db_session):
    branch = _branch(admin_client, "P2 NullNum")
    _unit_head(db_session, branch["id"], email="head.nullnum@example.com")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED, body="Asset: {{asset_number}}")

    _asset(admin_client, model["id"], branch["id"])  # no asset_number

    rows = _notifications(db_session, EVENT_ASSET_CREATED)
    assert rows[0].rendered_body == "Asset: Asset number not assigned"


def test_device_and_model_names_come_from_the_device_model(admin_client, db_session):
    branch = _branch(admin_client, "P2 Model")
    _unit_head(db_session, branch["id"], email="head.model@example.com")
    model = _model(admin_client, name="Specific Monitor", manufacturer="Contoso")
    _rule(db_session, event=EVENT_ASSET_CREATED,
          body="{{device_name}}|{{model_name}}|{{manufacturer}}|{{serial_number}}")

    _asset(admin_client, model["id"], branch["id"], "P2-M-1", serial_number="SN-77")

    body = _notifications(db_session, EVENT_ASSET_CREATED)[0].rendered_body
    assert body == "Specific Monitor|Specific Monitor|Contoso|SN-77"


def test_unknown_token_is_left_visible_rather_than_blanked(admin_client, db_session):
    branch = _branch(admin_client, "P2 Token")
    _unit_head(db_session, branch["id"], email="head.token@example.com")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED, body="X {{not_a_real_token}} Y")
    _asset(admin_client, model["id"], branch["id"], "P2-T-1")
    assert "{{not_a_real_token}}" in _notifications(db_session, EVENT_ASSET_CREATED)[0].rendered_body


# ---- ASSET_STATUS_CHANGED / trivial edits ----

def test_meaningful_status_change_fires_with_previous_and_new_values(admin_client, db_session):
    branch = _branch(admin_client, "P2 Status")
    _unit_head(db_session, branch["id"], email="head.status@example.com")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-S-1", status="OPERATIONAL")
    _rule(db_session, event=EVENT_ASSET_STATUS_CHANGED, body="{{previous_status}} -> {{new_status}}")

    assert _patch(admin_client, asset, status="UNDER_REPAIR").status_code == 200

    rows = _notifications(db_session, EVENT_ASSET_STATUS_CHANGED)
    assert len(rows) == 1
    assert rows[0].rendered_body == "OPERATIONAL -> UNDER_REPAIR"


def test_trivial_edit_notifies_nobody(admin_client, db_session):
    """Serial number / dates / criticality are not lifecycle changes."""
    branch = _branch(admin_client, "P2 Trivial")
    _unit_head(db_session, branch["id"], email="head.trivial@example.com")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-TR-1")
    for event in ASSET_EVENTS:
        _rule(db_session, event=event, code=f"TRIV_{event}")

    assert _patch(admin_client, asset, serial_number="SN-CHANGED",
                  criticality="HIGH", purchase_date="2025-01-01").status_code == 200

    for event in ASSET_EVENTS:
        if event == EVENT_ASSET_CREATED:
            continue
        assert _notifications(db_session, event) == [], f"{event} fired on a trivial edit"


def test_resubmitting_the_same_values_fires_nothing(admin_client, db_session):
    branch = _branch(admin_client, "P2 Noop")
    _unit_head(db_session, branch["id"], email="head.noop@example.com")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-NO-1")
    _rule(db_session, event=EVENT_ASSET_STATUS_CHANGED)

    assert _patch(admin_client, asset).status_code == 200
    assert _notifications(db_session, EVENT_ASSET_STATUS_CHANGED) == []


def test_repeated_real_status_changes_each_fire(admin_client, db_session):
    """Deduplication must not swallow a second, genuine transition."""
    branch = _branch(admin_client, "P2 Repeat")
    _unit_head(db_session, branch["id"], email="head.repeat@example.com")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-RP-1", status="OPERATIONAL")
    _rule(db_session, event=EVENT_ASSET_STATUS_CHANGED)

    _patch(admin_client, asset, status="UNDER_REPAIR")
    asset["status"] = "UNDER_REPAIR"
    _patch(admin_client, asset, status="OPERATIONAL")
    asset["status"] = "OPERATIONAL"
    _patch(admin_client, asset, status="UNDER_REPAIR")

    assert len(_notifications(db_session, EVENT_ASSET_STATUS_CHANGED)) == 3


# ---- ASSET_TRANSFERRED ----

def test_branch_transfer_fires_and_names_both_sides(admin_client, db_session):
    source = _branch(admin_client, "P2 Source")
    dest = _branch(admin_client, "P2 Dest")
    _unit_head(db_session, dest["id"], email="head.dest@example.com", phone="+919000000333")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], source["id"], "P2-TX-1")
    _rule(db_session, event=EVENT_ASSET_TRANSFERRED, body="{{previous_branch}} -> {{new_branch}}")

    assert _patch(admin_client, asset, branch_id=dest["id"]).status_code == 200

    rows = _notifications(db_session, EVENT_ASSET_TRANSFERRED)
    assert len(rows) == 1
    assert rows[0].rendered_body == "P2 Source -> P2 Dest"
    # Recipient resolved against the destination branch the asset now belongs to.
    assert rows[0].recipient == "+919000000333"


def test_transfer_can_address_the_previous_branch(admin_client, db_session):
    """The losing branch's stakeholders are reachable from the same single event."""
    source = _branch(admin_client, "P2 OldSide")
    dest = _branch(admin_client, "P2 NewSide")
    _unit_head(db_session, source["id"], email="head.oldside@example.com", phone="+919000000444")
    _unit_head(db_session, dest["id"], email="head.newside@example.com", phone="+919000000555")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], source["id"], "P2-TX-2")
    _rule(db_session, event=EVENT_ASSET_TRANSFERRED, receiver_whatsapp="{{previous_unit_head_phone}}")

    _patch(admin_client, asset, branch_id=dest["id"])

    rows = _notifications(db_session, EVENT_ASSET_TRANSFERRED)
    assert [r.recipient for r in rows] == ["+919000000444"]


def test_transfer_does_not_also_fire_a_status_change(admin_client, db_session):
    source = _branch(admin_client, "P2 TxOnly")
    dest = _branch(admin_client, "P2 TxOnlyDest")
    _unit_head(db_session, dest["id"], email="head.txonly@example.com")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], source["id"], "P2-TX-3")
    _rule(db_session, event=EVENT_ASSET_TRANSFERRED, code="TXO_T")
    _rule(db_session, event=EVENT_ASSET_STATUS_CHANGED, code="TXO_S")

    _patch(admin_client, asset, branch_id=dest["id"])

    assert len(_notifications(db_session, EVENT_ASSET_TRANSFERRED)) == 1
    assert _notifications(db_session, EVENT_ASSET_STATUS_CHANGED) == []


# ---- ASSET_LOCATION_CHANGED ----

def _department(admin_client, branch_id, name):
    master = admin_client.post("/api/v1/department-masters", json={"name": name, "status": "ACTIVE"})
    assert master.status_code == 201, master.text
    r = admin_client.post("/api/v1/departments", json={
        "branch_id": branch_id, "department_master_id": master.json()["id"], "name": name, "status": "ACTIVE",
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_department_move_fires_location_changed_once(admin_client, db_session):
    branch = _branch(admin_client, "P2 Loc")
    _unit_head(db_session, branch["id"], email="head.loc@example.com")
    model = _model(admin_client)
    icu = _department(admin_client, branch["id"], "P2 ICU")
    ward = _department(admin_client, branch["id"], "P2 Ward")
    asset = _asset(admin_client, model["id"], branch["id"], "P2-L-1", department_id=icu["id"])
    _rule(db_session, event=EVENT_ASSET_LOCATION_CHANGED,
          body="{{previous_department}} -> {{new_department}}")

    assert _patch(admin_client, asset, department_id=ward["id"]).status_code == 200

    rows = _notifications(db_session, EVENT_ASSET_LOCATION_CHANGED)
    assert len(rows) == 1
    assert rows[0].rendered_body == "P2 ICU -> P2 Ward"


# ---- ASSET_RETIRED ----

def test_archiving_fires_retired_not_a_generic_status_change(admin_client, db_session):
    branch = _branch(admin_client, "P2 Retire")
    _unit_head(db_session, branch["id"], email="head.retire@example.com")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-R-1")
    _rule(db_session, event=EVENT_ASSET_RETIRED, code="RET_R", body="{{previous_status}} -> {{new_status}}")
    _rule(db_session, event=EVENT_ASSET_STATUS_CHANGED, code="RET_S")

    assert admin_client.post(f"/api/v1/assets/{asset['id']}/archive").status_code == 200

    retired = _notifications(db_session, EVENT_ASSET_RETIRED)
    assert len(retired) == 1
    assert retired[0].rendered_body == "OPERATIONAL -> RETIRED"
    assert _notifications(db_session, EVENT_ASSET_STATUS_CHANGED) == []


def test_retiring_reaches_the_inventory_manager_when_configured(admin_client, db_session):
    branch = _branch(admin_client, "P2 RetInv")
    _role_user(db_session, "INVENTORY_MANAGER", email="inv.ret@example.com",
               branch_id=branch["id"], phone="+919000000777")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-R-2")
    _rule(db_session, event=EVENT_ASSET_RETIRED, receiver_whatsapp="{{inventory_manager_phone}}")

    admin_client.post(f"/api/v1/assets/{asset['id']}/archive")

    assert [r.recipient for r in _notifications(db_session, EVENT_ASSET_RETIRED)] == ["+919000000777"]


def test_unarchiving_fires_a_plain_status_change(admin_client, db_session):
    branch = _branch(admin_client, "P2 Unarchive")
    _unit_head(db_session, branch["id"], email="head.unarchive@example.com")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-U-1", status="RETIRED")
    _rule(db_session, event=EVENT_ASSET_STATUS_CHANGED, body="{{previous_status}} -> {{new_status}}")

    assert admin_client.post(f"/api/v1/assets/{asset['id']}/unarchive").status_code == 200

    rows = _notifications(db_session, EVENT_ASSET_STATUS_CHANGED)
    assert len(rows) == 1
    assert rows[0].rendered_body == "RETIRED -> OPERATIONAL"


def test_archive_still_records_asset_history(admin_client, db_session):
    """The communication event must not become the only record of the transition —
    AssetHistory stays authoritative."""
    branch = _branch(admin_client, "P2 RetHist")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-R-3")

    admin_client.post(f"/api/v1/assets/{asset['id']}/archive")

    history = admin_client.get(f"/api/v1/assets/{asset['id']}/history").json()["items"]
    status_rows = [h for h in history if h["event_type"] == "STATUS_CHANGE"]
    assert len(status_rows) == 1
    assert status_rows[0]["from_value"] == "OPERATIONAL"
    assert status_rows[0]["to_value"] == "RETIRED"


# ---- ASSET_CONDEMNED ----

def _condemnation_requirement(client, db_session, *, branch_id, asset_id, requester_email, manager_email):
    requester = create_user(db_session, requester_email, "UNIT_HEAD")
    requester.hospital_scope_id = branch_id
    db_session.commit()
    manager = create_user(db_session, manager_email, "MANAGER")
    db_session.commit()

    req_headers = auth_headers(client, requester_email, "Password123!")
    r = client.post("/api/v1/requirements", json={
        "branch_id": branch_id, "priority": "HIGH", "reason": "End of life",
        "items": [{"item_type": "CONDEMNATION", "asset_id": asset_id, "requested_quantity": 1}],
    }, headers=req_headers)
    assert r.status_code == 201, r.text
    requirement = r.json()
    assert client.post(f"/api/v1/requirements/{requirement['id']}/submit",
                       headers=req_headers).status_code == 200

    mgr_headers = auth_headers(client, manager_email, "Password123!")
    approved = client.post(f"/api/v1/requirements/{requirement['id']}/approve",
                           json={"remarks": "approved"}, headers=mgr_headers)
    assert approved.status_code == 200, approved.text
    return requirement, manager


def test_condemnation_fires_from_the_approved_requirement(admin_client, db_session):
    branch = _branch(admin_client, "P2 Condemn")
    _unit_head(db_session, branch["id"], email="head.condemn@example.com", phone="+919000000888")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-CD-1")
    _rule(db_session, event=EVENT_ASSET_CONDEMNED,
          body="{{device_name}} {{asset_number}} {{branch_name}} {{previous_status}}->{{new_status}} {{reference}}")

    _condemnation_requirement(
        admin_client, db_session, branch_id=branch["id"], asset_id=asset["id"],
        requester_email="req.condemn@example.com", manager_email="mgr.condemn@example.com",
    )

    rows = _notifications(db_session, EVENT_ASSET_CONDEMNED)
    assert len(rows) == 1
    assert rows[0].recipient == "+919000000888"
    body = rows[0].rendered_body
    assert "P2-CD-1" in body and "P2 Condemn" in body and "OPERATIONAL->CONDEMNED" in body
    assert "REQ-" in body, "the condemnation message should cite the authorizing requirement"


def test_condemnation_does_not_also_fire_a_generic_status_change(admin_client, db_session):
    branch = _branch(admin_client, "P2 CondOnly")
    _unit_head(db_session, branch["id"], email="head.condonly@example.com")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-CD-2")
    _rule(db_session, event=EVENT_ASSET_CONDEMNED, code="CO_C")
    _rule(db_session, event=EVENT_ASSET_STATUS_CHANGED, code="CO_S")

    _condemnation_requirement(
        admin_client, db_session, branch_id=branch["id"], asset_id=asset["id"],
        requester_email="req.condonly@example.com", manager_email="mgr.condonly@example.com",
    )

    assert len(_notifications(db_session, EVENT_ASSET_CONDEMNED)) == 1
    assert _notifications(db_session, EVENT_ASSET_STATUS_CHANGED) == []


def test_condemnation_vendor_rule_fails_safely_without_coverage(admin_client, db_session):
    """A vendor rule must never fall back to some other address: with no active
    AMC/CMC/warranty there is no vendor, so the notification FAILS visibly."""
    branch = _branch(admin_client, "P2 CondVend")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-CD-3")
    _rule(db_session, event=EVENT_ASSET_CONDEMNED, channel="EMAIL",
          receiver_email="{{vendor_email}}", code="CV_E")

    _condemnation_requirement(
        admin_client, db_session, branch_id=branch["id"], asset_id=asset["id"],
        requester_email="req.condvend@example.com", manager_email="mgr.condvend@example.com",
    )

    rows = _notifications(db_session, EVENT_ASSET_CONDEMNED)
    assert len(rows) == 1
    assert rows[0].status == "FAILED"
    assert rows[0].recipient == ""
    assert "No recipient address resolved" in rows[0].failure_reason


def test_condemnation_still_records_asset_history(admin_client, db_session):
    branch = _branch(admin_client, "P2 CondHist")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-CD-4")

    _condemnation_requirement(
        admin_client, db_session, branch_id=branch["id"], asset_id=asset["id"],
        requester_email="req.condhist@example.com", manager_email="mgr.condhist@example.com",
    )

    history = admin_client.get(f"/api/v1/assets/{asset['id']}/history").json()["items"]
    assert [h["to_value"] for h in history if h["event_type"] == "STATUS_CHANGE"] == ["CONDEMNED"]


# ---- ASSET_ALLOCATED ----

def _allocate_and_complete(admin_client, db_session, *, branch_id, model_id, asset_id,
                           requester_email, manager_email):
    """Drives the real requirement -> fulfillment -> delivered -> completed workflow,
    which is where an asset is actually issued to a branch."""
    requester = create_user(db_session, requester_email, "UNIT_HEAD")
    requester.hospital_scope_id = branch_id
    db_session.commit()
    create_user(db_session, manager_email, "MANAGER")
    db_session.commit()

    req_headers = auth_headers(admin_client, requester_email, "Password123!")
    r = admin_client.post("/api/v1/requirements", json={
        "branch_id": branch_id, "priority": "HIGH", "reason": "New device needed",
        "items": [{"item_type": "DEVICE_MODEL", "device_model_id": model_id, "requested_quantity": 1}],
    }, headers=req_headers)
    assert r.status_code == 201, r.text
    requirement = r.json()
    assert admin_client.post(f"/api/v1/requirements/{requirement['id']}/submit",
                             headers=req_headers).status_code == 200
    mgr_headers = auth_headers(admin_client, manager_email, "Password123!")
    assert admin_client.post(f"/api/v1/requirements/{requirement['id']}/approve",
                             json={"remarks": "ok"}, headers=mgr_headers).status_code == 200

    admin_headers = {"Authorization": admin_client.headers["Authorization"]}
    f = admin_client.post("/api/v1/fulfillments", json={"requirement_id": requirement["id"]},
                          headers=admin_headers)
    assert f.status_code == 201, f.text
    fulfillment = f.json()
    item = admin_client.get(f"/api/v1/fulfillments/{fulfillment['id']}",
                            headers=admin_headers).json()["items"][0]
    alloc = admin_client.post(f"/api/v1/fulfillments/{fulfillment['id']}/allocate", json={
        "items": [{"requirement_item_id": item["requirement_item_id"], "asset_ids": [asset_id]}],
    }, headers=admin_headers)
    assert alloc.status_code == 200, alloc.text
    for step in ("process", "pack", "out-for-delivery"):
        s = admin_client.post(f"/api/v1/fulfillments/{fulfillment['id']}/{step}", headers=admin_headers)
        assert s.status_code == 200, s.text
    d = admin_client.post(f"/api/v1/fulfillments/{fulfillment['id']}/deliver",
                          json={"delivery_remarks": "handed over"}, headers=admin_headers)
    assert d.status_code == 200, d.text
    c = admin_client.post(f"/api/v1/fulfillments/{fulfillment['id']}/complete", headers=admin_headers)
    assert c.status_code == 200, c.text
    return fulfillment


def test_allocation_fires_once_for_the_destination_branch(admin_client, db_session):
    central = _branch(admin_client, "P2 Central")
    dest = _branch(admin_client, "P2 Saket")
    _unit_head(db_session, dest["id"], email="head.saket@example.com", phone="+919000000999")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], central["id"], "P2-AL-1")
    _rule(db_session, event=EVENT_ASSET_ALLOCATED,
          body="{{device_name}} {{asset_number}} {{previous_branch}} -> {{new_branch}} {{reference}}")

    _allocate_and_complete(
        admin_client, db_session, branch_id=dest["id"], model_id=model["id"], asset_id=asset["id"],
        requester_email="req.alloc@example.com", manager_email="mgr.alloc@example.com",
    )

    rows = _notifications(db_session, EVENT_ASSET_ALLOCATED)
    assert len(rows) == 1, "one allocation must produce one event, not one per internal save"
    assert rows[0].recipient == "+919000000999"
    body = rows[0].rendered_body
    assert "P2 Central -> P2 Saket" in body
    assert "FUL-" in body


def test_allocation_and_transfer_are_not_double_reported(admin_client, db_session):
    """Completion moves the asset's branch, which writes a BRANCH_TRANSFER history
    row. That must not turn one allocation into an allocation AND a transfer message."""
    central = _branch(admin_client, "P2 Central2")
    dest = _branch(admin_client, "P2 Dest2")
    _unit_head(db_session, dest["id"], email="head.dest2@example.com")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], central["id"], "P2-AL-2")
    _rule(db_session, event=EVENT_ASSET_ALLOCATED, code="AL_A")
    _rule(db_session, event=EVENT_ASSET_TRANSFERRED, code="AL_T")
    _rule(db_session, event=EVENT_ASSET_STATUS_CHANGED, code="AL_S")

    _allocate_and_complete(
        admin_client, db_session, branch_id=dest["id"], model_id=model["id"], asset_id=asset["id"],
        requester_email="req.alloc2@example.com", manager_email="mgr.alloc2@example.com",
    )

    assert len(_notifications(db_session, EVENT_ASSET_ALLOCATED)) == 1
    assert _notifications(db_session, EVENT_ASSET_TRANSFERRED) == []
    assert _notifications(db_session, EVENT_ASSET_STATUS_CHANGED) == []


# ---- recipient resolution ----

def test_role_token_prefers_the_asset_branch_holder(admin_client, db_session):
    home = _branch(admin_client, "P2 BmeHome")
    other = _branch(admin_client, "P2 BmeOther")
    _role_user(db_session, "BIOMEDICAL_ENGINEER", email="bme.other@example.com",
               branch_id=other["id"], phone="+919000001111")
    _role_user(db_session, "BIOMEDICAL_ENGINEER", email="bme.home@example.com",
               branch_id=home["id"], phone="+919000002222")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED, receiver_whatsapp="{{bme_phone}}")

    _asset(admin_client, model["id"], home["id"], "P2-BME-1")

    assert [r.recipient for r in _notifications(db_session, EVENT_ASSET_CREATED)] == ["+919000002222"]


def test_role_token_never_resolves_to_another_branchs_holder(admin_client, db_session):
    """With no BME at the asset's branch and none unscoped, the rule must fail rather
    than reach into a different branch's staff."""
    home = _branch(admin_client, "P2 BmeNone")
    other = _branch(admin_client, "P2 BmeElsewhere")
    _role_user(db_session, "BIOMEDICAL_ENGINEER", email="bme.elsewhere@example.com",
               branch_id=other["id"], phone="+919000003333")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED, receiver_whatsapp="{{bme_phone}}")

    _asset(admin_client, model["id"], home["id"], "P2-BME-2")

    rows = _notifications(db_session, EVENT_ASSET_CREATED)
    assert len(rows) == 1
    assert rows[0].status == "FAILED"
    assert rows[0].recipient == ""


def test_role_token_falls_back_to_an_organization_wide_holder(admin_client, db_session):
    branch = _branch(admin_client, "P2 BmeGlobal")
    _role_user(db_session, "BIOMEDICAL_ENGINEER", email="bme.global@example.com",
               branch_id=None, phone="+919000004444")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED, receiver_whatsapp="{{bme_phone}}")

    _asset(admin_client, model["id"], branch["id"], "P2-BME-3")

    assert [r.recipient for r in _notifications(db_session, EVENT_ASSET_CREATED)] == ["+919000004444"]


def test_notification_preference_is_honoured(admin_client, db_session):
    branch = _branch(admin_client, "P2 Pref")
    head = _unit_head(db_session, branch["id"], email="head.pref@example.com")
    head.notification_preference = "EMAIL"
    db_session.commit()
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED)  # WhatsApp

    _asset(admin_client, model["id"], branch["id"], "P2-PR-1")

    rows = _notifications(db_session, EVENT_ASSET_CREATED)
    assert rows[0].status == "FAILED"
    assert "notification_preference" in rows[0].failure_reason


def test_email_rule_resolves_the_unit_head_email(admin_client, db_session):
    branch = _branch(admin_client, "P2 Mail")
    _unit_head(db_session, branch["id"], email="head.mail@example.com")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED, channel="EMAIL", code="MAIL_UH")

    _asset(admin_client, model["id"], branch["id"], "P2-ML-1")

    rows = _notifications(db_session, EVENT_ASSET_CREATED)
    assert rows[0].recipient == "head.mail@example.com"
    assert "P2-ML-1" in rows[0].rendered_subject


# ---- auto-send / delivery ----

def test_auto_send_off_preserves_the_approval_workflow(admin_client, db_session, monkeypatch):
    branch = _branch(admin_client, "P2 AsOff")
    _unit_head(db_session, branch["id"], email="head.asoff@example.com")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED, auto_send=False)

    import app.services.whatsapp_service as ws

    attempts = []
    monkeypatch.setattr(ws, "attempt_automation_send", lambda db, n: attempts.append(n))

    _asset(admin_client, model["id"], branch["id"], "P2-AO-1")

    assert _notifications(db_session, EVENT_ASSET_CREATED)[0].status == "PENDING_APPROVAL"
    assert attempts == []


def test_auto_send_on_invokes_the_existing_whatsapp_sender(admin_client, db_session, monkeypatch):
    branch = _branch(admin_client, "P2 AsOn")
    _unit_head(db_session, branch["id"], email="head.ason@example.com")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED, auto_send=True)

    import app.services.whatsapp_service as ws

    approved, attempted = [], []
    monkeypatch.setattr(ws, "approve_whatsapp_notification", lambda db, n: (approved.append(n), n)[1])
    monkeypatch.setattr(ws, "attempt_automation_send",
                        lambda db, n: (attempted.append(n), setattr(n, "status", "SENT"), n)[2])

    _asset(admin_client, model["id"], branch["id"], "P2-AN-1")

    assert len(approved) == 1 and len(attempted) == 1
    assert _notifications(db_session, EVENT_ASSET_CREATED)[0].status == "SENT"


def test_auto_send_email_calls_the_existing_smtp_provider(admin_client, db_session, monkeypatch):
    branch = _branch(admin_client, "P2 AsMail")
    _unit_head(db_session, branch["id"], email="head.asmail@example.com")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED, channel="EMAIL", auto_send=True, code="AM_E")

    import app.services.notification_service as ns

    sent = []
    monkeypatch.setattr(ns, "send_email_smtp", lambda **kw: sent.append(kw))

    _asset(admin_client, model["id"], branch["id"], "P2-AM-1")

    assert len(sent) == 1, "auto-send EMAIL must go through the existing SMTP provider"
    assert sent[0]["to_email"] == "head.asmail@example.com"
    assert _notifications(db_session, EVENT_ASSET_CREATED)[0].status == "SENT"


def test_disconnected_sender_does_not_roll_back_the_asset(admin_client, db_session, monkeypatch):
    """The device really was registered. A dead WhatsApp session is a delivery
    problem, not a reason to undo it."""
    branch = _branch(admin_client, "P2 Fail")
    _unit_head(db_session, branch["id"], email="head.fail@example.com")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED, auto_send=True)

    import app.services.whatsapp_service as ws

    def boom(db, n):
        raise RuntimeError("WhatsApp Web session is DISCONNECTED")

    monkeypatch.setattr(ws, "approve_whatsapp_notification", boom)

    asset = _asset(admin_client, model["id"], branch["id"], "P2-F-1")

    assert admin_client.get(f"/api/v1/assets/{asset['id']}").status_code == 200
    rows = _notifications(db_session, EVENT_ASSET_CREATED)
    assert rows[0].status == "FAILED"
    assert "DISCONNECTED" in rows[0].failure_reason


def test_smtp_failure_does_not_roll_back_a_status_change(admin_client, db_session, monkeypatch):
    branch = _branch(admin_client, "P2 MailFail")
    _unit_head(db_session, branch["id"], email="head.mailfail@example.com")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "P2-MF-1")
    _rule(db_session, event=EVENT_ASSET_STATUS_CHANGED, channel="EMAIL", auto_send=True, code="MF_E")

    import app.services.notification_service as ns

    def boom(**kw):
        raise RuntimeError("SMTP refused the connection")

    monkeypatch.setattr(ns, "send_email_smtp", boom)

    assert _patch(admin_client, asset, status="UNDER_REPAIR").status_code == 200
    assert admin_client.get(f"/api/v1/assets/{asset['id']}").json()["status"] == "UNDER_REPAIR"
    assert _notifications(db_session, EVENT_ASSET_STATUS_CHANGED)[0].status == "FAILED"


def test_no_false_sent_without_delivery_evidence(admin_client, db_session, monkeypatch):
    branch = _branch(admin_client, "P2 Evidence")
    _unit_head(db_session, branch["id"], email="head.evidence@example.com")
    model = _model(admin_client)
    _rule(db_session, event=EVENT_ASSET_CREATED, auto_send=True)

    import app.services.whatsapp_service as ws

    def unconfirmed(db, n):
        n.whatsapp_status = "REQUIRES_USER_CONFIRMATION"
        return n

    monkeypatch.setattr(ws, "approve_whatsapp_notification", lambda db, n: n)
    monkeypatch.setattr(ws, "attempt_automation_send", unconfirmed)

    _asset(admin_client, model["id"], branch["id"], "P2-EV-1")

    assert _notifications(db_session, EVENT_ASSET_CREATED)[0].status != "SENT"


# ---- security / RBAC ----

def test_branch_user_cannot_mutate_another_branchs_asset(client, db_session):
    """A UNIT_HEAD must not be able to reach an asset outside their branch by id, and
    so must not be able to make it emit an event naming that branch."""
    create_user(db_session, "sa.scope@example.com", "SUPER_ADMIN")
    headers = auth_headers(client, "sa.scope@example.com", "Password123!")
    client.headers.update(headers)
    home = _branch(client, "P2 ScopeHome")
    other = _branch(client, "P2 ScopeOther")
    model = _model(client)
    foreign = _asset(client, model["id"], other["id"], "P2-SC-1")

    unit_head = create_user(db_session, "uh.scope@example.com", "UNIT_HEAD")
    unit_head.hospital_scope_id = home["id"]
    db_session.commit()
    uh_headers = auth_headers(client, "uh.scope@example.com", "Password123!")

    # No asset.write at all for a branch role — the first line of defence.
    r = client.patch(f"/api/v1/assets/{foreign['id']}",
                     json={**foreign, "status": "UNDER_REPAIR"}, headers=uh_headers)
    assert r.status_code == 403
    assert client.post(f"/api/v1/assets/{foreign['id']}/archive", headers=uh_headers).status_code == 403


def test_branch_scoped_writer_is_confined_to_their_branch(admin_client, db_session):
    """Defence in depth: even holding asset.write, a branch-scoped caller sees a 404
    for another branch's asset rather than mutating it (and notifying its branch)."""
    from app.models.identity import Role, UserRole

    home = _branch(admin_client, "P2 WriterHome")
    other = _branch(admin_client, "P2 WriterOther")
    model = _model(admin_client)
    foreign = _asset(admin_client, model["id"], other["id"], "P2-SC-2")
    _unit_head(db_session, other["id"], email="head.writerother@example.com")
    _rule(db_session, event=EVENT_ASSET_STATUS_CHANGED)

    user = create_user(db_session, "writer.scope@example.com", "UNIT_HEAD")
    user.hospital_scope_id = home["id"]
    bme_role = db_session.query(Role).filter(Role.code == "BIOMEDICAL_ENGINEER").first()
    db_session.add(UserRole(user_id=user.id, role_id=bme_role.id))
    db_session.commit()

    headers = auth_headers(admin_client, "writer.scope@example.com", "Password123!")
    r = admin_client.patch(f"/api/v1/assets/{foreign['id']}",
                           json={**foreign, "status": "UNDER_REPAIR"}, headers=headers)
    assert r.status_code == 404, r.text
    assert _notifications(db_session, EVENT_ASSET_STATUS_CHANGED) == []
    assert db_session.get(Asset, foreign["id"]).status == "OPERATIONAL"


def test_configuring_rules_requires_the_notification_permission(client, db_session):
    create_user(db_session, "viewer.rules@example.com", "VIEWER")
    headers = auth_headers(client, "viewer.rules@example.com", "Password123!")
    r = client.post("/api/v1/notifications/templates", json={
        "code": "SNEAKY", "channel": "WHATSAPP", "event": EVENT_ASSET_CREATED,
        "body_template": "x", "receiver_whatsapp_number": "+911234567890",
    }, headers=headers)
    assert r.status_code == 403


# ---- seeding ----

def test_seeding_ships_asset_rules_disabled_and_is_idempotent(admin_client, db_session):
    first = admin_client.post("/api/v1/notifications/templates/seed-defaults")
    assert first.status_code == 200, first.text
    assert first.json()["created"] > 0
    second = admin_client.post("/api/v1/notifications/templates/seed-defaults")
    assert second.json()["created"] == 0

    seeded = db_session.query(NotificationTemplate).filter(
        NotificationTemplate.event.in_(ASSET_EVENTS)
    ).all()
    assert seeded, "no asset default rules were seeded"
    for rule in seeded:
        assert rule.is_active is False, f"{rule.code} must ship disabled"
        assert rule.auto_send is False, f"{rule.code} must ship with auto-send off"

    covered = {r.event for r in seeded}
    for event in ASSET_EVENTS:
        assert event in covered, f"{event} has no default rule"
    for event in ASSET_EVENTS:
        channels = {r.channel for r in seeded if r.event == event}
        assert channels == {"EMAIL", "WHATSAPP"}, f"{event} needs both an email and a WhatsApp template"


def test_seeding_never_overwrites_an_admin_edited_rule(admin_client, db_session):
    admin_client.post("/api/v1/notifications/templates/seed-defaults")
    rule = db_session.query(NotificationTemplate).filter(
        NotificationTemplate.code == "ASSET_CREATED_WA"
    ).first()
    rule.body_template = "Admin's own wording"
    rule.is_active = True
    db_session.commit()

    admin_client.post("/api/v1/notifications/templates/seed-defaults")
    db_session.refresh(rule)
    assert rule.body_template == "Admin's own wording"
    assert rule.is_active is True


def test_seeded_vendor_rule_exposes_no_internal_contacts(admin_client, db_session):
    """Vendor privacy: the one vendor-facing default must not carry internal staff
    contacts, internal notes or the acting user's name."""
    admin_client.post("/api/v1/notifications/templates/seed-defaults")
    rule = db_session.query(NotificationTemplate).filter(
        NotificationTemplate.code == "ASSET_CONDEMNED_VENDOR_EMAIL"
    ).first()
    assert rule is not None
    text = f"{rule.subject_template or ''} {rule.body_template}"
    for forbidden in ("unit_head", "bme_", "inventory_manager", "updated_by", "notes", "branch_email"):
        assert forbidden not in text, f"vendor template must not expose {{{{{forbidden}}}}}"
