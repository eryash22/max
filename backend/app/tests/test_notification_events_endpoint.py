"""Final Notification-Rule UI Fix.

The Communication Rules admin UI's event selector used to hard-code its own event
list in the frontend (frontend/types/index.ts), which had already drifted out of
sync with the backend (missing Incident/Procurement events, and later PM/
Calibration/vendor-visit events). GET /api/v1/notifications/events now exposes
the backend's single source of truth (schemas/notification.py's
ALL_NOTIFICATION_EVENTS/EVENT_LABELS) so the frontend fetches instead of
duplicating it. No dispatch logic, recipient resolution, or business event was
added — this is a read-only listing of events that already existed.
"""
from app.schemas.notification import ALL_NOTIFICATION_EVENTS

from .conftest import auth_headers, create_user


def test_events_endpoint_includes_new_maintenance_events(admin_client):
    response = admin_client.get("/api/v1/notifications/events")
    assert response.status_code == 200
    codes = {e["code"] for e in response.json()}
    for expected in ("PM_DUE", "PM_OVERDUE", "CALIBRATION_DUE", "CALIBRATION_OVERDUE", "VENDOR_VISIT_SCHEDULED"):
        assert expected in codes, f"{expected} missing from the events endpoint"


def test_events_endpoint_preserves_existing_events(admin_client):
    """Requirement, Incident, Fulfillment, and Procurement events must all still be
    present — this fix must not have dropped any previously-configurable event."""
    response = admin_client.get("/api/v1/notifications/events")
    codes = {e["code"] for e in response.json()}
    for expected in (
        "REQUIREMENT_SUBMITTED", "REQUIREMENT_APPROVED", "REQUIREMENT_REJECTED", "REQUIREMENT_CANCELLED",
        "FULFILLMENT_CREATED", "FULFILLMENT_UNDER_PROCESS", "FULFILLMENT_PACKED",
        "FULFILLMENT_OUT_FOR_DELIVERY", "FULFILLMENT_DELIVERY_DATE_CHANGED",
        "FULFILLMENT_DELIVERED", "FULFILLMENT_COMPLETED", "FULFILLMENT_CANCELLED",
        "INCIDENT_ASSIGNED", "INCIDENT_RESOLVED",
        "QUOTATION_SUBMITTED", "QUOTATION_APPROVED", "QUOTATION_REJECTED",
        "INDENT_CREATED", "PO_CREATED", "INVOICE_RECORDED", "INVOICE_VERIFIED", "PROCUREMENT_COMPLETED",
    ):
        assert expected in codes, f"{expected} was dropped from the events endpoint"


def test_events_endpoint_matches_backend_source_of_truth_exactly(admin_client):
    """The endpoint must not diverge from ALL_NOTIFICATION_EVENTS — no event added or
    dropped in the router layer beyond what the schema constant defines, and order
    is preserved (grouped by domain, not alphabetized/reshuffled)."""
    response = admin_client.get("/api/v1/notifications/events")
    codes = [e["code"] for e in response.json()]
    assert codes == ALL_NOTIFICATION_EVENTS


def test_events_endpoint_returns_label_for_every_code(admin_client):
    response = admin_client.get("/api/v1/notifications/events")
    for entry in response.json():
        assert entry["label"], f"{entry['code']} has an empty label"


def test_events_endpoint_requires_notification_read(client, db_session):
    create_user(db_session, "no.notif.perm@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "no.notif.perm@example.com", "Password123!")
    response = client.get("/api/v1/notifications/events", headers=headers)
    assert response.status_code == 403


def test_create_rule_for_pm_due_via_ui_payload(admin_client):
    """Exercises the exact payload shape the /communication-rules/new page posts."""
    response = admin_client.post(
        "/api/v1/notifications/templates",
        json={
            "code": "UI_PM_DUE_RULE", "channel": "EMAIL", "event": "PM_DUE",
            "subject_template": None, "is_active": True,
            "body_template": "{{schedule_type}} for {{asset_number}} is {{due_status}}",
            "sender_name": None, "sender_email": None, "sender_whatsapp_number": None,
            "receiver_name": None, "receiver_email": "biomed@example.com", "receiver_whatsapp_number": None,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["event"] == "PM_DUE"


def test_create_rule_for_calibration_due_via_ui_payload(admin_client):
    response = admin_client.post(
        "/api/v1/notifications/templates",
        json={
            "code": "UI_CAL_DUE_RULE", "channel": "EMAIL", "event": "CALIBRATION_DUE",
            "is_active": True, "body_template": "Calibration due for {{asset_number}}",
            "receiver_email": "biomed@example.com",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["event"] == "CALIBRATION_DUE"
