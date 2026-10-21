"""Permission code catalog and default role→permission bundles.

Roles are permission bundles configured in the database (role_permissions table);
this module only defines the fixed vocabulary of permission codes and the
recommended default grants used by the database seed step.
"""

HOSPITAL_READ = "hospital.read"
HOSPITAL_WRITE = "hospital.write"
DEVICE_READ = "device.read"
DEVICE_WRITE = "device.write"
ASSET_READ = "asset.read"
ASSET_WRITE = "asset.write"
INVENTORY_READ = "inventory.read"
INVENTORY_WRITE = "inventory.write"
# Business-workflow audit (2026-08-22) fix: Hospital TL/Unit Head must be able to
# pick an item by name when raising a requirement, but must NEVER see central
# warehouse/branch stock quantities (client-mandated, backend-enforced — not a UI
# hide). This permission grants list-only access to inventory items/consumables
# with quantity/stock fields stripped in the response; see
# inventory_service.strip_stock_fields() and routers/inventory.py list_items()/
# list_consumables(). Distinct from INVENTORY_READ, which still returns full stock.
INVENTORY_PICKER_READ = "inventory.picker_read"
IMPORT_CREATE = "import.create"
IMPORT_APPROVE = "import.approve"
REVIEW_READ = "review.read"
REVIEW_RESOLVE = "review.resolve"
USERS_MANAGE = "users.manage"
ROLES_MANAGE = "roles.manage"
AUDIT_READ = "audit.read"
DOCUMENT_READ = "document.read"
DOCUMENT_WRITE = "document.write"

# Biomedical operations domain (asset history, incidents, PM/calibration, vendors,
# contracts, notifications, business-impact estimates).
INCIDENT_CREATE = "incident.create"
INCIDENT_READ = "incident.read"
INCIDENT_MANAGE = "incident.manage"
PM_READ = "pm.read"
PM_WRITE = "pm.write"
CALIBRATION_READ = "calibration.read"
CALIBRATION_WRITE = "calibration.write"
VENDOR_READ = "vendor.read"
VENDOR_WRITE = "vendor.write"
# Final admin/inventory/vendor/dashboard polish pass — Part 5: vendor visibility
# masking. Distinct from VENDOR_READ the same way INVENTORY_PICKER_READ is distinct
# from INVENTORY_READ (see comment above): grants the same list/detail vendor
# endpoints but with contact fields (email/phone/contact-person details) nulled
# server-side — see services/vendor_service.strip_contact_fields() and
# routers/vendors.py. A caller holding VENDOR_READ (full) never has this code
# checked; it only matters for callers who hold VENDOR_READ_LIMITED instead.
VENDOR_READ_LIMITED = "vendor.read_limited"
CONTRACT_READ = "contract.read"
CONTRACT_WRITE = "contract.write"
NOTIFICATION_READ = "notification.read"
NOTIFICATION_APPROVE = "notification.approve"
# Batch F — Item 7: Super Admin-only template hard delete. Distinct code rather than
# overloading NOTIFICATION_APPROVE, same convention as REQUIREMENT_DELETE below (added
# to ALL_PERMISSIONS only, not to any other role's bundle, so SUPER_ADMIN gets it
# automatically via DEFAULT_ROLE_PERMISSIONS[SUPER_ADMIN] = ALL_PERMISSIONS).
NOTIFICATION_TEMPLATE_DELETE = "notification_template.delete"
BUSINESS_IMPACT_READ = "business_impact.read"

# Module 16A — Hospital Requirement & Approval Workflow (requirement raise -> manager
# approve/reject only; inventory execution/fulfillment states are a later module).
# REQUIREMENT_CREATE: raise/submit/cancel own requirements (Hospital TL / Unit Head).
# REQUIREMENT_READ: broad read-only visibility (Inventory Manager, Viewer, Admin).
# REQUIREMENT_APPROVE: manager decision — approve or reject a pending requirement, plus
# full visibility and cancel-any — bundled the same way INCIDENT_MANAGE bundles
# assign/status-change/close under one permission rather than one code per verb.
REQUIREMENT_CREATE = "requirement.create"
REQUIREMENT_READ = "requirement.read"
REQUIREMENT_APPROVE = "requirement.approve"
# Final admin/inventory/vendor/dashboard polish pass — Part 7: Super Admin requirement
# hard-delete. Distinct code rather than overloading REQUIREMENT_APPROVE, matching this
# codebase's one-permission-per-action convention. Added to ALL_PERMISSIONS only (not
# to any other role's bundle) so SUPER_ADMIN gets it automatically via
# DEFAULT_ROLE_PERMISSIONS[SUPER_ADMIN] = ALL_PERMISSIONS below.
REQUIREMENT_DELETE = "requirement.delete"

# Module 17A — Inventory Fulfillment & Delivery Workflow (executes an APPROVED
# requirement: availability check, allocation/reservation, pack, dispatch, delivery,
# inventory stock update, completion). One permission, same shape as the domains
# above — Inventory Manager/Admin/Super Admin only; no separate read code because
# fulfillment visibility always follows requirement visibility (REQUIREMENT_READ/
# REQUIREMENT_APPROVE/REQUIREMENT_CREATE), see routers/fulfillments.py.
REQUIREMENT_FULFILL = "requirement.fulfill"

# Module 21 — Procurement (BME incident -> AMC/CMC/Warranty check -> [fully covered:
# vendor service, no quotation] / [partial or not covered: quotation -> Manager
# approval -> Indent/CR -> PO -> vendor visit -> invoice -> Inventory Manager
# verification -> financial closure]). Naming follows the existing
# `<domain>.<verb>` convention (quotation.create mirrors requirement.create, etc).
# QUOTATION_CREATE: record a vendor quotation (BME normally; Procurement Officer
# where explicitly assigned).
# QUOTATION_READ: broad read-only visibility.
# QUOTATION_APPROVE: Manager-only spend approval; self-approval (creator==approver)
# is additionally blocked in services/procurement_service.py regardless of who holds
# this permission — backend enforcement, not just role assignment.
# PROCUREMENT_MANAGE: Indent/CR create + execute (Procurement Officer).
# PO_MANAGE: Purchase Order create + status execution (Procurement Officer).
# INVOICE_RECORD: record a vendor invoice against a PO (Procurement Officer).
# INVOICE_APPROVE: verify/reject a recorded invoice (Inventory Manager) — the Manager
# does NOT approve invoices again, spend was already approved at the quotation stage.
# PROCUREMENT_READ: broad cross-role read visibility over Indent/PO/Invoice status
# (decision: not explicitly named in the spec's permission list, added so Manager/BME/
# Inventory Manager can see procurement status beyond their own quotation.read/
# invoice.* scope — same shape as REQUIREMENT_READ next to REQUIREMENT_CREATE/
# REQUIREMENT_APPROVE above). Quotation visibility itself stays on QUOTATION_READ.
QUOTATION_CREATE = "quotation.create"
QUOTATION_READ = "quotation.read"
QUOTATION_APPROVE = "quotation.approve"
PROCUREMENT_MANAGE = "procurement.manage"
PO_MANAGE = "po.manage"
INVOICE_RECORD = "invoice.record"
INVOICE_APPROVE = "invoice.approve"
PROCUREMENT_READ = "procurement.read"

# Final admin/inventory/vendor/dashboard polish pass — Part 11: admin-controlled theme
# configuration. Grant to SUPER_ADMIN only, via ALL_PERMISSIONS below — no other
# role's bundle includes it. GET /api/v1/theme is open to any authenticated user
# (no permission check); only PUT /api/v1/theme is gated by this code.
SETTINGS_MANAGE = "settings.manage"

# DEV Data Cleanup — Super-Admin-only cascade purge of QA/demo/test data (a QA Branch
# and everything scoped to it). Same convention as SETTINGS_MANAGE/REQUIREMENT_DELETE
# above: added to ALL_PERMISSIONS only, never to any other role's bundle, so only
# SUPER_ADMIN ever has it. Distinct from every other *_MANAGE/*_DELETE code because
# this one permission gates an operation that intentionally bypasses individual
# entities' own status/reference delete-guards (see services/dev_cleanup.py) — it
# must never be bundled into a broader admin role by accident.
DEV_DATA_CLEANUP = "dev.data_cleanup"

# Branch-scoping hardening pass — UNIT_HEAD/TL must never see the global Device
# Catalog (device.read grants full catalog admin visibility: notes, data source,
# catalog-match/model-information status, known_quantity, plus create/archive
# actions gated on device.write). They still need to pick a device model by
# category when raising a DEVICE/DEVICE_MODEL requirement item, so this grants
# list-only access to /device-categories and /device-models with the
# catalog-admin-only fields stripped server-side — same shape as
# INVENTORY_PICKER_READ/VENDOR_READ_LIMITED above. A caller holding DEVICE_READ
# (full) never has this code checked; it only matters for callers who hold
# DEVICE_READ_PICKER instead (see routers/devices.py list_categories/
# list_device_models and services/device_service.strip_catalog_admin_fields()).
DEVICE_READ_PICKER = "device.read_picker"

# Device QR Code Management System — Super-Admin-only administration of QR identity
# generation/regeneration/revocation and the public-scan display-field configuration.
# Same convention as SETTINGS_MANAGE/DEV_DATA_CLEANUP above: added to ALL_PERMISSIONS
# only, never to any other role's bundle, so only SUPER_ADMIN ever has it. The public
# scan endpoint itself requires no permission at all (unauthenticated, read-only,
# allowlisted fields only — see routers/qr_public.py).
QR_MANAGE = "qr.manage"

ALL_PERMISSIONS = [
    HOSPITAL_READ,
    HOSPITAL_WRITE,
    DEVICE_READ,
    DEVICE_WRITE,
    DEVICE_READ_PICKER,
    ASSET_READ,
    ASSET_WRITE,
    INVENTORY_READ,
    INVENTORY_WRITE,
    INVENTORY_PICKER_READ,
    IMPORT_CREATE,
    IMPORT_APPROVE,
    REVIEW_READ,
    REVIEW_RESOLVE,
    USERS_MANAGE,
    ROLES_MANAGE,
    AUDIT_READ,
    DOCUMENT_READ,
    DOCUMENT_WRITE,
    INCIDENT_CREATE,
    INCIDENT_READ,
    INCIDENT_MANAGE,
    PM_READ,
    PM_WRITE,
    CALIBRATION_READ,
    CALIBRATION_WRITE,
    VENDOR_READ,
    VENDOR_WRITE,
    VENDOR_READ_LIMITED,
    CONTRACT_READ,
    CONTRACT_WRITE,
    NOTIFICATION_READ,
    NOTIFICATION_APPROVE,
    NOTIFICATION_TEMPLATE_DELETE,
    BUSINESS_IMPACT_READ,
    REQUIREMENT_CREATE,
    REQUIREMENT_READ,
    REQUIREMENT_APPROVE,
    REQUIREMENT_DELETE,
    REQUIREMENT_FULFILL,
    QUOTATION_CREATE,
    QUOTATION_READ,
    QUOTATION_APPROVE,
    PROCUREMENT_MANAGE,
    PO_MANAGE,
    INVOICE_RECORD,
    INVOICE_APPROVE,
    PROCUREMENT_READ,
    SETTINGS_MANAGE,
    DEV_DATA_CLEANUP,
    QR_MANAGE,
]

SUPER_ADMIN = "SUPER_ADMIN"
BIOMEDICAL_ADMIN = "BIOMEDICAL_ADMIN"
BIOMEDICAL_ENGINEER = "BIOMEDICAL_ENGINEER"
INVENTORY_MANAGER = "INVENTORY_MANAGER"
HOSPITAL_UNIT_MANAGER = "HOSPITAL_UNIT_MANAGER"
DOCTOR = "DOCTOR"
STAFF_OPERATOR = "STAFF_OPERATOR"
VIEWER = "VIEWER"
# Module 20 — dedicated requirement-approval Manager role. BIOMEDICAL_ADMIN previously
# stood in for this (documented limitation, Module 16A/17A); MANAGER is now the real,
# narrowly-scoped approver, additive alongside BIOMEDICAL_ADMIN which keeps
# requirement.approve for continuity of existing assignments (see PROJECT_STATUS.md).
MANAGER = "MANAGER"
# Module 21 — Procurement. New role: records quotations only where explicitly
# assigned (BME is the normal quotation recorder), owns Indent/CR and PO
# creation/execution, records invoices. Segregation of duties (backend-enforced,
# not just role scoping): never granted quotation.approve or invoice.approve, and
# services/procurement_service.py additionally blocks a quotation's own creator
# from approving it regardless of which permission they hold.
PROCUREMENT_OFFICER = "PROCUREMENT_OFFICER"

# Final hardening pass — real branch user hierarchy. HOSPITAL_UNIT_MANAGER was the
# single stand-in for both "branch operational owner" and "branch reporting user";
# the final business hierarchy needs them distinct (Branch -> Unit Head -> TL) so
# branch-scoped visibility (routers/requirements.py, routers/incidents.py,
# routers/devices.py) can tell an owning Unit Head apart from a reporting TL and,
# more importantly, so both can be backed by a real User row (Branch.unit_head_user_id,
# BranchTL) instead of a free-text name. Permission bundle is intentionally identical
# to HOSPITAL_UNIT_MANAGER's (same "no inventory quantities, no approval, no
# fulfillment" ceiling) — HOSPITAL_UNIT_MANAGER is kept, unchanged, for existing
# assignments; new branch users should be given UNIT_HEAD or TL going forward.
UNIT_HEAD = "UNIT_HEAD"
TL = "TL"

DEFAULT_ROLE_PERMISSIONS: dict[str, list[str]] = {
    SUPER_ADMIN: ALL_PERMISSIONS,
    BIOMEDICAL_ADMIN: [
        HOSPITAL_READ, HOSPITAL_WRITE,
        DEVICE_READ, DEVICE_WRITE,
        ASSET_READ, ASSET_WRITE,
        INVENTORY_READ, INVENTORY_WRITE,
        IMPORT_CREATE, IMPORT_APPROVE,
        REVIEW_READ, REVIEW_RESOLVE,
        AUDIT_READ,
        DOCUMENT_READ, DOCUMENT_WRITE,
        INCIDENT_CREATE, INCIDENT_READ, INCIDENT_MANAGE,
        PM_READ, PM_WRITE,
        CALIBRATION_READ, CALIBRATION_WRITE,
        VENDOR_READ, VENDOR_WRITE,
        CONTRACT_READ, CONTRACT_WRITE,
        NOTIFICATION_READ, NOTIFICATION_APPROVE,
        BUSINESS_IMPACT_READ,
        REQUIREMENT_CREATE, REQUIREMENT_READ, REQUIREMENT_APPROVE, REQUIREMENT_FULFILL,
        # Module 21 — continuity role, same treatment as REQUIREMENT_APPROVE above:
        # full procurement authority (minus nothing — segregation of duties is
        # enforced in services/procurement_service.py's creator!=approver check, not
        # by withholding permissions from this admin-continuity role).
        QUOTATION_CREATE, QUOTATION_READ, QUOTATION_APPROVE,
        PROCUREMENT_MANAGE, PO_MANAGE, INVOICE_RECORD, INVOICE_APPROVE, PROCUREMENT_READ,
    ],
    BIOMEDICAL_ENGINEER: [
        HOSPITAL_READ,
        DEVICE_READ, DEVICE_WRITE,
        ASSET_READ, ASSET_WRITE,
        INVENTORY_READ,
        IMPORT_CREATE,
        REVIEW_READ,
        DOCUMENT_READ, DOCUMENT_WRITE,
        INCIDENT_CREATE, INCIDENT_READ, INCIDENT_MANAGE,
        PM_READ, PM_WRITE,
        CALIBRATION_READ, CALIBRATION_WRITE,
        VENDOR_READ,
        CONTRACT_READ,
        NOTIFICATION_READ,
        # Module 21 — BME is the normal recorder of a vendor quotation once technical
        # inspection + AMC/CMC/Warranty check determine the repair is partially/not
        # covered (see live-verification flow in PROJECT_STATUS.md). No approve —
        # Manager-only.
        QUOTATION_CREATE, QUOTATION_READ, PROCUREMENT_READ,
    ],
    INVENTORY_MANAGER: [
        HOSPITAL_READ,
        DEVICE_READ,
        ASSET_READ,
        INVENTORY_READ, INVENTORY_WRITE,
        IMPORT_CREATE,
        REVIEW_READ,
        DOCUMENT_READ,
        INCIDENT_READ,
        VENDOR_READ,
        CONTRACT_READ,
        # Inventory Manager: broad read visibility over requirements (Module 16A) plus
        # the Module 17A fulfillment permission — the role that actually executes
        # availability check/allocation/pack/dispatch/delivery/completion.
        REQUIREMENT_READ,
        REQUIREMENT_FULFILL,
        # Module 21 — Inventory Manager verifies vendor invoices (financial closure);
        # the Manager does not approve invoices a second time.
        INVOICE_APPROVE, PROCUREMENT_READ,
    ],
    HOSPITAL_UNIT_MANAGER: [
        HOSPITAL_READ,
        DEVICE_READ,
        ASSET_READ,
        REVIEW_READ,
        DOCUMENT_READ,
        INCIDENT_READ,
        BUSINESS_IMPACT_READ,
        # Hospital TL / Unit Head: raises requirements and incidents, views/updates/
        # submits/cancels their own (own-scope enforced in the router). INCIDENT_CREATE
        # was missing here even though the /raise page already shows Incident tiles to
        # anyone with incident.read OR incident.create — a TL could reach the incident
        # form and get a 403 on submit. Workflow consolidation pass fix.
        REQUIREMENT_CREATE,
        INCIDENT_CREATE,
        # Consumables workflow pass: without inventory read access, /requirements/new's
        # consumable/spare-part pickers 403'd on GET /consumables and
        # /inventory/items, silently rendering as "No items available" — a TL could
        # never actually select what they were requesting. Business-workflow audit
        # (2026-08-22): the original fix granted full INVENTORY_READ, which let TL/
        # Unit Head see central stock quantities — the client explicitly forbids this.
        # Swapped to INVENTORY_PICKER_READ: same list endpoints, stock fields stripped
        # server-side. inventory.write (create/update stock) stays Inventory
        # Manager/Admin only.
        INVENTORY_PICKER_READ,
        # Final admin/inventory/vendor/dashboard polish pass — Part 5: masked
        # (contact-info-stripped) vendor visibility, same shape as
        # INVENTORY_PICKER_READ above. This role held no vendor permission at all
        # before this pass; VENDOR_READ_LIMITED is a net-new, deliberately limited
        # grant, never full VENDOR_READ.
        VENDOR_READ_LIMITED,
    ],
    DOCTOR: [
        HOSPITAL_READ,
        DEVICE_READ,
        ASSET_READ,
        DOCUMENT_READ,
        INCIDENT_CREATE, INCIDENT_READ,
        VENDOR_READ_LIMITED,
    ],
    STAFF_OPERATOR: [
        HOSPITAL_READ,
        DEVICE_READ,
        ASSET_READ,
        DOCUMENT_READ,
        INCIDENT_CREATE, INCIDENT_READ,
        VENDOR_READ_LIMITED,
    ],
    VIEWER: [
        HOSPITAL_READ,
        DEVICE_READ,
        ASSET_READ,
        DOCUMENT_READ,
        INCIDENT_READ,
        REQUIREMENT_READ,
        VENDOR_READ_LIMITED,
    ],
    # Manager: least-privilege requirement-approval role. Read within scope, approve/
    # reject (requirement.approve bundles both, see comment above), full requirement
    # history and fulfillment-state visibility (fulfillment router's broad-visibility
    # check already includes requirement.approve), and incident read for context —
    # deliberately excludes device/asset/inventory/vendor/contract/PM/calibration
    # write access that BIOMEDICAL_ADMIN's broader bundle carries.
    MANAGER: [
        HOSPITAL_READ,
        REQUIREMENT_READ,
        REQUIREMENT_APPROVE,
        INCIDENT_READ,
        # Consumables workflow pass: without inventory.read, /requirements/[id]'s
        # consumable/spare-part name lookup 403'd for this role, so the Manager saw a
        # raw consumable_id instead of its name/stock on the very approval screen the
        # brief requires ("Manager should see: ... Consumable ..."). Read-only.
        INVENTORY_READ,
        # Asset Digital Passport pass: a Manager approves requirements for physical
        # devices and needs to see what they approved become — read-only, same as
        # every other read-only role above; no ASSET_WRITE.
        ASSET_READ,
        # Module 21 — Manager is the canonical spend approver for quotations. No new
        # procurement execution permissions beyond what approval needs (no
        # procurement.manage/po.manage/invoice.record/invoice.approve — those stay
        # with Procurement Officer/Inventory Manager).
        QUOTATION_READ, QUOTATION_APPROVE, PROCUREMENT_READ,
    ],
    # Module 21 — Procurement Officer: records quotations only where explicitly
    # assigned (BME is the normal recorder), owns Indent/CR and PO
    # creation/execution end-to-end, records vendor invoices. Deliberately excludes
    # QUOTATION_APPROVE and INVOICE_APPROVE (segregation of duties).
    PROCUREMENT_OFFICER: [
        HOSPITAL_READ,
        DEVICE_READ,
        ASSET_READ,
        DOCUMENT_READ, DOCUMENT_WRITE,
        INCIDENT_READ,
        VENDOR_READ,
        CONTRACT_READ,
        QUOTATION_CREATE, QUOTATION_READ,
        PROCUREMENT_MANAGE, PO_MANAGE, INVOICE_RECORD, PROCUREMENT_READ,
    ],
    # Final hardening pass — same permission ceiling as HOSPITAL_UNIT_MANAGER (see
    # comment above the role constants): branch operational owner/requester, no
    # inventory quantities, no approval or fulfillment authority. Distinguished from
    # HOSPITAL_UNIT_MANAGER only by branch-scoped visibility (routers use
    # current_user.roles + hospital_scope_id to scope requirements/incidents/assets to
    # the assigned branch instead of "own submissions only").
    #
    # Branch-scoping hardening pass, continued: DEVICE_READ swapped for
    # DEVICE_READ_PICKER (see its definition above) — no Device Catalog admin
    # visibility, just enough to pick a device model by category when raising a
    # requirement.
    UNIT_HEAD: [
        HOSPITAL_READ,
        DEVICE_READ_PICKER,
        ASSET_READ,
        REVIEW_READ,
        DOCUMENT_READ,
        INCIDENT_READ,
        BUSINESS_IMPACT_READ,
        REQUIREMENT_CREATE,
        INCIDENT_CREATE,
        INVENTORY_PICKER_READ,
        VENDOR_READ_LIMITED,
    ],
    TL: [
        HOSPITAL_READ,
        DEVICE_READ_PICKER,
        ASSET_READ,
        REVIEW_READ,
        DOCUMENT_READ,
        INCIDENT_READ,
        BUSINESS_IMPACT_READ,
        REQUIREMENT_CREATE,
        INCIDENT_CREATE,
        INVENTORY_PICKER_READ,
        VENDOR_READ_LIMITED,
    ],
}

def branch_scope_id(roles: list[str], hospital_scope_id: str | None) -> str | None:
    """Real branch user hierarchy (final hardening pass). UNIT_HEAD/TL users are
    scoped to their assigned branch (`User.hospital_scope_id`) rather than only their
    own submissions — returns that branch id, or None if the caller isn't a
    branch-scoped role (or has no branch assigned, in which case callers should fall
    back to their existing own-record scoping rather than granting global visibility).
    HOSPITAL_UNIT_MANAGER is deliberately excluded — its existing own-record-only
    scope is unchanged for back-compat with data/assignments made before these two
    roles existed."""
    if hospital_scope_id and (UNIT_HEAD in roles or TL in roles):
        return hospital_scope_id
    return None


DEFAULT_ROLE_LABELS: dict[str, str] = {
    SUPER_ADMIN: "Super Admin",
    BIOMEDICAL_ADMIN: "Biomedical Admin",
    BIOMEDICAL_ENGINEER: "Biomedical Engineer",
    INVENTORY_MANAGER: "Inventory Manager",
    HOSPITAL_UNIT_MANAGER: "Hospital/Unit Manager",
    DOCTOR: "Doctor",
    STAFF_OPERATOR: "Staff/Operator",
    VIEWER: "Viewer",
    MANAGER: "Manager",
    PROCUREMENT_OFFICER: "Procurement Officer",
    UNIT_HEAD: "Unit Head",
    TL: "TL",
}
