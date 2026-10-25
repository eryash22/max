from datetime import datetime

from pydantic import BaseModel

from .asset_lifecycle import ContractCoverageOut
from .procurement import IncidentProcurementSummaryOut

VALID_STATUSES = ["OPEN", "WORK_IN_PROGRESS", "TO_BE_CONDEMNED", "OPERATIONAL", "CANCELLED"]

# The canonical "is this incident still open?" definition. Before this existed the
# Dashboard derived open counts client-side as `total - RESOLVED - CLOSED`, which
# silently counted every CANCELLED incident as open — CANCELLED is a terminal
# "archive" state (incident_service.ALLOWED_TRANSITIONS maps both OPERATIONAL and
# CANCELLED to an empty transition set). Defined here next to VALID_STATUSES so the
# vocabulary and its open/closed partition can never drift apart, and so routers,
# services and the dashboard summary all share one definition rather than each
# re-deriving it by subtraction.
#
# Simplified to a 4-state vocabulary (plus CANCELLED as the existing archive action)
# per explicit product direction: OPEN, WORK_IN_PROGRESS, TO_BE_CONDEMNED are all
# still-open problems needing attention; OPERATIONAL (the device is back in service)
# and CANCELLED are the only terminal/non-open states.
NON_OPEN_STATUSES = frozenset({"OPERATIONAL", "CANCELLED"})
OPEN_STATUSES = tuple(s for s in VALID_STATUSES if s not in NON_OPEN_STATUSES)


class IncidentIn(BaseModel):
    branch_id: str
    department_id: str | None = None
    location_id: str | None = None
    asset_id: str | None = None
    problem_category: str | None = None
    description: str
    severity: str = "MEDIUM"
    clinical_impact: str | None = None
    patient_care_impact: str | None = None
    device_status_at_report: str | None = None


class IncidentUpdateIn(BaseModel):
    branch_id: str | None = None
    asset_id: str | None = None
    created_at: datetime | None = None
    clinical_impact: str | None = None
    patient_care_impact: str | None = None
    device_status_at_report: str | None = None
    assigned_engineer_id: str | None = None
    assigned_vendor_id: str | None = None
    resolution: str | None = None
    parts_used: str | None = None
    downtime_hours: float | None = None
    root_cause: str | None = None
    corrective_action: str | None = None
    preventive_action: str | None = None
    problem_category: str | None = None
    cer_number: str | None = None
    indent_number: str | None = None
    po_number: str | None = None
    po_closure_date: str | None = None
    po_cost_excl_gst: float | None = None
    po_gst_percent: float | None = None
    po_amount: float | None = None
    vendor_estimate_cost_excl_gst: float | None = None
    vendor_estimate_gst_percent: float | None = None
    vendor_estimate_cost_incl_gst: float | None = None
    device_name: str | None = None
    manufacturer: str | None = None
    vendor_name: str | None = None
    remark: str | None = None


class IncidentStatusChangeIn(BaseModel):
    to_status: str
    notes: str | None = None


class IncidentAssignIn(BaseModel):
    assigned_engineer_id: str | None = None
    assigned_vendor_id: str | None = None
    notes: str | None = None


class IncidentAttachmentIn(BaseModel):
    document_id: str


class IncidentAttachmentOut(BaseModel):
    id: str
    document_id: str
    entity_type: str
    entity_id: str

    model_config = {"from_attributes": True}


class IncidentCoverageOut(BaseModel):
    """Business-workflow audit — surfaces the asset's existing, already-derived
    AMC/CMC/Warranty coverage (see asset_lifecycle_service._warranty_for_asset /
    _amc_for_asset / _cmc_for_asset) at the point a BME actually needs it: deciding
    whether a vendor repair follows the covered-service path or needs a quotation.
    Nothing here is stored — computed fresh on every incident detail read, same as
    the asset detail page's own coverage table. Read-only, no new contract fields."""

    asset_id: str
    coverage_status: str  # "AMC" | "CMC" | "WARRANTY" | "NOT_COVERED"
    vendor_id: str | None = None
    amc: ContractCoverageOut | None = None
    cmc: ContractCoverageOut | None = None
    warranty: ContractCoverageOut | None = None


class IncidentOut(BaseModel):
    id: str
    created_at: datetime
    branch_id: str
    department_id: str | None = None
    location_id: str | None = None
    asset_id: str | None = None
    reported_by: str | None = None
    problem_category: str | None = None
    description: str
    severity: str
    clinical_impact: str | None = None
    patient_care_impact: str | None = None
    device_status_at_report: str | None = None
    status: str
    assigned_engineer_id: str | None = None
    assigned_vendor_id: str | None = None
    resolution: str | None = None
    parts_used: str | None = None
    downtime_hours: float | None = None
    root_cause: str | None = None
    corrective_action: str | None = None
    preventive_action: str | None = None
    closed_by: str | None = None
    closed_at: str | None = None
    closure_approved_by: str | None = None
    cer_number: str | None = None
    indent_number: str | None = None
    po_number: str | None = None
    po_closure_date: str | None = None
    po_cost_excl_gst: float | None = None
    po_gst_percent: float | None = None
    po_amount: float | None = None
    vendor_estimate_cost_excl_gst: float | None = None
    vendor_estimate_gst_percent: float | None = None
    vendor_estimate_cost_incl_gst: float | None = None
    device_name: str | None = None
    manufacturer: str | None = None
    vendor_name: str | None = None
    remark: str | None = None
    # Only populated by GET /incidents/{id} (detail) when asset_id is set — list
    # endpoints leave this None to avoid an extra query per row. See routers/incidents.py.
    coverage: IncidentCoverageOut | None = None
    # Module 21 — Procurement status rollup (quotations/indents/POs/invoices raised
    # against this incident). Same "detail-only, read-only, nothing stored" discipline
    # as `coverage` above; visible to anyone who can already read the incident, no new
    # permission required.
    procurement: IncidentProcurementSummaryOut | None = None

    model_config = {"from_attributes": True}


class IncidentStatusHistoryOut(BaseModel):
    id: str
    incident_id: str
    from_status: str | None = None
    to_status: str
    changed_by: str | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}


# ---- Bulk delete / Replace-all from Excel ----
# Explicit, user-initiated destructive operations (see routers/incidents.py) — the
# Excel workbook is the source of truth; neither of these ever runs automatically.

class IncidentBulkSelectionIn(BaseModel):
    """Either an explicit id list, or 'every row matching these filters' — the same
    filter params list_incidents already accepts, reused rather than re-invented.
    Shared by both the preview and the real bulk-delete endpoint so what you
    previewed is exactly what gets deleted."""
    incident_ids: list[str] | None = None
    select_all_matching_filter: bool = False
    search: str | None = None
    branch_id: str | None = None
    status_filter: str | None = None
    severity: str | None = None
    vendor_id: str | None = None


class IncidentBulkDeleteBlockedOut(BaseModel):
    incident_id: str
    description: str
    blocked_by: str  # e.g. "2 quotation(s), 1 invoice(s)"


class IncidentBulkDeletePreviewOut(BaseModel):
    total_selected: int
    deletable_count: int
    blocked: list[IncidentBulkDeleteBlockedOut] = []


class IncidentBulkDeleteResultOut(BaseModel):
    deleted: int
    skipped: int
    skipped_incidents: list[IncidentBulkDeleteBlockedOut] = []


class IncidentReplaceAllIssueOut(BaseModel):
    row_number: int
    field: str | None = None
    severity: str
    message: str


class IncidentReplaceAllPreviewOut(BaseModel):
    current_incident_count: int
    incoming_valid_rows: int
    incoming_invalid_rows: int
    issues: list[IncidentReplaceAllIssueOut] = []
    # Non-empty only if at least one EXISTING incident can't be safely deleted
    # (a real procurement record still references it) — if this is non-empty, the
    # real replace-all endpoint will refuse entirely, atomically, per the explicit
    # "either the complete replacement succeeds or nothing changes" requirement.
    blocking_existing_incidents: list[IncidentBulkDeleteBlockedOut] = []


class IncidentReplaceAllConfirmIn(BaseModel):
    confirm_replace: bool = False
    # Echoes the preview's current_incident_count back — guards against confirming
    # a stale preview if the data changed (e.g. someone else deleted/added an
    # incident) between preview and confirm.
    expected_current_count: int


class IncidentReplaceAllResultOut(BaseModel):
    deleted: int
    inserted: int
