"""Module 17B — WhatsApp + Email Communication Automation.

Fires configured `NotificationTemplate` "communication rules" for the ten Requirement/
Fulfillment workflow events, rendering `{{variable}}` tokens from the real entity data and
writing a `Notification` history row per matching, enabled (EMAIL/WHATSAPP) rule.

Deliberately has no import of requirement_service/fulfillment_service (those import this
module instead, calling `dispatch_event_safe` after their own commit) to avoid a circular
dependency — this module only ever receives already-loaded model instances.
"""
import re
from urllib.parse import quote

from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..models.device import Asset, DeviceModel
from ..models.fulfillment import FulfillmentRequest
from ..models.hospital import Branch, Department, HospitalNetwork, Location
from ..models.identity import User
from ..models.incident import Incident
from ..models.inventory import Consumable, InventoryItem
from ..models.maintenance import CalibrationRecord, CalibrationSchedule, PMRecord, PMSchedule
from ..models.notifications import Notification, NotificationTemplate
from ..models.requirement import Requirement, RequirementItem
from ..models.vendor import Vendor
from ..schemas.notification import COMMUNICATION_CHANNELS
from .notification_service import submit_notification

SAMPLE_CONTEXT: dict[str, str] = {
    "requirement_number": "REQ-000123",
    "hospital": "Sample Hospital Network",
    "branch": "Sample Branch",
    "department": "Sample Department",
    "priority": "MEDIUM",
    "items": "Infusion Pump x2, Gauze (Consumable) x50",
    "status": "SUBMITTED",
    "expected_delivery_date": "2026-09-01",
    "fulfillment_number": "FUL-000045",
    "receiver_name": "Sample Recipient",
    "requester_name": "Sample Requester",
    "requester_email": "requester@example.com",
    "manager_name": "Sample Manager",
    "manager_email": "manager@example.com",
    "inventory_manager_name": "Sample Inventory Manager",
    "inventory_manager_email": "inventory@example.com",
    "reason": "Sample requirement reason",
    "required_by_date": "2026-09-10",
}

_TOKEN_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render_template(text: str | None, context: dict[str, str]) -> str:
    """Substitutes every `{{key}}` present in `context`; an unresolved token (typo, or a
    variable this event doesn't have) is left literal rather than blanked, so an admin
    previewing/reading history can see exactly what went wrong instead of silent data loss."""
    if not text:
        return ""

    def _sub(match: re.Match) -> str:
        key = match.group(1)
        return context[key] if key in context else match.group(0)

    return _TOKEN_RE.sub(_sub, text)


def _item_label(db: Session, item: RequirementItem) -> str:
    if item.item_type == "OTHER":
        label = item.description or "Other item"
    elif item.device_model_id:
        model = db.get(DeviceModel, item.device_model_id)
        label = model.name if model else "Unknown device"
    elif item.inventory_item_id:
        inv = db.get(InventoryItem, item.inventory_item_id)
        label = f"{inv.name} (Spare part)" if inv else "Unknown spare part"
    elif item.consumable_id:
        cons = db.get(Consumable, item.consumable_id)
        label = f"{cons.name} (Consumable)" if cons else "Unknown consumable"
    else:
        label = item.item_type
    return f"{label} x{item.requested_quantity}"


def build_context(db: Session, *, requirement: Requirement, fulfillment: FulfillmentRequest | None = None) -> dict[str, str]:
    branch = db.get(Branch, requirement.branch_id)
    hospital = db.get(HospitalNetwork, branch.network_id) if branch and branch.network_id else None
    department = db.get(Department, requirement.department_id) if requirement.department_id else None
    requester = db.get(User, requirement.requester_id)
    manager = db.get(User, requirement.manager_id) if requirement.manager_id else None
    inventory_manager = db.get(User, fulfillment.inventory_manager_id) if fulfillment and fulfillment.inventory_manager_id else None

    items = db.query(RequirementItem).filter(RequirementItem.requirement_id == requirement.id).all()
    items_summary = ", ".join(_item_label(db, item) for item in items) or "-"

    context = {
        "requirement_number": requirement.request_number,
        "hospital": hospital.name if hospital else "-",
        "branch": branch.name if branch else "-",
        "department": department.name if department else "-",
        "priority": requirement.priority,
        "items": items_summary,
        "status": fulfillment.status if fulfillment else requirement.status,
        "expected_delivery_date": (fulfillment.expected_delivery_date if fulfillment else None) or "-",
        "fulfillment_number": fulfillment.fulfillment_number if fulfillment else "-",
        "receiver_name": requester.name if requester else "-",
        "requester_name": requester.name if requester else "-",
        "requester_email": requester.email if requester else "",
        "manager_name": manager.name if manager else "-",
        "manager_email": manager.email if manager else "",
        "inventory_manager_name": inventory_manager.name if inventory_manager else "-",
        "inventory_manager_email": inventory_manager.email if inventory_manager else "",
        "reason": requirement.reason,
        "required_by_date": requirement.required_by_date or "-",
    }
    return context


def build_whatsapp_url(recipient: str, rendered_body: str) -> str | None:
    digits = re.sub(r"[^0-9]", "", recipient or "")
    if not digits or not rendered_body:
        return None
    return f"https://wa.me/{digits}?text={quote(rendered_body)}"


def _existing_notification(
    db: Session, *, event: str, channel: str, template_id: str, requirement_id: str, fulfillment_id: str | None,
) -> Notification | None:
    """Duplicate-prevention: one Notification per (event, template, requirement,
    fulfillment) combination — re-dispatching the same real-world transition (e.g. a
    retried request) returns the existing row instead of sending/logging twice.
    Module 19: keyed on `template_id` (not just event+channel) so two active rules of
    the same channel targeting the same event each get their own Notification row
    instead of the second rule silently reusing the first rule's — channel stays in
    the filter for defense-in-depth/readability even though a template has exactly
    one channel."""
    return (
        db.query(Notification)
        .filter(
            Notification.event == event,
            Notification.channel == channel,
            Notification.template_id == template_id,
            Notification.related_requirement_id == requirement_id,
            Notification.related_fulfillment_id == fulfillment_id,
        )
        .first()
    )


def _preference_blocks_channel(db: Session, *, channel: str, address: str) -> str | None:
    """Module 19 Enhancement — honors User.notification_preference (stored since Module
    19 but never previously consulted here, a documented gap). Only applies when the
    resolved recipient address matches a real User row (by email for EMAIL, by
    whatsapp_number for WHATSAPP) — a fixed non-user address (e.g. a procurement inbox
    template literal) has no preference to honor and is unaffected. Returns a
    human-readable skip reason, or None if the channel is allowed."""
    if channel == "EMAIL":
        user = db.query(User).filter(User.email == address).first()
        blocked = user is not None and user.notification_preference in ("WHATSAPP", "NONE")
    elif channel == "WHATSAPP":
        user = db.query(User).filter(User.whatsapp_number == address).first()
        blocked = user is not None and user.notification_preference in ("EMAIL", "NONE")
    else:
        return None
    if blocked:
        return f"Skipped: recipient's notification_preference ({user.notification_preference}) excludes {channel}"
    return None


def _maybe_auto_send(db: Session, rule, notification: Notification) -> None:
    """Deliver a just-submitted notification immediately, when its rule opts in.

    Only runs for `rule.auto_send` — every other rule keeps the existing two-step path
    (admin approves, then delivery happens), so the global `approval_required` control
    is untouched and rules default to OFF.

    This is not a second send pathway. It drives exactly the functions the manual UI
    already calls, per channel:
      * WHATSAPP -> approve_whatsapp_notification() then attempt_automation_send(), so
        the BROWSER_AUTOMATION provider, the persistent WhatsApp Web session and the
        DOM-evidence honesty contract are the existing ones. SENT is only ever set by
        that evidence check, never here.
      * EMAIL    -> approve_notification(), which performs the real SMTP send through
        services/email_provider.py and reflects the true outcome.

    Nothing here may raise. A delivery problem — WhatsApp session disconnected, SMTP
    unconfigured or refusing — must never roll back the business transaction that
    triggered the event (a contract really was renewed, a requirement really was
    approved). Failures are recorded on the notification so the outcome stays visible
    and truthful rather than silently swallowed."""
    if not getattr(rule, "auto_send", False):
        return
    if notification.status != "PENDING_APPROVAL":
        # Nothing to do: a rule with approval_required=False is already SENT, and a
        # FAILED one has a recorded reason worth keeping.
        return

    try:
        if notification.channel == "WHATSAPP":
            from .whatsapp_service import approve_whatsapp_notification, attempt_automation_send

            approve_whatsapp_notification(db, notification)
            notification.approved_by = "system:auto_send"
            attempt_automation_send(db, notification)
        elif notification.channel == "EMAIL":
            from .notification_service import approve_notification

            approve_notification(db, notification, "system:auto_send")
        else:
            return
    except Exception as exc:  # noqa: BLE001 - see docstring: must never break the caller
        if notification.channel == "WHATSAPP":
            notification.whatsapp_status = notification.whatsapp_status or "FAILED"
        notification.status = "FAILED"
        notification.failure_reason = f"Auto-send failed: {exc}"[:500]




def dispatch_event(
    db: Session, event: str, *, requirement: Requirement, fulfillment: FulfillmentRequest | None = None, user_id: str | None,
) -> list[Notification]:
    """Finds every enabled EMAIL/WHATSAPP rule configured for `event`, renders it against
    the real requirement/fulfillment, and writes+submits one Notification per rule.
    A rule whose resolved receiver address is blank still gets a history row (status
    FAILED, failure_reason set) rather than being silently skipped — visibility over
    silence, matching this codebase's documented-limitation discipline elsewhere."""
    rules = (
        db.query(NotificationTemplate)
        .filter(
            NotificationTemplate.event == event,
            NotificationTemplate.channel.in_(COMMUNICATION_CHANNELS),
            NotificationTemplate.is_active.is_(True),
        )
        .all()
    )
    if not rules:
        return []

    context = build_context(db, requirement=requirement, fulfillment=fulfillment)
    fulfillment_id = fulfillment.id if fulfillment else None
    results: list[Notification] = []

    for rule in rules:
        existing = _existing_notification(
            db, event=event, channel=rule.channel, template_id=rule.id,
            requirement_id=requirement.id, fulfillment_id=fulfillment_id,
        )
        if existing is not None:
            results.append(existing)
            continue

        if rule.channel == "EMAIL":
            address = render_template(rule.receiver_email, context).strip()
        else:
            address = render_template(rule.receiver_whatsapp_number, context).strip()
        subject = render_template(rule.subject_template, context) if rule.subject_template else None
        body = render_template(rule.body_template, context)

        notification = Notification(
            template_id=rule.id,
            recipient=address,
            channel=rule.channel,
            event=event,
            related_requirement_id=requirement.id,
            related_fulfillment_id=fulfillment_id,
            related_entity_type="requirement",
            related_entity_id=requirement.id,
            rendered_subject=subject,
            rendered_body=body,
            status="DRAFT",
            approval_required=True,
        )
        db.add(notification)
        db.flush()

        preference_skip_reason = _preference_blocks_channel(db, channel=rule.channel, address=address) if address else None
        if not address:
            notification.status = "FAILED"
            notification.failure_reason = "No recipient address resolved for this communication rule"
        elif preference_skip_reason:
            notification.status = "FAILED"
            notification.failure_reason = preference_skip_reason
        else:
            submit_notification(db, notification)
            _maybe_auto_send(db, rule, notification)

        audit_record(
            db, user_id=user_id, action="CREATE", entity="notification", entity_id=notification.id,
            new_value={"event": event, "channel": rule.channel, "recipient": address, "status": notification.status},
        )
        results.append(notification)

    db.commit()
    for notification in results:
        db.refresh(notification)
    return results


def dispatch_event_safe(
    db: Session, event: str, *, requirement: Requirement, fulfillment: FulfillmentRequest | None = None, user_id: str | None,
) -> list[Notification]:
    """Best-effort wrapper for workflow call sites (requirement_service.py,
    fulfillment_service.py): a communication failure must never break the primary
    Requirement/Fulfillment transition it's reacting to, since that transition already
    committed successfully before this is called."""
    try:
        return dispatch_event(db, event, requirement=requirement, fulfillment=fulfillment, user_id=user_id)
    except Exception:
        db.rollback()
        return []


# ---- Workflow consolidation pass — Incident notifications ----
#
# Incident previously never called this module at all (zero EVENT_INCIDENT_* events, no
# dispatch). Kept as a parallel, minimal path rather than folding into dispatch_event/
# build_context above: those are Requirement/Fulfillment-typed by name and by every field
# they resolve, and reshaping them to be entity-agnostic would be exactly the kind of
# notification-architecture rewrite the brief says not to do. Incident notifications reuse
# the same Notification table (via the generic related_entity_type/related_entity_id pair,
# which already exists for this), the same render_template/submit_notification/
# _preference_blocks_channel helpers, and the same admin-configures-a-NotificationTemplate-
# row convention — no default/seeded templates, same as Requirement/Fulfillment.

INCIDENT_SAMPLE_CONTEXT: dict[str, str] = {
    "incident_id": "INC-SAMPLE",
    "hospital": "Sample Hospital Network",
    "branch": "Sample Branch",
    "problem_category": "Device Issue",
    "severity": "MEDIUM",
    "status": "ASSIGNED",
    "description": "Sample incident description",
    "reported_by_name": "Sample Reporter",
    "reported_by_email": "reporter@example.com",
    "assigned_engineer_name": "Sample Engineer",
    "assigned_engineer_email": "engineer@example.com",
}


def build_incident_context(db: Session, *, incident: Incident) -> dict[str, str]:
    branch = db.get(Branch, incident.branch_id)
    hospital = db.get(HospitalNetwork, branch.network_id) if branch and branch.network_id else None
    reported_by = db.get(User, incident.reported_by) if incident.reported_by else None
    assigned_engineer = db.get(User, incident.assigned_engineer_id) if incident.assigned_engineer_id else None

    return {
        "incident_id": incident.id,
        "hospital": hospital.name if hospital else "-",
        "branch": branch.name if branch else "-",
        "problem_category": incident.problem_category or "-",
        "severity": incident.severity,
        "status": incident.status,
        "description": incident.description,
        "reported_by_name": reported_by.name if reported_by else "-",
        "reported_by_email": reported_by.email if reported_by else "",
        "assigned_engineer_name": assigned_engineer.name if assigned_engineer else "-",
        "assigned_engineer_email": assigned_engineer.email if assigned_engineer else "",
    }


def _existing_incident_notification(
    db: Session, *, event: str, channel: str, template_id: str, incident_id: str,
) -> Notification | None:
    return (
        db.query(Notification)
        .filter(
            Notification.event == event,
            Notification.channel == channel,
            Notification.template_id == template_id,
            Notification.related_entity_type == "incident",
            Notification.related_entity_id == incident_id,
        )
        .first()
    )


def dispatch_incident_event(db: Session, event: str, *, incident: Incident, user_id: str | None) -> list[Notification]:
    rules = (
        db.query(NotificationTemplate)
        .filter(
            NotificationTemplate.event == event,
            NotificationTemplate.channel.in_(COMMUNICATION_CHANNELS),
            NotificationTemplate.is_active.is_(True),
        )
        .all()
    )
    if not rules:
        return []

    context = build_incident_context(db, incident=incident)
    results: list[Notification] = []

    for rule in rules:
        existing = _existing_incident_notification(
            db, event=event, channel=rule.channel, template_id=rule.id, incident_id=incident.id,
        )
        if existing is not None:
            results.append(existing)
            continue

        if rule.channel == "EMAIL":
            address = render_template(rule.receiver_email, context).strip()
        else:
            address = render_template(rule.receiver_whatsapp_number, context).strip()
        subject = render_template(rule.subject_template, context) if rule.subject_template else None
        body = render_template(rule.body_template, context)

        notification = Notification(
            template_id=rule.id,
            recipient=address,
            channel=rule.channel,
            event=event,
            related_entity_type="incident",
            related_entity_id=incident.id,
            rendered_subject=subject,
            rendered_body=body,
            status="DRAFT",
            approval_required=True,
        )
        db.add(notification)
        db.flush()

        preference_skip_reason = _preference_blocks_channel(db, channel=rule.channel, address=address) if address else None
        if not address:
            notification.status = "FAILED"
            notification.failure_reason = "No recipient address resolved for this communication rule"
        elif preference_skip_reason:
            notification.status = "FAILED"
            notification.failure_reason = preference_skip_reason
        else:
            submit_notification(db, notification)
            _maybe_auto_send(db, rule, notification)

        audit_record(
            db, user_id=user_id, action="CREATE", entity="notification", entity_id=notification.id,
            new_value={"event": event, "channel": rule.channel, "recipient": address, "status": notification.status},
        )
        results.append(notification)

    db.commit()
    for notification in results:
        db.refresh(notification)
    return results


def dispatch_incident_event_safe(db: Session, event: str, *, incident: Incident, user_id: str | None) -> list[Notification]:
    """Best-effort wrapper for incident_service.py, matching dispatch_event_safe's
    contract: a communication failure must never break the primary Incident transition
    it's reacting to."""
    try:
        return dispatch_incident_event(db, event, incident=incident, user_id=user_id)
    except Exception:
        db.rollback()
        return []


# ---- Module 21 — Procurement notifications ----
#
# Quotation/Indent/PurchaseOrder/Invoice are four distinct entity types (unlike
# Incident above), so this stays generic over entity_type/entity rather than four
# separate typed dispatch functions — same underlying render_template/
# submit_notification/_preference_blocks_channel helpers and the same admin-
# configures-a-NotificationTemplate-row convention as every other event in this
# module (no default/seeded templates).

def build_procurement_context(db: Session, *, entity_type: str, entity) -> dict[str, str]:
    incident = db.get(Incident, getattr(entity, "incident_id", None)) if getattr(entity, "incident_id", None) else None
    asset = db.get(Asset, getattr(entity, "asset_id", None)) if getattr(entity, "asset_id", None) else None
    vendor_id = getattr(entity, "vendor_id", None)
    vendor = db.get(Vendor, vendor_id) if vendor_id else None
    assigned_engineer = db.get(User, incident.assigned_engineer_id) if incident and incident.assigned_engineer_id else None

    context = {
        "entity_type": entity_type,
        "incident_id": incident.id if incident else "-",
        "asset_id": asset.id if asset else "-",
        "asset_number": (asset.asset_number or asset.id) if asset else "-",
        "vendor_name": vendor.name if vendor else "-",
        "status": getattr(entity, "status", None) or getattr(entity, "verification_status", None) or "-",
        "assigned_engineer_name": assigned_engineer.name if assigned_engineer else "-",
        "assigned_engineer_email": assigned_engineer.email if assigned_engineer else "",
        "quotation_number": "-",
        "indent_number": "-",
        "po_number": "-",
        "invoice_number": "-",
        "amount": "-",
        "coverage_status": "-",
        "coverage_note": "-",
        "rejection_reason": "-",
    }

    if entity_type == "quotation":
        context["quotation_number"] = entity.quotation_number
        context["amount"] = str(entity.total)
        context["coverage_status"] = entity.coverage_status
        context["coverage_note"] = entity.coverage_note or "-"
        context["rejection_reason"] = entity.rejection_reason or "-"
        creator = db.get(User, entity.created_by) if entity.created_by else None
        context["created_by_name"] = creator.name if creator else "-"
        context["created_by_email"] = creator.email if creator else ""
    elif entity_type == "indent":
        context["indent_number"] = entity.indent_number
        context["amount"] = str(entity.amount)
    elif entity_type == "purchase_order":
        context["po_number"] = entity.po_number
        context["amount"] = str(entity.total)
    elif entity_type == "invoice":
        context["invoice_number"] = entity.invoice_number
        context["amount"] = str(entity.amount)

    return context


def _existing_procurement_notification(
    db: Session, *, event: str, channel: str, template_id: str, entity_type: str, entity_id: str,
) -> Notification | None:
    return (
        db.query(Notification)
        .filter(
            Notification.event == event,
            Notification.channel == channel,
            Notification.template_id == template_id,
            Notification.related_entity_type == entity_type,
            Notification.related_entity_id == entity_id,
        )
        .first()
    )


def dispatch_procurement_event(db: Session, event: str, *, entity_type: str, entity, user_id: str | None) -> list[Notification]:
    rules = (
        db.query(NotificationTemplate)
        .filter(
            NotificationTemplate.event == event,
            NotificationTemplate.channel.in_(COMMUNICATION_CHANNELS),
            NotificationTemplate.is_active.is_(True),
        )
        .all()
    )
    if not rules:
        return []

    context = build_procurement_context(db, entity_type=entity_type, entity=entity)
    results: list[Notification] = []

    for rule in rules:
        existing = _existing_procurement_notification(
            db, event=event, channel=rule.channel, template_id=rule.id, entity_type=entity_type, entity_id=entity.id,
        )
        if existing is not None:
            results.append(existing)
            continue

        if rule.channel == "EMAIL":
            address = render_template(rule.receiver_email, context).strip()
        else:
            address = render_template(rule.receiver_whatsapp_number, context).strip()
        subject = render_template(rule.subject_template, context) if rule.subject_template else None
        body = render_template(rule.body_template, context)

        notification = Notification(
            template_id=rule.id,
            recipient=address,
            channel=rule.channel,
            event=event,
            related_entity_type=entity_type,
            related_entity_id=entity.id,
            rendered_subject=subject,
            rendered_body=body,
            status="DRAFT",
            approval_required=True,
        )
        db.add(notification)
        db.flush()

        preference_skip_reason = _preference_blocks_channel(db, channel=rule.channel, address=address) if address else None
        if not address:
            notification.status = "FAILED"
            notification.failure_reason = "No recipient address resolved for this communication rule"
        elif preference_skip_reason:
            notification.status = "FAILED"
            notification.failure_reason = preference_skip_reason
        else:
            submit_notification(db, notification)
            _maybe_auto_send(db, rule, notification)

        audit_record(
            db, user_id=user_id, action="CREATE", entity="notification", entity_id=notification.id,
            new_value={"event": event, "channel": rule.channel, "recipient": address, "status": notification.status},
        )
        results.append(notification)

    db.commit()
    for notification in results:
        db.refresh(notification)
    return results


def dispatch_procurement_event_safe(db: Session, event: str, *, entity_type: str, entity, user_id: str | None) -> list[Notification]:
    """Best-effort wrapper for services/procurement_service.py, matching
    dispatch_event_safe's contract: a communication failure must never break the
    primary Quotation/Indent/PurchaseOrder/Invoice transition it's reacting to
    (which has already committed by the time this is called)."""
    try:
        return dispatch_procurement_event(db, event, entity_type=entity_type, entity=entity, user_id=user_id)
    except Exception:
        db.rollback()
        return []


# ---- Final Operational Gap Fix Pass — PM/Calibration due/overdue notifications ----
#
# PMSchedule and CalibrationSchedule are structurally identical (asset_id, frequency,
# next_due_date), so this stays generic over entity_type ("pm_schedule"/
# "calibration_schedule") the same way build_procurement_context/dispatch_procurement_event
# already do for four different procurement entity types — not a new pattern.

_MAINTENANCE_RECORD_MODEL = {"pm_schedule": PMRecord, "calibration_schedule": CalibrationRecord}
_MAINTENANCE_RECORD_SCHEDULE_FIELD = {"pm_schedule": "schedule_id", "calibration_schedule": "schedule_id"}


def build_maintenance_context(db: Session, *, entity_type: str, schedule, due_status: str) -> dict[str, str]:
    asset = db.get(Asset, schedule.asset_id)
    device_model = db.get(DeviceModel, asset.device_model_id) if asset else None
    branch = db.get(Branch, asset.branch_id) if asset and asset.branch_id else None
    department = db.get(Department, asset.department_id) if asset and asset.department_id else None
    hospital = db.get(HospitalNetwork, branch.network_id) if branch and branch.network_id else None

    # No "responsible BME" field exists on either schedule model — the closest
    # existing-architecture signal is whoever performed the most recent record
    # against this schedule (PMRecord.engineer_id has no Calibration equivalent, so
    # this is best-effort and legitimately blank for calibration or a never-serviced
    # schedule, same "visible not hidden" discipline as every other context builder here).
    last_engineer = None
    record_model = _MAINTENANCE_RECORD_MODEL[entity_type]
    if hasattr(record_model, "engineer_id"):
        last_record = (
            db.query(record_model)
            .filter(record_model.schedule_id == schedule.id)
            .order_by(record_model.created_at.desc())
            .first()
        )
        if last_record is not None and getattr(last_record, "engineer_id", None):
            last_engineer = db.get(User, last_record.engineer_id)

    return {
        "schedule_type": "Preventive Maintenance" if entity_type == "pm_schedule" else "Calibration",
        "schedule_id": schedule.id,
        "asset_id": asset.id if asset else "-",
        "asset_number": (asset.asset_number or asset.id) if asset else "-",
        "device_model_name": device_model.name if device_model else "-",
        "hospital": hospital.name if hospital else "-",
        "branch": branch.name if branch else "-",
        "department": department.name if department else "-",
        "frequency": schedule.frequency or "-",
        "next_due_date": schedule.next_due_date or "-",
        "due_status": due_status,
        "last_engineer_name": last_engineer.name if last_engineer else "-",
        "last_engineer_email": last_engineer.email if last_engineer else "",
    }


def _existing_maintenance_notification(
    db: Session, *, event: str, channel: str, template_id: str, entity_type: str, schedule_id: str,
) -> Notification | None:
    return (
        db.query(Notification)
        .filter(
            Notification.event == event,
            Notification.channel == channel,
            Notification.template_id == template_id,
            Notification.related_entity_type == entity_type,
            Notification.related_entity_id == schedule_id,
        )
        .first()
    )


def dispatch_maintenance_event(
    db: Session, event: str, *, entity_type: str, schedule, due_status: str, user_id: str | None,
) -> list[Notification]:
    rules = (
        db.query(NotificationTemplate)
        .filter(
            NotificationTemplate.event == event,
            NotificationTemplate.channel.in_(COMMUNICATION_CHANNELS),
            NotificationTemplate.is_active.is_(True),
        )
        .all()
    )
    if not rules:
        return []

    context = build_maintenance_context(db, entity_type=entity_type, schedule=schedule, due_status=due_status)
    results: list[Notification] = []

    for rule in rules:
        existing = _existing_maintenance_notification(
            db, event=event, channel=rule.channel, template_id=rule.id, entity_type=entity_type, schedule_id=schedule.id,
        )
        if existing is not None:
            results.append(existing)
            continue

        if rule.channel == "EMAIL":
            address = render_template(rule.receiver_email, context).strip()
        else:
            address = render_template(rule.receiver_whatsapp_number, context).strip()
        subject = render_template(rule.subject_template, context) if rule.subject_template else None
        body = render_template(rule.body_template, context)

        notification = Notification(
            template_id=rule.id,
            recipient=address,
            channel=rule.channel,
            event=event,
            related_entity_type=entity_type,
            related_entity_id=schedule.id,
            rendered_subject=subject,
            rendered_body=body,
            status="DRAFT",
            approval_required=True,
        )
        db.add(notification)
        db.flush()

        preference_skip_reason = _preference_blocks_channel(db, channel=rule.channel, address=address) if address else None
        if not address:
            notification.status = "FAILED"
            notification.failure_reason = "No recipient address resolved for this communication rule"
        elif preference_skip_reason:
            notification.status = "FAILED"
            notification.failure_reason = preference_skip_reason
        else:
            submit_notification(db, notification)
            _maybe_auto_send(db, rule, notification)

        audit_record(
            db, user_id=user_id, action="CREATE", entity="notification", entity_id=notification.id,
            new_value={"event": event, "channel": rule.channel, "recipient": address, "status": notification.status},
        )
        results.append(notification)

    db.commit()
    for notification in results:
        db.refresh(notification)
    return results


def dispatch_maintenance_event_safe(
    db: Session, event: str, *, entity_type: str, schedule, due_status: str, user_id: str | None,
) -> list[Notification]:
    """Best-effort wrapper for services/pm_service.py and services/calibration_service.py,
    matching dispatch_event_safe's contract: a communication failure must never break the
    primary schedule create/update/record transition it's reacting to (already committed
    by the time this is called)."""
    try:
        return dispatch_maintenance_event(db, event, entity_type=entity_type, schedule=schedule, due_status=due_status, user_id=user_id)
    except Exception:
        db.rollback()
        return []


# ---------------------------------------------------------------------------
# Contract (AMC / CMC / Warranty) communication
#
# Same shape as dispatch_procurement_event above — one entity-generic path over
# three tables sharing a business vocabulary — rather than three typed families.
# Contract routers/services never build notification text themselves; they emit an
# event and the configured Communication Rules decide channel, recipient and wording.
# ---------------------------------------------------------------------------

def _contract_covered_assets(db: Session, contract_type: str, contract) -> list[Asset]:
    """The PHYSICAL assets a contract covers, via the existing relationships:
    Warranty.asset_id directly, AMC/CMC through their join tables."""
    from ..models.contracts import AmcContractAsset, CmcContractAsset

    if contract_type == "WARRANTY":
        asset = db.get(Asset, contract.asset_id) if getattr(contract, "asset_id", None) else None
        return [asset] if asset is not None else []
    link_model, fk_field = (
        (AmcContractAsset, "amc_contract_id") if contract_type == "AMC" else (CmcContractAsset, "cmc_contract_id")
    )
    asset_ids = [
        row[0]
        for row in db.query(link_model.asset_id).filter(getattr(link_model, fk_field) == contract.id).all()
    ]
    if not asset_ids:
        return []
    return db.query(Asset).filter(Asset.id.in_(asset_ids)).all()


def _joined(values: list[str]) -> str:
    """Comma-separated, de-duplicated, order-preserving — a contract can cover several
    assets across several branches, so the asset/branch tokens are lists."""
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return ", ".join(seen) if seen else "-"


def build_contract_context(
    db: Session, *, contract_type: str, contract, event: str = "", expiry_days: str = "-",
    updated_by: str | None = None, assets: list | None = None,
) -> dict[str, str]:
    """Template variables for CONTRACT_EVENTS (see schemas/notification.py
    CONTRACT_TEMPLATE_VARIABLES).

    `assets` overrides the covered-asset set — used by the asset added/removed events so
    the message names the asset that actually changed rather than the whole contract.
    Vendor contact values come from the Vendor record; nothing here is free text.
    """
    covered = assets if assets is not None else _contract_covered_assets(db, contract_type, contract)
    covered = [a for a in covered if a is not None]

    model_ids = {a.device_model_id for a in covered if a.device_model_id}
    models = (
        {m.id: m for m in db.query(DeviceModel).filter(DeviceModel.id.in_(model_ids)).all()} if model_ids else {}
    )
    branch_ids = {a.branch_id for a in covered if a.branch_id}
    branches = (
        {b.id: b for b in db.query(Branch).filter(Branch.id.in_(branch_ids)).all()} if branch_ids else {}
    )
    department_ids = {a.department_id for a in covered if getattr(a, "department_id", None)}
    departments = (
        {d.id: d for d in db.query(Department).filter(Department.id.in_(department_ids)).all()}
        if department_ids
        else {}
    )

    vendor = db.get(Vendor, contract.vendor_id) if getattr(contract, "vendor_id", None) else None
    updater = db.get(User, updated_by) if updated_by else None

    device_names = [models[a.device_model_id].name for a in covered if a.device_model_id in models]
    # asset_number is nullable — say so rather than rendering an empty gap.
    asset_numbers = [(a.asset_number or "Asset number not assigned") for a in covered]

    number = getattr(contract, "contract_number", None) or getattr(contract, "warranty_number", None) or "-"

    return {
        "event": event or "-",
        "contract_type": contract_type,
        "contract_number": number,
        "contract_name": f"{contract_type} {number}" if number != "-" else contract_type,
        "status": getattr(contract, "status", None) or "-",
        "device_name": _joined(device_names),
        "model_name": _joined(device_names),
        "asset_number": _joined(asset_numbers),
        "asset_count": str(len(covered)),
        "branch_name": _joined([branches[a.branch_id].name for a in covered if a.branch_id in branches]),
        "department_name": _joined(
            [departments[a.department_id].name for a in covered if getattr(a, "department_id", None) in departments]
        ),
        "vendor_name": vendor.name if vendor else "-",
        # Vendor contact tokens resolve from the Vendor record only. A rule referencing
        # {{vendor_email}} against a vendor with no email renders empty, which
        # dispatch treats as "no recipient resolved" and FAILS the notification rather
        # than sending somewhere unintended.
        "vendor_email": (vendor.email or "") if vendor else "",
        "vendor_phone": (vendor.phone or "") if vendor else "",
        "contract_start": getattr(contract, "start_date", None) or "-",
        "contract_end": getattr(contract, "end_date", None) or "-",
        "expiry_days": expiry_days,
        "updated_by": updater.name if updater else "-",
    }


def _existing_contract_notification(
    db: Session, *, event: str, channel: str, template_id: str, entity_type: str, entity_id: str,
    dedup_key: str | None,
):
    """Idempotency. A `dedup_key` (used by the expiry scan, which is designed to be run
    daily) makes the check exact: same contract + same event + same reminder window is
    never notified twice, however many times the scan runs. Without one, the check falls
    back to the same event+channel+template+entity guard the procurement path uses."""
    query = db.query(Notification).filter(
        Notification.event == event,
        Notification.channel == channel,
        Notification.template_id == template_id,
        Notification.related_entity_type == entity_type,
        Notification.related_entity_id == entity_id,
    )
    if dedup_key is not None:
        query = query.filter(Notification.dedup_key == dedup_key)
    return query.first()


def dispatch_contract_event(
    db: Session, event: str, *, contract_type: str, contract, user_id: str | None,
    expiry_days: str = "-", assets: list | None = None, dedup_key: str | None = None,
) -> list[Notification]:
    rules = (
        db.query(NotificationTemplate)
        .filter(
            NotificationTemplate.event == event,
            NotificationTemplate.channel.in_(COMMUNICATION_CHANNELS),
            NotificationTemplate.is_active.is_(True),
        )
        .all()
    )
    if not rules:
        return []

    entity_type = f"{contract_type.lower()}_contract"
    context = build_contract_context(
        db, contract_type=contract_type, contract=contract, event=event,
        expiry_days=expiry_days, updated_by=user_id, assets=assets,
    )
    results: list[Notification] = []

    for rule in rules:
        existing = _existing_contract_notification(
            db, event=event, channel=rule.channel, template_id=rule.id,
            entity_type=entity_type, entity_id=contract.id, dedup_key=dedup_key,
        )
        if existing is not None:
            results.append(existing)
            continue

        if rule.channel == "EMAIL":
            address = render_template(rule.receiver_email, context).strip()
        else:
            address = render_template(rule.receiver_whatsapp_number, context).strip()
        subject = render_template(rule.subject_template, context) if rule.subject_template else None
        body = render_template(rule.body_template, context)

        notification = Notification(
            template_id=rule.id,
            recipient=address,
            channel=rule.channel,
            event=event,
            related_entity_type=entity_type,
            related_entity_id=contract.id,
            rendered_subject=subject,
            rendered_body=body,
            status="DRAFT",
            approval_required=True,
            dedup_key=dedup_key,
        )
        db.add(notification)
        db.flush()

        preference_skip_reason = _preference_blocks_channel(db, channel=rule.channel, address=address) if address else None
        if not address:
            notification.status = "FAILED"
            notification.failure_reason = "No recipient address resolved for this communication rule"
        elif preference_skip_reason:
            notification.status = "FAILED"
            notification.failure_reason = preference_skip_reason
        else:
            submit_notification(db, notification)
            _maybe_auto_send(db, rule, notification)

        audit_record(
            db, user_id=user_id, action="CREATE", entity="notification", entity_id=notification.id,
            new_value={"event": event, "channel": rule.channel, "recipient": address, "status": notification.status},
        )
        results.append(notification)

    db.commit()
    for notification in results:
        db.refresh(notification)
    return results


def dispatch_contract_event_safe(
    db: Session, event: str, *, contract_type: str, contract, user_id: str | None,
    expiry_days: str = "-", assets: list | None = None, dedup_key: str | None = None,
) -> list[Notification]:
    """Best-effort wrapper, matching dispatch_event_safe's contract: a communication
    failure must never break the contract transaction that triggered it."""
    try:
        return dispatch_contract_event(
            db, event, contract_type=contract_type, contract=contract, user_id=user_id,
            expiry_days=expiry_days, assets=assets, dedup_key=dedup_key,
        )
    except Exception:  # noqa: BLE001 - see docstring
        db.rollback()
        return []


# ---------------------------------------------------------------------------
# Phase 2 — Physical asset / device lifecycle communication
#
# Same shape as dispatch_contract_event above (one entity-generic path, the same
# rules -> template -> recipient -> Notification -> optional auto-send pipeline);
# only the context builder and the dedup key differ. Asset routers/services never
# build notification text themselves — they emit an event and the configured
# Communication Rules decide channel, recipient and wording.
#
# AssetHistory is NOT replaced or duplicated here. It stays the authoritative
# physical-asset history; a Phase 2 notification is keyed to the very AssetHistory
# row that recorded the change (see `dedup_key` below), so the communication trail
# and the asset trail name the same transition rather than telling two versions of it.
# ---------------------------------------------------------------------------

# Roles whose branch-scoped holders are offered as recipient tokens. Resolved from
# real UserRole rows — never a free-text address, and never "everyone".
_ASSET_ROLE_TOKENS = {
    "bme": "BIOMEDICAL_ENGINEER",
    "inventory_manager": "INVENTORY_MANAGER",
}


def _role_holder_for_branch(db: Session, role_code: str, branch_id: str | None) -> User | None:
    """The single user this role token resolves to for one branch.

    Prefers a user assigned to that exact branch (User.hospital_scope_id), falling
    back to an unscoped (organization-wide) holder of the same role — a BME or
    Inventory Manager covering every branch is how this product is actually staffed
    today, and returning nothing for them would silently drop the notification.
    Never returns a holder scoped to a DIFFERENT branch: that would leak one
    branch's device movements to another branch's staff.

    Deterministic (oldest matching user wins) so a rule's recipient does not change
    between two runs. One rule addresses one recipient — exactly as every Phase 1
    rule does; an admin who needs three stakeholders notified configures three
    rules, which is also what makes per-recipient channel/auto-send choices possible.
    """
    from ..models.identity import Role, UserRole

    role = db.query(Role).filter(Role.code == role_code).first()
    if role is None:
        return None
    candidates = (
        db.query(User)
        .join(UserRole, UserRole.user_id == User.id)
        .filter(UserRole.role_id == role.id, User.is_active.is_(True))
        .order_by(User.created_at, User.id)
        .all()
    )
    if branch_id is not None:
        for user in candidates:
            if user.hospital_scope_id == branch_id:
                return user
    for user in candidates:
        if user.hospital_scope_id is None:
            return user
    return None


def _branch_recipient_tokens(db: Session, branch, prefix: str) -> dict[str, str]:
    """Contact tokens for one branch: the branch's own stored contact columns and its
    real Unit Head (Branch.unit_head_user_id -> User). `prefix` is "" for the asset's
    current branch and "previous_" for the branch it left, so a transfer rule can
    address either side without needing a second event."""
    unit_head = db.get(User, branch.unit_head_user_id) if branch and branch.unit_head_user_id else None
    return {
        f"{prefix}branch_email": (branch.branch_email or "") if branch else "",
        f"{prefix}branch_phone": (branch.branch_phone or "") if branch else "",
        f"{prefix}branch_whatsapp": (branch.branch_whatsapp or "") if branch else "",
        f"{prefix}unit_head_name": unit_head.name if unit_head else "-",
        f"{prefix}unit_head_email": (unit_head.email or "") if unit_head else "",
        f"{prefix}unit_head_phone": (unit_head.whatsapp_number or unit_head.phone or "") if unit_head else "",
    }


def build_asset_context(
    db: Session,
    *,
    asset: Asset,
    event: str = "",
    updated_by: str | None = None,
    previous: dict | None = None,
    reference: str | None = None,
    notes: str | None = None,
) -> dict[str, str]:
    """Template variables for ASSET_EVENTS (see schemas/notification.py
    ASSET_TEMPLATE_VARIABLES).

    `previous` is the pre-change column snapshot the calling workflow already builds
    for its own audit/history write (`{c.name: getattr(obj, c.name) ...}`), reused
    verbatim rather than recomputed, so the previous/new values in a message can never
    disagree with the values recorded in AssetHistory.

    Every value is derived from stored records. asset_number is nullable by design
    (a device can exist before a physical tag does), so it renders as an explicit
    "Asset number not assigned" rather than an empty gap in the middle of a message.
    """
    previous = previous or {}
    device_model = db.get(DeviceModel, asset.device_model_id) if asset.device_model_id else None
    branch = db.get(Branch, asset.branch_id) if asset.branch_id else None
    department = db.get(Department, asset.department_id) if asset.department_id else None
    location = db.get(Location, asset.location_id) if asset.location_id else None
    updater = db.get(User, updated_by) if updated_by else None

    def _named(model, entity_id) -> str:
        if not entity_id:
            return "-"
        row = db.get(model, entity_id)
        return row.name if row else "-"

    previous_branch_id = previous.get("branch_id")
    previous_branch = db.get(Branch, previous_branch_id) if previous_branch_id else None

    # Coverage vendor is derived live from the AMC/CMC/Warranty tables by the one
    # existing helper (asset_lifecycle_service), never duplicated onto Asset.
    from .asset_lifecycle_service import get_incident_coverage

    try:
        coverage = get_incident_coverage(db, asset.id)
        vendor = db.get(Vendor, coverage.vendor_id) if coverage.vendor_id else None
    except Exception:  # noqa: BLE001 — a coverage lookup problem must not break messaging
        vendor = None

    context = {
        "event": event or "-",
        "asset_id": asset.id,
        # Nullable by design — say so rather than rendering an empty gap.
        "asset_number": asset.asset_number or "Asset number not assigned",
        "serial_number": asset.serial_number or "-",
        "device_name": device_model.name if device_model else "-",
        "model_name": device_model.name if device_model else "-",
        "device_model": (device_model.model_number or device_model.name) if device_model else "-",
        "manufacturer": (device_model.manufacturer or "-") if device_model else "-",
        "category": (device_model.category or "-") if device_model else "-",
        "status": asset.status or "-",
        "previous_status": previous.get("status") or "-",
        "new_status": asset.status or "-",
        "criticality": asset.criticality or "-",
        "branch_name": branch.name if branch else "-",
        "department_name": department.name if department else "-",
        "location_name": location.name if location else "-",
        "previous_branch": _named(Branch, previous_branch_id),
        "new_branch": branch.name if branch else "-",
        "previous_department": _named(Department, previous.get("department_id")),
        "new_department": department.name if department else "-",
        "previous_location": _named(Location, previous.get("location_id")),
        "new_location": location.name if location else "-",
        "vendor_name": vendor.name if vendor else "-",
        "vendor_email": (vendor.email or "") if vendor else "",
        "vendor_phone": (vendor.phone or "") if vendor else "",
        "updated_by": updater.name if updater else "-",
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else "-",
        "reference": reference or "-",
        "notes": notes or "-",
    }
    context.update(_branch_recipient_tokens(db, branch, ""))
    context.update(_branch_recipient_tokens(db, previous_branch, "previous_"))
    for token, role_code in _ASSET_ROLE_TOKENS.items():
        holder = _role_holder_for_branch(db, role_code, asset.branch_id)
        context[f"{token}_name"] = holder.name if holder else "-"
        context[f"{token}_email"] = (holder.email or "") if holder else ""
        context[f"{token}_phone"] = (holder.whatsapp_number or holder.phone or "") if holder else ""
    return context


def _existing_asset_notification(
    db: Session, *, event: str, channel: str, template_id: str, asset_id: str, dedup_key: str,
) -> Notification | None:
    """Deduplication for asset events.

    Unlike the contract/procurement families, one asset legitimately experiences the
    SAME event many times (a device can change status repeatedly), so the entity-level
    guard those use would wrongly suppress the second, genuine occurrence. The key is
    therefore always the specific transition — normally the AssetHistory row id that
    recorded it, so the notification and the authoritative asset history point at one
    another. That also gives the property the brief asks for: one allocation event for
    one allocation, however many internal save/update steps ran underneath it.
    """
    return (
        db.query(Notification)
        .filter(
            Notification.event == event,
            Notification.channel == channel,
            Notification.template_id == template_id,
            Notification.related_entity_type == "asset",
            Notification.related_entity_id == asset_id,
            Notification.dedup_key == dedup_key,
        )
        .first()
    )


def dispatch_asset_event(
    db: Session,
    event: str,
    *,
    asset: Asset,
    user_id: str | None,
    dedup_key: str,
    previous: dict | None = None,
    reference: str | None = None,
    notes: str | None = None,
) -> list[Notification]:
    rules = (
        db.query(NotificationTemplate)
        .filter(
            NotificationTemplate.event == event,
            NotificationTemplate.channel.in_(COMMUNICATION_CHANNELS),
            NotificationTemplate.is_active.is_(True),
        )
        .all()
    )
    if not rules:
        return []

    context = build_asset_context(
        db, asset=asset, event=event, updated_by=user_id, previous=previous,
        reference=reference, notes=notes,
    )
    results: list[Notification] = []

    for rule in rules:
        existing = _existing_asset_notification(
            db, event=event, channel=rule.channel, template_id=rule.id,
            asset_id=asset.id, dedup_key=dedup_key,
        )
        if existing is not None:
            results.append(existing)
            continue

        if rule.channel == "EMAIL":
            address = render_template(rule.receiver_email, context).strip()
        else:
            address = render_template(rule.receiver_whatsapp_number, context).strip()
        subject = render_template(rule.subject_template, context) if rule.subject_template else None
        body = render_template(rule.body_template, context)

        notification = Notification(
            template_id=rule.id,
            recipient=address,
            channel=rule.channel,
            event=event,
            related_entity_type="asset",
            related_entity_id=asset.id,
            rendered_subject=subject,
            rendered_body=body,
            status="DRAFT",
            approval_required=True,
            dedup_key=dedup_key,
        )
        db.add(notification)
        db.flush()

        preference_skip_reason = _preference_blocks_channel(db, channel=rule.channel, address=address) if address else None
        if not address:
            notification.status = "FAILED"
            notification.failure_reason = "No recipient address resolved for this communication rule"
        elif preference_skip_reason:
            notification.status = "FAILED"
            notification.failure_reason = preference_skip_reason
        else:
            submit_notification(db, notification)
            _maybe_auto_send(db, rule, notification)

        audit_record(
            db, user_id=user_id, action="CREATE", entity="notification", entity_id=notification.id,
            new_value={"event": event, "channel": rule.channel, "recipient": address, "status": notification.status},
        )
        results.append(notification)

    db.commit()
    for notification in results:
        db.refresh(notification)
    return results


def dispatch_asset_event_safe(
    db: Session,
    event: str,
    *,
    asset: Asset,
    user_id: str | None,
    dedup_key: str,
    previous: dict | None = None,
    reference: str | None = None,
    notes: str | None = None,
) -> list[Notification]:
    """Best-effort wrapper, matching dispatch_event_safe's contract: the asset really
    was created / allocated / transferred / condemned, and a delivery or configuration
    problem must never roll that back. Always called AFTER the business transaction
    that triggered it has committed."""
    try:
        return dispatch_asset_event(
            db, event, asset=asset, user_id=user_id, dedup_key=dedup_key,
            previous=previous, reference=reference, notes=notes,
        )
    except Exception:  # noqa: BLE001 - see docstring
        db.rollback()
        return []
