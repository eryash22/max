"""Default Communication Rules for the contract (AMC/CMC/Warranty) events.

Every rule here is seeded **disabled and non-auto-send**. That is deliberate: seeding
an enabled rule would start messaging real vendors the moment this code deploys, and
the recipient expression is a starting point an admin must confirm — not something
this file should decide on their behalf. A Super Admin reviews the wording and the
recipient, then flips `is_active` (and optionally `auto_send`) in the
Communication Rules screen.

Idempotent: seeding is keyed on the rule `code`, so an admin's later edits to wording,
recipient, channel or the enabled flag are never overwritten on restart.

The recipient expressions use the contract context tokens (see
schemas/notification.py CONTRACT_TEMPLATE_VARIABLES). `{{vendor_email}}` /
`{{vendor_phone}}` resolve from the Vendor record; if that vendor has no contact
stored the token renders empty and dispatch marks the notification FAILED with
"No recipient address resolved" rather than sending anywhere unintended.
"""
from sqlalchemy.orm import Session

from ..models.notifications import NotificationTemplate
from ..schemas.notification import (
    EVENT_AMC_RENEWED,
    EVENT_CMC_RENEWED,
    EVENT_CONTRACT_ASSET_ADDED,
    EVENT_CONTRACT_ASSET_REMOVED,
    EVENT_CONTRACT_CREATED,
    EVENT_CONTRACT_EXPIRED,
    EVENT_CONTRACT_EXPIRING,
    EVENT_CONTRACT_STATUS_CHANGED,
    EVENT_WARRANTY_RENEWED,
)

_WA_CREATED = (
    "{{contract_type}} registered for {{device_name}}\n"
    "Asset No: {{asset_number}}\n"
    "Branch: {{branch_name}}\n"
    "Vendor: {{vendor_name}}\n"
    "Valid: {{contract_start}} to {{contract_end}}"
)
_WA_RENEWED = (
    "{{contract_type}} renewed for {{device_name}}\n"
    "Asset No: {{asset_number}}\n"
    "Branch: {{branch_name}}\n"
    "Vendor: {{vendor_name}}\n"
    "Valid Until: {{contract_end}}"
)
_WA_EXPIRING = (
    "{{contract_type}} expiring in {{expiry_days}} day(s)\n"
    "Contract: {{contract_number}}\n"
    "Device: {{device_name}} ({{asset_number}})\n"
    "Branch: {{branch_name}}\n"
    "Vendor: {{vendor_name}}\n"
    "Expires: {{contract_end}}"
)
_WA_EXPIRED = (
    "{{contract_type}} EXPIRED\n"
    "Contract: {{contract_number}}\n"
    "Device: {{device_name}} ({{asset_number}})\n"
    "Branch: {{branch_name}}\n"
    "Vendor: {{vendor_name}}\n"
    "Expired on: {{contract_end}}"
)
_WA_ASSET_ADDED = (
    "Device added to {{contract_type}} {{contract_number}}\n"
    "Device: {{device_name}}\n"
    "Asset No: {{asset_number}}\n"
    "Branch: {{branch_name}}\n"
    "Covered until: {{contract_end}}"
)
_WA_ASSET_REMOVED = (
    "Device removed from {{contract_type}} {{contract_number}}\n"
    "Device: {{device_name}}\n"
    "Asset No: {{asset_number}}\n"
    "Branch: {{branch_name}}"
)
_WA_STATUS_CHANGED = (
    "{{contract_type}} {{contract_number}} status changed to {{status}}\n"
    "Device: {{device_name}} ({{asset_number}})\n"
    "Branch: {{branch_name}}\n"
    "Vendor: {{vendor_name}}"
)

_EMAIL_BODY = (
    "Contract: {{contract_type}} {{contract_number}}\n"
    "Status: {{status}}\n"
    "Vendor: {{vendor_name}}\n"
    "Covered device(s): {{device_name}}\n"
    "Asset number(s): {{asset_number}} ({{asset_count}} covered)\n"
    "Branch: {{branch_name}}\n"
    "Department: {{department_name}}\n"
    "Coverage: {{contract_start}} to {{contract_end}}\n"
    "\n"
    "This message was generated automatically by MAX Biomedical."
)
_EMAIL_EXPIRY_BODY = (
    "Contract: {{contract_type}} {{contract_number}}\n"
    "Expires in {{expiry_days}} day(s), on {{contract_end}}.\n"
    "Vendor: {{vendor_name}}\n"
    "Covered device(s): {{device_name}}\n"
    "Asset number(s): {{asset_number}} ({{asset_count}} covered)\n"
    "Branch: {{branch_name}}\n"
    "\n"
    "Please arrange renewal before the expiry date.\n"
    "This message was generated automatically by MAX Biomedical."
)

# (code, channel, event, subject, body, receiver_email, receiver_whatsapp)
_DEFAULT_RULES: list[tuple[str, str, str, str | None, str, str | None, str | None]] = [
    ("CONTRACT_CREATED_EMAIL", "EMAIL", EVENT_CONTRACT_CREATED,
     "{{contract_type}} {{contract_number}} registered", _EMAIL_BODY, "{{vendor_email}}", None),
    ("CONTRACT_CREATED_WA", "WHATSAPP", EVENT_CONTRACT_CREATED,
     None, _WA_CREATED, None, "{{vendor_phone}}"),

    ("AMC_RENEWED_EMAIL", "EMAIL", EVENT_AMC_RENEWED,
     "AMC {{contract_number}} renewed", _EMAIL_BODY, "{{vendor_email}}", None),
    ("AMC_RENEWED_WA", "WHATSAPP", EVENT_AMC_RENEWED,
     None, _WA_RENEWED, None, "{{vendor_phone}}"),

    ("CMC_RENEWED_EMAIL", "EMAIL", EVENT_CMC_RENEWED,
     "CMC {{contract_number}} renewed", _EMAIL_BODY, "{{vendor_email}}", None),
    ("CMC_RENEWED_WA", "WHATSAPP", EVENT_CMC_RENEWED,
     None, _WA_RENEWED, None, "{{vendor_phone}}"),

    ("WARRANTY_RENEWED_EMAIL", "EMAIL", EVENT_WARRANTY_RENEWED,
     "Warranty {{contract_number}} extended", _EMAIL_BODY, "{{vendor_email}}", None),
    ("WARRANTY_RENEWED_WA", "WHATSAPP", EVENT_WARRANTY_RENEWED,
     None, _WA_RENEWED, None, "{{vendor_phone}}"),

    ("CONTRACT_EXPIRING_EMAIL", "EMAIL", EVENT_CONTRACT_EXPIRING,
     "{{contract_type}} {{contract_number}} expires in {{expiry_days}} day(s)",
     _EMAIL_EXPIRY_BODY, "{{vendor_email}}", None),
    ("CONTRACT_EXPIRING_WA", "WHATSAPP", EVENT_CONTRACT_EXPIRING,
     None, _WA_EXPIRING, None, "{{vendor_phone}}"),

    ("CONTRACT_EXPIRED_EMAIL", "EMAIL", EVENT_CONTRACT_EXPIRED,
     "{{contract_type}} {{contract_number}} has expired", _EMAIL_EXPIRY_BODY, "{{vendor_email}}", None),
    ("CONTRACT_EXPIRED_WA", "WHATSAPP", EVENT_CONTRACT_EXPIRED,
     None, _WA_EXPIRED, None, "{{vendor_phone}}"),

    ("CONTRACT_ASSET_ADDED_EMAIL", "EMAIL", EVENT_CONTRACT_ASSET_ADDED,
     "Device added to {{contract_type}} {{contract_number}}", _EMAIL_BODY, "{{vendor_email}}", None),
    ("CONTRACT_ASSET_ADDED_WA", "WHATSAPP", EVENT_CONTRACT_ASSET_ADDED,
     None, _WA_ASSET_ADDED, None, "{{vendor_phone}}"),

    ("CONTRACT_ASSET_REMOVED_EMAIL", "EMAIL", EVENT_CONTRACT_ASSET_REMOVED,
     "Device removed from {{contract_type}} {{contract_number}}", _EMAIL_BODY, "{{vendor_email}}", None),
    ("CONTRACT_ASSET_REMOVED_WA", "WHATSAPP", EVENT_CONTRACT_ASSET_REMOVED,
     None, _WA_ASSET_REMOVED, None, "{{vendor_phone}}"),

    # Security/completeness pass — every other contract event family has a seeded
    # default rule; CONTRACT_STATUS_CHANGED (dispatched by
    # contract_service._dispatch_status_change) was the one missing, so a status
    # change silently notified nobody (dispatch_contract_event_safe finds zero rules
    # and no-ops) rather than erroring, which made the gap easy to miss.
    ("CONTRACT_STATUS_CHANGED_EMAIL", "EMAIL", EVENT_CONTRACT_STATUS_CHANGED,
     "{{contract_type}} {{contract_number}} status changed to {{status}}", _EMAIL_BODY, "{{vendor_email}}", None),
    ("CONTRACT_STATUS_CHANGED_WA", "WHATSAPP", EVENT_CONTRACT_STATUS_CHANGED,
     None, _WA_STATUS_CHANGED, None, "{{vendor_phone}}"),
]


def ensure_contract_templates(db: Session) -> int:
    """Insert any missing default contract rule. Returns how many were created.

    Never updates an existing row: once a rule exists, the admin owns it."""
    existing = {
        code
        for (code,) in db.query(NotificationTemplate.code).filter(
            NotificationTemplate.code.in_([r[0] for r in _DEFAULT_RULES])
        )
    }
    created = 0
    for code, channel, event, subject, body, receiver_email, receiver_whatsapp in _DEFAULT_RULES:
        if code in existing:
            continue
        db.add(
            NotificationTemplate(
                code=code,
                channel=channel,
                event=event,
                subject_template=subject,
                body_template=body,
                # Seeded OFF — an admin reviews wording and recipient before anything
                # reaches a real vendor.
                is_active=False,
                auto_send=False,
                receiver_email=receiver_email,
                receiver_whatsapp_number=receiver_whatsapp,
            )
        )
        created += 1
    if created:
        db.commit()
    return created
