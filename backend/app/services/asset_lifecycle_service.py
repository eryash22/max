"""Module 18A — Asset Lifecycle, AMC/CMC & Maintenance Management.

AMC/CMC/Warranty coverage is always derived live from the existing Module 05A contract
tables (never duplicated onto Asset) — this module is the single place that reads them
for lifecycle purposes, reusing contract_service.compute_expiry_status verbatim so the
"ACTIVE/EXPIRED/EXPIRING_SOON/UPCOMING/NOT_SET" vocabulary stays identical to the
Contracts module. Maintenance history is a read-only union over PMRecord/
CalibrationRecord/ServiceVisit (Modules 02A/03A/12) — no new maintenance table.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .contract_service import compute_expiry_status
from ..models.contracts import AmcContract, AmcContractAsset, CmcContract, CmcContractAsset, Warranty
from ..models.device import Asset
from ..models.fulfillment import FulfillmentItem, FulfillmentItemAsset, FulfillmentRequest
from ..models.identity import User
from ..models.maintenance import CalibrationRecord, CalibrationSchedule, PMRecord, PMSchedule
from ..models.incident import ServiceVisit
from ..models.requirement import Requirement
from ..models.vendor import Vendor

# Module 19 — in-flight fulfillment statuses whose expected_delivery_date is a real,
# still-relevant commitment. DELIVERED/COMPLETED/CANCELLED are excluded (history, not
# an upcoming date), matching how PM/calibration schedules only surface upcoming due
# dates, not past ones.
_IN_FLIGHT_FULFILLMENT_STATUSES = ("ALLOCATED", "UNDER_PROCESS", "PACKED", "OUT_FOR_DELIVERY")
from ..schemas.asset_lifecycle import (
    AssetDashboardSummaryOut,
    AssetLifecycleSummaryOut,
    AssetProvenanceEntryOut,
    CalendarEventOut,
    ContractCoverageOut,
    MaintenanceHistoryEntryOut,
)
from ..schemas.incident import IncidentCoverageOut

# Statuses treated as "under maintenance" / "under repair" for dashboard bucketing —
# matches Module 18A's recommended vocabulary (LIFECYCLE_STATUSES) case-insensitively.
# Legacy/free-text values that predate this module (e.g. "Pending", "In Service") fall
# into none of these buckets, which is correct: they were never lifecycle-classified.
_OPERATIONAL_VALUES = {"OPERATIONAL"}
_UNDER_MAINTENANCE_VALUES = {"UNDER_MAINTENANCE"}
_UNDER_REPAIR_VALUES = {"UNDER_REPAIR"}
_CONDEMNED_VALUES = {"CONDEMNED"}
_RETIRED_VALUES = {"RETIRED"}
UPCOMING_WINDOW_DAYS = 30


def is_asset_available_for_allocation(asset: Asset) -> bool:
    """True unless the asset's lifecycle status is CONDEMNED/RETIRED (case-
    insensitive, same canonical vocabulary as compute_dashboard_summary above) —
    the single shared definition of "eligible for fulfillment allocation /
    central-warehouse stock count" so fulfillment_service.compute_available_assets
    and compute_central_device_stock (Part B, central warehouse device stock) never
    drift from each other or from the dashboard's own condemned/retired bucketing."""
    status = (asset.status or "").upper()
    return status not in _CONDEMNED_VALUES and status not in _RETIRED_VALUES


def compute_central_device_stock(db: Session) -> list[dict]:
    """Physical-asset count per DeviceModel at the branch(es) flagged
    is_central_warehouse=True — the canonical "central warehouse device stock"
    (Part B). Deliberately distinct from central *consumable* stock (Consumable.
    branch_id IS NULL, no Branch row involved at all): a central Asset is a normal
    Asset row whose branch_id happens to point at the central-warehouse Branch."""
    from ..models.device import DeviceModel
    from ..models.hospital import Branch

    central_branch_ids = [b.id for b in db.query(Branch.id).filter(Branch.is_central_warehouse.is_(True)).all()]
    if not central_branch_ids:
        return []
    assets = db.query(Asset).filter(Asset.branch_id.in_(central_branch_ids)).all()
    counts: dict[str, int] = {}
    for asset in assets:
        if not is_asset_available_for_allocation(asset):
            continue
        counts[asset.device_model_id] = counts.get(asset.device_model_id, 0) + 1
    if not counts:
        return []
    models = {m.id: m for m in db.query(DeviceModel).filter(DeviceModel.id.in_(counts.keys())).all()}
    rows = [
        {
            "device_model_id": model_id,
            "name": models[model_id].name if model_id in models else model_id,
            "manufacturer": models[model_id].manufacturer if model_id in models else None,
            "model_number": models[model_id].model_number if model_id in models else None,
            "category": models[model_id].category if model_id in models else None,
            "available_count": count,
        }
        for model_id, count in counts.items()
    ]
    rows.sort(key=lambda r: r["name"])
    return rows


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _warranty_for_asset(db: Session, asset_id: str) -> Warranty | None:
    return (
        db.query(Warranty)
        .filter(Warranty.asset_id == asset_id)
        .order_by(Warranty.created_at.desc())
        .first()
    )


def _amc_for_asset(db: Session, asset_id: str) -> AmcContract | None:
    link = (
        db.query(AmcContractAsset)
        .join(AmcContract, AmcContractAsset.amc_contract_id == AmcContract.id)
        .filter(AmcContractAsset.asset_id == asset_id)
        .order_by(AmcContract.created_at.desc())
        .first()
    )
    return db.get(AmcContract, link.amc_contract_id) if link else None


def _cmc_for_asset(db: Session, asset_id: str) -> CmcContract | None:
    link = (
        db.query(CmcContractAsset)
        .join(CmcContract, CmcContractAsset.cmc_contract_id == CmcContract.id)
        .filter(CmcContractAsset.asset_id == asset_id)
        .order_by(CmcContract.created_at.desc())
        .first()
    )
    return db.get(CmcContract, link.cmc_contract_id) if link else None


def _to_coverage_out(contract, *, number_field: str = "contract_number") -> ContractCoverageOut | None:
    if contract is None:
        return None
    return ContractCoverageOut(
        contract_id=contract.id,
        contract_number=getattr(contract, number_field, None),
        vendor_id=contract.vendor_id,
        start_date=contract.start_date,
        end_date=contract.end_date,
        status=contract.status,
        expiry_status=compute_expiry_status(contract.start_date, contract.end_date),
    )


def _current_vendor_id(warranty: Warranty | None, amc: AmcContract | None, cmc: CmcContract | None) -> str | None:
    """Whichever of AMC/CMC/Warranty is currently ACTIVE wins (AMC/CMC preferred over
    Warranty, matching real-world precedence — an AMC/CMC vendor is who actually
    services the device day to day). Falls back to any configured vendor_id if none
    is presently ACTIVE, so the field isn't blank just because coverage lapsed."""
    for contract in (amc, cmc, warranty):
        if contract and contract.vendor_id and compute_expiry_status(contract.start_date, contract.end_date) == "ACTIVE":
            return contract.vendor_id
    for contract in (amc, cmc, warranty):
        if contract and contract.vendor_id:
            return contract.vendor_id
    return None


def get_incident_coverage(db: Session, asset_id: str) -> IncidentCoverageOut:
    """Business-workflow audit — Incident coverage visibility. Reuses the exact same
    private lookup functions get_asset_lifecycle_summary() already uses; returns the
    IncidentCoverageOut shape instead (asset_id + a single coverage_status verdict +
    all three layers) since an incident only needs coverage, not the full lifecycle
    summary."""
    warranty = _warranty_for_asset(db, asset_id)
    amc = _amc_for_asset(db, asset_id)
    cmc = _cmc_for_asset(db, asset_id)

    amc_out = _to_coverage_out(amc)
    cmc_out = _to_coverage_out(cmc)
    warranty_out = _to_coverage_out(warranty, number_field="warranty_number")

    coverage_status = "NOT_COVERED"
    for label, coverage_out in (("AMC", amc_out), ("CMC", cmc_out), ("WARRANTY", warranty_out)):
        if coverage_out is not None and coverage_out.expiry_status == "ACTIVE":
            coverage_status = label
            break

    return IncidentCoverageOut(
        asset_id=asset_id,
        coverage_status=coverage_status,
        vendor_id=_current_vendor_id(warranty, amc, cmc),
        amc=amc_out,
        cmc=cmc_out,
        warranty=warranty_out,
    )


def get_asset_lifecycle_summary(db: Session, asset: Asset) -> AssetLifecycleSummaryOut:
    warranty = _warranty_for_asset(db, asset.id)
    amc = _amc_for_asset(db, asset.id)
    cmc = _cmc_for_asset(db, asset.id)

    last_pm = db.query(PMRecord).filter(PMRecord.asset_id == asset.id).order_by(PMRecord.performed_date.desc()).first()
    pm_schedule = db.query(PMSchedule).filter(PMSchedule.asset_id == asset.id).first()
    last_calibration = (
        db.query(CalibrationRecord).filter(CalibrationRecord.asset_id == asset.id)
        .order_by(CalibrationRecord.performed_date.desc()).first()
    )
    calibration_schedule = db.query(CalibrationSchedule).filter(CalibrationSchedule.asset_id == asset.id).first()
    last_visit = (
        db.query(ServiceVisit).filter(ServiceVisit.asset_id == asset.id)
        .order_by(ServiceVisit.visit_date.desc()).first()
    )

    return AssetLifecycleSummaryOut(
        asset_id=asset.id,
        asset_number=asset.asset_number,
        serial_number=asset.serial_number,
        device_model_id=asset.device_model_id,
        status=asset.status,
        branch_id=asset.branch_id,
        department_id=asset.department_id,
        location_id=asset.location_id,
        criticality=asset.criticality,
        purchase_date=asset.purchase_date,
        installation_date=asset.installation_date,
        operational_since=asset.operational_since,
        warranty=_to_coverage_out(warranty, number_field="warranty_number"),
        amc=_to_coverage_out(amc),
        cmc=_to_coverage_out(cmc),
        current_vendor_id=_current_vendor_id(warranty, amc, cmc),
        last_pm_date=last_pm.performed_date if last_pm else None,
        next_pm_due_date=pm_schedule.next_due_date if pm_schedule else None,
        last_calibration_date=last_calibration.performed_date if last_calibration else None,
        next_calibration_due_date=calibration_schedule.next_due_date if calibration_schedule else None,
        last_service_visit_date=last_visit.visit_date if last_visit else None,
    )


def list_asset_provenance(db: Session, asset_id: str) -> list[AssetProvenanceEntryOut]:
    """Requirement/fulfillment provenance for one asset, walking the existing chain
    Asset -> FulfillmentItemAsset -> FulfillmentItem -> FulfillmentRequest ->
    Requirement (Modules 16A/17A) — no data duplicated onto Asset. Most recently
    created link first."""
    links = (
        db.query(FulfillmentItemAsset)
        .filter(FulfillmentItemAsset.asset_id == asset_id)
        .order_by(FulfillmentItemAsset.created_at.desc())
        .all()
    )
    entries: list[AssetProvenanceEntryOut] = []
    for link in links:
        item = db.get(FulfillmentItem, link.fulfillment_item_id)
        if item is None:
            continue
        fulfillment = db.get(FulfillmentRequest, item.fulfillment_id)
        if fulfillment is None:
            continue
        requirement = db.get(Requirement, fulfillment.requirement_id)
        if requirement is None:
            continue
        entries.append(AssetProvenanceEntryOut(
            requirement_id=requirement.id,
            requirement_number=requirement.request_number,
            requirement_branch_id=requirement.branch_id,
            requirement_status=requirement.status,
            fulfillment_id=fulfillment.id,
            fulfillment_number=fulfillment.fulfillment_number,
            fulfillment_status=fulfillment.status,
            expected_delivery_date=fulfillment.expected_delivery_date,
            actual_delivery_date=fulfillment.actual_delivery_date,
            delivered=link.delivered,
        ))
    return entries


def _user_label(db: Session, user_id: str | None) -> str | None:
    if not user_id:
        return None
    user = db.get(User, user_id)
    return user.name if user else None


def _vendor_label(db: Session, vendor_id: str | None) -> str | None:
    if not vendor_id:
        return None
    vendor = db.get(Vendor, vendor_id)
    return vendor.name if vendor else None


def list_maintenance_history(
    db: Session, asset_id: str, *, page: int = 1, page_size: int = 25,
) -> tuple[list[MaintenanceHistoryEntryOut], int]:
    """Merges PMRecord + CalibrationRecord + ServiceVisit for one asset, sorted by date
    descending. Merged and paginated in Python — same documented scaling caveat as
    Module 05A's cross-table contract list, fine at this project's demo data volume."""
    entries: list[MaintenanceHistoryEntryOut] = []

    for pm in db.query(PMRecord).filter(PMRecord.asset_id == asset_id).all():
        entries.append(MaintenanceHistoryEntryOut(
            id=pm.id, source_type="PM", asset_id=asset_id, maintenance_type="PM",
            service_date=pm.performed_date, engineer_id=pm.engineer_id, vendor_id=None,
            performed_by_label=_user_label(db, pm.engineer_id),
            issue=None, action_taken=pm.notes, parts_used=pm.parts_replaced,
            downtime_hours=None,
            result=pm.functional_check_result or pm.electrical_safety_result,
            next_maintenance_date=pm.next_due_date, remarks=pm.notes,
        ))

    for cal in db.query(CalibrationRecord).filter(CalibrationRecord.asset_id == asset_id).all():
        entries.append(MaintenanceHistoryEntryOut(
            id=cal.id, source_type="CALIBRATION", asset_id=asset_id, maintenance_type="CALIBRATION",
            service_date=cal.performed_date, engineer_id=None, vendor_id=None,
            performed_by_label=cal.calibration_agency,
            issue=None, action_taken=cal.result, parts_used=None, downtime_hours=None,
            result=cal.pass_fail, next_maintenance_date=cal.next_due_date, remarks=cal.notes,
        ))

    for visit in db.query(ServiceVisit).filter(ServiceVisit.asset_id == asset_id).all():
        entries.append(MaintenanceHistoryEntryOut(
            id=visit.id, source_type="SERVICE_VISIT", asset_id=asset_id, maintenance_type=visit.visit_type,
            service_date=visit.visit_date, engineer_id=visit.engineer_id, vendor_id=visit.vendor_id,
            performed_by_label=_user_label(db, visit.engineer_id) or _vendor_label(db, visit.vendor_id),
            issue=visit.issue, action_taken=visit.summary, parts_used=visit.parts_used,
            downtime_hours=visit.downtime_hours, result=None,
            next_maintenance_date=None, remarks=visit.next_action,
        ))

    entries.sort(key=lambda e: e.service_date or "", reverse=True)
    total = len(entries)
    start = (page - 1) * page_size
    return entries[start:start + page_size], total


def _recent_maintenance(db: Session, *, limit: int = 10) -> list[MaintenanceHistoryEntryOut]:
    all_assets = db.query(Asset.id).all()
    entries: list[MaintenanceHistoryEntryOut] = []
    for (asset_id,) in all_assets:
        page_entries, _ = list_maintenance_history(db, asset_id, page=1, page_size=limit)
        entries.extend(page_entries)
    entries.sort(key=lambda e: e.service_date or "", reverse=True)
    return entries[:limit]


def _branch_asset_ids(db: Session, branch_id: str) -> set[str]:
    return {row[0] for row in db.query(Asset.id).filter(Asset.branch_id == branch_id).all()}


def compute_dashboard_summary(db: Session, *, branch_id: str | None = None) -> AssetDashboardSummaryOut:
    """UNIT_HEAD/TL pass their own hospital_scope_id as `branch_id` (see
    routers/devices.py:get_asset_dashboard_summary) so every count below reflects only
    their branch, never the whole organization — every other caller passes None and
    gets the original unscoped view."""
    today = _today()
    upcoming_cutoff = (today + timedelta(days=UPCOMING_WINDOW_DAYS)).isoformat()
    branch_asset_ids = _branch_asset_ids(db, branch_id) if branch_id else None

    asset_query = db.query(Asset)
    if branch_id is not None:
        asset_query = asset_query.filter(Asset.branch_id == branch_id)
    assets = asset_query.all()
    counts = {
        "total_operational": 0, "under_maintenance": 0, "under_repair": 0,
        "condemned": 0, "retired": 0,
    }
    for asset in assets:
        status = (asset.status or "").upper()
        if status in _OPERATIONAL_VALUES:
            counts["total_operational"] += 1
        elif status in _UNDER_MAINTENANCE_VALUES:
            counts["under_maintenance"] += 1
        elif status in _UNDER_REPAIR_VALUES:
            counts["under_repair"] += 1
        elif status in _CONDEMNED_VALUES:
            counts["condemned"] += 1
        elif status in _RETIRED_VALUES:
            counts["retired"] += 1

    def _amc_cmc_contract_ids(junction_model) -> set[str] | None:
        if branch_asset_ids is None:
            return None
        fk = "amc_contract_id" if junction_model is AmcContractAsset else "cmc_contract_id"
        return {
            getattr(row, fk)
            for row in db.query(junction_model).filter(junction_model.asset_id.in_(branch_asset_ids)).all()
        }

    def _contract_expiry_counts(model, contract_ids: set[str] | None):
        active = expired = upcoming = 0
        query = db.query(model)
        if contract_ids is not None:
            if not contract_ids:
                return 0, 0, 0
            query = query.filter(model.id.in_(contract_ids))
        for contract in query.all():
            expiry = compute_expiry_status(contract.start_date, contract.end_date, today=today)
            if expiry in ("ACTIVE", "EXPIRING_SOON"):
                active += 1
            elif expiry == "EXPIRED":
                expired += 1
            if contract.end_date and today.isoformat() <= contract.end_date <= upcoming_cutoff:
                upcoming += 1
        return active, expired, upcoming

    amc_active, amc_expired, amc_upcoming = _contract_expiry_counts(AmcContract, _amc_cmc_contract_ids(AmcContractAsset))
    cmc_active, cmc_expired, cmc_upcoming = _contract_expiry_counts(CmcContract, _amc_cmc_contract_ids(CmcContractAsset))

    warranty_query = db.query(Warranty)
    if branch_asset_ids is not None:
        warranty_query = warranty_query.filter(Warranty.asset_id.in_(branch_asset_ids))
    warranty_active = warranty_expired = 0
    for warranty in warranty_query.all():
        expiry = compute_expiry_status(warranty.start_date, warranty.end_date, today=today)
        if expiry in ("ACTIVE", "EXPIRING_SOON"):
            warranty_active += 1
        elif expiry == "EXPIRED":
            warranty_expired += 1

    upcoming_maintenance = 0
    pm_query = db.query(PMSchedule).filter(PMSchedule.is_active.is_(True))
    if branch_asset_ids is not None:
        pm_query = pm_query.filter(PMSchedule.asset_id.in_(branch_asset_ids))
    for schedule in pm_query.all():
        if schedule.next_due_date and today.isoformat() <= schedule.next_due_date <= upcoming_cutoff:
            upcoming_maintenance += 1
    cal_query = db.query(CalibrationSchedule).filter(CalibrationSchedule.is_active.is_(True))
    if branch_asset_ids is not None:
        cal_query = cal_query.filter(CalibrationSchedule.asset_id.in_(branch_asset_ids))
    for schedule in cal_query.all():
        if schedule.next_due_date and today.isoformat() <= schedule.next_due_date <= upcoming_cutoff:
            upcoming_maintenance += 1

    return AssetDashboardSummaryOut(
        total_operational=counts["total_operational"],
        under_maintenance=counts["under_maintenance"],
        under_repair=counts["under_repair"],
        amc_active=amc_active, amc_expired=amc_expired,
        cmc_active=cmc_active, cmc_expired=cmc_expired,
        warranty_active=warranty_active, warranty_expired=warranty_expired,
        condemned=counts["condemned"], retired=counts["retired"],
        upcoming_amc_expiry=amc_upcoming, upcoming_cmc_expiry=cmc_upcoming,
        upcoming_maintenance=upcoming_maintenance,
        recent_maintenance=_recent_maintenance(db),
    )


def compute_calendar_events(
    db: Session, *, start_date: str | None = None, end_date: str | None = None, branch_id: str | None = None,
) -> list[CalendarEventOut]:
    """Dates needed for a future calendar — PM/calibration due dates, AMC/CMC/warranty
    expiry, and service visits. No UI is built here, per this task's explicit
    'foundation only' instruction. `branch_id` scopes every event to one branch's own
    assets (UNIT_HEAD/TL) — see compute_dashboard_summary's docstring above."""
    events: list[CalendarEventOut] = []
    branch_asset_ids = _branch_asset_ids(db, branch_id) if branch_id else None

    def _in_range(value: str | None) -> bool:
        if not value:
            return False
        if start_date and value < start_date:
            return False
        if end_date and value > end_date:
            return False
        return True

    pm_query = db.query(PMSchedule)
    if branch_asset_ids is not None:
        pm_query = pm_query.filter(PMSchedule.asset_id.in_(branch_asset_ids))
    for schedule in pm_query.all():
        if _in_range(schedule.next_due_date):
            events.append(CalendarEventOut(
                event_type="PM_DUE", date=schedule.next_due_date, asset_id=schedule.asset_id,
                reference_id=schedule.id, label="PM due",
            ))
    cal_query = db.query(CalibrationSchedule)
    if branch_asset_ids is not None:
        cal_query = cal_query.filter(CalibrationSchedule.asset_id.in_(branch_asset_ids))
    for schedule in cal_query.all():
        if _in_range(schedule.next_due_date):
            events.append(CalendarEventOut(
                event_type="CALIBRATION_DUE", date=schedule.next_due_date, asset_id=schedule.asset_id,
                reference_id=schedule.id, label="Calibration due",
            ))

    if branch_asset_ids is None:
        amc_contracts = db.query(AmcContract).all()
        cmc_contracts = db.query(CmcContract).all()
    else:
        amc_ids = {r.amc_contract_id for r in db.query(AmcContractAsset).filter(AmcContractAsset.asset_id.in_(branch_asset_ids)).all()}
        cmc_ids = {r.cmc_contract_id for r in db.query(CmcContractAsset).filter(CmcContractAsset.asset_id.in_(branch_asset_ids)).all()}
        amc_contracts = db.query(AmcContract).filter(AmcContract.id.in_(amc_ids)).all() if amc_ids else []
        cmc_contracts = db.query(CmcContract).filter(CmcContract.id.in_(cmc_ids)).all() if cmc_ids else []

    for contract in amc_contracts:
        if _in_range(contract.end_date):
            events.append(CalendarEventOut(
                event_type="AMC_EXPIRY", date=contract.end_date, asset_id=None,
                reference_id=contract.id, label=f"AMC expiry ({contract.contract_number or contract.id})",
            ))
    for contract in cmc_contracts:
        if _in_range(contract.end_date):
            events.append(CalendarEventOut(
                event_type="CMC_EXPIRY", date=contract.end_date, asset_id=None,
                reference_id=contract.id, label=f"CMC expiry ({contract.contract_number or contract.id})",
            ))

    warranty_query = db.query(Warranty)
    if branch_asset_ids is not None:
        warranty_query = warranty_query.filter(Warranty.asset_id.in_(branch_asset_ids))
    for warranty in warranty_query.all():
        if _in_range(warranty.end_date):
            events.append(CalendarEventOut(
                event_type="WARRANTY_EXPIRY", date=warranty.end_date, asset_id=warranty.asset_id,
                reference_id=warranty.id, label=f"Warranty expiry ({warranty.warranty_number or warranty.id})",
            ))

    visit_query = db.query(ServiceVisit)
    if branch_asset_ids is not None:
        visit_query = visit_query.filter(ServiceVisit.asset_id.in_(branch_asset_ids))
    for visit in visit_query.all():
        if _in_range(visit.visit_date):
            events.append(CalendarEventOut(
                event_type="SERVICE_VISIT", date=visit.visit_date, asset_id=visit.asset_id,
                reference_id=visit.id, label=f"Service visit ({visit.visit_type or 'OTHER'})",
            ))

    fulfillment_query = db.query(FulfillmentRequest).filter(FulfillmentRequest.status.in_(_IN_FLIGHT_FULFILLMENT_STATUSES))
    if branch_id is not None:
        branch_requirement_ids = {r[0] for r in db.query(Requirement.id).filter(Requirement.branch_id == branch_id).all()}
        fulfillment_query = fulfillment_query.filter(FulfillmentRequest.requirement_id.in_(branch_requirement_ids))
    for fulfillment in fulfillment_query.all():
        if _in_range(fulfillment.expected_delivery_date):
            events.append(CalendarEventOut(
                event_type="EXPECTED_DELIVERY", date=fulfillment.expected_delivery_date, asset_id=None,
                reference_id=fulfillment.id, label=f"Expected delivery ({fulfillment.fulfillment_number})",
            ))

    events.sort(key=lambda e: e.date)
    return events
