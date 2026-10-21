"""Module 19 — WhatsApp provider abstraction.

Three implementations, matching services/email_provider.py's isolation shape
(is_configured() / a single action method / a dedicated exception):

- BrowserLinkProvider: wraps the existing, fully-working wa.me deep-link flow
  (communication_service.build_whatsapp_url). Always configured — no external
  credentials needed.
- BrowserAutomationProvider: Module 19 Enhancement — a Playwright-driven
  WhatsApp Web session (services/whatsapp_automation_service.py). configured()
  only when an admin has connected and scanned the QR code. approve() queues
  the notification exactly like BrowserLinkProvider (whatsapp_url stays
  available as a manual fallback); the actual automated send is a separate,
  explicit action (POST .../whatsapp/automation/attempt-send) so approving a
  notification never blocks on a slow browser action, and a real DOM-evidence
  check — never a guess — decides SENT vs REQUIRES_USER_CONFIRMATION.
- BusinessApiProvider: a structural stub for a future official WhatsApp Business
  API integration. No credentials exist in this project, so it always reports
  not-configured and never claims to send — callers must fall back to a human
  (whatsapp_status REQUIRES_USER_CONFIRMATION), never a silent/fake SENT.
"""
from typing import Protocol

from ..models.notifications import Notification
from . import whatsapp_automation_service


class WhatsAppProviderNotConfiguredError(Exception):
    """Raised by a provider that cannot handle the notification right now."""


class WhatsAppProvider(Protocol):
    name: str

    def is_configured(self) -> bool: ...

    def approve(self, notification: Notification) -> None:
        """Mutates notification.whatsapp_status (and whatsapp_provider) in place to
        reflect this provider's handling of an approved WHATSAPP notification."""
        ...


class BrowserLinkProvider:
    """The real, working mode: a human opens the pre-filled wa.me link and sends it
    manually. approve() only queues it — whatsapp_url (models/notifications.py)
    becomes available once whatsapp_status is QUEUED."""

    name = "BROWSER_LINK"

    def is_configured(self) -> bool:
        return True

    def approve(self, notification: Notification) -> None:
        notification.whatsapp_status = "QUEUED"
        notification.whatsapp_provider = self.name


class BrowserAutomationProvider:
    """Module 19 Enhancement — real Playwright/WhatsApp Web browser automation. Only
    reports configured when the admin-managed session is actually connected (see
    services/whatsapp_automation_service.py); otherwise falls back to a human like
    BusinessApiProvider does, never faking readiness."""

    name = "BROWSER_AUTOMATION"

    def is_configured(self) -> bool:
        return whatsapp_automation_service.get_session().is_connected()

    def approve(self, notification: Notification) -> None:
        if not self.is_configured():
            raise WhatsAppProviderNotConfiguredError(
                "No connected WhatsApp Web automation session — connect and scan the QR code first."
            )
        notification.whatsapp_status = "QUEUED"
        notification.whatsapp_provider = self.name


class BusinessApiProvider:
    """Structural stub for an official WhatsApp Business API integration. No
    credentials exist in this project (verified before writing this module) — this
    always reports not-configured so the caller falls back to a human instead of
    faking a send."""

    name = "BUSINESS_API"

    def is_configured(self) -> bool:
        return False

    def approve(self, notification: Notification) -> None:
        raise WhatsAppProviderNotConfiguredError(
            "The WhatsApp Business API provider has no credentials configured."
        )


PROVIDERS: dict[str, WhatsAppProvider] = {
    "BROWSER_LINK": BrowserLinkProvider(),
    "BROWSER_AUTOMATION": BrowserAutomationProvider(),
    "BUSINESS_API": BusinessApiProvider(),
}
ACTIVE_PROVIDER_CHOICES = list(PROVIDERS.keys())
