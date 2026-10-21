from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies.auth import require_any_permission, require_permission
from ..models.fulfillment import FulfillmentRequest, FulfillmentStatusHistory
from ..models.requirement import Requirement
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.fulfillment import (
    FulfillmentAllocateIn,
    FulfillmentCancelIn,
    FulfillmentCreateIn,
    FulfillmentDeliverIn,
    FulfillmentOut,
    FulfillmentStatusHistoryOut,
)
from ..security.permissions import (
    DEV_DATA_CLEANUP, INVENTORY_READ, REQUIREMENT_APPROVE, REQUIREMENT_CREATE, REQUIREMENT_FULFILL, REQUIREMENT_READ,
    branch_scope_id,
)
from ..services import fulfillment_service
from ..services.fulfillment_service import (
    FulfillmentAvailabilityError,
    FulfillmentReferenceError,
    FulfillmentServiceError,
    FulfillmentStateError,
)
from ..audit.service import record as audit_record
from ..services.force_delete import assert_dev_environment, force_delete_row

router = APIRouter(prefix="/api/v1/fulfillments", tags=["fulfillments"])

_READ_ACCESS = require_any_permission(REQUIREMENT_CREATE, REQUIREMENT_READ, REQUIREMENT_APPROVE, REQUIREMENT_FULFILL)
_FULFILL_ACCESS = require_permission(REQUIREMENT_FULFILL)


def _get_or_404(db: Session, fulfillment_id: str) -> FulfillmentRequest:
    fulfillment = db.get(FulfillmentRequest, fulfillment_id)
    if fulfillment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fulfillment not found")
    return fulfillment


def _mask_stock(current_user: CurrentUser) -> bool:
    """Same masking convention as routers/inventory.py — a caller without full
    inventory.read (i.e. only inventory.picker_read: TL/Unit Head/Hospital-Unit-
    Manager) must never see central stock quantities, including through
    available_quantity on a fulfillment they're otherwise allowed to view."""
    return INVENTORY_READ not in current_user.permissions


def _has_broad_visibility(current_user: CurrentUser) -> bool:
    """Inventory Manager/Admin (requirement.fulfill), Manager/Admin
    (requirement.approve), and read-only roles (requirement.read) see every
    fulfillment. A user holding only requirement.create falls through to the
    branch/own-record scoping in _scope_filter() below."""
    return any(p in current_user.permissions for p in (REQUIREMENT_APPROVE, REQUIREMENT_READ, REQUIREMENT_FULFILL))


def _enforce_own_scope(db: Session, fulfillment: FulfillmentRequest, current_user: CurrentUser) -> None:
    """Direct-ID guard matching the list scoping below.

    Branch-scope hardening (F8/F11): a UNIT_HEAD/TL is a *branch* operator, not a
    personal-submissions operator — their dashboard and lists must show the whole
    assigned branch's fulfillment activity, not only requirements they happened to
    raise themselves. So branch_scope_id() is consulted FIRST (same precedence as
    routers/requirements.py), and the requester_id fallback now only covers a
    branch-scoped role with no branch assigned, or some future create-only role that
    isn't branch-scoped at all — never granting wider visibility than before.

    This is also the F11 guard: even if a Super Admin later grants a branch role
    requirement.read/approve/fulfill through the Role & Permissions screen,
    _has_broad_visibility() would return True, so the branch check is deliberately
    evaluated BEFORE it — a branch-scoped user never escapes their branch here."""
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        requirement = db.get(Requirement, fulfillment.requirement_id)
        if requirement is None or requirement.branch_id != scoped_branch_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fulfillment not found")
        return
    if _has_broad_visibility(current_user):
        return
    requirement = db.get(Requirement, fulfillment.requirement_id)
    if requirement is None or requirement.requester_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fulfillment not found")


def _map_service_error(exc: FulfillmentServiceError) -> HTTPException:
    if isinstance(exc, FulfillmentReferenceError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, (FulfillmentStateError, FulfillmentAvailabilityError)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("", response_model=Page[FulfillmentOut])
def list_fulfillments(
    page: int = 1, page_size: int = 25,
    search: str | None = None,
    status_filter: str | None = None,
    branch_id: str | None = None,
    department_id: str | None = None,
    requester_id: str | None = None,
    priority: str | None = None,
    inventory_manager_id: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_READ_ACCESS),
):
    query = db.query(FulfillmentRequest).join(Requirement, FulfillmentRequest.requirement_id == Requirement.id)

    if status_filter:
        query = query.filter(FulfillmentRequest.status == status_filter)
    if inventory_manager_id:
        query = query.filter(FulfillmentRequest.inventory_manager_id == inventory_manager_id)
    if branch_id:
        query = query.filter(Requirement.branch_id == branch_id)
    if department_id:
        query = query.filter(Requirement.department_id == department_id)
    if requester_id:
        query = query.filter(Requirement.requester_id == requester_id)
    if priority:
        query = query.filter(Requirement.priority == priority)
    if search:
        like = f"%{search}%"
        query = query.filter(or_(FulfillmentRequest.fulfillment_number.ilike(like), Requirement.request_number.ilike(like)))
    if created_after:
        query = query.filter(FulfillmentRequest.created_at >= datetime.fromisoformat(created_after))
    if created_before:
        query = query.filter(FulfillmentRequest.created_at <= datetime.fromisoformat(created_before))

    # Branch scope takes precedence over _has_broad_visibility (see _enforce_own_scope):
    # a UNIT_HEAD/TL sees their whole branch's fulfillments, and can never widen past it
    # even if granted requirement.read/approve/fulfill later.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        query = query.filter(Requirement.branch_id == scoped_branch_id)
    elif not _has_broad_visibility(current_user):
        query = query.filter(Requirement.requester_id == current_user.id)

    total = query.count()
    rows = query.order_by(FulfillmentRequest.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    mask_stock = _mask_stock(current_user)
    items = [fulfillment_service.to_fulfillment_out(db, row, mask_stock=mask_stock) for row in rows]
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=FulfillmentOut, status_code=status.HTTP_201_CREATED)
def create_fulfillment(payload: FulfillmentCreateIn, db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(_FULFILL_ACCESS)):
    try:
        fulfillment = fulfillment_service.create_fulfillment(db, payload, user_id=current_user.id)
    except FulfillmentServiceError as exc:
        raise _map_service_error(exc)
    return fulfillment_service.to_fulfillment_out(db, fulfillment)


@router.get("/{fulfillment_id}", response_model=FulfillmentOut)
def get_fulfillment(fulfillment_id: str, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(_READ_ACCESS)):
    fulfillment = _get_or_404(db, fulfillment_id)
    _enforce_own_scope(db, fulfillment, current_user)
    return fulfillment_service.to_fulfillment_out(db, fulfillment, mask_stock=_mask_stock(current_user))


@router.get("/{fulfillment_id}/availability", response_model=FulfillmentOut)
def get_fulfillment_availability(fulfillment_id: str, db: Session = Depends(get_db),
                                  current_user: CurrentUser = Depends(_READ_ACCESS)):
    """Read-only 'inventory availability check' stage — same shape as GET /{id}, since
    availability is already computed live on every item in that response; kept as a
    separate named endpoint so the check is independently callable/refreshable
    without re-fetching the full detail semantics."""
    fulfillment = _get_or_404(db, fulfillment_id)
    _enforce_own_scope(db, fulfillment, current_user)
    return fulfillment_service.to_fulfillment_out(db, fulfillment, mask_stock=_mask_stock(current_user))


@router.get("/{fulfillment_id}/history", response_model=list[FulfillmentStatusHistoryOut])
def get_fulfillment_history(fulfillment_id: str, db: Session = Depends(get_db),
                             current_user: CurrentUser = Depends(_READ_ACCESS)):
    fulfillment = _get_or_404(db, fulfillment_id)
    _enforce_own_scope(db, fulfillment, current_user)
    return (
        db.query(FulfillmentStatusHistory)
        .filter(FulfillmentStatusHistory.fulfillment_id == fulfillment_id)
        .order_by(FulfillmentStatusHistory.created_at)
        .all()
    )


@router.post("/{fulfillment_id}/allocate", response_model=FulfillmentOut)
def allocate_fulfillment(fulfillment_id: str, payload: FulfillmentAllocateIn, db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(_FULFILL_ACCESS)):
    fulfillment = _get_or_404(db, fulfillment_id)
    try:
        fulfillment = fulfillment_service.allocate_fulfillment(db, fulfillment, payload, user_id=current_user.id)
    except FulfillmentServiceError as exc:
        raise _map_service_error(exc)
    return fulfillment_service.to_fulfillment_out(db, fulfillment)


@router.post("/{fulfillment_id}/process", response_model=FulfillmentOut)
def process_fulfillment(fulfillment_id: str, db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(_FULFILL_ACCESS)):
    fulfillment = _get_or_404(db, fulfillment_id)
    try:
        fulfillment = fulfillment_service.process_fulfillment(db, fulfillment, user_id=current_user.id)
    except FulfillmentServiceError as exc:
        raise _map_service_error(exc)
    return fulfillment_service.to_fulfillment_out(db, fulfillment)


@router.post("/{fulfillment_id}/pack", response_model=FulfillmentOut)
def pack_fulfillment(fulfillment_id: str, db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(_FULFILL_ACCESS)):
    fulfillment = _get_or_404(db, fulfillment_id)
    try:
        fulfillment = fulfillment_service.pack_fulfillment(db, fulfillment, user_id=current_user.id)
    except FulfillmentServiceError as exc:
        raise _map_service_error(exc)
    return fulfillment_service.to_fulfillment_out(db, fulfillment)


@router.post("/{fulfillment_id}/out-for-delivery", response_model=FulfillmentOut)
def dispatch_fulfillment(fulfillment_id: str, db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(_FULFILL_ACCESS)):
    fulfillment = _get_or_404(db, fulfillment_id)
    try:
        fulfillment = fulfillment_service.dispatch_fulfillment(db, fulfillment, user_id=current_user.id)
    except FulfillmentServiceError as exc:
        raise _map_service_error(exc)
    return fulfillment_service.to_fulfillment_out(db, fulfillment)


@router.post("/{fulfillment_id}/deliver", response_model=FulfillmentOut)
def deliver_fulfillment(fulfillment_id: str, payload: FulfillmentDeliverIn = FulfillmentDeliverIn(),
                         db: Session = Depends(get_db), current_user: CurrentUser = Depends(_FULFILL_ACCESS)):
    fulfillment = _get_or_404(db, fulfillment_id)
    try:
        fulfillment = fulfillment_service.deliver_fulfillment(db, fulfillment, payload, user_id=current_user.id)
    except FulfillmentServiceError as exc:
        raise _map_service_error(exc)
    return fulfillment_service.to_fulfillment_out(db, fulfillment)


@router.post("/{fulfillment_id}/complete", response_model=FulfillmentOut)
def complete_fulfillment(fulfillment_id: str, db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(_FULFILL_ACCESS)):
    fulfillment = _get_or_404(db, fulfillment_id)
    try:
        fulfillment = fulfillment_service.complete_fulfillment(db, fulfillment, user_id=current_user.id)
    except FulfillmentServiceError as exc:
        raise _map_service_error(exc)
    return fulfillment_service.to_fulfillment_out(db, fulfillment)


@router.post("/{fulfillment_id}/cancel", response_model=FulfillmentOut)
def cancel_fulfillment(fulfillment_id: str, payload: FulfillmentCancelIn = FulfillmentCancelIn(),
                        db: Session = Depends(get_db), current_user: CurrentUser = Depends(_FULFILL_ACCESS)):
    fulfillment = _get_or_404(db, fulfillment_id)
    try:
        fulfillment = fulfillment_service.cancel_fulfillment(db, fulfillment, payload, user_id=current_user.id)
    except FulfillmentServiceError as exc:
        raise _map_service_error(exc)
    return fulfillment_service.to_fulfillment_out(db, fulfillment)


@router.delete("/{fulfillment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fulfillment(fulfillment_id: str, force: bool = False, db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(_FULFILL_ACCESS)):
    """No non-force hard delete exists here — /cancel is the normal archive path for
    a fulfillment. ``force=true`` (Super Admin only, gated on DEV_DATA_CLEANUP) is the
    one escape hatch, purging the fulfillment and its items/asset links/status
    history via the generic FK-graph cascade (force_delete_row)."""
    fulfillment = _get_or_404(db, fulfillment_id)
    if not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Fulfillments are never hard-deleted directly — cancel it instead, or use force-delete (Super Admin).",
        )
    if DEV_DATA_CLEANUP not in current_user.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Force-delete requires Super Admin (dev.data_cleanup permission)",
        )
    assert_dev_environment()
    deleted_count = force_delete_row(db, "fulfillment_requests", fulfillment.id)
    audit_record(
        db, user_id=current_user.id, action="FORCE_DELETE", entity="fulfillment", entity_id=fulfillment_id,
        new_value={"cascaded_row_count": deleted_count},
    )
    db.commit()
