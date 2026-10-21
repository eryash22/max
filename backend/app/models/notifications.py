import re

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel

# channel values: IN_APP, EMAIL, WHATSAPP, SMS.
# status values: DRAFT, PENDING_APPROVAL, APPROVED, SENT, FAILED.
# External channels (EMAIL/WHATSAPP/SMS) default approval_required=True; no outbound send
# happens automatically without a configured approval rule — see SECURITY.md.
#
# Module 17B — WhatsApp + Email Communication Automation. `NotificationTemplate` doubles
# as the "communication rule" for the Requirement/Fulfillment workflow: sender_*/receiver_*
# fields hold either a literal value (e.g. a fixed procurement inbox) or a {{variable}}
# token resolved per-event by services/communication_service.py (e.g. "{{requester_email}}")
# — one rendering mechanism covers both static and dynamic recipient configuration, see
# PROJECT_STATUS.md. Only the columns relevant to a row's own `channel` are populated
# (e.g. a WHATSAPP row leaves sender_email/receiver_email null), same nullable-by-channel
# convention `subject_template` already used before this module.


class NotificationTemplate(BaseModel):
    __tablename__ = "notification_templates"

    code: Mapped[str] = mapped_column(String, unique=True)
    channel: Mapped[str] = mapped_column(String)
    event: Mapped[str] = mapped_column(String)
    subject_template: Mapped[str | None] = mapped_column(String, nullable=True)
    body_template: Mapped[str] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sender_name: Mapped[str | None] = mapped_column(String, nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String, nullable=True)
    sender_whatsapp_number: Mapped[str | None] = mapped_column(String, nullable=True)
    receiver_name: Mapped[str | None] = mapped_column(String, nullable=True)
    receiver_email: Mapped[str | None] = mapped_column(String, nullable=True)
    receiver_whatsapp_number: Mapped[str | None] = mapped_column(String, nullable=True)
    # Opt-in: when true, a notification produced by this rule is delivered immediately
    # on its own channel (WhatsApp Web automation, or SMTP for EMAIL) instead of waiting
    # for manual approval. Scoped to this rule only — the global approval_required
    # control is untouched (see migrations 0025/0026).
    auto_send: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Notification(BaseModel):
    __tablename__ = "notifications"

    template_id: Mapped[str | None] = mapped_column(ForeignKey("notification_templates.id"), nullable=True)
    recipient: Mapped[str] = mapped_column(String)
    channel: Mapped[str] = mapped_column(String)
    event: Mapped[str] = mapped_column(String)
    related_entity_type: Mapped[str | None] = mapped_column(String, nullable=True)
    related_entity_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="DRAFT")
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_at: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # Idempotency key for dispatch that can legitimately run more than once (the daily
    # contract expiry scan). NULL for every mutation-driven notification.
    dedup_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # Module 17B additions. related_requirement_id/related_fulfillment_id are first-class
    # FKs (in addition to the generic related_entity_type/id pair above, which stays as-is
    # for every other module's usage) since the task names Requirement/Fulfillment
    # explicitly as the two entities communication history must be traceable to, and a
    # Fulfillment-stage event is related to both at once (the generic pair only holds one).
    # rendered_subject/rendered_body capture the fully-substituted message at dispatch
    # time, so history stays accurate even if the underlying template is edited later.
    related_requirement_id: Mapped[str | None] = mapped_column(ForeignKey("requirements.id"), nullable=True)
    related_fulfillment_id: Mapped[str | None] = mapped_column(ForeignKey("fulfillment_requests.id"), nullable=True)
    rendered_subject: Mapped[str | None] = mapped_column(String, nullable=True)
    rendered_body: Mapped[str | None] = mapped_column(String, nullable=True)
    # Module 19 — WhatsApp honest state machine. Only ever populated for channel=="WHATSAPP";
    # `status` above stays the outer envelope (unchanged for EMAIL/IN_APP, and mirrored to
    # SENT/FAILED at the terminal whatsapp_status values so every existing status_filter
    # query keeps working). whatsapp_status values: QUEUED, OPENED,
    # REQUIRES_USER_CONFIRMATION, SENT, FAILED — see services/whatsapp_service.py. There is
    # no automated way to detect a real WhatsApp Web send (no browser automation in this
    # project, by design — see PROJECT_STATUS.md Module 19), so SENT/FAILED are always the
    # result of an explicit user action, never inferred.
    whatsapp_status: Mapped[str | None] = mapped_column(String, nullable=True)
    whatsapp_opened_at: Mapped[str | None] = mapped_column(String, nullable=True)
    whatsapp_confirmed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    whatsapp_provider: Mapped[str | None] = mapped_column(String, nullable=True)

    @property
    def whatsapp_url(self) -> str | None:
        """Browser-based WhatsApp workflow: a wa.me deep link pre-filled with the
        rendered message, for the frontend to open in a new tab/WhatsApp Web so a human
        sends it. Available while still awaiting confirmation — QUEUED/OPENED (the
        BROWSER_LINK provider's own flow) and also REQUIRES_USER_CONFIRMATION (the
        BUSINESS_API provider's not-configured fallback: automatic send failed, so the
        human still needs this link to send manually) — not a WhatsApp API
        integration, see SECURITY.md/PROJECT_STATUS.md."""
        if self.channel != "WHATSAPP" or not self.rendered_body or not self.recipient:
            return None
        if self.whatsapp_status not in ("QUEUED", "OPENED", "REQUIRES_USER_CONFIRMATION"):
            return None
        digits = re.sub(r"[^0-9]", "", self.recipient)
        if not digits:
            return None
        from urllib.parse import quote

        return f"https://wa.me/{digits}?text={quote(self.rendered_body)}"


class CommunicationProviderConfig(BaseModel):
    """Module 19 — one row per channel selecting which provider handles it. Only
    WHATSAPP is populated today (BROWSER_LINK is the only real implementation;
    BUSINESS_API is a structural stub with no credentials — see
    services/whatsapp_provider.py). Rows are lazily get-or-created on first read
    (services/whatsapp_service.py), matching how NotificationTemplate rows are
    admin-created rather than data-migration-seeded."""

    __tablename__ = "communication_provider_configs"

    channel: Mapped[str] = mapped_column(String, unique=True)
    active_provider: Mapped[str] = mapped_column(String, default="BROWSER_LINK")
