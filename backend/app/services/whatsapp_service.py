"""Module 19 — WhatsApp honest state machine: QUEUED -> OPENED ->
REQUIRES_USER_CONFIRMATION -> SENT / FAILED. See models/notifications.py's
Notification.whatsapp_status docstring and services/whatsapp_provider.py for why
there is no automated send-confirmation in this project.
"""
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models.notifications import CommunicationProviderConfig, Notification
from . import whatsapp_automation_service
from .whatsapp_provider import PROVIDERS, WhatsAppProviderNotConfiguredError

CONFIRMABLE_STATES = {"QUEUED", "OPENED", "REQUIRES_USER_CONFIRMATION"}


def get_or_create_provider_config(db: Session, *, channel: str = "WHATSAPP") -> CommunicationProviderConfig:
    config = db.query(CommunicationProviderConfig).filter(CommunicationProviderConfig.channel == channel).first()
    if config is None:
        config = CommunicationProviderConfig(channel=channel, active_provider="BROWSER_LINK")
        db.add(config)
        db.flush()
    return config


def get_active_provider(db: Session):
    config = get_or_create_provider_config(db)
    return PROVIDERS[config.active_provider]


def approve_whatsapp_notification(db: Session, notification: Notification) -> Notification:
    if notification.channel != "WHATSAPP":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not a WHATSAPP notification")
    if notification.status != "PENDING_APPROVAL":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a notification pending approval can be approved")

    provider = get_active_provider(db)
    try:
        provider.approve(notification)
    except WhatsAppProviderNotConfiguredError:
        notification.whatsapp_status = "REQUIRES_USER_CONFIRMATION"
        notification.whatsapp_provider = provider.name
    return notification


def mark_whatsapp_opened(db: Session, notification: Notification) -> Notification:
    """Best-effort telemetry — the frontend calls this right after opening the wa.me
    link. Not required for the state machine to progress (see confirm/fail below), so
    a missed call (closed tab, ad-blocker) never blocks anything."""
    if notification.channel != "WHATSAPP":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not a WHATSAPP notification")
    if notification.whatsapp_status == "QUEUED":
        notification.whatsapp_status = "OPENED"
        notification.whatsapp_opened_at = datetime.utcnow().isoformat()
    return notification


def confirm_whatsapp_sent(db: Session, notification: Notification, *, confirmed_by: str) -> Notification:
    if notification.channel != "WHATSAPP":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not a WHATSAPP notification")
    if notification.whatsapp_status not in CONFIRMABLE_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot confirm sent from whatsapp_status {notification.whatsapp_status}",
        )
    notification.whatsapp_status = "SENT"
    notification.whatsapp_confirmed_by = confirmed_by
    notification.status = "SENT"
    notification.sent_at = datetime.utcnow().isoformat()
    return notification


def mark_whatsapp_failed(db: Session, notification: Notification, *, failure_reason: str) -> Notification:
    if notification.channel != "WHATSAPP":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not a WHATSAPP notification")
    if notification.whatsapp_status not in CONFIRMABLE_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot mark failed from whatsapp_status {notification.whatsapp_status}",
        )
    notification.whatsapp_status = "FAILED"
    notification.status = "FAILED"
    notification.failure_reason = failure_reason
    return notification


def attempt_automation_send(db: Session, notification: Notification) -> Notification:
    """Module 19 Enhancement — the explicit, separate action that actually drives the
    WhatsApp Web browser automation for a notification approve() already queued via
    BrowserAutomationProvider. Deliberately not folded into approve() itself: a real
    browser send can take tens of seconds, and approve() must stay fast/synchronous
    like every other approval action in this codebase."""
    if notification.channel != "WHATSAPP":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not a WHATSAPP notification")
    if notification.whatsapp_provider != "BROWSER_AUTOMATION":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This notification is not queued on the BROWSER_AUTOMATION provider",
        )
    if notification.whatsapp_status not in ("QUEUED", "OPENED"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot attempt automated send from whatsapp_status {notification.whatsapp_status}",
        )

    session = whatsapp_automation_service.get_session()
    result = session.send_message(recipient=notification.recipient, text=notification.rendered_body or "")

    if result.state == whatsapp_automation_service.SEND_SENT:
        notification.whatsapp_status = "SENT"
        notification.status = "SENT"
        notification.whatsapp_confirmed_by = "system:automation"
        notification.sent_at = datetime.utcnow().isoformat()
    elif result.state == whatsapp_automation_service.SEND_FAILED:
        notification.whatsapp_status = "FAILED"
        notification.status = "FAILED"
        notification.failure_reason = result.detail
    else:
        notification.whatsapp_status = "REQUIRES_USER_CONFIRMATION"
        notification.failure_reason = result.detail
    return notification


def update_provider_config(db: Session, *, active_provider: str) -> CommunicationProviderConfig:
    if active_provider not in PROVIDERS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown provider: {active_provider}")
    config = get_or_create_provider_config(db)
    config.active_provider = active_provider
    return config
