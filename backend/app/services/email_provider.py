"""Configurable SMTP/email provider — Module 17B.

Deliberately a thin, isolated module (one function) so a future provider (SES, SendGrid,
etc.) can replace the body of `send_email_smtp` without touching any caller. No manager/
admin email or credential is hard-coded anywhere in this file — every value comes from
`app.config.settings`, which reads only from environment/.env (see config.py). Callers
never see `smtp_password`; it is read once here and never returned or logged.
"""
import smtplib
from email.message import EmailMessage

from ..config import settings


class EmailSendError(Exception):
    """Raised when a real SMTP send could not be completed — surfaced to the caller as a
    FAILED notification with this message as failure_reason, never silently swallowed."""


def is_smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from_email)


def send_email_smtp(*, to_email: str, subject: str, body: str) -> None:
    if not is_smtp_configured():
        raise EmailSendError("SMTP is not configured (smtp_host/smtp_from_email unset) — set them in .env")

    message = EmailMessage()
    message["Subject"] = subject or "(no subject)"
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>" if settings.smtp_from_name else settings.smtp_from_email
    message["To"] = to_email
    message.set_content(body or "")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailSendError(f"SMTP send failed: {exc}") from exc
