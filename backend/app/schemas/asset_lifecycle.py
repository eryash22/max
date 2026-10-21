from datetime import date, datetime

from pydantic import BaseModel

# Module 18A — recommended (not DB-enforced) lifecycle status vocabulary, taken verbatim
# from this task's spec. Asset.status stays free text (see models/device.py's docstring
# for why enforcing this would break existing data) — this list is exposed for the
# frontend's status picker only.
LIFECYCLE_STATUSES = [
    "PROCUREMENT", "RECEIVED", "INSTALLED", "OPERATIONAL", "UNDER_MAINTENANCE",
    "UNDER_REPAIR", "AMC", "CMC", "WARRANTY", "CONDEMNED", "RETIRED",
]

MAINTENANCE_SOURCE_TYPES = ["PM", "CALIBRATION", "SERVICE_VISIT"]


class ContractCoverageOut(BaseModel):
    """One AMC/CMC/Warranty coverage summary, derived live from Module 05A's contract
    tables — never stored on Asset. `expiry_status` reuses
    contract_service.compute_expiry_status verbatim."""

    contract_id: str | None = None
    contract_number: str | None = None
    vendor_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    status: str | None = None
    expiry_status: str


class AssetLifecycleSummaryOut(BaseModel):
    asset_id: str
    asset_number: str | None = None
    serial_number: str | None = None
    device_model_id: str
    status: str
    branch_id: str
    department_id: str | None = None
    location_id: str | None = None
    criticality: str | None = None
    purchase_date: str | None = None
    installation_date: str | None = None
    operational_since: str | None = None
    warranty: ContractCoverageOut | None = None
    amc: ContractCoverageOut | None = None
    cmc: ContractCoverageOut | None = None
    current_vendor_id: str | None = None
    last_pm_date: str | None = None
    next_pm_due_date: str | None = None
    last_calibration_date: str | None = None
    next_calibration_due_date: str | None = None
    last_service_visit_date: str | None = None


class MaintenanceHistoryEntryOut(BaseModel):
    """Unified view over PMRecord/CalibrationRecord/ServiceVisit for one asset — no new
    table, per this task's "reuse existing PM, Calibration, Service Visit" instruction.
    Fields with no equivalent on a given source row are left null rather than invented
    (e.g. `issue`/`downtime_hours` only ever come from ServiceVisit)."""

    id: str
    source_type: str
    asset_id: str
    maintenance_type: str | None = None
    service_date: str | None = None
    engineer_id: str | None = None
    vendor_id: str | None = None
    performed_by_label: str | None = None
    issue: str | None = None
    action_taken: str | None = None
    parts_used: str | None = None
    downtime_hours: float | None = None
    result: str | None = None
    next_maintenance_date: str | None = None
    remarks: str | None = None


class AssetDashboardSummaryOut(BaseModel):
    total_operational: int
    under_maintenance: int
    under_repair: int
    amc_active: int
    amc_expired: int
    cmc_active: int
    cmc_expired: int
    warranty_active: int
    warranty_expired: int
    condemned: int
    retired: int
    upcoming_amc_expiry: int
    upcoming_cmc_expiry: int
    upcoming_maintenance: int
    recent_maintenance: list[MaintenanceHistoryEntryOut]


class AssetProvenanceEntryOut(BaseModel):
    """One requirement/fulfillment pairing an asset was delivered through — read-only
    projection over the existing Asset -> FulfillmentItemAsset -> FulfillmentItem ->
    FulfillmentRequest -> Requirement chain (Modules 16A/17A). No new table; an asset
    can appear in more than one entry if it was allocated/reallocated across
    fulfillments."""

    requirement_id: str
    requirement_number: str
    requirement_branch_id: str
    requirement_status: str
    fulfillment_id: str
    fulfillment_number: str
    fulfillment_status: str
    expected_delivery_date: str | None = None
    actual_delivery_date: str | None = None
    delivered: bool


class CentralDeviceStockOut(BaseModel):
    """One row of Part B's "central warehouse device stock" — a physical-asset count
    (never a DeviceModel catalog count) grouped by DeviceModel, for whichever
    Branch(es) have is_central_warehouse=True. See
    asset_lifecycle_service.compute_central_device_stock."""

    device_model_id: str
    name: str
    manufacturer: str | None = None
    model_number: str | None = None
    category: str | None = None
    available_count: int


class CalendarEventOut(BaseModel):
    # PM_DUE, CALIBRATION_DUE, AMC_EXPIRY, CMC_EXPIRY, WARRANTY_EXPIRY, SERVICE_VISIT,
    # EXPECTED_DELIVERY (Module 19 — in-flight Fulfillment.expected_delivery_date)
    event_type: str
    date: str
    asset_id: str | None = None
    reference_id: str
    label: str
