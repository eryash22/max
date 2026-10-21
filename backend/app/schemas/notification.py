from pydantic import BaseModel, field_validator

# Vocabulary taken verbatim from models/notifications.py's module docstring.
CHANNELS = ["IN_APP", "EMAIL", "WHATSAPP", "SMS"]
STATUSES = ["DRAFT", "PENDING_APPROVAL", "APPROVED", "SENT", "FAILED"]
EXTERNAL_CHANNELS = ["EMAIL", "WHATSAPP", "SMS"]

# Module 17B — the 10 Requirement/Fulfillment workflow events communication rules can be
# configured against. Taken verbatim from this task's spec; "Requirement completed" maps
# to FULFILLMENT_COMPLETED since Module 17A models completion as FulfillmentRequest.status
# -> COMPLETED, not a separate Requirement-level status (see fulfillment_service.py).
EVENT_REQUIREMENT_SUBMITTED = "REQUIREMENT_SUBMITTED"
EVENT_REQUIREMENT_APPROVED = "REQUIREMENT_APPROVED"
EVENT_REQUIREMENT_REJECTED = "REQUIREMENT_REJECTED"
EVENT_FULFILLMENT_CREATED = "FULFILLMENT_CREATED"
EVENT_FULFILLMENT_UNDER_PROCESS = "FULFILLMENT_UNDER_PROCESS"
EVENT_FULFILLMENT_PACKED = "FULFILLMENT_PACKED"
EVENT_FULFILLMENT_OUT_FOR_DELIVERY = "FULFILLMENT_OUT_FOR_DELIVERY"
EVENT_FULFILLMENT_DELIVERY_DATE_CHANGED = "FULFILLMENT_DELIVERY_DATE_CHANGED"
EVENT_FULFILLMENT_DELIVERED = "FULFILLMENT_DELIVERED"
EVENT_FULFILLMENT_COMPLETED = "FULFILLMENT_COMPLETED"
# Module 19 — closes the gap where cancelling a requirement/fulfillment notified no one.
EVENT_REQUIREMENT_CANCELLED = "REQUIREMENT_CANCELLED"
EVENT_FULFILLMENT_CANCELLED = "FULFILLMENT_CANCELLED"

# Workflow consolidation pass — Incident previously fired zero notification events. These
# two cover the brief's explicit routing: "device issue raised -> Biomedical Engineer"
# (fires once an engineer is actually assigned, since that's the earliest point a real
# recipient exists — same instance-derived-token constraint Requirement/Fulfillment
# already have) and "completion -> relevant requester" (fires on RESOLVED or CLOSED).
EVENT_INCIDENT_ASSIGNED = "INCIDENT_ASSIGNED"
EVENT_INCIDENT_RESOLVED = "INCIDENT_RESOLVED"

# Module 21 — Procurement events. Dispatched via
# services/communication_service.dispatch_procurement_event(_safe), a parallel,
# minimal path matching dispatch_incident_event(_safe)'s shape — Quotation/Indent/
# PurchaseOrder/Invoice are four different entity types, not one, so this stays
# generic (entity_type/entity) rather than four separate typed dispatch functions.
EVENT_QUOTATION_SUBMITTED = "QUOTATION_SUBMITTED"
EVENT_QUOTATION_APPROVED = "QUOTATION_APPROVED"
EVENT_QUOTATION_REJECTED = "QUOTATION_REJECTED"
EVENT_INDENT_CREATED = "INDENT_CREATED"
EVENT_PO_CREATED = "PO_CREATED"
EVENT_VENDOR_VISIT_SCHEDULED = "VENDOR_VISIT_SCHEDULED"
EVENT_INVOICE_RECORDED = "INVOICE_RECORDED"
EVENT_INVOICE_VERIFIED = "INVOICE_VERIFIED"
EVENT_PROCUREMENT_COMPLETED = "PROCUREMENT_COMPLETED"

PROCUREMENT_EVENTS = [
    EVENT_QUOTATION_SUBMITTED,
    EVENT_QUOTATION_APPROVED,
    EVENT_QUOTATION_REJECTED,
    EVENT_INDENT_CREATED,
    EVENT_PO_CREATED,
    EVENT_VENDOR_VISIT_SCHEDULED,
    EVENT_INVOICE_RECORDED,
    EVENT_INVOICE_VERIFIED,
    EVENT_PROCUREMENT_COMPLETED,
]

# Final Operational Gap Fix Pass — PM/Calibration schedules compute a due_status
# (NOT_SCHEDULED/OVERDUE/DUE/UPCOMING) at read time (pm_service.compute_due_status /
# calibration_service.compute_due_status) but that status never generated a
# notification. Fired from pm_service/calibration_service right after a schedule's
# next_due_date is set or refreshed (on schedule create/update and on record
# completion) if the freshly computed status is DUE or OVERDUE — there is no
# background scheduler in this codebase, so "at read time" recomputation is not a
# viable dispatch trigger; every other event in this module fires on a mutation,
# and this reuses that same discipline rather than adding a new subsystem.
EVENT_PM_DUE = "PM_DUE"
EVENT_PM_OVERDUE = "PM_OVERDUE"
EVENT_CALIBRATION_DUE = "CALIBRATION_DUE"
EVENT_CALIBRATION_OVERDUE = "CALIBRATION_OVERDUE"

MAINTENANCE_EVENTS = [
    EVENT_PM_DUE,
    EVENT_PM_OVERDUE,
    EVENT_CALIBRATION_DUE,
    EVENT_CALIBRATION_OVERDUE,
]

# Template variables resolvable by services/communication_service.build_maintenance_context()
# for the four MAINTENANCE_EVENTS above. No "responsible BME" field exists on
# PMSchedule/CalibrationSchedule (verified against models/maintenance.py) — the closest
# existing-architecture equivalent is the engineer who performed the most recent record
# against that schedule, so that is offered as a token rather than inventing a new
# assignment field.
MAINTENANCE_TEMPLATE_VARIABLES = [
    "schedule_type", "schedule_id", "asset_id", "asset_number", "device_model_name",
    "hospital", "branch", "department", "frequency", "next_due_date", "due_status",
    "last_engineer_name", "last_engineer_email",
]

# ---- Contract (AMC / CMC / Warranty) events ----
#
# Contracts previously fired no notification events at all. These follow the same
# entity-generic shape as PROCUREMENT_EVENTS (one dispatch function over
# entity_type/entity) rather than three typed families, because AMC/CMC/Warranty are
# three tables sharing one business vocabulary.
#
# Renewal is deliberately its own event per contract type: extending an end_date is a
# commercially different thing from creating coverage, and the vendor/finance
# recipients usually differ. Everything else that can change on a contract is a
# trivial field edit and intentionally fires nothing.
EVENT_CONTRACT_CREATED = "CONTRACT_CREATED"
EVENT_AMC_RENEWED = "AMC_RENEWED"
EVENT_CMC_RENEWED = "CMC_RENEWED"
EVENT_WARRANTY_RENEWED = "WARRANTY_RENEWED"
EVENT_CONTRACT_EXPIRING = "CONTRACT_EXPIRING"
EVENT_CONTRACT_EXPIRED = "CONTRACT_EXPIRED"
EVENT_CONTRACT_ASSET_ADDED = "CONTRACT_ASSET_ADDED"
EVENT_CONTRACT_ASSET_REMOVED = "CONTRACT_ASSET_REMOVED"
EVENT_CONTRACT_STATUS_CHANGED = "CONTRACT_STATUS_CHANGED"

CONTRACT_EVENTS = [
    EVENT_CONTRACT_CREATED,
    EVENT_AMC_RENEWED,
    EVENT_CMC_RENEWED,
    EVENT_WARRANTY_RENEWED,
    EVENT_CONTRACT_EXPIRING,
    EVENT_CONTRACT_EXPIRED,
    EVENT_CONTRACT_ASSET_ADDED,
    EVENT_CONTRACT_ASSET_REMOVED,
    EVENT_CONTRACT_STATUS_CHANGED,
]

# Renewal event per contract type — a renewal means "the same coverage was extended",
# which is type-specific for the recipients who care about it.
CONTRACT_RENEWAL_EVENT_BY_TYPE = {
    "AMC": EVENT_AMC_RENEWED,
    "CMC": EVENT_CMC_RENEWED,
    "WARRANTY": EVENT_WARRANTY_RENEWED,
}

# Template variables resolvable by communication_service.build_contract_context().
# `asset_number`/`device_name`/`branch_name` describe the covered PHYSICAL assets — a
# contract can cover several, so those render as comma-separated lists (and
# `asset_count` gives the total). Vendor contact tokens come from the Vendor record;
# they are never free text.
CONTRACT_TEMPLATE_VARIABLES = [
    "contract_number", "contract_type", "contract_name", "status", "event",
    "device_name", "model_name", "asset_number", "asset_count",
    "branch_name", "department_name",
    "vendor_name", "vendor_email", "vendor_phone",
    "contract_start", "contract_end", "expiry_days", "updated_by",
]

# ---- Phase 2 — Physical asset / device lifecycle events ----
#
# These fire for real, already-existing Asset workflows only (verified against
# routers/devices.py, services/fulfillment_service.py and
# services/requirement_service.py before any were added). Dispatched through
# communication_service.dispatch_asset_event(_safe) — the same rule/template/
# recipient/channel/auto-send foundation every other family above uses, keyed on
# the generic related_entity_type="asset"/related_entity_id pair.
#
# Deliberately absent — ASSET_COVERAGE_CHANGED. AMC/CMC/Warranty coverage is never
# stored on Asset (see models/device.py); it is derived live from the contract
# tables, and every state change in it IS a contract mutation, already covered by
# CONTRACT_ASSET_ADDED/REMOVED/EXPIRING/EXPIRED — all of which already render the
# covered physical asset's number, device name and branch. A second asset-side
# event would notify the same people twice about one business fact.
#
# CONDEMNED and RETIRED are separate events because they are separate workflows in
# this product, not two names for one transition: CONDEMNED is reachable only
# through an approved CONDEMNATION requirement (requirement_service.
# _apply_condemnation_items), RETIRED only through archive_asset. A status edit
# landing on either value emits that specific event INSTEAD of the generic
# ASSET_STATUS_CHANGED, never both.
EVENT_ASSET_CREATED = "ASSET_CREATED"
EVENT_ASSET_ALLOCATED = "ASSET_ALLOCATED"
EVENT_ASSET_TRANSFERRED = "ASSET_TRANSFERRED"
EVENT_ASSET_STATUS_CHANGED = "ASSET_STATUS_CHANGED"
EVENT_ASSET_CONDEMNED = "ASSET_CONDEMNED"
EVENT_ASSET_RETIRED = "ASSET_RETIRED"
EVENT_ASSET_LOCATION_CHANGED = "ASSET_LOCATION_CHANGED"

ASSET_EVENTS = [
    EVENT_ASSET_CREATED,
    EVENT_ASSET_ALLOCATED,
    EVENT_ASSET_TRANSFERRED,
    EVENT_ASSET_STATUS_CHANGED,
    EVENT_ASSET_CONDEMNED,
    EVENT_ASSET_RETIRED,
    EVENT_ASSET_LOCATION_CHANGED,
]

# Template variables resolvable by communication_service.build_asset_context().
#
# Recipient tokens resolve from real stored relationships only — Branch.
# unit_head_user_id, the Branch's own contact columns, the branch-scoped
# BIOMEDICAL_ENGINEER/INVENTORY_MANAGER role holders, and whichever vendor currently
# holds active AMC/CMC/Warranty coverage. Nothing here is free text, and an
# unresolvable token renders empty, which dispatch treats as "no recipient
# resolved" and FAILS the notification rather than sending somewhere unintended.
#
# Vendor privacy: a vendor-facing rule must stay on the device/branch/date tokens.
# The internal-only tokens (the *_email/*_phone recipient tokens for staff, and
# {{updated_by}}) exist for internal rules; the seeded vendor-facing defaults in
# services/asset_template_seed.py never reference them.
ASSET_TEMPLATE_VARIABLES = [
    "event", "asset_id", "asset_number", "serial_number",
    "device_name", "model_name", "device_model", "manufacturer", "category",
    "status", "previous_status", "new_status", "criticality",
    "branch_name", "department_name", "location_name",
    "previous_branch", "new_branch", "previous_department", "new_department",
    "previous_location", "new_location",
    "vendor_name", "vendor_email", "vendor_phone",
    "updated_by", "updated_at", "reference", "notes",
    "branch_email", "branch_phone", "branch_whatsapp",
    "unit_head_name", "unit_head_email", "unit_head_phone",
    "bme_name", "bme_email", "bme_phone",
    "inventory_manager_name", "inventory_manager_email", "inventory_manager_phone",
    "previous_branch_email", "previous_branch_whatsapp",
    "previous_unit_head_email", "previous_unit_head_phone",
]

COMMUNICATION_EVENTS = [
    EVENT_REQUIREMENT_SUBMITTED,
    EVENT_REQUIREMENT_APPROVED,
    EVENT_REQUIREMENT_REJECTED,
    EVENT_REQUIREMENT_CANCELLED,
    EVENT_FULFILLMENT_CREATED,
    EVENT_FULFILLMENT_UNDER_PROCESS,
    EVENT_FULFILLMENT_PACKED,
    EVENT_FULFILLMENT_OUT_FOR_DELIVERY,
    EVENT_FULFILLMENT_DELIVERY_DATE_CHANGED,
    EVENT_FULFILLMENT_DELIVERED,
    EVENT_FULFILLMENT_COMPLETED,
    EVENT_FULFILLMENT_CANCELLED,
    EVENT_INCIDENT_ASSIGNED,
    EVENT_INCIDENT_RESOLVED,
]
COMMUNICATION_CHANNELS = ["EMAIL", "WHATSAPP"]

# Final Notification-Rule UI Fix — single source of truth for "every event a
# communication rule can be configured against", reused by the admin UI's event
# selector (GET /api/v1/notifications/events, routers/notifications.py) instead
# of a second, independently-maintained list living in the frontend. This is
# additive only: no dispatch logic, recipient resolution, or business event was
# added here — COMMUNICATION_EVENTS/PROCUREMENT_EVENTS/MAINTENANCE_EVENTS above
# already existed (or were added in the prior pass) and already are what
# dispatch_event/dispatch_procurement_event/dispatch_maintenance_event key off;
# this just concatenates them for display purposes.
ALL_NOTIFICATION_EVENTS = (
    COMMUNICATION_EVENTS + PROCUREMENT_EVENTS + MAINTENANCE_EVENTS + CONTRACT_EVENTS + ASSET_EVENTS
)

EVENT_LABELS: dict[str, str] = {
    EVENT_REQUIREMENT_SUBMITTED: "Requirement submitted",
    EVENT_REQUIREMENT_APPROVED: "Requirement approved",
    EVENT_REQUIREMENT_REJECTED: "Requirement rejected",
    EVENT_REQUIREMENT_CANCELLED: "Requirement cancelled",
    EVENT_FULFILLMENT_CREATED: "Fulfillment created",
    EVENT_FULFILLMENT_UNDER_PROCESS: "Under process",
    EVENT_FULFILLMENT_PACKED: "Packed",
    EVENT_FULFILLMENT_OUT_FOR_DELIVERY: "Out for delivery",
    EVENT_FULFILLMENT_DELIVERY_DATE_CHANGED: "Expected delivery date changed",
    EVENT_FULFILLMENT_DELIVERED: "Delivered",
    EVENT_FULFILLMENT_COMPLETED: "Requirement completed",
    EVENT_FULFILLMENT_CANCELLED: "Fulfillment cancelled",
    EVENT_INCIDENT_ASSIGNED: "Incident assigned",
    EVENT_INCIDENT_RESOLVED: "Incident resolved",
    EVENT_QUOTATION_SUBMITTED: "Quotation submitted",
    EVENT_QUOTATION_APPROVED: "Quotation approved",
    EVENT_QUOTATION_REJECTED: "Quotation rejected",
    EVENT_INDENT_CREATED: "Indent created",
    EVENT_PO_CREATED: "Purchase order created",
    EVENT_VENDOR_VISIT_SCHEDULED: "Vendor visit scheduled",
    EVENT_INVOICE_RECORDED: "Invoice recorded",
    EVENT_INVOICE_VERIFIED: "Invoice verified",
    EVENT_PROCUREMENT_COMPLETED: "Procurement completed",
    EVENT_PM_DUE: "PM due soon",
    EVENT_PM_OVERDUE: "PM overdue",
    EVENT_CALIBRATION_DUE: "Calibration due soon",
    EVENT_CALIBRATION_OVERDUE: "Calibration overdue",
    EVENT_CONTRACT_CREATED: "Contract created",
    EVENT_AMC_RENEWED: "AMC renewed",
    EVENT_CMC_RENEWED: "CMC renewed",
    EVENT_WARRANTY_RENEWED: "Warranty renewed",
    EVENT_CONTRACT_EXPIRING: "Contract approaching expiry",
    EVENT_CONTRACT_EXPIRED: "Contract expired",
    EVENT_CONTRACT_ASSET_ADDED: "Covered asset added",
    EVENT_CONTRACT_ASSET_REMOVED: "Covered asset removed",
    EVENT_CONTRACT_STATUS_CHANGED: "Contract status changed",
    EVENT_ASSET_CREATED: "New asset added",
    EVENT_ASSET_ALLOCATED: "Device allocated to branch",
    EVENT_ASSET_TRANSFERRED: "Device transferred between branches",
    EVENT_ASSET_STATUS_CHANGED: "Device status changed",
    EVENT_ASSET_CONDEMNED: "Device condemned",
    EVENT_ASSET_RETIRED: "Device retired",
    EVENT_ASSET_LOCATION_CHANGED: "Device location changed",
}


class NotificationEventOut(BaseModel):
    code: str
    label: str

# Template variables resolvable by services/communication_service.build_incident_context()
# for the two INCIDENT_* events above — kept in a separate list from TEMPLATE_VARIABLES
# below since they come from a different context builder (incident, not requirement).
INCIDENT_TEMPLATE_VARIABLES = [
    "incident_id", "hospital", "branch", "problem_category", "severity", "status",
    "description", "reported_by_name", "reported_by_email",
    "assigned_engineer_name", "assigned_engineer_email",
]

# Module 19 — WhatsApp honest state machine (Notification.whatsapp_status).
WHATSAPP_STATUSES = ["QUEUED", "OPENED", "REQUIRES_USER_CONFIRMATION", "SENT", "FAILED"]

# Template variables resolvable by services/communication_service.build_context(), used
# for validation-free reference in the preview endpoint's response only (not enforced —
# an unresolved {{token}} is left literal in the rendered text, same "visible not hidden"
# discipline as the rest of this codebase's documented gaps).
TEMPLATE_VARIABLES = [
    "requirement_number", "hospital", "branch", "department", "priority", "items",
    "status", "expected_delivery_date", "fulfillment_number", "receiver_name",
    "requester_name", "requester_email", "manager_name", "manager_email",
    "inventory_manager_name", "inventory_manager_email", "reason", "required_by_date",
]


class NotificationTemplateIn(BaseModel):
    code: str
    channel: str
    event: str
    subject_template: str | None = None
    body_template: str
    is_active: bool = True
    sender_name: str | None = None
    sender_email: str | None = None
    sender_whatsapp_number: str | None = None
    receiver_name: str | None = None
    receiver_email: str | None = None
    receiver_whatsapp_number: str | None = None
    # Opt-in per rule: deliver this rule's notifications immediately on its own channel
    # (WhatsApp Web automation, or SMTP for EMAIL) instead of requiring manual approval.
    # Defaults False so existing rules are unchanged.
    auto_send: bool = False

    @field_validator("code", "event", "body_template")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        if value not in CHANNELS:
            raise ValueError(f"channel must be one of {CHANNELS}")
        return value


class NotificationTemplateOut(NotificationTemplateIn):
    id: str

    model_config = {"from_attributes": True}


class NotificationTemplateDeleteOut(BaseModel):
    """Batch F — Item 7: template delete is a WARNING, not a hard block — unlike the
    reference-count-then-409 pattern used elsewhere in this codebase (see
    routers/vendors.py delete_vendor, routers/devices.py delete_device_model), the
    delete always proceeds; `warning` is populated only when this was the last ACTIVE
    template covering `event` (on the given `channel`), so the caller knows the event
    is now uncovered without the API needing to invent a 409 for something that isn't
    actually blocked."""

    deleted: bool
    warning: str | None = None


class NotificationTemplatePreviewIn(BaseModel):
    """Renders a template's fields against real or sample data without saving anything
    or sending anything. `requirement_id` (and optional `fulfillment_id`) preview
    against real data; omit both to preview against a fixed sample context."""

    channel: str
    event: str
    subject_template: str | None = None
    body_template: str
    sender_name: str | None = None
    sender_email: str | None = None
    sender_whatsapp_number: str | None = None
    receiver_name: str | None = None
    receiver_email: str | None = None
    receiver_whatsapp_number: str | None = None
    requirement_id: str | None = None
    fulfillment_id: str | None = None

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        if value not in CHANNELS:
            raise ValueError(f"channel must be one of {CHANNELS}")
        return value


class NotificationTemplatePreviewOut(BaseModel):
    rendered_subject: str | None = None
    rendered_body: str
    rendered_sender_name: str | None = None
    rendered_sender_email: str | None = None
    rendered_sender_whatsapp_number: str | None = None
    rendered_receiver_name: str | None = None
    rendered_receiver_email: str | None = None
    rendered_receiver_whatsapp_number: str | None = None
    used_sample_data: bool


class NotificationIn(BaseModel):
    template_id: str | None = None
    recipient: str
    channel: str
    event: str
    related_entity_type: str | None = None
    related_entity_id: str | None = None

    @field_validator("recipient", "event")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        if value not in CHANNELS:
            raise ValueError(f"channel must be one of {CHANNELS}")
        return value


class NotificationOut(BaseModel):
    """Independent of NotificationIn (not a subclass) — output must not re-apply
    NotificationIn's "recipient must not be blank" input validator, since a Module 17B
    rule whose receiver template resolves to nothing legitimately produces a FAILED
    notification with an empty `recipient` (see communication_service.dispatch_event);
    that is a real, representable state, not bad input."""

    id: str
    template_id: str | None = None
    recipient: str
    channel: str
    event: str
    related_entity_type: str | None = None
    related_entity_id: str | None = None
    status: str
    approval_required: bool
    approved_by: str | None = None
    sent_at: str | None = None
    failure_reason: str | None = None
    related_requirement_id: str | None = None
    related_fulfillment_id: str | None = None
    rendered_subject: str | None = None
    rendered_body: str | None = None
    whatsapp_url: str | None = None
    whatsapp_status: str | None = None
    whatsapp_opened_at: str | None = None
    whatsapp_confirmed_by: str | None = None
    whatsapp_provider: str | None = None

    model_config = {"from_attributes": True}


class NotificationRejectIn(BaseModel):
    failure_reason: str

    @field_validator("failure_reason")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("failure_reason must not be blank")
        return value


class WhatsAppMarkFailedIn(BaseModel):
    failure_reason: str

    @field_validator("failure_reason")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("failure_reason must not be blank")
        return value


class CommunicationProviderConfigOut(BaseModel):
    channel: str
    active_provider: str
    is_configured: bool


class CommunicationProviderConfigIn(BaseModel):
    active_provider: str


# ---- Module 19 Enhancement — WhatsApp Web browser-automation session ----

class WhatsAppAutomationStatusOut(BaseModel):
    state: str
    qr_code_base64: str | None = None
    connected_at: str | None = None
    last_error: str | None = None
    last_checked_at: str | None = None


class WhatsAppTestMessageIn(BaseModel):
    recipient: str
    message: str

    @field_validator("recipient", "message")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class WhatsAppTestMessageOut(BaseModel):
    state: str
    detail: str
    evidence: str | None = None
