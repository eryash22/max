from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import require_permission
from ..exports.writer import EXPORT_FORMATS, build_export_response
from ..models.asset_history import AssetHistory
from ..models.business_impact import BusinessImpactAssumption
from ..models.device import Asset, BranchDeviceAssignment
from ..models.hospital import Branch, BranchTL, Building, Department, DepartmentMaster, Floor, HospitalNetwork, Location
from ..models.identity import User
from ..models.incident import Incident
from ..models.inventory import Consumable, ConsumableTransaction, InventoryLocation
from ..models.requirement import Requirement
from ..repositories.crud import paginate
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.hospital import (
    BranchIn, BranchOut, BranchTLIn, BranchTLOut, BuildingIn, BuildingOut, DepartmentIn, DepartmentMasterIn,
    DepartmentMasterOut, DepartmentOut, FloorIn, FloorOut, HospitalNetworkIn, HospitalNetworkOut, LocationIn,
    LocationOut,
)
from ..security.permissions import DEV_DATA_CLEANUP, HOSPITAL_READ, HOSPITAL_WRITE, branch_scope_id
from ..services.export_service import MAX_EXPORT_ROWS, record_export_audit
from ..services.force_delete import assert_dev_environment, force_delete_row

router = APIRouter(prefix="/api/v1", tags=["hospitals"])


def _get_or_404(db: Session, model, entity_id: str, label: str):
    obj = db.get(model, entity_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return obj


# ---- Hospital networks ----

@router.get("/hospital-networks", response_model=Page[HospitalNetworkOut])
def list_networks(page: int = 1, page_size: int = 25, search: str | None = None,
                   db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(HOSPITAL_READ))):
    items, total = paginate(db, HospitalNetwork, page=page, page_size=page_size, search=search, search_fields=["name", "code"])
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/hospital-networks", response_model=HospitalNetworkOut, status_code=status.HTTP_201_CREATED)
def create_network(payload: HospitalNetworkIn, db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    obj = HospitalNetwork(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="hospital_network", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


# ---- Branches ----
# Branch-scoping hardening pass — UNIT_HEAD/TL must not be able to browse/enumerate
# every branch just because they hold hospital.read (needed so they can resolve their
# own branch's name/details elsewhere in the app). Every endpoint below reuses
# branch_scope_id(), the one existing branch-authorization mechanism — no second
# authorization system. Any other HOSPITAL_READ holder (Super Admin, Biomedical
# Admin/Engineer, Inventory Manager, ...) is unaffected, same as every other
# branch_scope_id() call site.

@router.get("/branches", response_model=Page[BranchOut])
def list_branches(page: int = 1, page_size: int = 25, search: str | None = None, network_id: str | None = None,
                   status_filter: str | None = None,
                   db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(HOSPITAL_READ))):
    # status_filter is opt-in (None = unfiltered, matching the existing Vendor/
    # DepartmentMaster convention) so the Branches admin list keeps showing every
    # status by default — every "new record" branch picker instead passes
    # status_filter=ACTIVE explicitly (see frontend *_new/page.tsx call sites) so a
    # deactivated branch drops out of new-record creation without touching admin
    # visibility.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        items, total = paginate(db, Branch, page=page, page_size=page_size, id=scoped_branch_id)
        return Page(items=items, total=total, page=page, page_size=page_size)
    filters = {"status": status_filter} if status_filter else {}
    items, total = paginate(db, Branch, page=page, page_size=page_size, search=search,
                             search_fields=["name", "code", "source_name"], network_id=network_id, **filters)
    return Page(items=items, total=total, page=page, page_size=page_size)


def _branch_export_row(db: Session, branch: Branch) -> list:
    unit_head = db.get(User, branch.unit_head_user_id) if branch.unit_head_user_id else None
    tl_users = (
        db.query(User)
        .join(BranchTL, BranchTL.user_id == User.id)
        .filter(BranchTL.branch_id == branch.id)
        .all()
    )
    tl_names = ", ".join(u.name for u in tl_users) if tl_users else None

    return [
        branch.name, branch.code, branch.region, branch.city, branch.state, branch.address,
        branch.status, unit_head.name if unit_head else branch.unit_head, tl_names,
        branch.is_central_warehouse, branch.branch_email, branch.branch_phone, branch.branch_whatsapp,
    ]


@router.get("/branches/export")
def export_branches(
    format: str = "xlsx", search: str | None = None, network_id: str | None = None,
    db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(HOSPITAL_READ)),
):
    """Same filters as `GET /api/v1/branches`, exported instead of paginated, gated on
    the identical `hospital.read` permission the list endpoint uses (matches the
    vendor/asset export precedent, see `routers/vendors.py:export_vendors` and
    `routers/devices.py:export_assets`). No BME/Inventory-Manager branch assignment
    column — `Branch` has no such FK today (only `unit_head_user_id` and the
    many-per-branch `branch_tls` join table); adding one is out of scope for this
    pass and is documented as a limitation rather than invented. Registered before
    `/branches/{branch_id}` (same static-before-dynamic-path discipline as every other
    module's `/export` endpoint) so this literal path isn't swallowed by the
    `branch_id` path parameter."""
    if format not in EXPORT_FORMATS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"format must be one of {', '.join(EXPORT_FORMATS)}")

    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        items, total = paginate(db, Branch, page=1, page_size=MAX_EXPORT_ROWS, id=scoped_branch_id)
    else:
        items, total = paginate(
            db, Branch, page=1, page_size=MAX_EXPORT_ROWS, search=search,
            search_fields=["name", "code", "source_name"], network_id=network_id,
        )

    headers = [
        "Name", "Code", "Region", "City", "State", "Address", "Status",
        "Unit Head", "TL(s)", "Central Warehouse", "Branch Email", "Branch Phone", "Branch WhatsApp",
    ]
    rows = [_branch_export_row(db, branch) for branch in items]

    record_export_audit(
        db, user_id=current_user.id, module="branches",
        filters={"search": search, "network_id": network_id},
        format=format, record_count=len(rows), truncated=total > MAX_EXPORT_ROWS,
    )
    return build_export_response(headers=headers, rows=rows, format=format, filename_stem="branches_export")


@router.get("/branches/{branch_id}", response_model=BranchOut)
def get_branch(branch_id: str, db: Session = Depends(get_db),
                current_user: CurrentUser = Depends(require_permission(HOSPITAL_READ))):
    branch = _get_or_404(db, Branch, branch_id, "Branch")
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and branch_id != scoped_branch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
    return branch


def _validate_unit_head_user_id(db: Session, unit_head_user_id: str | None) -> None:
    if unit_head_user_id is not None and db.get(User, unit_head_user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unit Head user not found")


@router.post("/branches", response_model=BranchOut, status_code=status.HTTP_201_CREATED)
def create_branch(payload: BranchIn, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    _validate_unit_head_user_id(db, payload.unit_head_user_id)
    obj = Branch(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="branch", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/branches/{branch_id}", response_model=BranchOut)
def update_branch(branch_id: str, payload: BranchIn, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    obj = _get_or_404(db, Branch, branch_id, "Branch")
    _validate_unit_head_user_id(db, payload.unit_head_user_id)
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    for key, value in payload.model_dump().items():
        setattr(obj, key, value)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="branch", entity_id=obj.id, old_value=before, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


def _branch_reference_count(db: Session, branch_id: str) -> int:
    """Batch C — Part 1: how many rows across the schema currently reference this
    Branch. Zero means it can be hard-deleted; any other count means it must be
    archived (status=INACTIVE via deactivate) instead — same reference-check-then-
    hard-delete-or-reject pattern as routers/devices.py:_device_model_in_use /
    delete_device_model and routers/vendors.py:_vendor_reference_count /
    delete_vendor. `branch_tls` is deliberately excluded — that table already
    cascade-deletes with its Branch (see models/hospital.py's
    tl_assignments relationship), so it is never a real blocker. Fulfillments and
    Contracts have no direct branch_id FK (they reach a branch only via Asset/
    Requirement), so they are covered transitively by the Asset/Requirement counts
    below rather than double-counted."""
    counts = [
        db.query(Asset).filter(Asset.branch_id == branch_id).count(),
        db.query(BranchDeviceAssignment).filter(BranchDeviceAssignment.branch_id == branch_id).count(),
        db.query(Building).filter(Building.branch_id == branch_id).count(),
        db.query(Department).filter(Department.branch_id == branch_id).count(),
        db.query(Requirement).filter(Requirement.branch_id == branch_id).count(),
        db.query(Incident).filter(Incident.branch_id == branch_id).count(),
        db.query(InventoryLocation).filter(InventoryLocation.branch_id == branch_id).count(),
        db.query(Consumable).filter(Consumable.branch_id == branch_id).count(),
        db.query(ConsumableTransaction).filter(ConsumableTransaction.source_branch_id == branch_id).count(),
        db.query(ConsumableTransaction).filter(ConsumableTransaction.destination_branch_id == branch_id).count(),
        db.query(BusinessImpactAssumption).filter(BusinessImpactAssumption.branch_id == branch_id).count(),
        db.query(AssetHistory).filter(AssetHistory.branch_id == branch_id).count(),
    ]
    return sum(counts)


@router.delete("/branches/{branch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_branch(branch_id: str, force: bool = False, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    """Batch C — Part 1: hard delete only when zero rows reference this Branch;
    otherwise 409, directing the caller to deactivate (archive) instead.

    ``force=true`` (Super Admin only, gated on DEV_DATA_CLEANUP) bypasses that guard
    via the generic FK-graph cascade (force_delete_row) — everything transitively
    hanging off this branch (assets and their full history, requirements,
    incidents, inventory locations/consumables, business-impact assumptions) is
    purged too."""
    obj = _get_or_404(db, Branch, branch_id, "Branch")
    if force:
        if DEV_DATA_CLEANUP not in current_user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Force-delete requires Super Admin (dev.data_cleanup permission)",
            )
        assert_dev_environment()
        before = {"id": obj.id, "name": obj.name}
        deleted_count = force_delete_row(db, "branches", branch_id)
        audit_record(
            db, user_id=current_user.id, action="FORCE_DELETE", entity="branch", entity_id=branch_id,
            old_value=before, new_value={"cascaded_row_count": deleted_count},
        )
        db.commit()
        return
    in_use = _branch_reference_count(db, branch_id)
    if in_use > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Branch is referenced by {in_use} record(s) and cannot be deleted — deactivate it instead",
        )
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    audit_record(db, user_id=current_user.id, action="DELETE", entity="branch", entity_id=branch_id, old_value=before)
    db.delete(obj)
    db.commit()


def _set_branch_status(db: Session, branch: Branch, new_status: str, *, user_id: str) -> Branch:
    before = {"status": branch.status}
    branch.status = new_status
    audit_record(db, user_id=user_id, action="UPDATE", entity="branch", entity_id=branch.id,
                 old_value=before, new_value={"status": new_status})
    db.commit()
    db.refresh(branch)
    return branch


@router.post("/branches/{branch_id}/activate", response_model=BranchOut)
def activate_branch(branch_id: str, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    branch = _get_or_404(db, Branch, branch_id, "Branch")
    return _set_branch_status(db, branch, "ACTIVE", user_id=current_user.id)


@router.post("/branches/{branch_id}/deactivate", response_model=BranchOut)
def deactivate_branch(branch_id: str, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    """Deactivate (archive) — used when a hard DELETE is blocked by references, or
    any time a branch should stop appearing as an active option elsewhere without
    losing its history. Always allowed, even when referenced — only the hard DELETE
    above is reference-guarded."""
    branch = _get_or_404(db, Branch, branch_id, "Branch")
    return _set_branch_status(db, branch, "INACTIVE", user_id=current_user.id)


# ---- Branch TLs (real branch user hierarchy: many TL users per branch) ----

@router.get("/branches/{branch_id}/tls", response_model=list[BranchTLOut])
def list_branch_tls(branch_id: str, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(HOSPITAL_READ))):
    _get_or_404(db, Branch, branch_id, "Branch")
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and branch_id != scoped_branch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
    return db.query(BranchTL).filter(BranchTL.branch_id == branch_id).all()


@router.post("/branches/{branch_id}/tls", response_model=BranchTLOut, status_code=status.HTTP_201_CREATED)
def add_branch_tl(branch_id: str, payload: BranchTLIn, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    _get_or_404(db, Branch, branch_id, "Branch")
    if db.get(User, payload.user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    existing = db.query(BranchTL).filter(
        BranchTL.branch_id == branch_id, BranchTL.user_id == payload.user_id
    ).first()
    if existing is not None:
        return existing
    obj = BranchTL(branch_id=branch_id, user_id=payload.user_id)
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="branch_tl", entity_id=obj.id,
                 new_value={"branch_id": branch_id, "user_id": payload.user_id})
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/branches/{branch_id}/tls/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_branch_tl(branch_id: str, assignment_id: str, db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    obj = db.query(BranchTL).filter(BranchTL.id == assignment_id, BranchTL.branch_id == branch_id).first()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch TL assignment not found")
    db.delete(obj)
    audit_record(db, user_id=current_user.id, action="DELETE", entity="branch_tl", entity_id=assignment_id)
    db.commit()


# ---- Buildings ----

@router.get("/buildings", response_model=Page[BuildingOut])
def list_buildings(page: int = 1, page_size: int = 25, branch_id: str | None = None,
                    db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(HOSPITAL_READ))):
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        branch_id = scoped_branch_id
    items, total = paginate(db, Building, page=page, page_size=page_size, branch_id=branch_id)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/buildings", response_model=BuildingOut, status_code=status.HTTP_201_CREATED)
def create_building(payload: BuildingIn, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    _get_or_404(db, Branch, payload.branch_id, "Branch")
    obj = Building(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="building", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


# ---- Floors ----

@router.get("/floors", response_model=Page[FloorOut])
def list_floors(page: int = 1, page_size: int = 25, building_id: str | None = None,
                 db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(HOSPITAL_READ))):
    # Branch-scoping hardening pass — UNIT_HEAD/TL must not enumerate another branch's
    # buildings/floors by name, same as every other branch-scoped domain.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        query = db.query(Floor).join(Building, Floor.building_id == Building.id).filter(Building.branch_id == scoped_branch_id)
        if building_id:
            query = query.filter(Floor.building_id == building_id)
        total = query.count()
        items = query.order_by(Floor.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return Page(items=items, total=total, page=page, page_size=page_size)
    items, total = paginate(db, Floor, page=page, page_size=page_size, building_id=building_id)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/floors", response_model=FloorOut, status_code=status.HTTP_201_CREATED)
def create_floor(payload: FloorIn, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    _get_or_404(db, Building, payload.building_id, "Building")
    obj = Floor(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="floor", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


# ---- Department Master (central catalog) ----
#
# Physiotherapy/ICU/Radiology etc. are defined ONCE here, then assigned to one or
# more branches via the Department rows below (which point back at a master via
# department_master_id). Same shape as DeviceCategory -> DeviceModel/Asset: one
# global master, many branch-scoped instances. name/code are unique on the master
# so two admins can't accidentally fork "Physiotherapy" into two catalogs; a
# Department assignment's own name/code are always synced from here, never edited
# independently (see create_department/update_department/update_department_master).

@router.get("/department-masters", response_model=Page[DepartmentMasterOut])
def list_department_masters(page: int = 1, page_size: int = 25, search: str | None = None,
                             status_filter: str | None = None,
                             db: Session = Depends(get_db),
                             _: CurrentUser = Depends(require_permission(HOSPITAL_READ))):
    filters = {"status": status_filter} if status_filter else {}
    items, total = paginate(db, DepartmentMaster, page=page, page_size=page_size, search=search,
                             search_fields=["name", "code"], **filters)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/department-masters", response_model=DepartmentMasterOut, status_code=status.HTTP_201_CREATED)
def create_department_master(payload: DepartmentMasterIn, db: Session = Depends(get_db),
                              current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    # Trimmed before comparison AND before storage -- a stray leading/trailing space
    # must never be how two rows meaning the same department both pass the duplicate
    # check yet end up stored as two distinct strings (exactly the defect the
    # migration's own backfill had to clean up on the pre-existing "Physiotherapy "
    # row; see migrations/versions/0023_department_master.py).
    name = payload.name.strip()
    code = payload.code.strip() if payload.code else None
    existing = db.query(DepartmentMaster).filter(DepartmentMaster.name.ilike(name)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A department master named '{existing.name}' already exists — reuse it instead of creating a duplicate.",
        )
    obj = DepartmentMaster(name=name, code=code, status=payload.status)
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="department_master", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/department-masters/{department_master_id}", response_model=DepartmentMasterOut)
def update_department_master(department_master_id: str, payload: DepartmentMasterIn, db: Session = Depends(get_db),
                              current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    obj = _get_or_404(db, DepartmentMaster, department_master_id, "Department master")
    name = payload.name.strip()
    code = payload.code.strip() if payload.code else None
    duplicate = db.query(DepartmentMaster).filter(
        DepartmentMaster.name.ilike(name), DepartmentMaster.id != department_master_id,
    ).first()
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A department master named '{duplicate.name}' already exists.",
        )
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    obj.name = name
    obj.code = code
    obj.status = payload.status
    # The whole point of a central master: renaming it here must not leave branch
    # assignments quoting a stale name. Every Department row synced from write time
    # (not derived at read time) so every existing consumer that already reads
    # department.name/code directly keeps working unchanged.
    linked = db.query(Department).filter(Department.department_master_id == obj.id).all()
    for assignment in linked:
        assignment.name = obj.name
        assignment.code = obj.code
    audit_record(
        db, user_id=current_user.id, action="UPDATE", entity="department_master", entity_id=obj.id,
        old_value=before, new_value={"name": obj.name, "code": obj.code, "status": obj.status,
                                      "propagated_to_assignments": len(linked)},
    )
    db.commit()
    db.refresh(obj)
    return obj


def _department_master_reference_count(db: Session, department_master_id: str) -> int:
    return db.query(Department).filter(Department.department_master_id == department_master_id).count()


@router.delete("/department-masters/{department_master_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department_master(department_master_id: str, db: Session = Depends(get_db),
                              current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    """Hard delete only when zero branches have this master assigned; otherwise 409,
    directing the caller to deactivate (archive) instead — same pattern as every
    other master/catalog entity in this app (_department_reference_count,
    _device_model_in_use, _vendor_reference_count)."""
    obj = _get_or_404(db, DepartmentMaster, department_master_id, "Department master")
    in_use = _department_master_reference_count(db, department_master_id)
    if in_use > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Department master is assigned to {in_use} branch(es) and cannot be deleted — deactivate it instead",
        )
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    audit_record(db, user_id=current_user.id, action="DELETE", entity="department_master", entity_id=department_master_id, old_value=before)
    db.delete(obj)
    db.commit()


@router.post("/department-masters/{department_master_id}/activate", response_model=DepartmentMasterOut)
def activate_department_master(department_master_id: str, db: Session = Depends(get_db),
                                current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    obj = _get_or_404(db, DepartmentMaster, department_master_id, "Department master")
    before = {"status": obj.status}
    obj.status = "ACTIVE"
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="department_master", entity_id=obj.id,
                 old_value=before, new_value={"status": "ACTIVE"})
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/department-masters/{department_master_id}/deactivate", response_model=DepartmentMasterOut)
def deactivate_department_master(department_master_id: str, db: Session = Depends(get_db),
                                  current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    """Deactivating a master does not touch its existing branch assignments (a
    branch already using it keeps working) — it only stops the master from being
    offered for NEW assignments; see the frontend's active-masters picker."""
    obj = _get_or_404(db, DepartmentMaster, department_master_id, "Department master")
    before = {"status": obj.status}
    obj.status = "INACTIVE"
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="department_master", entity_id=obj.id,
                 old_value=before, new_value={"status": "INACTIVE"})
    db.commit()
    db.refresh(obj)
    return obj


# ---- Departments (branch assignment of a Department Master) ----

@router.get("/departments", response_model=Page[DepartmentOut])
def list_departments(page: int = 1, page_size: int = 25, search: str | None = None, branch_id: str | None = None,
                      status_filter: str | None = None,
                      db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(require_permission(HOSPITAL_READ))):
    # Branch-scoping hardening pass — a UNIT_HEAD/TL's department dropdown (requirement/
    # incident creation forms) must only ever offer their own branch's departments.
    # status_filter is opt-in (None = unfiltered) — /departments/page.tsx's own
    # branch-assignment toggle view relies on seeing both ACTIVE and INACTIVE
    # assignments, so it deliberately omits this param; every "new record" department
    # picker (devices/new, incidents/new, requirements/new) instead passes
    # status_filter=ACTIVE so a deactivated department drops out of new-record forms.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        branch_id = scoped_branch_id
    filters = {"status": status_filter} if status_filter else {}
    items, total = paginate(db, Department, page=page, page_size=page_size, search=search,
                             search_fields=["name", "code"], branch_id=branch_id, **filters)
    return Page(items=items, total=total, page=page, page_size=page_size)


def _reject_foreign_branch_department(current_user: CurrentUser, branch_id: str) -> None:
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and branch_id != scoped_branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only manage departments for your own assigned branch.")


@router.post("/departments", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
def create_department(payload: DepartmentIn, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    _reject_foreign_branch_department(current_user, payload.branch_id)
    _get_or_404(db, Branch, payload.branch_id, "Branch")
    master = _get_or_404(db, DepartmentMaster, payload.department_master_id, "Department master")
    duplicate = db.query(Department).filter(
        Department.branch_id == payload.branch_id, Department.department_master_id == payload.department_master_id,
    ).first()
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{master.name}' is already assigned to this branch (status: {duplicate.status}) — reactivate it instead of creating a duplicate.",
        )
    obj = Department(
        branch_id=payload.branch_id, floor_id=payload.floor_id, department_master_id=master.id,
        name=master.name, code=master.code, status=payload.status,
    )
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="department", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/departments/{department_id}", response_model=DepartmentOut)
def update_department(department_id: str, payload: DepartmentIn, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    obj = _get_or_404(db, Department, department_id, "Department")
    _reject_foreign_branch_department(current_user, obj.branch_id)
    _reject_foreign_branch_department(current_user, payload.branch_id)
    _get_or_404(db, Branch, payload.branch_id, "Branch")
    master = _get_or_404(db, DepartmentMaster, payload.department_master_id, "Department master")
    duplicate = db.query(Department).filter(
        Department.branch_id == payload.branch_id, Department.department_master_id == payload.department_master_id,
        Department.id != department_id,
    ).first()
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{master.name}' is already assigned to this branch (status: {duplicate.status}).",
        )
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    obj.branch_id = payload.branch_id
    obj.floor_id = payload.floor_id
    obj.department_master_id = master.id
    obj.name = master.name
    obj.code = master.code
    obj.status = payload.status
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="department", entity_id=obj.id, old_value=before, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


def _department_reference_count(db: Session, department_id: str) -> int:
    """Same reference-check-then-hard-delete-or-reject pattern as
    _branch_reference_count / _device_model_in_use / _vendor_reference_count."""
    counts = [
        db.query(Asset).filter(Asset.department_id == department_id).count(),
        db.query(Incident).filter(Incident.department_id == department_id).count(),
        db.query(AssetHistory).filter(AssetHistory.department_id == department_id).count(),
        db.query(Requirement).filter(Requirement.department_id == department_id).count(),
        db.query(Location).filter(Location.department_id == department_id).count(),
        db.query(User).filter(User.department_id == department_id).count(),
    ]
    return sum(counts)


@router.delete("/departments/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department(department_id: str, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    """Hard delete only when zero rows reference this Department; otherwise 409,
    directing the caller to deactivate (archive) instead."""
    obj = _get_or_404(db, Department, department_id, "Department")
    _reject_foreign_branch_department(current_user, obj.branch_id)
    in_use = _department_reference_count(db, department_id)
    if in_use > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Department is referenced by {in_use} record(s) and cannot be deleted — deactivate it instead",
        )
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    audit_record(db, user_id=current_user.id, action="DELETE", entity="department", entity_id=department_id, old_value=before)
    db.delete(obj)
    db.commit()


def _set_department_status(db: Session, department: Department, new_status: str, *, user_id: str) -> Department:
    before = {"status": department.status}
    department.status = new_status
    audit_record(db, user_id=user_id, action="UPDATE", entity="department", entity_id=department.id,
                 old_value=before, new_value={"status": new_status})
    db.commit()
    db.refresh(department)
    return department


@router.post("/departments/{department_id}/activate", response_model=DepartmentOut)
def activate_department(department_id: str, db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    department = _get_or_404(db, Department, department_id, "Department")
    _reject_foreign_branch_department(current_user, department.branch_id)
    return _set_department_status(db, department, "ACTIVE", user_id=current_user.id)


@router.post("/departments/{department_id}/deactivate", response_model=DepartmentOut)
def deactivate_department(department_id: str, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    """Deactivate (archive) — always allowed, even when referenced; only the hard
    DELETE above is reference-guarded."""
    department = _get_or_404(db, Department, department_id, "Department")
    _reject_foreign_branch_department(current_user, department.branch_id)
    return _set_department_status(db, department, "INACTIVE", user_id=current_user.id)


# ---- Locations ----

@router.get("/locations", response_model=Page[LocationOut])
def list_locations(page: int = 1, page_size: int = 25, search: str | None = None, department_id: str | None = None,
                    status_filter: str | None = None,
                    db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(HOSPITAL_READ))):
    # Branch-scoping hardening pass — same rationale as list_floors above.
    # status_filter is opt-in (None = unfiltered, same convention as branches/
    # departments above) — new-record location pickers (devices/new, assets/[id])
    # pass status_filter=ACTIVE so a deactivated location drops out of new selection.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        query = db.query(Location).join(Department, Location.department_id == Department.id).filter(Department.branch_id == scoped_branch_id)
        if department_id:
            query = query.filter(Location.department_id == department_id)
        if status_filter:
            query = query.filter(Location.status == status_filter)
        if search:
            like = f"%{search}%"
            query = query.filter(Location.name.ilike(like) | Location.code.ilike(like))
        total = query.count()
        items = query.order_by(Location.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return Page(items=items, total=total, page=page, page_size=page_size)
    filters = {"status": status_filter} if status_filter else {}
    items, total = paginate(db, Location, page=page, page_size=page_size, search=search,
                             search_fields=["name", "code"], department_id=department_id, **filters)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/locations", response_model=LocationOut, status_code=status.HTTP_201_CREATED)
def create_location(payload: LocationIn, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    _get_or_404(db, Department, payload.department_id, "Department")
    obj = Location(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="location", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/locations/{location_id}", response_model=LocationOut)
def update_location(location_id: str, payload: LocationIn, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    obj = _get_or_404(db, Location, location_id, "Location")
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    for key, value in payload.model_dump().items():
        setattr(obj, key, value)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="location", entity_id=obj.id, old_value=before, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj
