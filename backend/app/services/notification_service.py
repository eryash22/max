from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .document_service import ENTITY_MODEL_MAP
from .email_provider import EmailSendError, send_email_smtp
from ..models.notifications import Notification
from ..schemas.notification import EXTERNAL_CHANNELS


def validate_related_entity(db: Session, entity_type: str | None, entity_id: str | None) -> None:
    if (entity_type is None) != (entity_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="related_entity_type and related_entity_id must be provided together",
        )
    if entity_type is None:
        return
    if entity_type not in ENTITY_MODEL_MAP:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unsupported related_entity_type: {entity_type}")
    model = ENTITY_MODEL_MAP[entity_type]
    if db.get(model, entity_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{entity_type} not found")


def default_approval_required(channel: str) -> bool:
    """External channels (EMAIL/WHATSAPP/SMS) default approval_required=True per the
    models/notifications.py docstring; IN_APP carries no external-send risk so it
    defaults to False and can go straight to SENT.
    """
    return channel in EXTERNAL_CHANNELS


def get_notification_or_404(db: Session, notification_id: str) -> Notification:
    obj = db.get(Notification, notification_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return obj


def submit_notification(db: Session, notification: Notification) -> Notification:
    if notification.status != "DRAFT":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a DRAFT notification can be submitted")
    if notification.approval_required:
        notification.status = "PENDING_APPROVAL"
    else:
        # No external send-provider abstraction exists in this codebase (verified before
        # writing this module) — an in-app notification with no approval requirement is
        # considered "sent" the moment it's submitted, since there is no external transport
        # step to wait on.
        notification.status = "SENT"
        notification.sent_at = datetime.utcnow().isoformat()
    return notification


def approve_notification(db: Session, notification: Notification, approved_by: str) -> Notification:
    if notification.status != "PENDING_APPROVAL":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a notification pending approval can be approved")
    notification.approved_by = approved_by

    # Module 17B: a notification carrying a rendered_body was created by
    # communication_service.dispatch_event() from a configured rule — for EMAIL, approving
    # it now attempts a real SMTP send (see services/email_provider.py) and reflects the
    # real outcome (SENT or FAILED+failure_reason) instead of an unconditional SENT.
    # A notification with no rendered_body (the original Module 10 manual-create path,
    # with no template rendering ever run) keeps the exact pre-17B behavior below —
    # approving just marks it SENT within this system, no real dispatch attempted — so no
    # existing caller of this generic endpoint is affected by this module.
    # Module 19: WHATSAPP no longer jumps straight to SENT on approve — there is no way
    # to detect a real send without browser automation (deliberately not built, see
    # PROJECT_STATUS.md), so approving only queues the wa.me link; `status` stays
    # PENDING_APPROVAL until an explicit confirm-sent/mark-failed action — see
    # services/whatsapp_service.py, called from routers/notifications.py instead of here
    # (avoids a circular import: whatsapp_service also needs get_notification_or_404-style
    # helpers from this module).
    if notification.channel == "EMAIL" and notification.rendered_body:
        try:
            send_email_smtp(
                to_email=notification.recipient,
                subject=notification.rendered_subject or "",
                body=notification.rendered_body,
            )
        except EmailSendError as exc:
            notification.status = "FAILED"
            notification.failure_reason = str(exc)
            return notification
        notification.status = "SENT"
        notification.sent_at = datetime.utcnow().isoformat()
        return notification

    if notification.channel == "WHATSAPP":
        from .whatsapp_service import approve_whatsapp_notification

        return approve_whatsapp_notification(db, notification)

    notification.status = "SENT"
    notification.sent_at = datetime.utcnow().isoformat()
    return notification


def retry_email_notification(db: Session, notification: Notification) -> Notification:
    """Module 19 Enhancement — a confirmed gap: a FAILED EMAIL send (e.g. SMTP was
    briefly unreachable) previously had no way back to SENT short of manually creating
    a new notification. This re-attempts the same rendered_subject/rendered_body against
    the same recipient — no re-rendering, no new approval step, since nothing about the
    message changed, only the transport attempt. Deliberately a manual, explicit action
    (not an automatic background retry loop/queue, which is a bigger addition than this
    hardening pass's scope) — see PROJECT_STATUS.md Module 19 Enhancement."""
    if notification.channel != "EMAIL":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only an EMAIL notification can be retried")
    if notification.status != "FAILED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a FAILED notification can be retried")
    if not notification.rendered_body:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Nothing to retry — this notification has no rendered content")

    try:
        send_email_smtp(
            to_email=notification.recipient,
            subject=notification.rendered_subject or "",
            body=notification.rendered_body,
        )
    except EmailSendError as exc:
        notification.failure_reason = str(exc)
        return notification
    notification.status = "SENT"
    notification.sent_at = datetime.utcnow().isoformat()
    notification.failure_reason = None
    return notification


def reject_notification(db: Session, notification: Notification, failure_reason: str) -> Notification:
    if notification.status != "PENDING_APPROVAL":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a notification pending approval can be rejected")
    notification.status = "FAILED"
    notification.failure_reason = failure_reason
    return notification
