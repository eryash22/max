from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.fulfillment import FulfillmentRequest
from ..models.notifications import Notification, NotificationTemplate
from ..models.requirement import Requirement
from ..repositories.crud import paginate
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.notification import (
    ALL_NOTIFICATION_EVENTS,
    EVENT_LABELS,
    CommunicationProviderConfigIn,
    CommunicationProviderConfigOut,
    NotificationEventOut,
    NotificationIn,
    NotificationOut,
    NotificationRejectIn,
    NotificationTemplateDeleteOut,
    NotificationTemplateIn,
    NotificationTemplateOut,
    NotificationTemplatePreviewIn,
    NotificationTemplatePreviewOut,
    WhatsAppAutomationStatusOut,
    WhatsAppMarkFailedIn,
    WhatsAppTestMessageIn,
    WhatsAppTestMessageOut,
)
from ..security.permissions import NOTIFICATION_APPROVE, NOTIFICATION_READ, NOTIFICATION_TEMPLATE_DELETE
from ..services import whatsapp_automation_service
from ..services.communication_service import SAMPLE_CONTEXT, build_context, render_template
from ..services.notification_service import (
    approve_notification,
    default_approval_required,
    get_notification_or_404,
    reject_notification,
    retry_email_notification,
    submit_notification,
    validate_related_entity,
)
from ..services.whatsapp_provider import PROVIDERS
from ..services.whatsapp_service import (
    attempt_automation_send,
    confirm_whatsapp_sent,
    get_or_create_provider_config,
    mark_whatsapp_failed,
    mark_whatsapp_opened,
    update_provider_config,
)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


def _get_template_or_404(db: Session, template_id: str) -> NotificationTemplate:
    obj = db.get(NotificationTemplate, template_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification template not found")
    return obj


# ---- Templates ----
# Template management is gated on NOTIFICATION_APPROVE (the more privileged of the two
# pre-seeded notification permission codes, held only by Biomedical Admin) rather than a
# dedicated notification.write code, since none was pre-seeded in security/permissions.py
# (verified before writing any code) and configuring what gets sent is the same class of
# admin-level policy action RBAC_MATRIX.md calls "Approve external communication and
# high-risk action" — see PROJECT_STATUS.md for the full rationale.

@router.get("/events", response_model=list[NotificationEventOut])
def list_events(_: CurrentUser = Depends(require_permission(NOTIFICATION_READ))):
    """Every event a communication rule can be configured against — the admin UI's
    event selector fetches this instead of hard-coding its own (second, driftable)
    list. Source of truth is schemas/notification.py's ALL_NOTIFICATION_EVENTS,
    the same constant dispatch_event/dispatch_procurement_event/
    dispatch_maintenance_event's callers already key off; nothing new is added
    here beyond that list."""
    return [NotificationEventOut(code=event, label=EVENT_LABELS.get(event, event)) for event in ALL_NOTIFICATION_EVENTS]


@router.get("/templates", response_model=Page[NotificationTemplateOut])
def list_templates(page: int = 1, page_size: int = 25, search: str | None = None, channel: str | None = None,
                    event: str | None = None, is_active: bool | None = None,
                    db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(NOTIFICATION_READ))):
    items, total = paginate(db, NotificationTemplate, page=page, page_size=page_size, search=search,
                             search_fields=["code"], channel=channel, event=event, is_active=is_active)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/templates/{template_id}", response_model=NotificationTemplateOut)
def get_template(template_id: str, db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(NOTIFICATION_READ))):
    return _get_template_or_404(db, template_id)


@router.post("/templates", response_model=NotificationTemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(payload: NotificationTemplateIn, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    if db.query(NotificationTemplate).filter(NotificationTemplate.code == payload.code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A template with this code already exists")
    obj = NotificationTemplate(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="notification_template", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/templates/{template_id}", response_model=NotificationTemplateOut)
def update_template(template_id: str, payload: NotificationTemplateIn, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    obj = _get_template_or_404(db, template_id)
    duplicate = db.query(NotificationTemplate).filter(
        NotificationTemplate.code == payload.code, NotificationTemplate.id != template_id
    ).first()
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A template with this code already exists")
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    for key, value in payload.model_dump().items():
        setattr(obj, key, value)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="notification_template", entity_id=obj.id, old_value=before, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/templates/{template_id}", response_model=NotificationTemplateDeleteOut)
def delete_template(template_id: str, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(NOTIFICATION_TEMPLATE_DELETE))):
    """Batch F — Item 7: Super Admin-only (notification_template.delete, SUPER_ADMIN via
    ALL_PERMISSIONS, no other default role — same convention as
    routers/requirements.py delete_requirement / REQUIREMENT_DELETE). Unlike the
    reference-count-then-409 hard-delete pattern used elsewhere (vendors/hospitals/
    devices/contracts), this is a WARNING not a hard block: the model has no
    is_system_required/is_critical flag (verified against models/notifications.py and
    the migrations), so "critical" is computed here — if this was the last remaining
    ACTIVE template for this template's (channel, event) pair, the event is now
    uncovered and the response carries a warning, but the delete still proceeds.

    Notification.template_id is nullable — it's a record of what was sent, not owned
    by the template — so any Notification still pointing at this template is detached
    (template_id set to NULL) rather than blocking the delete; a naive `db.delete()`
    here 500s on the FK instead of the intended clean delete-with-warning."""
    obj = _get_template_or_404(db, template_id)
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    db.query(Notification).filter(Notification.template_id == obj.id).update(
        {"template_id": None}, synchronize_session=False
    )

    other_active = (
        db.query(NotificationTemplate)
        .filter(
            NotificationTemplate.id != obj.id,
            NotificationTemplate.channel == obj.channel,
            NotificationTemplate.event == obj.event,
            NotificationTemplate.is_active.is_(True),
        )
        .first()
    )
    warning = None
    if obj.is_active and other_active is None:
        warning = (
            f"No active {obj.channel} template now covers the '{EVENT_LABELS.get(obj.event, obj.event)}' event"
        )

    audit_record(db, user_id=current_user.id, action="DELETE", entity="notification_template",
                 entity_id=obj.id, old_value=before)
    db.delete(obj)
    db.commit()
    return NotificationTemplateDeleteOut(deleted=True, warning=warning)


@router.post("/templates/preview", response_model=NotificationTemplatePreviewOut)
def preview_template(payload: NotificationTemplatePreviewIn, db: Session = Depends(get_db),
                      _: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    """Renders a template's fields before saving — against a real requirement (optionally
    + fulfillment) when `requirement_id` is given, otherwise against a fixed sample
    context. Never creates a Notification and never sends anything."""
    used_sample_data = payload.requirement_id is None
    if payload.requirement_id is not None:
        requirement = db.get(Requirement, payload.requirement_id)
        if requirement is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found")
        fulfillment = None
        if payload.fulfillment_id is not None:
            fulfillment = db.get(FulfillmentRequest, payload.fulfillment_id)
            if fulfillment is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fulfillment not found")
        context = build_context(db, requirement=requirement, fulfillment=fulfillment)
    else:
        context = SAMPLE_CONTEXT

    return NotificationTemplatePreviewOut(
        rendered_subject=render_template(payload.subject_template, context) if payload.subject_template else None,
        rendered_body=render_template(payload.body_template, context),
        rendered_sender_name=render_template(payload.sender_name, context) or None,
        rendered_sender_email=render_template(payload.sender_email, context) or None,
        rendered_sender_whatsapp_number=render_template(payload.sender_whatsapp_number, context) or None,
        rendered_receiver_name=render_template(payload.receiver_name, context) or None,
        rendered_receiver_email=render_template(payload.receiver_email, context) or None,
        rendered_receiver_whatsapp_number=render_template(payload.receiver_whatsapp_number, context) or None,
        used_sample_data=used_sample_data,
    )


# ---- Notifications ----

@router.get("", response_model=Page[NotificationOut])
def list_notifications(page: int = 1, page_size: int = 25, channel: str | None = None, event: str | None = None,
                        status_filter: str | None = None, recipient: str | None = None,
                        related_entity_type: str | None = None, related_entity_id: str | None = None,
                        related_requirement_id: str | None = None, related_fulfillment_id: str | None = None,
                        db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(NOTIFICATION_READ))):
    if (related_entity_type is None) != (related_entity_id is None):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                             detail="related_entity_type and related_entity_id must be provided together")
    filters = {"channel": channel, "event": event, "recipient": recipient,
               "related_entity_type": related_entity_type, "related_entity_id": related_entity_id,
               "related_requirement_id": related_requirement_id, "related_fulfillment_id": related_fulfillment_id}
    if status_filter:
        filters["status"] = status_filter
    items, total = paginate(db, Notification, page=page, page_size=page_size, **filters)
    return Page(items=items, total=total, page=page, page_size=page_size)


# ---- Module 19 — communication provider configuration (admin-only) ----
# Registered ahead of GET /{notification_id} below: a path-parameter route would
# otherwise swallow "/provider-config" as if it were a notification_id, since routes
# match in registration order. Gated on NOTIFICATION_APPROVE, not NOTIFICATION_READ,
# so a read-only role (e.g. Biomedical Engineer) cannot see or change which provider
# is active.

@router.get("/provider-config", response_model=CommunicationProviderConfigOut)
def get_provider_config(db: Session = Depends(get_db),
                         _: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    config = get_or_create_provider_config(db)
    db.commit()
    return CommunicationProviderConfigOut(
        channel=config.channel, active_provider=config.active_provider,
        is_configured=PROVIDERS[config.active_provider].is_configured(),
    )


@router.patch("/provider-config", response_model=CommunicationProviderConfigOut)
def patch_provider_config(payload: CommunicationProviderConfigIn, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    config = get_or_create_provider_config(db)
    before = {"active_provider": config.active_provider}
    update_provider_config(db, active_provider=payload.active_provider)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="communication_provider_config",
                 entity_id=config.id, old_value=before, new_value={"active_provider": config.active_provider})
    db.commit()
    db.refresh(config)
    return CommunicationProviderConfigOut(
        channel=config.channel, active_provider=config.active_provider,
        is_configured=PROVIDERS[config.active_provider].is_configured(),
    )


# ---- Module 19 Enhancement — WhatsApp Web browser-automation session (admin-only) ----
# Same NOTIFICATION_APPROVE gate as provider-config above, and registered ahead of the
# "/{notification_id}" path-parameter route for the same reason.

@router.get("/whatsapp-automation/status", response_model=WhatsAppAutomationStatusOut)
def whatsapp_automation_status(_: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    return whatsapp_automation_service.get_session().get_status()


@router.post("/whatsapp-automation/connect", response_model=WhatsAppAutomationStatusOut)
def whatsapp_automation_connect(current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE)),
                                 db: Session = Depends(get_db)):
    result = whatsapp_automation_service.get_session().connect()
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="whatsapp_automation_session",
                 entity_id="singleton", new_value={"action": "connect", "state": result["state"]})
    db.commit()
    return result


@router.post("/whatsapp-automation/disconnect", response_model=WhatsAppAutomationStatusOut)
def whatsapp_automation_disconnect(current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE)),
                                    db: Session = Depends(get_db)):
    result = whatsapp_automation_service.get_session().disconnect()
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="whatsapp_automation_session",
                 entity_id="singleton", new_value={"action": "disconnect", "state": result["state"]})
    db.commit()
    return result


@router.post("/whatsapp-automation/test-message", response_model=WhatsAppTestMessageOut)
def whatsapp_automation_test_message(payload: WhatsAppTestMessageIn,
                                      current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE)),
                                      db: Session = Depends(get_db)):
    """Admin-only manual probe of the connected session — does not create or touch any
    Notification row, mirroring how templates/preview never sends anything either."""
    result = whatsapp_automation_service.get_session().send_message(recipient=payload.recipient, text=payload.message)
    audit_record(db, user_id=current_user.id, action="CREATE", entity="whatsapp_automation_test_message",
                 entity_id="n/a", new_value={"recipient": payload.recipient, "result_state": result.state})
    db.commit()
    return WhatsAppTestMessageOut(state=result.state, detail=result.detail, evidence=result.evidence)


@router.post("/{notification_id}/whatsapp/automation/attempt-send", response_model=NotificationOut)
def whatsapp_automation_attempt_send(notification_id: str, db: Session = Depends(get_db),
                                      current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    obj = get_notification_or_404(db, notification_id)
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    attempt_automation_send(db, obj)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="notification", entity_id=obj.id,
                 old_value=before, new_value={"status": obj.status, "whatsapp_status": obj.whatsapp_status,
                                               "failure_reason": obj.failure_reason})
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/{notification_id}", response_model=NotificationOut)
def get_notification(notification_id: str, db: Session = Depends(get_db),
                      _: CurrentUser = Depends(require_permission(NOTIFICATION_READ))):
    return get_notification_or_404(db, notification_id)


@router.post("", response_model=NotificationOut, status_code=status.HTTP_201_CREATED)
def create_notification(payload: NotificationIn, db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    # Security hardening pass — this legacy manual-create path takes a free-text
    # `recipient` (schemas/notification.py) instead of resolving one from a
    # configured communication rule the way every dispatch_event auto-send path does
    # (services/communication_service.py). Gating it on the same NOTIFICATION_APPROVE
    # every other notification-mutating endpoint below already requires (not the
    # broader, non-admin-held NOTIFICATION_READ) stops a caller from redirecting a
    # notification to an arbitrary address outside the rule-driven design.
    if payload.template_id is not None:
        _get_template_or_404(db, payload.template_id)
    validate_related_entity(db, payload.related_entity_type, payload.related_entity_id)

    obj = Notification(**payload.model_dump(), status="DRAFT", approval_required=default_approval_required(payload.channel))
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="notification", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/{notification_id}/submit", response_model=NotificationOut)
def submit(notification_id: str, db: Session = Depends(get_db),
           current_user: CurrentUser = Depends(require_permission(NOTIFICATION_READ))):
    obj = get_notification_or_404(db, notification_id)
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    submit_notification(db, obj)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="notification", entity_id=obj.id,
                 old_value=before, new_value={"status": obj.status})
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/{notification_id}/approve", response_model=NotificationOut)
def approve(notification_id: str, db: Session = Depends(get_db),
            current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    obj = get_notification_or_404(db, notification_id)
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    approve_notification(db, obj, approved_by=current_user.id)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="notification", entity_id=obj.id,
                 old_value=before, new_value={"status": obj.status, "approved_by": obj.approved_by})
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/{notification_id}/retry", response_model=NotificationOut)
def retry(notification_id: str, db: Session = Depends(get_db),
          current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    """Module 19 Enhancement — manual retry of a FAILED EMAIL send. See
    services/notification_service.retry_email_notification for why this is manual
    rather than an automatic background retry queue."""
    obj = get_notification_or_404(db, notification_id)
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    retry_email_notification(db, obj)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="notification", entity_id=obj.id,
                 old_value=before, new_value={"status": obj.status, "failure_reason": obj.failure_reason})
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/{notification_id}/reject", response_model=NotificationOut)
def reject(notification_id: str, payload: NotificationRejectIn, db: Session = Depends(get_db),
           current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    obj = get_notification_or_404(db, notification_id)
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    reject_notification(db, obj, payload.failure_reason)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="notification", entity_id=obj.id,
                 old_value=before, new_value={"status": obj.status, "failure_reason": obj.failure_reason})
    db.commit()
    db.refresh(obj)
    return obj


# ---- Module 19 — WhatsApp honest state machine ----
# See models/notifications.py's Notification.whatsapp_status docstring and
# services/whatsapp_service.py for the state machine itself.

@router.post("/{notification_id}/whatsapp/opened", response_model=NotificationOut)
def whatsapp_opened(notification_id: str, db: Session = Depends(get_db),
                     _: CurrentUser = Depends(require_permission(NOTIFICATION_READ))):
    """Best-effort telemetry the frontend fires right after opening the wa.me link.
    Not required for the state machine to progress — a missed call never blocks
    confirm-sent/mark-failed below."""
    obj = get_notification_or_404(db, notification_id)
    mark_whatsapp_opened(db, obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/{notification_id}/whatsapp/confirm-sent", response_model=NotificationOut)
def whatsapp_confirm_sent(notification_id: str, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    obj = get_notification_or_404(db, notification_id)
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    confirm_whatsapp_sent(db, obj, confirmed_by=current_user.id)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="notification", entity_id=obj.id,
                 old_value=before, new_value={"status": obj.status, "whatsapp_status": obj.whatsapp_status})
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/{notification_id}/whatsapp/mark-failed", response_model=NotificationOut)
def whatsapp_mark_failed(notification_id: str, payload: WhatsAppMarkFailedIn, db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    obj = get_notification_or_404(db, notification_id)
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    mark_whatsapp_failed(db, obj, failure_reason=payload.failure_reason)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="notification", entity_id=obj.id,
                 old_value=before, new_value={"status": obj.status, "whatsapp_status": obj.whatsapp_status,
                                               "failure_reason": obj.failure_reason})
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/templates/seed-defaults")
def seed_default_templates(db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(NOTIFICATION_APPROVE))):
    """Insert the built-in contract and physical-asset Communication Rules that don't exist yet.

    Idempotent and non-destructive: seeding is keyed on rule `code` and an existing
    rule is never modified, so an admin's edits to wording, recipient or the enabled
    flag survive re-running this.

    Every seeded rule arrives DISABLED and non-auto-send — an admin reviews the wording
    and the resolved recipient before anything reaches a real vendor.

    Exposed as an explicit action rather than run at startup because this application's
    startup seeding hook is currently absent from app/main.py (see the /api restructure
    in progress); an endpoint also makes the seeding attributable in the audit trail.
    """
    from ..services.asset_template_seed import ensure_asset_templates
    from ..services.contract_template_seed import ensure_contract_templates

    # One seeding action, not one per event family — an admin should not have to know
    # which module a default rule came from.
    created = ensure_contract_templates(db) + ensure_asset_templates(db)
    if created:
        audit_record(db, user_id=current_user.id, action="CREATE", entity="notification_template",
                     entity_id="seed-defaults", new_value={"created": created})
        db.commit()
    return {"created": created}
