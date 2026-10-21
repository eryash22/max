from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import require_any_permission, require_permission
from ..exports.writer import EXPORT_FORMATS, build_export_response
from ..models.asset_history import AssetHistory
from ..models.contracts import AmcContract, AmcContractAsset, CmcContract, CmcContractAsset, Warranty
from ..models.device import Asset, BranchDeviceAssignment, DeviceCategory, DeviceModel
from ..models.documents import Document
from ..models.fulfillment import FulfillmentItemAsset
from ..models.hospital import Branch, Department, Location
from ..models.incident import Incident, ServiceVisit
from ..models.inventory import SparePartCompatibility
from ..models.maintenance import CalibrationRecord, CalibrationSchedule, PMRecord, PMSchedule
from ..models.procurement import Indent, Invoice, PurchaseOrder, Quotation
from ..models.requirement import Requirement, RequirementItem
from ..models.vendor import DeviceModelVendor
from ..services.force_delete import assert_dev_environment
from ..services.procurement_purge import collect_procurement_chain_ids, delete_procurement_chain
from ..repositories.crud import paginate
from ..schemas.asset_history import AssetHistoryIn, AssetHistoryOut
from ..schemas.asset_lifecycle import (
    AssetDashboardSummaryOut,
    AssetLifecycleSummaryOut,
    AssetProvenanceEntryOut,
    CalendarEventOut,
    CentralDeviceStockOut,
    LIFECYCLE_STATUSES,
    MaintenanceHistoryEntryOut,
)
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.device import (
    ALLOWED_ASSET_STATUSES, AssetAllocateIn, AssetIn, AssetOut, BranchDeviceAssignmentIn, BranchDeviceAssignmentOut,
    DeviceCategoryIn, DeviceCategoryOut, DeviceCreateIn, DeviceCreateOut, DeviceModelIn, DeviceModelOut,
    DeviceModelStockOut,
)
from ..schemas.procurement import AssetProcurementProvenanceEntryOut
from ..security.permissions import (
    ASSET_READ, ASSET_WRITE, DEV_DATA_CLEANUP, DEVICE_READ, DEVICE_READ_PICKER, DEVICE_WRITE, INVENTORY_READ,
    branch_scope_id,
)
from ..services import asset_event_service, asset_lifecycle_service, procurement_service, qr_service
from ..services.asset_history_service import record_asset_history, record_auto_history_for_update
from ..services.export_service import MAX_EXPORT_ROWS, record_export_audit

router = APIRouter(prefix="/api/v1", tags=["devices"])


def _get_or_404(db: Session, model, entity_id: str, label: str):
    obj = db.get(model, entity_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return obj


def _device_model_in_use(db: Session, device_model_id: str) -> int:
    """Final admin/inventory/vendor/dashboard polish pass — Part 2 (extended for the
    DEV Data Cleanup safety fix): how many rows across the schema currently reference
    this `DeviceModel`. Zero means the model can be hard-deleted; any other count
    means it must be archived (is_active=False) instead — see
    delete_device_model()/archive_device_model() below. Every real FK into
    `device_models.id` is covered (verified by inspecting every model file — see
    `ForeignKey("device_models.id")` occurrences): `Asset` (the original check —
    live-verification of the DEV Data Cleanup tool found a `DeviceModel` referenced
    only via a `RequirementItem`, with zero `Asset` rows, still passed this guard and
    then 500'd on the actual DELETE with a Postgres FK violation instead of the
    intended clean 409), plus `RequirementItem`, `SparePartCompatibility`,
    `DeviceModelVendor`, `BranchDeviceAssignment`, and `Document`. Same
    reference-check-then-hard-delete-or-reject pattern as
    routers/hospitals.py:_branch_reference_count / routers/vendors.py:
    _vendor_reference_count — sum multiple reference-count queries rather than a
    single-table check."""
    counts = [
        db.query(Asset).filter(Asset.device_model_id == device_model_id).count(),
        db.query(RequirementItem).filter(RequirementItem.device_model_id == device_model_id).count(),
        db.query(SparePartCompatibility).filter(SparePartCompatibility.device_model_id == device_model_id).count(),
        db.query(DeviceModelVendor).filter(DeviceModelVendor.device_model_id == device_model_id).count(),
        db.query(BranchDeviceAssignment).filter(BranchDeviceAssignment.device_model_id == device_model_id).count(),
        db.query(Document).filter(Document.device_model_id == device_model_id).count(),
    ]
    return sum(counts)


def _has_approved_condemnation(db: Session, asset_id: str) -> bool:
    """Business-workflow audit — condemnation gap: a direct PATCH must not be able to
    set status=CONDEMNED on its own; it may only do so if an approved CONDEMNATION
    requirement already exists for this asset (the normal path already applies
    CONDEMNED automatically on approval via requirement_service._apply_condemnation_items,
    so this only matters for a manual/defensive re-application of an already-approved
    decision, never as the primary path)."""
    return (
        db.query(RequirementItem)
        .join(Requirement, RequirementItem.requirement_id == Requirement.id)
        .filter(
            RequirementItem.item_type == "CONDEMNATION",
            RequirementItem.asset_id == asset_id,
            Requirement.status == "APPROVED",
        )
        .first()
        is not None
    )


def _asset_export_row(db: Session, asset: Asset) -> list:
    device_model = db.get(DeviceModel, asset.device_model_id)
    branch = db.get(Branch, asset.branch_id)
    department = db.get(Department, asset.department_id) if asset.department_id else None
    location = db.get(Location, asset.location_id) if asset.location_id else None

    amc_link = db.query(AmcContractAsset).filter(AmcContractAsset.asset_id == asset.id).first()
    amc = db.get(AmcContract, amc_link.amc_contract_id) if amc_link else None
    cmc_link = db.query(CmcContractAsset).filter(CmcContractAsset.asset_id == asset.id).first()
    cmc = db.get(CmcContract, cmc_link.cmc_contract_id) if cmc_link else None
    warranty = db.query(Warranty).filter(Warranty.asset_id == asset.id).first()

    pm_schedule = db.query(PMSchedule).filter(PMSchedule.asset_id == asset.id).first()
    last_pm = (db.query(PMRecord).filter(PMRecord.asset_id == asset.id)
               .order_by(PMRecord.performed_date.desc()).first())
    calibration_schedule = db.query(CalibrationSchedule).filter(CalibrationSchedule.asset_id == asset.id).first()
    last_calibration = (db.query(CalibrationRecord).filter(CalibrationRecord.asset_id == asset.id)
                         .order_by(CalibrationRecord.performed_date.desc()).first())

    return [
        asset.asset_number, asset.serial_number,
        device_model.name if device_model else None, device_model.manufacturer if device_model else None,
        device_model.model_number if device_model else None,
        branch.name if branch else None, department.name if department else None, location.name if location else None,
        asset.status, asset.installation_date,
        amc.status if amc else "Not Covered", cmc.status if cmc else "Not Covered",
        warranty.status if warranty else "Not Covered",
        last_pm.performed_date if last_pm else None, pm_schedule.next_due_date if pm_schedule else None,
        last_calibration.performed_date if last_calibration else None,
        calibration_schedule.next_due_date if calibration_schedule else None,
    ]


# ---- Device categories ----

_CATALOG_OR_PICKER = require_any_permission(DEVICE_READ, DEVICE_READ_PICKER)


def _strip_catalog_admin_fields(model: DeviceModelOut) -> DeviceModelOut:
    """Branch-scoping hardening pass — device.read_picker callers (UNIT_HEAD/TL) may
    list device models to pick one by category when raising a requirement, but must
    not receive catalog-admin-only data (internal notes, data source, catalog-match/
    model-information review status, known-quantity planning figure). Builds a
    detached Pydantic copy rather than mutating the ORM row in place — mutating the
    session-attached `DeviceModel` instance directly would mark it dirty and could
    flush the nulled-out values back to the database on the next commit (same
    "response object, not the DB row" discipline as inventory_service.
    strip_stock_fields/vendor_service.strip_contact_fields; those work on plain
    dicts, this one needs model_copy since DeviceModelOut has no dict step here).
    Takes an already-built DeviceModelOut (or subclass, e.g. DeviceModelStockOut —
    model_copy preserves whatever extra fields the subclass carries) rather than the
    ORM row, so callers that already enriched the row (e.g. with stock counts) don't
    lose that work."""
    return model.model_copy(update={
        "notes": None,
        "data_source": None,
        "model_information_status": "MODEL_NOT_PROVIDED",
        "catalog_match_status": "UNMATCHED",
        "known_quantity": None,
    })


def _compute_model_stock_counts(db: Session, model_ids: list[str]) -> dict[str, dict[str, int]]:
    """Device Catalog / Allocate-to-Branch unification pass — one grouped query for
    however many models are on the current page, rather than a per-row round trip.
    `total_units` counts every Asset row regardless of status/branch; `available_units`
    is the subset sitting at a central-warehouse branch and not CONDEMNED/RETIRED —
    the same eligibility definition as asset_lifecycle_service.is_asset_available_for_allocation
    and compute_central_device_stock, so this never drifts from those; `allocated_units`
    is whatever's left (sitting at a non-central branch, any status) — "allocated
    across branches" per the product brief, independent of current lifecycle state."""
    if not model_ids:
        return {}
    from sqlalchemy import case

    rows = (
        db.query(
            Asset.device_model_id,
            func.count(Asset.id),
            func.sum(case(
                (
                    (Branch.is_central_warehouse.is_(True))
                    & (~func.upper(Asset.status).in_(["CONDEMNED", "RETIRED"])),
                    1,
                ),
                else_=0,
            )),
            func.sum(case((Branch.is_central_warehouse.isnot(True), 1), else_=0)),
        )
        .join(Branch, Branch.id == Asset.branch_id)
        .filter(Asset.device_model_id.in_(model_ids))
        .group_by(Asset.device_model_id)
        .all()
    )
    return {
        model_id: {"total_units": total, "available_units": int(available or 0), "allocated_units": int(allocated or 0)}
        for model_id, total, available, allocated in rows
    }


@router.get("/device-categories", response_model=Page[DeviceCategoryOut])
def list_categories(page: int = 1, page_size: int = 25, search: str | None = None,
                     db: Session = Depends(get_db), _: CurrentUser = Depends(_CATALOG_OR_PICKER)):
    items, total = paginate(db, DeviceCategory, page=page, page_size=page_size, search=search, search_fields=["name", "code"])
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/device-categories", response_model=DeviceCategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(payload: DeviceCategoryIn, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(DEVICE_WRITE))):
    obj = DeviceCategory(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="device_category", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


# ---- Device models ----

@router.get("/device-models", response_model=Page[DeviceModelStockOut])
def list_device_models(page: int = 1, page_size: int = 25, search: str | None = None,
                        manufacturer: str | None = None, catalog_match_status: str | None = None,
                        is_active: bool | None = None, category_id: str | None = None,
                        db: Session = Depends(get_db), current_user: CurrentUser = Depends(_CATALOG_OR_PICKER)):
    # Final admin/inventory/vendor/dashboard polish pass — Part 2: archived
    # (is_active=False) models are excluded by default from this list — matching the
    # is_active filter convention already used in escalation.py/pm.py — so device-model
    # pickers used when creating new Assets/Requirements never offer an archived model.
    # Pass is_active=false explicitly (e.g. an "Archived" tab) to see them.
    effective_is_active = True if is_active is None else is_active
    items, total = paginate(db, DeviceModel, page=page, page_size=page_size, search=search,
                             search_fields=["name", "model_number", "manufacturer"],
                             manufacturer=manufacturer, catalog_match_status=catalog_match_status,
                             is_active=effective_is_active, category_id=category_id)
    counts_by_model = _compute_model_stock_counts(db, [item.id for item in items])
    zero_counts = {"total_units": 0, "available_units": 0, "allocated_units": 0}
    stock_items = [
        DeviceModelStockOut(**DeviceModelOut.model_validate(item).model_dump(),
                             **counts_by_model.get(item.id, zero_counts))
        for item in items
    ]
    if DEVICE_READ not in current_user.permissions:
        # device.read_picker-only caller (UNIT_HEAD/TL) — see _strip_catalog_admin_fields.
        stock_items = [_strip_catalog_admin_fields(item) for item in stock_items]
    return Page(items=stock_items, total=total, page=page, page_size=page_size)


@router.get("/device-models/{device_model_id}", response_model=DeviceModelOut)
def get_device_model(device_model_id: str, db: Session = Depends(get_db),
                      _: CurrentUser = Depends(require_permission(DEVICE_READ))):
    return _get_or_404(db, DeviceModel, device_model_id, "Device model")


@router.post("/device-models", response_model=DeviceModelOut, status_code=status.HTTP_201_CREATED)
def create_device_model(payload: DeviceModelIn, db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(require_permission(DEVICE_WRITE))):
    obj = DeviceModel(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="device_model", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/device-models/{device_model_id}", response_model=DeviceModelOut)
def update_device_model(device_model_id: str, payload: DeviceModelIn, db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(require_permission(DEVICE_WRITE))):
    obj = _get_or_404(db, DeviceModel, device_model_id, "Device model")
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    for key, value in payload.model_dump().items():
        setattr(obj, key, value)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="device_model", entity_id=obj.id, old_value=before, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


def _force_delete_asset(db: Session, asset: Asset, *, user_id: str) -> None:
    """Purges one Asset and every row that hangs off it, as part of a force-delete
    cascade (never reachable on its own — there is still no standalone
    DELETE /assets/{id}; archive_asset's "never hard-deleted" rule is about the
    *normal* product surface, not about a Super-Admin cascade purge that the caller
    already explicitly opted into). Every real FK into assets.id (verified by
    inspecting every model file) is covered: maintenance/compliance history
    (PMSchedule/PMRecord, CalibrationSchedule/CalibrationRecord, AssetHistory,
    ServiceVisit, Warranty, AmcContractAsset/CmcContractAsset links) and the
    procurement chain scoped to this asset (Quotation+lines, Indent,
    PurchaseOrder+lines, Invoice) are deleted outright; FulfillmentItemAsset (a pure
    join row) is deleted outright too. Rows belonging to another domain that only
    optionally reference this asset (Incident, RequirementItem, Document) are
    detached (asset_id set to NULL) instead of destroyed."""
    asset_id = asset.id

    db.query(PMRecord).filter(PMRecord.asset_id == asset_id).delete(synchronize_session=False)
    db.query(PMSchedule).filter(PMSchedule.asset_id == asset_id).delete(synchronize_session=False)
    db.query(CalibrationRecord).filter(CalibrationRecord.asset_id == asset_id).delete(synchronize_session=False)
    db.query(CalibrationSchedule).filter(CalibrationSchedule.asset_id == asset_id).delete(synchronize_session=False)
    db.query(AssetHistory).filter(AssetHistory.asset_id == asset_id).delete(synchronize_session=False)
    db.query(ServiceVisit).filter(ServiceVisit.asset_id == asset_id).delete(synchronize_session=False)
    db.query(Warranty).filter(Warranty.asset_id == asset_id).delete(synchronize_session=False)
    db.query(AmcContractAsset).filter(AmcContractAsset.asset_id == asset_id).delete(synchronize_session=False)
    db.query(CmcContractAsset).filter(CmcContractAsset.asset_id == asset_id).delete(synchronize_session=False)
    db.query(FulfillmentItemAsset).filter(FulfillmentItemAsset.asset_id == asset_id).delete(synchronize_session=False)

    quotation_ids, indent_ids, po_ids, invoice_ids = collect_procurement_chain_ids(
        db,
        quotation_seed=Quotation.asset_id == asset_id,
        indent_seed=Indent.asset_id == asset_id,
        po_seed=PurchaseOrder.asset_id == asset_id,
        invoice_seed=Invoice.asset_id == asset_id,
    )
    delete_procurement_chain(db, quotation_ids, indent_ids, po_ids, invoice_ids)

    db.query(Incident).filter(Incident.asset_id == asset_id).update({"asset_id": None}, synchronize_session=False)
    db.query(RequirementItem).filter(RequirementItem.asset_id == asset_id).update(
        {"asset_id": None}, synchronize_session=False
    )
    db.query(Document).filter(Document.asset_id == asset_id).update({"asset_id": None}, synchronize_session=False)

    before = {c.name: getattr(asset, c.name) for c in asset.__table__.columns}
    audit_record(db, user_id=user_id, action="FORCE_DELETE", entity="asset", entity_id=asset.id, old_value=before)
    db.delete(asset)


def _force_delete_device_model(db: Session, obj: DeviceModel, *, user_id: str) -> None:
    """Super-Admin-only cascade purge (gated on DEV_DATA_CLEANUP). Junction/model-owned
    rows (SparePartCompatibility, DeviceModelVendor, BranchDeviceAssignment) are
    deleted outright; rows that belong to another domain and only optionally
    reference this model (RequirementItem, Document) are detached instead of
    destroyed, so the underlying Requirement/Document survives. Every Asset still on
    this model is purged too (see _force_delete_asset) — force-delete is an explicit,
    Super-Admin-only opt-in, so it's allowed to go further than the normal product
    surface (which never hard-deletes an Asset on its own)."""
    device_model_id = obj.id

    db.query(SparePartCompatibility).filter(SparePartCompatibility.device_model_id == device_model_id).delete(
        synchronize_session=False
    )
    db.query(DeviceModelVendor).filter(DeviceModelVendor.device_model_id == device_model_id).delete(
        synchronize_session=False
    )
    db.query(BranchDeviceAssignment).filter(BranchDeviceAssignment.device_model_id == device_model_id).delete(
        synchronize_session=False
    )
    db.query(RequirementItem).filter(RequirementItem.device_model_id == device_model_id).update(
        {"device_model_id": None}, synchronize_session=False
    )
    db.query(Document).filter(Document.device_model_id == device_model_id).update(
        {"device_model_id": None}, synchronize_session=False
    )

    for asset in db.query(Asset).filter(Asset.device_model_id == device_model_id).all():
        _force_delete_asset(db, asset, user_id=user_id)

    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    audit_record(db, user_id=user_id, action="FORCE_DELETE", entity="device_model", entity_id=obj.id, old_value=before)
    db.delete(obj)
    db.commit()


@router.delete("/device-models/{device_model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device_model(device_model_id: str, force: bool = False, db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(require_permission(DEVICE_WRITE))):
    """Final admin/inventory/vendor/dashboard polish pass — Part 2: hard delete only
    when zero rows reference this model across every real FK into `device_models.id`
    (Asset, RequirementItem, SparePartCompatibility, DeviceModelVendor,
    BranchDeviceAssignment, Document — see _device_model_in_use); otherwise 409 with
    the reference count, directing the caller to archive instead (see
    archive_device_model below).

    ``force=true`` (Super Admin only, gated on DEV_DATA_CLEANUP) cascade-deletes every
    referencing row, including any Asset still on this model and everything that
    hangs off each asset — see _force_delete_device_model / _force_delete_asset."""
    obj = _get_or_404(db, DeviceModel, device_model_id, "Device model")
    if force:
        if DEV_DATA_CLEANUP not in current_user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Force-delete requires Super Admin (dev.data_cleanup permission)",
            )
        assert_dev_environment()
        _force_delete_device_model(db, obj, user_id=current_user.id)
        return
    in_use = _device_model_in_use(db, device_model_id)
    if in_use > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Device model is referenced by {in_use} record(s) and cannot be deleted — archive it instead",
        )
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    audit_record(db, user_id=current_user.id, action="DELETE", entity="device_model", entity_id=obj.id, old_value=before)
    db.delete(obj)
    db.commit()


def _set_device_model_active(db: Session, obj: DeviceModel, is_active: bool, *, user_id: str) -> DeviceModel:
    before = {"is_active": obj.is_active}
    obj.is_active = is_active
    audit_record(db, user_id=user_id, action="ARCHIVE" if not is_active else "ACTIVATE", entity="device_model",
                 entity_id=obj.id, old_value=before, new_value={"is_active": is_active})
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/device-models/{device_model_id}/archive", response_model=DeviceModelOut)
def archive_device_model(device_model_id: str, db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(require_permission(DEVICE_WRITE))):
    obj = _get_or_404(db, DeviceModel, device_model_id, "Device model")
    return _set_device_model_active(db, obj, False, user_id=current_user.id)


@router.post("/device-models/{device_model_id}/activate", response_model=DeviceModelOut)
def activate_device_model(device_model_id: str, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(DEVICE_WRITE))):
    obj = _get_or_404(db, DeviceModel, device_model_id, "Device model")
    return _set_device_model_active(db, obj, True, user_id=current_user.id)


# ---- Combined device master creation (Part D, restructure consolidation pass) ----
# Composes the two existing DeviceModel/Asset endpoints into one transaction instead
# of a new duplicate Device/Asset table. Gated on BOTH device.write and asset.write
# since it can create rows in both tables (Biomedical Admin/Engineer already hold
# both, confirmed in security/permissions.py).

@router.post("/devices", response_model=DeviceCreateOut, status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceCreateIn, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(DEVICE_WRITE, ASSET_WRITE))):
    _get_or_404(db, Branch, payload.branch_id, "Branch")
    if payload.department_id is not None:
        _get_or_404(db, Department, payload.department_id, "Department")
    if payload.location_id is not None:
        _get_or_404(db, Location, payload.location_id, "Location")

    if payload.device_model_id:
        device_model = _get_or_404(db, DeviceModel, payload.device_model_id, "Device model")
    else:
        if not payload.model_name or not (payload.category or payload.category_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="model_name and category (or category_id) are required to create a new device model",
            )
        if payload.category_id is not None:
            _get_or_404(db, DeviceCategory, payload.category_id, "Device category")
        device_model = DeviceModel(
            category_id=payload.category_id,
            name=payload.model_name,
            manufacturer=payload.manufacturer,
            model_number=payload.model_number,
            category=payload.category or "",
            criticality_default=payload.criticality_default,
        )
        db.add(device_model)
        db.flush()
        audit_record(db, user_id=current_user.id, action="CREATE", entity="device_model", entity_id=device_model.id,
                      new_value={"name": device_model.name, "manufacturer": device_model.manufacturer,
                                 "model_number": device_model.model_number, "category": device_model.category,
                                 "category_id": device_model.category_id})

    # Final Operational Gap Pass, Part 1 — asset_number is genuinely optional, never
    # auto-generated. NULL means "not assigned yet", not a fake placeholder value.
    if payload.asset_number and _duplicate_asset_number(db, payload.asset_number):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset number already exists")
    _reject_unknown_asset_status(payload.status)

    asset = Asset(
        asset_number=payload.asset_number, serial_number=payload.serial_number, device_model_id=device_model.id,
        branch_id=payload.branch_id, department_id=payload.department_id, location_id=payload.location_id,
        status=payload.status, criticality=payload.criticality, purchase_date=payload.purchase_date,
        installation_date=payload.installation_date, operational_since=payload.operational_since,
    )
    db.add(asset)
    # Device QR Code Management System — every physical Asset gets a stable QR
    # identity automatically, regardless of which creation workflow made it.
    qr_service.ensure_qr_token(db, asset)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="asset", entity_id=asset.id,
                  new_value={"asset_number": asset.asset_number, "device_model_id": asset.device_model_id,
                             "branch_id": asset.branch_id, "status": asset.status})
    db.commit()
    db.refresh(device_model)
    db.refresh(asset)
    # Phase 2 — this endpoint composes the same Asset creation create_asset performs,
    # so it raises the same single ASSET_CREATED event (dedup is per asset, so the two
    # paths can never both notify about one device).
    asset_event_service.dispatch_created(db, asset=asset, user_id=current_user.id)
    return DeviceCreateOut(device_model=device_model, asset=asset)


# ---- Assets ----

@router.get("/assets", response_model=Page[AssetOut])
def list_assets(page: int = 1, page_size: int = 25, search: str | None = None,
                 branch_id: str | None = None, device_model_id: str | None = None, status_filter: str | None = None,
                 category: str | None = None, available_for_allocation: bool = False, central_only: bool = False,
                 db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(ASSET_READ))):
    # Real branch user hierarchy (final hardening pass): UNIT_HEAD/TL only ever see
    # their own assigned branch's assets, regardless of the branch_id filter passed
    # in — every other ASSET_READ-holding role is unaffected (unscoped, as before).
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        branch_id = scoped_branch_id

    # Inventory/Asset unification pass — "Allocate Device to Branch" picker: only
    # genuinely eligible assets (never CONDEMNED/RETIRED) should be selectable as
    # source stock, reusing the one existing eligibility definition
    # (asset_lifecycle_service.is_asset_available_for_allocation) rather than a
    # second ad-hoc status list. Pagination-safe (filters at the query level, same
    # pattern as the `category` branch below) rather than a page of already-sliced
    # rows.
    if available_for_allocation:
        query = db.query(Asset).filter(~func.upper(Asset.status).in_(["CONDEMNED", "RETIRED"]))
        if central_only:
            # "Allocate Device to Branch" Section A — eligible stock must actually be
            # sitting at a central-warehouse branch right now, not merely eligible
            # status at some other branch already; same is_central_warehouse flag
            # asset_lifecycle_service.compute_central_device_stock uses, so this never
            # drifts from the dashboard's own "central warehouse stock" figure.
            query = query.join(Branch, Branch.id == Asset.branch_id).filter(Branch.is_central_warehouse.is_(True))
        if branch_id:
            query = query.filter(Asset.branch_id == branch_id)
        if device_model_id:
            query = query.filter(Asset.device_model_id == device_model_id)
        if search:
            like = f"%{search}%"
            query = query.filter(
                Asset.asset_number.ilike(like) | Asset.serial_number.ilike(like)
                | Asset.device_model.has(DeviceModel.name.ilike(like))
            )
        total = query.count()
        items = query.order_by(Asset.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return Page(items=items, total=total, page=page, page_size=page_size)

    filters = {"branch_id": branch_id, "device_model_id": device_model_id}
    if status_filter:
        filters["status"] = status_filter

    if category:
        # Batch D, Part 4 — server-side category filter for the unified inventory
        # DEVICES tab, replacing a client-side sample. `category` lives on DeviceModel,
        # not Asset, so `paginate()`'s plain equality-filter helper can't express it
        # directly; narrow to the matching device_model_id set first (small, safe,
        # consistent with the join-free style every other filter here already uses)
        # rather than adding a generic join capability to paginate() for one caller.
        matched_model_ids = [m.id for m in db.query(DeviceModel.id).filter(DeviceModel.category == category).all()]
        if device_model_id:
            matched_model_ids = [m for m in matched_model_ids if m == device_model_id]
        if not matched_model_ids:
            return Page(items=[], total=0, page=page, page_size=page_size)
        query = db.query(Asset).filter(Asset.device_model_id.in_(matched_model_ids))
        if branch_id:
            query = query.filter(Asset.branch_id == branch_id)
        if status_filter:
            query = query.filter(Asset.status == status_filter)
        if search:
            like = f"%{search}%"
            query = query.filter(Asset.asset_number.ilike(like) | Asset.serial_number.ilike(like))
        total = query.count()
        items = query.order_by(Asset.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return Page(items=items, total=total, page=page, page_size=page_size)

    items, total = paginate(db, Asset, page=page, page_size=page_size, search=search,
                             search_fields=["asset_number", "serial_number"], **filters)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/assets/export")
def export_assets(
    format: str = "xlsx", search: str | None = None, branch_id: str | None = None,
    device_model_id: str | None = None, status_filter: str | None = None,
    db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(ASSET_READ)),
):
    """Same filters as `GET /assets`, exported instead of paginated, gated on the
    identical `asset.read` permission. Columns beyond the raw `Asset` row (device
    name/manufacturer/model, branch/department/location names, AMC/CMC/warranty
    coverage, last/next PM and calibration dates) are joined in Python per row — fine
    at this project's demo data volume, same documented scaling caveat as every other
    Python-side join in this codebase (e.g. Module 05A's contracts list, Module 11's
    failure-summary list). "Operational Since" was requested in this task's example
    column list but has no matching `Asset` column (only `installation_date` exists,
    see Module 14's schema notes) — omitted rather than invented or aliased under a
    misleading label."""
    if format not in EXPORT_FORMATS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"format must be one of {', '.join(EXPORT_FORMATS)}")
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        branch_id = scoped_branch_id

    filters = {"branch_id": branch_id, "device_model_id": device_model_id}
    if status_filter:
        filters["status"] = status_filter
    items, total = paginate(db, Asset, page=1, page_size=MAX_EXPORT_ROWS, search=search,
                             search_fields=["asset_number", "serial_number"], **filters)

    headers = [
        "Asset Number", "Serial Number", "Device Name", "Manufacturer", "Model",
        "Branch", "Department", "Location", "Status", "Installation Date",
        "AMC Status", "CMC Status", "Warranty Status",
        "Last PM", "Next PM", "Last Calibration", "Next Calibration",
    ]
    rows = [_asset_export_row(db, asset) for asset in items]

    record_export_audit(
        db, user_id=current_user.id, module="assets",
        filters={"search": search, "branch_id": branch_id, "device_model_id": device_model_id, "status_filter": status_filter},
        format=format, record_count=len(rows), truncated=total > MAX_EXPORT_ROWS,
    )
    return build_export_response(headers=headers, rows=rows, format=format, filename_stem="assets_export")


# ---- Asset lifecycle — dashboard/calendar data foundation ----
# Module 18A. Registered before `/assets/{asset_id}` (same discipline as `/assets/export`
# above it) so these static paths aren't swallowed by the asset_id path parameter.
# Foundation-only per this task's explicit instruction: raw counts/dated events, no
# dashboard or calendar UI built on top of them yet.

@router.get("/assets/lifecycle-statuses", response_model=list[str])
def list_lifecycle_statuses(_: CurrentUser = Depends(require_permission(ASSET_READ))):
    return LIFECYCLE_STATUSES


@router.get("/assets/dashboard-summary", response_model=AssetDashboardSummaryOut)
def get_asset_dashboard_summary(db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(ASSET_READ))):
    # Branch-scoping hardening pass — UNIT_HEAD/TL must see only their own branch's
    # asset/lifecycle counts on the Dashboard, same branch_scope_id() mechanism every
    # other domain (Requirements, Incidents, Assets list) already uses.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    return asset_lifecycle_service.compute_dashboard_summary(db, branch_id=scoped_branch_id)


@router.get("/assets/central-device-stock", response_model=list[CentralDeviceStockOut])
def get_central_device_stock(db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(INVENTORY_READ))):
    # Part B — "central warehouse device stock". Gated on INVENTORY_READ (the same
    # full-stock-visibility permission consumables' central_only view uses), not
    # ASSET_READ — a requester role (UNIT_HEAD/TL) holds ASSET_READ but only
    # INVENTORY_PICKER_READ, so this quantity view is correctly withheld from them;
    # they never need to pick a stock source anyway (server resolves it).
    return asset_lifecycle_service.compute_central_device_stock(db)


@router.get("/assets/calendar-events", response_model=list[CalendarEventOut])
def get_asset_calendar_events(
    start_date: str | None = None, end_date: str | None = None,
    db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(ASSET_READ)),
):
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    return asset_lifecycle_service.compute_calendar_events(
        db, start_date=start_date, end_date=end_date, branch_id=scoped_branch_id
    )


def _enforce_asset_branch_scope(asset: Asset, current_user: CurrentUser) -> None:
    """Same branch-scope check as GET /assets/{id} below — UNIT_HEAD/TL must not be
    able to reach another branch's asset data via a sibling by-ID endpoint
    (lifecycle/provenance/procurement-provenance) just because the base asset lookup
    is scoped there but these weren't. Reuses branch_scope_id(), the one existing
    branch-authorization mechanism — no second authorization system."""
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and asset.branch_id != scoped_branch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: str, db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(ASSET_READ))):
    asset = _get_or_404(db, Asset, asset_id, "Asset")
    _enforce_asset_branch_scope(asset, current_user)
    return asset


def _duplicate_asset_number(db: Session, asset_number: str, *, exclude_asset_id: str | None = None) -> bool:
    """Inventory/Asset unification pass — org-wide Asset Number uniqueness (never
    just per-branch, per the domain rule that two physical machines must never
    share one identity). Case-insensitive on top of the existing DB-level
    `unique=True` column constraint (models/device.py), which is exact-string
    only: without this, "MAX-DEV-001" and "max-dev-001" would pass the raw column
    constraint as two different values but actually name the same physical asset
    tag to a human reading a printed label. The live dataset was inspected before
    adding this check and has zero existing case/whitespace-variant collisions, so
    no backfill/migration is needed — this only changes what a *new* write
    accepts."""
    query = db.query(Asset).filter(func.lower(Asset.asset_number) == asset_number.lower())
    if exclude_asset_id is not None:
        query = query.filter(Asset.id != exclude_asset_id)
    return query.first() is not None


def _reject_unknown_asset_status(status_value: str) -> None:
    """Final hardening pass — canonical asset status vocabulary. Asset.status stays a
    free-text DB column (existing legacy data is never rewritten, see models/device.py's
    docstring), but a *new* write must use a real status: the canonical lifecycle
    vocabulary or one of the three legacy values already used in real data before this
    pass. Callers only invoke this for values genuinely being introduced/changed —
    resubmitting an asset's own already-stored (possibly older, untracked) legacy value
    unchanged is never blocked."""
    if status_value not in ALLOWED_ASSET_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status must be one of {sorted(ALLOWED_ASSET_STATUSES)}",
        )


@router.post("/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
def create_asset(payload: AssetIn, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_permission(ASSET_WRITE))):
    _get_or_404(db, DeviceModel, payload.device_model_id, "Device model")
    _get_or_404(db, Branch, payload.branch_id, "Branch")
    _reject_unknown_asset_status(payload.status)
    if payload.asset_number and _duplicate_asset_number(db, payload.asset_number):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset number already exists")

    obj = Asset(**payload.model_dump())
    db.add(obj)
    # Device QR Code Management System — automatic provisioning on creation.
    qr_service.ensure_qr_token(db, obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="asset", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    # Phase 2 — after the asset really exists. Never before: a communication problem
    # must not be able to prevent a device being registered.
    asset_event_service.dispatch_created(db, asset=obj, user_id=current_user.id)
    return obj


@router.patch("/assets/{asset_id}", response_model=AssetOut)
def update_asset(asset_id: str, payload: AssetIn, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_permission(ASSET_WRITE))):
    obj = _get_or_404(db, Asset, asset_id, "Asset")
    # Phase 2 security: the same branch-scope check the by-ID read endpoints already
    # apply. A branch-scoped caller must not be able to mutate — and so raise a device
    # event naming — an asset outside their branch by supplying its id. UNIT_HEAD/TL
    # do not hold asset.write today, so this changes nobody's access; it closes the
    # path rather than relying on the permission bundle staying as it is.
    _enforce_asset_branch_scope(obj, current_user)
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    # Business-workflow audit — condemnation gap: closing the bypass where any
    # ASSET_WRITE holder could set status=CONDEMNED directly with no approval step.
    # Only blocks the specific CONDEMNED transition; every other status value and
    # every other field on this same full-replace PATCH is unaffected.
    if payload.status == "CONDEMNED" and obj.status != "CONDEMNED" and not _has_approved_condemnation(db, asset_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Asset can only be set to CONDEMNED through an approved condemnation requirement",
        )
    if payload.status != obj.status:
        _reject_unknown_asset_status(payload.status)
    # Final Operational Gap Pass, Part 1 — asset_number can be attached (or changed)
    # later via this same full-replace PATCH; check uniqueness explicitly so a
    # conflict surfaces as a 409, not a raw IntegrityError/500.
    if (
        payload.asset_number
        and payload.asset_number != obj.asset_number
        and _duplicate_asset_number(db, payload.asset_number, exclude_asset_id=asset_id)
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset number already exists")
    for key, value in payload.model_dump().items():
        setattr(obj, key, value)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="asset", entity_id=obj.id, old_value=before, new_value=payload.model_dump())
    history_entries = record_auto_history_for_update(db, before=before, after=obj, changed_by=current_user.id)
    db.commit()
    db.refresh(obj)
    # Phase 2 — one event per meaningful lifecycle change actually recorded in
    # AssetHistory. A PATCH that only touches serial number/dates/criticality records
    # no history row and therefore notifies nobody. Dispatched post-commit so a
    # communication problem can never roll the edit back.
    asset_event_service.dispatch_for_history(
        db, asset=obj, entries=history_entries, previous=before, user_id=current_user.id,
    )
    return obj


@router.post("/assets/{asset_id}/allocate", response_model=AssetOut)
def allocate_asset_to_branch(asset_id: str, payload: AssetAllocateIn, db: Session = Depends(get_db),
                              current_user: CurrentUser = Depends(require_permission(ASSET_WRITE))):
    """Inventory/Asset unification pass — 'Allocate Device to Branch': a narrow,
    purpose-built action for moving ONE already-registered physical Asset (e.g.
    sitting in central warehouse stock) to its destination branch/department/
    location. This UPDATEs the existing Asset row in place — same id, same
    asset_number, same serial_number, same QR token/status, same AssetHistory —
    it never creates a second Asset for the destination branch. Reuses exactly the
    same branch-scope check, history recording (asset_history_service maps a
    branch_id change to a BRANCH_TRANSFER row) and notification dispatch as the
    general-purpose `update_asset` PATCH above; the only difference is the
    request body only ever touches location fields, so this screen can't
    accidentally clobber anything else about the asset."""
    obj = _get_or_404(db, Asset, asset_id, "Asset")
    _enforce_asset_branch_scope(obj, current_user)
    _get_or_404(db, Branch, payload.branch_id, "Branch")
    if payload.department_id is not None:
        _get_or_404(db, Department, payload.department_id, "Department")
    if payload.location_id is not None:
        _get_or_404(db, Location, payload.location_id, "Location")

    # Only genuinely eligible physical assets may be allocated — condemned/retired
    # devices are never available stock, matching the one existing eligibility
    # definition fulfillment/central-stock already use (asset_lifecycle_service).
    if not asset_lifecycle_service.is_asset_available_for_allocation(obj):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Asset with status '{obj.status}' is not eligible for allocation",
        )

    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    obj.branch_id = payload.branch_id
    obj.department_id = payload.department_id
    obj.location_id = payload.location_id
    audit_record(db, user_id=current_user.id, action="ALLOCATE", entity="asset", entity_id=obj.id,
                 old_value={"branch_id": before["branch_id"], "department_id": before["department_id"],
                            "location_id": before["location_id"]},
                 new_value={"branch_id": obj.branch_id, "department_id": obj.department_id,
                            "location_id": obj.location_id})
    history_entries = record_auto_history_for_update(db, before=before, after=obj, changed_by=current_user.id)
    db.commit()
    db.refresh(obj)
    asset_event_service.dispatch_for_history(
        db, asset=obj, entries=history_entries, previous=before, user_id=current_user.id,
    )
    return obj


def _set_asset_status(db: Session, asset: Asset, new_status: str, *, user_id: str, action: str) -> Asset:
    """Archive/unarchive status flip.

    Now also writes the AssetHistory STATUS_CHANGE row this transition always should
    have had — archiving genuinely changes an asset's lifecycle status, and until now
    only the generic audit_logs entry recorded it, leaving a real gap in the
    authoritative asset history. This is the same append-only lifecycle log
    record_auto_history_for_update writes for the equivalent PATCH, not a second
    version of the audit record.
    """
    before = {c.name: getattr(asset, c.name) for c in asset.__table__.columns}
    from_status = asset.status
    asset.status = new_status
    audit_record(db, user_id=user_id, action=action, entity="asset", entity_id=asset.id,
                 old_value={"status": from_status}, new_value={"status": new_status})
    entry = record_asset_history(
        db, asset_id=asset.id, event_type="STATUS_CHANGE", changed_by=user_id,
        from_value=from_status, to_value=new_status, branch_id=asset.branch_id,
        notes=f"Asset {action.lower()}d",
    )
    db.commit()
    db.refresh(asset)
    asset_event_service.dispatch_for_history(
        db, asset=asset, entries=[entry], previous=before, user_id=user_id,
    )
    return asset


@router.post("/assets/{asset_id}/archive", response_model=AssetOut)
def archive_asset(asset_id: str, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(ASSET_WRITE))):
    """Final admin/inventory/vendor/dashboard polish pass — Batch C, Part 3: Assets are
    never hard-deleted (no DELETE /assets/{id} exists, and none is added here —
    historical assets, including their PM/calibration/incident/contract history, must
    stay retrievable). Archiving is a pure status flip to the canonical RETIRED
    lifecycle status (see schemas/asset_lifecycle.py's LIFECYCLE_STATUSES) and is
    always allowed, regardless of any Incidents/Requirements/Fulfillments/PM or
    Calibration schedules still pointing at this asset — those are its legitimate
    history, not a reason to block archiving the way an in-use DeviceModel/Branch
    blocks a hard delete."""
    asset = _get_or_404(db, Asset, asset_id, "Asset")
    _enforce_asset_branch_scope(asset, current_user)
    return _set_asset_status(db, asset, "RETIRED", user_id=current_user.id, action="ARCHIVE")


@router.post("/assets/{asset_id}/unarchive", response_model=AssetOut)
def unarchive_asset(asset_id: str, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(ASSET_WRITE))):
    """Reverses archive_asset back to the OPERATIONAL lifecycle status. Only meaningful
    when the asset is currently RETIRED via archive_asset — restoring a CONDEMNED
    asset is intentionally not offered here (that status can only be reached through
    an approved condemnation requirement, see _has_approved_condemnation, and
    reversing it is a bigger workflow decision than a simple un-archive toggle)."""
    asset = _get_or_404(db, Asset, asset_id, "Asset")
    _enforce_asset_branch_scope(asset, current_user)
    if asset.status != "RETIRED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a RETIRED (archived) asset can be unarchived",
        )
    return _set_asset_status(db, asset, "OPERATIONAL", user_id=current_user.id, action="ACTIVATE")


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: str, force: bool = False, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_permission(ASSET_WRITE))):
    """There is deliberately no non-force path here — Assets are never hard-deleted on
    the normal product surface (see archive_asset's docstring: PM/calibration/incident/
    contract history must stay retrievable), so archiving (RETIRED) stays the only
    option for a plain ASSET_WRITE caller. ``force=true`` (Super Admin only, gated on
    DEV_DATA_CLEANUP) is the one escape hatch — reuses _force_delete_asset, the same
    cascade delete_device_model's force path uses per-asset."""
    asset = _get_or_404(db, Asset, asset_id, "Asset")
    if not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assets are never hard-deleted directly — archive it instead, or use force-delete (Super Admin).",
        )
    if DEV_DATA_CLEANUP not in current_user.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Force-delete requires Super Admin (dev.data_cleanup permission)",
        )
    assert_dev_environment()
    _force_delete_asset(db, asset, user_id=current_user.id)
    db.commit()


# ---- Asset lifecycle history ----

@router.get("/assets/{asset_id}/history", response_model=Page[AssetHistoryOut])
def list_asset_history(asset_id: str, page: int = 1, page_size: int = 25, event_type: str | None = None,
                        db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(ASSET_READ))):
    asset = _get_or_404(db, Asset, asset_id, "Asset")
    _enforce_asset_branch_scope(asset, current_user)
    items, total = paginate(db, AssetHistory, page=page, page_size=page_size,
                             asset_id=asset_id, event_type=event_type)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/assets/{asset_id}/history", response_model=AssetHistoryOut, status_code=status.HTTP_201_CREATED)
def create_asset_history_entry(asset_id: str, payload: AssetHistoryIn, db: Session = Depends(get_db),
                                current_user: CurrentUser = Depends(require_permission(ASSET_WRITE))):
    _get_or_404(db, Asset, asset_id, "Asset")
    if payload.branch_id is not None:
        _get_or_404(db, Branch, payload.branch_id, "Branch")
    if payload.department_id is not None:
        _get_or_404(db, Department, payload.department_id, "Department")
    if payload.location_id is not None:
        _get_or_404(db, Location, payload.location_id, "Location")

    entry = record_asset_history(
        db,
        asset_id=asset_id,
        event_type=payload.event_type,
        changed_by=current_user.id,
        from_value=payload.from_value,
        to_value=payload.to_value,
        branch_id=payload.branch_id,
        department_id=payload.department_id,
        location_id=payload.location_id,
        notes=payload.notes,
    )
    audit_record(db, user_id=current_user.id, action="CREATE", entity="asset_history", entity_id=entry.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(entry)
    return entry


# ---- Asset lifecycle — per-asset summary + unified maintenance history ----

@router.get("/assets/{asset_id}/lifecycle", response_model=AssetLifecycleSummaryOut)
def get_asset_lifecycle(asset_id: str, db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(ASSET_READ))):
    asset = _get_or_404(db, Asset, asset_id, "Asset")
    _enforce_asset_branch_scope(asset, current_user)
    return asset_lifecycle_service.get_asset_lifecycle_summary(db, asset)


@router.get("/assets/{asset_id}/maintenance-history", response_model=Page[MaintenanceHistoryEntryOut])
def get_asset_maintenance_history(
    asset_id: str, page: int = 1, page_size: int = 25,
    db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(ASSET_READ)),
):
    asset = _get_or_404(db, Asset, asset_id, "Asset")
    _enforce_asset_branch_scope(asset, current_user)
    items, total = asset_lifecycle_service.list_maintenance_history(db, asset_id, page=page, page_size=page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/assets/{asset_id}/provenance", response_model=list[AssetProvenanceEntryOut])
def get_asset_provenance(asset_id: str, db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(ASSET_READ))):
    """Digital Passport — Requirement/Fulfillment provenance for one asset."""
    asset = _get_or_404(db, Asset, asset_id, "Asset")
    _enforce_asset_branch_scope(asset, current_user)
    return asset_lifecycle_service.list_asset_provenance(db, asset_id)


@router.get("/assets/{asset_id}/procurement-provenance", response_model=list[AssetProcurementProvenanceEntryOut])
def get_asset_procurement_provenance(asset_id: str, db: Session = Depends(get_db),
                                      current_user: CurrentUser = Depends(require_permission(ASSET_READ))):
    """Digital Passport — Module 21 Quotation -> Indent -> PO -> Invoice provenance for
    one asset. Same read-only, computed-fresh, no-new-permission discipline as
    get_asset_provenance above (which covers Requirement/Fulfillment provenance)."""
    asset = _get_or_404(db, Asset, asset_id, "Asset")
    _enforce_asset_branch_scope(asset, current_user)
    return procurement_service.list_asset_procurement_provenance(db, asset_id)


# ---- Branch device assignments ----

@router.get("/branch-device-assignments", response_model=Page[BranchDeviceAssignmentOut])
def list_branch_device_assignments(page: int = 1, page_size: int = 25, branch_id: str | None = None,
                                    assignment_status: str | None = None, data_quality_flag: str | None = None,
                                    db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(DEVICE_READ))):
    items, total = paginate(db, BranchDeviceAssignment, page=page, page_size=page_size,
                             branch_id=branch_id, assignment_status=assignment_status, data_quality_flag=data_quality_flag)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/branch-device-assignments", response_model=BranchDeviceAssignmentOut, status_code=status.HTTP_201_CREATED)
def create_branch_device_assignment(payload: BranchDeviceAssignmentIn, db: Session = Depends(get_db),
                                     current_user: CurrentUser = Depends(require_permission(DEVICE_WRITE))):
    _get_or_404(db, Branch, payload.branch_id, "Branch")
    obj = BranchDeviceAssignment(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="branch_device_assignment", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj
