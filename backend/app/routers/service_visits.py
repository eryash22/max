from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import get_current_user, require_permission
from ..models.device import Asset
from ..models.identity import User
from ..models.incident import Incident, ServiceVisit, ServiceVisitType
from ..models.procurement import PurchaseOrder
from ..models.vendor import Vendor
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.service_visit import (
    ServiceVisitIn,
    ServiceVisitOut,
    ServiceVisitTypeIn,
    ServiceVisitTypeOut,
    ServiceVisitUpdateIn,
)
from ..security.permissions import ASSET_READ, ASSET_WRITE, SETTINGS_MANAGE, branch_scope_id
from ..services.service_visit_service import create_visit, delete_visit, update_visit

# No dedicated `service_visit.*` permission code exists in security/permissions.py (verified
# before writing any code, same check performed for Modules 08/09/10/11's reused domains).
# ServiceVisit.asset_id is the only non-nullable relationship on the model (incident_id,
# vendor_id, engineer_id are all optional) — a visit is fundamentally a service action taken
# against an asset, so this module reuses ASSET_READ/ASSET_WRITE rather than PM_READ/PM_WRITE
# or INCIDENT_READ/INCIDENT_MANAGE, which would misrepresent the BREAKDOWN/INSTALLATION/OTHER
# visit types that have nothing to do with those specific domains. See PROJECT_STATUS.md.

router = APIRouter(prefix="/api/v1/service-visits", tags=["service-visits"])


def _get_asset_or_404(db: Session, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return asset


def _get_visit_or_404(db: Session, visit_id: str) -> ServiceVisit:
    visit = db.get(ServiceVisit, visit_id)
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service visit not found")
    return visit


def _validate_related(db: Session, *, incident_id: str | None, vendor_id: str | None, engineer_id: str | None,
                       purchase_order_id: str | None = None) -> None:
    if incident_id is not None and db.get(Incident, incident_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    if vendor_id is not None and db.get(Vendor, vendor_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found")
    if engineer_id is not None and db.get(User, engineer_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Engineer not found")
    # Module 21 — Procurement. A vendor visit scheduled against a PO.
    if purchase_order_id is not None and db.get(PurchaseOrder, purchase_order_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase order not found")


def _validate_visit_type(db: Session, visit_type: str | None) -> None:
    """Batch E, Part 20 — a NEW/edited ServiceVisit's `visit_type` (when given) must
    match an active `ServiceVisitType.name`. Historical rows are never re-validated
    (this only runs on create/update payloads), and `visit_type=None` (unspecified) is
    always allowed, matching the column's existing nullable-free-text shape."""
    if visit_type is None:
        return
    active = db.query(ServiceVisitType).filter(
        ServiceVisitType.name == visit_type, ServiceVisitType.is_active.is_(True)
    ).first()
    if active is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"visit_type '{visit_type}' is not an active service visit type",
        )


@router.get("", response_model=Page[ServiceVisitOut])
def list_service_visits(
    page: int = 1, page_size: int = 25,
    asset_id: str | None = None,
    incident_id: str | None = None,
    vendor_id: str | None = None,
    engineer_id: str | None = None,
    visit_type: str | None = None,
    purchase_order_id: str | None = None,
    search: str | None = None,
    # Workflow consolidation pass — Branch detail hub filters Service Visits via the
    # asset's branch, same join pattern the hub already uses for /assets?branch_id=.
    branch_id: str | None = None,
    visit_date_after: str | None = None,
    visit_date_before: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(ASSET_READ)),
):
    # No active/known-type restriction on the filter itself (unlike create/update) —
    # a caller must still be able to filter historical visits by a visit_type value
    # that has since been deactivated or predates ServiceVisitType altogether.

    # A branch-scoped caller (UNIT_HEAD/TL) only ever sees service visits on their own
    # branch's assets, regardless of the branch_id filter passed in — same convention
    # as routers/devices.py:list_assets. ServiceVisit has no branch_id of its own
    # (asset_id is its only non-nullable relationship, see module docstring above), so
    # the scope is enforced via a join on the visit's asset.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        branch_id = scoped_branch_id

    query = db.query(ServiceVisit)
    exact_filters = {
        "asset_id": asset_id, "incident_id": incident_id, "vendor_id": vendor_id,
        "engineer_id": engineer_id, "visit_type": visit_type, "purchase_order_id": purchase_order_id,
    }
    for field, value in exact_filters.items():
        if value is not None:
            query = query.filter(getattr(ServiceVisit, field) == value)
    if branch_id:
        query = query.join(Asset, Asset.id == ServiceVisit.asset_id).filter(Asset.branch_id == branch_id)
    if search:
        query = query.filter(ServiceVisit.summary.ilike(f"%{search}%"))
    if visit_date_after:
        query = query.filter(ServiceVisit.visit_date.isnot(None), ServiceVisit.visit_date >= visit_date_after)
    if visit_date_before:
        query = query.filter(ServiceVisit.visit_date.isnot(None), ServiceVisit.visit_date <= visit_date_before)

    total = query.count()
    items = query.order_by(ServiceVisit.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=ServiceVisitOut, status_code=status.HTTP_201_CREATED)
def create_service_visit(payload: ServiceVisitIn, db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(require_permission(ASSET_WRITE))):
    _get_asset_or_404(db, payload.asset_id)
    _validate_related(db, incident_id=payload.incident_id, vendor_id=payload.vendor_id, engineer_id=payload.engineer_id,
                       purchase_order_id=payload.purchase_order_id)
    _validate_visit_type(db, payload.visit_type)
    return create_visit(db, payload, user_id=current_user.id)


# ---- Service visit types (Batch E, Part 20) ----
# Small admin-managed lookup table, same router-per-domain placement DeviceCategory
# uses inside devices.py (its parent domain's router) rather than a standalone
# router for one small sub-resource. List is open to any authenticated user (visit
# forms everywhere need it); create/deactivate are SETTINGS_MANAGE (Super Admin by
# default), matching theme.py's PUT /theme gating. A type already referenced by a
# historical ServiceVisit is never hard-deleted — only ever deactivated (excluded
# from future create/update acceptance, per _validate_visit_type above) — same
# discipline as DeviceModel archive (routers/devices.py).
#
# Registered ahead of GET/PATCH/DELETE "/{visit_id}" below — FastAPI matches routes
# in registration order, so "/types" must come first or "/{visit_id}" would swallow
# it (treating "types" as a visit_id).

def _get_visit_type_or_404(db: Session, visit_type_id: str) -> ServiceVisitType:
    visit_type = db.get(ServiceVisitType, visit_type_id)
    if visit_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service visit type not found")
    return visit_type


@router.get("/types", response_model=Page[ServiceVisitTypeOut])
def list_service_visit_types(
    page: int = 1, page_size: int = 200,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    # Page-wrapped (rather than a bare list) so this plugs straight into the
    # frontend's existing `useLookup<T>` hook, same convention every other lookup
    # endpoint in this codebase follows (vendors, assets, incidents, ...).
    query = db.query(ServiceVisitType)
    if not include_inactive:
        query = query.filter(ServiceVisitType.is_active.is_(True))
    query = query.order_by(ServiceVisitType.name)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/types", response_model=ServiceVisitTypeOut, status_code=status.HTTP_201_CREATED)
def create_service_visit_type(payload: ServiceVisitTypeIn, db: Session = Depends(get_db),
                               current_user: CurrentUser = Depends(require_permission(SETTINGS_MANAGE))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name must not be blank")
    existing = db.query(ServiceVisitType).filter(ServiceVisitType.name == name).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A service visit type with this name already exists")
    visit_type = ServiceVisitType(name=name, is_active=True)
    db.add(visit_type)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="service_visit_type", entity_id=visit_type.id,
                 new_value={"name": name, "is_active": True})
    db.commit()
    db.refresh(visit_type)
    return visit_type


@router.post("/types/{visit_type_id}/deactivate", response_model=ServiceVisitTypeOut)
def deactivate_service_visit_type(visit_type_id: str, db: Session = Depends(get_db),
                                   current_user: CurrentUser = Depends(require_permission(SETTINGS_MANAGE))):
    """Deactivate only — never a hard delete, so any historical ServiceVisit already
    carrying this type's name keeps displaying/filtering correctly forever."""
    visit_type = _get_visit_type_or_404(db, visit_type_id)
    before = {"is_active": visit_type.is_active}
    visit_type.is_active = False
    audit_record(db, user_id=current_user.id, action="ARCHIVE", entity="service_visit_type", entity_id=visit_type.id,
                 old_value=before, new_value={"is_active": False})
    db.commit()
    db.refresh(visit_type)
    return visit_type


@router.post("/types/{visit_type_id}/activate", response_model=ServiceVisitTypeOut)
def activate_service_visit_type(visit_type_id: str, db: Session = Depends(get_db),
                                 current_user: CurrentUser = Depends(require_permission(SETTINGS_MANAGE))):
    visit_type = _get_visit_type_or_404(db, visit_type_id)
    before = {"is_active": visit_type.is_active}
    visit_type.is_active = True
    audit_record(db, user_id=current_user.id, action="ACTIVATE", entity="service_visit_type", entity_id=visit_type.id,
                 old_value=before, new_value={"is_active": True})
    db.commit()
    db.refresh(visit_type)
    return visit_type


def _enforce_visit_branch_scope(db: Session, visit: ServiceVisit, current_user: CurrentUser) -> None:
    """Same branch-scope check as GET /assets/{id} (devices.py) — a UNIT_HEAD/TL
    blocked from a branch's asset must not be able to reach that asset's service
    visit history by visit id instead."""
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is None:
        return
    asset = db.get(Asset, visit.asset_id)
    if asset is None or asset.branch_id != scoped_branch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service visit not found")


@router.get("/{visit_id}", response_model=ServiceVisitOut)
def get_service_visit(visit_id: str, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(ASSET_READ))):
    visit = _get_visit_or_404(db, visit_id)
    _enforce_visit_branch_scope(db, visit, current_user)
    return visit


@router.patch("/{visit_id}", response_model=ServiceVisitOut)
def patch_service_visit(visit_id: str, payload: ServiceVisitUpdateIn, db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(require_permission(ASSET_WRITE))):
    visit = _get_visit_or_404(db, visit_id)
    _validate_related(db, incident_id=payload.incident_id, vendor_id=payload.vendor_id, engineer_id=payload.engineer_id)
    _validate_visit_type(db, payload.visit_type)
    return update_visit(db, visit, payload, user_id=current_user.id)


@router.delete("/{visit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service_visit(visit_id: str, db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(require_permission(ASSET_WRITE))):
    visit = _get_visit_or_404(db, visit_id)
    delete_visit(db, visit, user_id=current_user.id)
    return None
