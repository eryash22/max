"""Default Communication Rules for the Phase 2 physical asset / device events.

Same contract as services/contract_template_seed.py, deliberately: every rule is
seeded **disabled and non-auto-send**, seeding is idempotent and keyed on the rule
`code`, and an existing rule is never modified — once a rule exists, the admin owns
its wording, recipient, channel and enabled flag. Turning device-movement messages on
is a decision about who inside the hospital gets pinged, and this file must not make
it on an admin's behalf.

Recipient expressions use the asset context tokens (see schemas/notification.py
ASSET_TEMPLATE_VARIABLES) and resolve from real records only — the branch's Unit Head
(Branch.unit_head_user_id), the branch-scoped BIOMEDICAL_ENGINEER / INVENTORY_MANAGER
role holders, the Branch's own contact columns, or the vendor currently holding active
coverage. If the token cannot resolve, dispatch marks the notification FAILED with
"No recipient address resolved" rather than sending anywhere unintended.

One rule addresses one recipient, exactly as in Phase 1. The defaults below cover the
primary stakeholder per event; an admin who wants the Unit Head AND the BME AND the
Inventory Manager notified adds the extra rules in the Communication Rules screen,
which is also what lets each of them have their own channel and auto-send setting.

Vendor privacy: only the condemnation vendor rule is vendor-facing, and it stays on
device/branch/date tokens — no internal staff contacts, no internal notes, no
{{updated_by}}. It is seeded disabled like everything else here.
"""
from sqlalchemy.orm import Session

from ..models.notifications import NotificationTemplate
from ..schemas.notification import (
    EVENT_ASSET_ALLOCATED,
    EVENT_ASSET_CONDEMNED,
    EVENT_ASSET_CREATED,
    EVENT_ASSET_LOCATION_CHANGED,
    EVENT_ASSET_RETIRED,
    EVENT_ASSET_STATUS_CHANGED,
    EVENT_ASSET_TRANSFERRED,
)

_WA_CREATED = (
    "New device added: {{device_name}}\n"
    "Asset No: {{asset_number}}\n"
    "Serial: {{serial_number}}\n"
    "Branch: {{branch_name}}\n"
    "Department: {{department_name}}\n"
    "Status: {{status}}"
)
_WA_ALLOCATED = (
    "Device allocated: {{device_name}}\n"
    "Asset No: {{asset_number}}\n"
    "To branch: {{new_branch}}\n"
    "Department: {{department_name}}\n"
    "Reference: {{reference}}\n"
    "Status: {{status}}"
)
_WA_TRANSFERRED = (
    "Device transferred: {{device_name}}\n"
    "Asset No: {{asset_number}}\n"
    "From: {{previous_branch}}\n"
    "To: {{new_branch}}\n"
    "Status: {{status}}"
)
_WA_STATUS = (
    "Device status changed: {{device_name}}\n"
    "Asset No: {{asset_number}}\n"
    "Branch: {{branch_name}}\n"
    "{{previous_status}} -> {{new_status}}"
)
_WA_CONDEMNED = (
    "Device CONDEMNED: {{device_name}}\n"
    "Asset No: {{asset_number}}\n"
    "Branch: {{branch_name}}\n"
    "Department: {{department_name}}\n"
    "Reference: {{reference}}"
)
_WA_RETIRED = (
    "Device retired: {{device_name}}\n"
    "Asset No: {{asset_number}}\n"
    "Branch: {{branch_name}}\n"
    "{{previous_status}} -> {{new_status}}"
)
_WA_LOCATION = (
    "Device moved: {{device_name}}\n"
    "Asset No: {{asset_number}}\n"
    "Branch: {{branch_name}}\n"
    "Department: {{previous_department}} -> {{new_department}}\n"
    "Location: {{previous_location}} -> {{new_location}}"
)

_EMAIL_BODY = (
    "Device: {{device_name}} ({{model_name}})\n"
    "Manufacturer: {{manufacturer}}\n"
    "Asset number: {{asset_number}}\n"
    "Serial number: {{serial_number}}\n"
    "Branch: {{branch_name}}\n"
    "Department: {{department_name}}\n"
    "Location: {{location_name}}\n"
    "Status: {{status}}\n"
    "Updated by: {{updated_by}} on {{updated_at}}\n"
    "\n"
    "This message was generated automatically by MAX Biomedical."
)
_EMAIL_MOVE_BODY = (
    "Device: {{device_name}} ({{model_name}})\n"
    "Asset number: {{asset_number}}\n"
    "Serial number: {{serial_number}}\n"
    "From branch: {{previous_branch}}\n"
    "To branch: {{new_branch}}\n"
    "Department: {{department_name}}\n"
    "Status: {{status}}\n"
    "Reference: {{reference}}\n"
    "Updated by: {{updated_by}} on {{updated_at}}\n"
    "\n"
    "This message was generated automatically by MAX Biomedical."
)
_EMAIL_STATUS_BODY = (
    "Device: {{device_name}} ({{model_name}})\n"
    "Asset number: {{asset_number}}\n"
    "Branch: {{branch_name}}\n"
    "Department: {{department_name}}\n"
    "Status: {{previous_status}} -> {{new_status}}\n"
    "Reference: {{reference}}\n"
    "Updated by: {{updated_by}} on {{updated_at}}\n"
    "\n"
    "This message was generated automatically by MAX Biomedical."
)
# Vendor-facing. Device/branch/date facts only — no internal staff contacts, no
# internal notes, no actor name.
_EMAIL_VENDOR_CONDEMNED_BODY = (
    "Device: {{device_name}} ({{model_name}})\n"
    "Asset number: {{asset_number}}\n"
    "Serial number: {{serial_number}}\n"
    "Branch: {{branch_name}}\n"
    "This device has been condemned and is no longer in service.\n"
    "Please treat any active service coverage for it accordingly.\n"
    "\n"
    "This message was generated automatically by MAX Biomedical."
)

# (code, channel, event, subject, body, receiver_email, receiver_whatsapp)
_DEFAULT_RULES: list[tuple[str, str, str, str | None, str, str | None, str | None]] = [
    # New device registered — the branch BME owns the physical device register.
    ("ASSET_CREATED_EMAIL", "EMAIL", EVENT_ASSET_CREATED,
     "New device added at {{branch_name}}: {{device_name}}", _EMAIL_BODY, "{{bme_email}}", None),
    ("ASSET_CREATED_WA", "WHATSAPP", EVENT_ASSET_CREATED,
     None, _WA_CREATED, None, "{{bme_phone}}"),

    # Allocation — the receiving branch's Unit Head, plus the Inventory Manager who
    # issued it. Two recipients, so two rules per channel pair.
    ("ASSET_ALLOCATED_EMAIL", "EMAIL", EVENT_ASSET_ALLOCATED,
     "Device allocated to {{new_branch}}: {{device_name}}", _EMAIL_MOVE_BODY, "{{unit_head_email}}", None),
    ("ASSET_ALLOCATED_WA", "WHATSAPP", EVENT_ASSET_ALLOCATED,
     None, _WA_ALLOCATED, None, "{{unit_head_phone}}"),
    ("ASSET_ALLOCATED_INVMGR_EMAIL", "EMAIL", EVENT_ASSET_ALLOCATED,
     "Device allocated to {{new_branch}}: {{device_name}}", _EMAIL_MOVE_BODY, "{{inventory_manager_email}}", None),

    # Transfer — both sides of the move.
    ("ASSET_TRANSFERRED_EMAIL", "EMAIL", EVENT_ASSET_TRANSFERRED,
     "Device transferred to {{new_branch}}: {{device_name}}", _EMAIL_MOVE_BODY, "{{unit_head_email}}", None),
    ("ASSET_TRANSFERRED_WA", "WHATSAPP", EVENT_ASSET_TRANSFERRED,
     None, _WA_TRANSFERRED, None, "{{unit_head_phone}}"),
    ("ASSET_TRANSFERRED_FROM_EMAIL", "EMAIL", EVENT_ASSET_TRANSFERRED,
     "Device transferred out of {{previous_branch}}: {{device_name}}", _EMAIL_MOVE_BODY,
     "{{previous_unit_head_email}}", None),

    ("ASSET_STATUS_CHANGED_EMAIL", "EMAIL", EVENT_ASSET_STATUS_CHANGED,
     "{{device_name}} is now {{new_status}}", _EMAIL_STATUS_BODY, "{{bme_email}}", None),
    ("ASSET_STATUS_CHANGED_WA", "WHATSAPP", EVENT_ASSET_STATUS_CHANGED,
     None, _WA_STATUS, None, "{{bme_phone}}"),

    ("ASSET_CONDEMNED_EMAIL", "EMAIL", EVENT_ASSET_CONDEMNED,
     "Device condemned at {{branch_name}}: {{device_name}}", _EMAIL_STATUS_BODY,
     "{{inventory_manager_email}}", None),
    ("ASSET_CONDEMNED_WA", "WHATSAPP", EVENT_ASSET_CONDEMNED,
     None, _WA_CONDEMNED, None, "{{unit_head_phone}}"),
    # Vendor-facing, and only relevant where the device is under active AMC/CMC/
    # warranty coverage — {{vendor_email}} resolves from that coverage, and renders
    # empty (so dispatch FAILS the notification rather than guessing) when there is none.
    ("ASSET_CONDEMNED_VENDOR_EMAIL", "EMAIL", EVENT_ASSET_CONDEMNED,
     "Device condemned: {{device_name}} ({{asset_number}})", _EMAIL_VENDOR_CONDEMNED_BODY,
     "{{vendor_email}}", None),

    ("ASSET_RETIRED_EMAIL", "EMAIL", EVENT_ASSET_RETIRED,
     "Device retired at {{branch_name}}: {{device_name}}", _EMAIL_STATUS_BODY,
     "{{inventory_manager_email}}", None),
    ("ASSET_RETIRED_WA", "WHATSAPP", EVENT_ASSET_RETIRED,
     None, _WA_RETIRED, None, "{{inventory_manager_phone}}"),

    ("ASSET_LOCATION_CHANGED_EMAIL", "EMAIL", EVENT_ASSET_LOCATION_CHANGED,
     "Device moved within {{branch_name}}: {{device_name}}", _EMAIL_BODY, "{{unit_head_email}}", None),
    ("ASSET_LOCATION_CHANGED_WA", "WHATSAPP", EVENT_ASSET_LOCATION_CHANGED,
     None, _WA_LOCATION, None, "{{unit_head_phone}}"),
]


def ensure_asset_templates(db: Session) -> int:
    """Insert any missing default asset rule. Returns how many were created.

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
                # Seeded OFF — an admin reviews wording and recipient before any device
                # movement starts messaging people.
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
