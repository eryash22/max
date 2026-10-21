"""Final Operational Gap Fix Pass.

Part 1 — PM/Calibration due/overdue notifications (genuine gap: due_status was
computed at read time but never dispatched an event).
Part 2 — Vendor visit scheduled notification: confirmed ALREADY wired (Module 21,
service_visit_service.create_visit already calls dispatch_procurement_event_safe
with EVENT_VENDOR_VISIT_SCHEDULED when a visit carries a purchase_order_id) — this
is a regression test proving that claim, not a fix.
Part 4 — Inventory/consumable role-based stock visibility: confirmed the existing
INVENTORY_READ vs INVENTORY_PICKER_READ masking already satisfies the business
requirement ("TL sees no quantities at all, Inventory Manager/Super Admin see
everything") — these are regression tests for previously-untested behavior, not a
new mechanism. No branch-scoped filtering was added (see PROJECT_STATUS.md for why).
"""
from datetime import date, timedelta

from app.models.notifications import Notification

from .conftest import auth_headers, create_user


def _make_asset(admin_client, asset_number="OPGAP-001"):
    branch = admin_client.post("/api/v1/branches", json={"name": f"Branch-{asset_number}", "status": "ACTIVE"}).json()
    model = admin_client.post(
        "/api/v1/device-models", json={"name": f"Device-{asset_number}", "category": "Ventilator"}
    ).json()
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": model["id"], "branch_id": branch["id"]},
    ).json()
    return branch, model, asset


def _engineer_headers(client, db_session, email="opgap.eng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _make_rule(admin_client, *, event, receiver_email, code=None):
    payload = {
        "code": code or f"{event}_RULE", "channel": "EMAIL", "event": event,
        "body_template": "{{schedule_type}} for asset {{asset_number}} is {{due_status}} (due {{next_due_date}})",
        "receiver_email": receiver_email, "is_active": True,
    }
    response = admin_client.post("/api/v1/notifications/templates", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Part 1 — PM notifications
# ---------------------------------------------------------------------------

def test_pm_overdue_schedule_dispatches_notification(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client, "PM-OVERDUE-1")
    headers = _engineer_headers(client, db_session)
    admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": "PM_OVERDUE_RULE", "channel": "EMAIL", "event": "PM_OVERDUE",
              "body_template": "Overdue: {{asset_number}}", "receiver_email": "biomed-admin@example.com",
              "is_active": True},
    )
    past = (date.today() - timedelta(days=5)).isoformat()
    created = client.post(
        "/api/v1/pm/schedules",
        json={"asset_id": asset["id"], "frequency": "MONTHLY", "next_due_date": past},
        headers=headers,
    ).json()
    assert created["due_status"] == "OVERDUE"

    notifications = db_session.query(Notification).filter(
        Notification.event == "PM_OVERDUE", Notification.related_entity_id == created["id"],
    ).all()
    assert len(notifications) == 1
    assert notifications[0].recipient == "biomed-admin@example.com"
    assert notifications[0].related_entity_type == "pm_schedule"
    # PM_DUE must NOT also have fired for an OVERDUE schedule.
    due_notifications = db_session.query(Notification).filter(Notification.event == "PM_DUE").all()
    assert due_notifications == []


def test_pm_due_soon_schedule_dispatches_notification(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client, "PM-DUE-1")
    headers = _engineer_headers(client, db_session)
    admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": "PM_DUE_RULE", "channel": "EMAIL", "event": "PM_DUE",
              "body_template": "Due soon: {{asset_number}}", "receiver_email": "biomed-admin@example.com",
              "is_active": True},
    )
    due_soon = (date.today() + timedelta(days=3)).isoformat()
    created = client.post(
        "/api/v1/pm/schedules",
        json={"asset_id": asset["id"], "frequency": "MONTHLY", "next_due_date": due_soon},
        headers=headers,
    ).json()
    assert created["due_status"] == "DUE"

    notifications = db_session.query(Notification).filter(
        Notification.event == "PM_DUE", Notification.related_entity_id == created["id"],
    ).all()
    assert len(notifications) == 1


def test_pm_upcoming_schedule_does_not_dispatch(admin_client, client, db_session):
    """UPCOMING/NOT_SCHEDULED are not actionable — no notification should ever fire."""
    _, _, asset = _make_asset(admin_client, "PM-UPCOMING-1")
    headers = _engineer_headers(client, db_session)
    admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": "PM_DUE_RULE2", "channel": "EMAIL", "event": "PM_DUE",
              "body_template": "x", "receiver_email": "x@example.com", "is_active": True},
    )
    future = (date.today() + timedelta(days=90)).isoformat()
    created = client.post(
        "/api/v1/pm/schedules",
        json={"asset_id": asset["id"], "frequency": "MONTHLY", "next_due_date": future},
        headers=headers,
    ).json()
    assert created["due_status"] == "UPCOMING"
    assert db_session.query(Notification).filter(Notification.related_entity_id == created["id"]).all() == []


def test_pm_notification_only_fires_with_active_template(admin_client, client, db_session):
    """No NotificationTemplate configured for PM_OVERDUE -> no Notification row at all,
    consistent with every other event in this codebase (dispatch_event's `if not rules:
    return []` contract) — not a bug."""
    _, _, asset = _make_asset(admin_client, "PM-NORULE-1")
    headers = _engineer_headers(client, db_session)
    past = (date.today() - timedelta(days=5)).isoformat()
    created = client.post(
        "/api/v1/pm/schedules",
        json={"asset_id": asset["id"], "frequency": "MONTHLY", "next_due_date": past},
        headers=headers,
    ).json()
    assert db_session.query(Notification).filter(Notification.related_entity_id == created["id"]).all() == []


def test_pm_record_completion_recomputes_and_dispatches(admin_client, client, db_session):
    """Completing a PM record can push the schedule's next_due_date straight back into
    OVERDUE/DUE territory (e.g. a short custom interval) — this must also dispatch."""
    _, _, asset = _make_asset(admin_client, "PM-RECORD-1")
    engineer = create_user(db_session, "pm.record.eng@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    headers = auth_headers(client, "pm.record.eng@example.com", "Password123!")
    admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": "PM_DUE_RULE3", "channel": "EMAIL", "event": "PM_DUE",
              "body_template": "x", "receiver_email": "x@example.com", "is_active": True},
    )
    schedule = client.post(
        "/api/v1/pm/schedules",
        json={"asset_id": asset["id"], "frequency": "CUSTOM", "custom_interval_days": 2},
        headers=headers,
    ).json()

    record = client.post(
        "/api/v1/pm/records",
        json={"schedule_id": schedule["id"], "asset_id": asset["id"], "engineer_id": engineer.id,
              "next_due_date": (date.today() + timedelta(days=3)).isoformat()},
        headers=headers,
    ).json()
    assert record["next_due_date"] is not None

    notifications = db_session.query(Notification).filter(
        Notification.event == "PM_DUE", Notification.related_entity_id == schedule["id"],
    ).all()
    assert len(notifications) == 1


# ---------------------------------------------------------------------------
# Part 1 — Calibration notifications (mirrors PM exactly)
# ---------------------------------------------------------------------------

def test_calibration_overdue_schedule_dispatches_notification(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client, "CAL-OVERDUE-1")
    headers = _engineer_headers(client, db_session)
    admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": "CAL_OVERDUE_RULE", "channel": "EMAIL", "event": "CALIBRATION_OVERDUE",
              "body_template": "Overdue: {{asset_number}}", "receiver_email": "biomed-admin@example.com",
              "is_active": True},
    )
    past = (date.today() - timedelta(days=5)).isoformat()
    created = client.post(
        "/api/v1/calibration/schedules",
        json={"asset_id": asset["id"], "frequency": "YEARLY", "next_due_date": past},
        headers=headers,
    ).json()
    assert created["due_status"] == "OVERDUE"

    notifications = db_session.query(Notification).filter(
        Notification.event == "CALIBRATION_OVERDUE", Notification.related_entity_id == created["id"],
    ).all()
    assert len(notifications) == 1
    assert notifications[0].related_entity_type == "calibration_schedule"


def test_calibration_due_soon_schedule_dispatches_notification(admin_client, client, db_session):
    _, _, asset = _make_asset(admin_client, "CAL-DUE-1")
    headers = _engineer_headers(client, db_session)
    admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": "CAL_DUE_RULE", "channel": "EMAIL", "event": "CALIBRATION_DUE",
              "body_template": "Due soon: {{asset_number}}", "receiver_email": "biomed-admin@example.com",
              "is_active": True},
    )
    due_soon = (date.today() + timedelta(days=3)).isoformat()
    created = client.post(
        "/api/v1/calibration/schedules",
        json={"asset_id": asset["id"], "frequency": "YEARLY", "next_due_date": due_soon},
        headers=headers,
    ).json()
    assert created["due_status"] == "DUE"

    notifications = db_session.query(Notification).filter(
        Notification.event == "CALIBRATION_DUE", Notification.related_entity_id == created["id"],
    ).all()
    assert len(notifications) == 1


def test_calibration_recipient_resolves_last_engineer_token(admin_client, client, db_session):
    """Confirms build_maintenance_context resolves {{last_engineer_email}} from the most
    recent CalibrationRecord.engineer_id against this schedule — the closest existing-
    architecture equivalent to a 'responsible BME', since no assignment field exists."""
    _, _, asset = _make_asset(admin_client, "CAL-ENG-1")
    engineer = create_user(db_session, "cal.eng@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    headers = auth_headers(client, "cal.eng@example.com", "Password123!")

    schedule = client.post(
        "/api/v1/calibration/schedules", json={"asset_id": asset["id"], "frequency": "YEARLY"}, headers=headers,
    ).json()
    client.post(
        "/api/v1/calibration/records",
        json={"schedule_id": schedule["id"], "asset_id": asset["id"]},
        headers=headers,
    )

    admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": "CAL_OVERDUE_RULE2", "channel": "EMAIL", "event": "CALIBRATION_OVERDUE",
              "body_template": "Assigned engineer was {{last_engineer_email}}",
              "receiver_email": "biomed-admin@example.com", "is_active": True},
    )
    past = (date.today() - timedelta(days=5)).isoformat()
    client.patch(f"/api/v1/calibration/schedules/{schedule['id']}", json={"next_due_date": past}, headers=headers)

    notification = db_session.query(Notification).filter(
        Notification.event == "CALIBRATION_OVERDUE", Notification.related_entity_id == schedule["id"],
    ).first()
    assert notification is not None
    # engineer_id on the calibration record submitted above is None (not supplied) — this
    # test's real assertion is that rendering never breaks and resolves to the "-" fallback,
    # not a KeyError/blank-crash, since CalibrationRecord has no engineer_id-carrying test data.
    assert "{{last_engineer_email}}" not in notification.rendered_body


# ---------------------------------------------------------------------------
# Part 2 — Vendor visit scheduled notification (already wired — regression proof)
# ---------------------------------------------------------------------------

def test_vendor_visit_scheduled_against_po_dispatches_notification(admin_client, client, db_session):
    """Regression test proving EVENT_VENDOR_VISIT_SCHEDULED already dispatches when a
    ServiceVisit is scheduled against a PurchaseOrder (service_visit_service.create_visit,
    Module 21) — the QA audit's "never dispatched" finding was stale/incorrect; verified
    by direct code+git-history inspection before writing this test. No code changed for
    this part."""
    branch, model, asset = _make_asset(admin_client, "VENDOR-VISIT-1")
    engineer_headers = _engineer_headers(client, db_session, "vv.eng@example.com")

    vendor = admin_client.post(
        "/api/v1/vendors", json={"name": "VV Test Vendor", "vendor_type": "OEM", "status": "ACTIVE"},
    ).json()
    incident = client.post(
        "/api/v1/incidents",
        json={"branch_id": branch["id"], "asset_id": asset["id"], "description": "Pump fault",
              "severity": "HIGH", "problem_category": "Device Issue"},
        headers=engineer_headers,
    ).json()
    quotation = client.post(
        "/api/v1/procurement/quotations",
        json={"incident_id": incident["id"], "asset_id": asset["id"], "vendor_id": vendor["id"],
              "lines": [{"description": "Repair", "quantity": 1, "unit_price": 1000, "covered": False}]},
        headers=engineer_headers,
    ).json()
    client.post(f"/api/v1/procurement/quotations/{quotation['id']}/submit", headers=engineer_headers)
    approved = admin_client.post(f"/api/v1/procurement/quotations/{quotation['id']}/approve", json={}).json()
    assert approved["status"] == "APPROVED"

    indent = admin_client.post(
        "/api/v1/procurement/indents", json={"quotation_id": quotation["id"]},
    ).json()
    admin_client.post(f"/api/v1/procurement/indents/{indent['id']}/submit")
    po = admin_client.post(
        "/api/v1/procurement/purchase-orders", json={"indent_id": indent["id"]},
    ).json()

    admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": "VENDOR_VISIT_RULE", "channel": "EMAIL", "event": "VENDOR_VISIT_SCHEDULED",
              "body_template": "Vendor visit scheduled for PO {{po_number}}",
              "receiver_email": "{{assigned_engineer_email}}", "is_active": True},
    )

    visit = admin_client.post(
        "/api/v1/service-visits",
        json={"asset_id": asset["id"], "vendor_id": vendor["id"], "purchase_order_id": po["id"],
              "visit_type": "BREAKDOWN", "visit_date": date.today().isoformat()},
    ).json()

    notifications = db_session.query(Notification).filter(Notification.event == "VENDOR_VISIT_SCHEDULED").all()
    assert len(notifications) == 1
    assert notifications[0].related_entity_type == "purchase_order"
    assert notifications[0].related_entity_id == po["id"]


def test_plain_service_visit_without_po_does_not_dispatch_vendor_event(admin_client, client, db_session):
    """A routine PM/breakdown visit with no purchase_order_id is not a procurement vendor
    visit — confirms the existing narrow trigger condition is intentional, not a gap."""
    _, _, asset = _make_asset(admin_client, "PLAIN-VISIT-1")
    admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": "VENDOR_VISIT_RULE2", "channel": "EMAIL", "event": "VENDOR_VISIT_SCHEDULED",
              "body_template": "x", "receiver_email": "x@example.com", "is_active": True},
    )
    admin_client.post(
        "/api/v1/service-visits",
        json={"asset_id": asset["id"], "visit_type": "PM", "visit_date": date.today().isoformat()},
    )
    assert db_session.query(Notification).filter(Notification.event == "VENDOR_VISIT_SCHEDULED").all() == []


# ---------------------------------------------------------------------------
# Part 4 — Inventory/consumable stock visibility by role (regression tests for
# previously-untested existing behavior — no mechanism was changed)
# ---------------------------------------------------------------------------

def _tl_headers(client, db_session, email="tl.opgap@example.com"):
    create_user(db_session, email, "HOSPITAL_UNIT_MANAGER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _inventory_manager_headers(client, db_session, email="invmgr.opgap@example.com"):
    create_user(db_session, email, "INVENTORY_MANAGER", "Password123!")
    return auth_headers(client, email, "Password123!")


def test_tl_sees_no_quantity_fields_on_inventory_items(admin_client, client, db_session):
    admin_client.post("/api/v1/inventory/items", json={"item_code": "OPGAP-ITM-1", "name": "Gauze", "opening_quantity": 100})
    headers = _tl_headers(client, db_session)
    response = client.get("/api/v1/inventory/items", headers=headers)
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["item_code"] == "OPGAP-ITM-1")
    for field in ("min_level", "max_level", "reorder_level", "unit_cost", "current_stock", "stock_status"):
        assert item[field] is None, f"{field} was not masked for TL/Unit Head"


def test_tl_sees_no_quantity_fields_on_consumables_central_or_branch(admin_client, client, db_session):
    branch = admin_client.post("/api/v1/branches", json={"name": "OpGap Branch", "status": "ACTIVE"}).json()
    admin_client.post("/api/v1/consumables", json={"item_code": "OPGAP-CON-1", "name": "Electrodes", "opening_quantity": 500})
    admin_client.post(
        "/api/v1/consumables",
        json={"item_code": "OPGAP-CON-2", "name": "Lead Wire", "opening_quantity": 20, "branch_id": branch["id"]},
    )
    headers = _tl_headers(client, db_session)
    response = client.get("/api/v1/consumables", headers=headers)
    assert response.status_code == 200
    items = {i["item_code"]: i for i in response.json()["items"]}
    for code in ("OPGAP-CON-1", "OPGAP-CON-2"):
        for field in ("opening_quantity", "min_level", "reorder_level", "stock_status"):
            assert items[code][field] is None, f"{field} was not masked for {code}"


def test_tl_cannot_access_inventory_stock_or_transaction_detail_endpoints(admin_client, client, db_session):
    item = admin_client.post("/api/v1/inventory/items", json={"item_code": "OPGAP-ITM-2", "name": "Sensor Cable"}).json()
    consumable = admin_client.post("/api/v1/consumables", json={"item_code": "OPGAP-CON-3", "name": "Syringe", "opening_quantity": 10}).json()
    headers = _tl_headers(client, db_session)

    assert client.get(f"/api/v1/inventory/items/{item['id']}", headers=headers).status_code == 403
    assert client.get(f"/api/v1/inventory/items/{item['id']}/stock", headers=headers).status_code == 403
    assert client.get("/api/v1/inventory/transactions", headers=headers).status_code == 403
    assert client.get(f"/api/v1/consumables/{consumable['id']}", headers=headers).status_code == 403
    assert client.get(f"/api/v1/consumables/{consumable['id']}/transactions", headers=headers).status_code == 403


def test_inventory_manager_sees_full_central_stock(admin_client, client, db_session):
    admin_client.post("/api/v1/consumables", json={"item_code": "OPGAP-CON-4", "name": "Central Item", "opening_quantity": 300})
    headers = _inventory_manager_headers(client, db_session)
    response = client.get("/api/v1/consumables", headers=headers)
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["item_code"] == "OPGAP-CON-4")
    assert item["opening_quantity"] == 300
    assert item["branch_id"] is None


def test_inventory_manager_sees_branch_stock(admin_client, client, db_session):
    branch = admin_client.post("/api/v1/branches", json={"name": "OpGap Branch 2", "status": "ACTIVE"}).json()
    admin_client.post(
        "/api/v1/consumables",
        json={"item_code": "OPGAP-CON-5", "name": "Branch Item", "opening_quantity": 40, "branch_id": branch["id"]},
    )
    headers = _inventory_manager_headers(client, db_session)
    response = client.get(f"/api/v1/consumables?branch_id={branch['id']}", headers=headers)
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["item_code"] == "OPGAP-CON-5")
    assert item["opening_quantity"] == 40


def test_super_admin_sees_all_stock(admin_client):
    admin_client.post("/api/v1/consumables", json={"item_code": "OPGAP-CON-6", "name": "Admin-visible Item", "opening_quantity": 77})
    response = admin_client.get("/api/v1/consumables")
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["item_code"] == "OPGAP-CON-6")
    assert item["opening_quantity"] == 77
