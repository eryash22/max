from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies.auth import require_any_permission, require_permission
from ..models.hospital import Branch
from ..models.requirement import Requirement, RequirementItem, RequirementStatusHistory
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.requirement import (
    RequirementCancelIn,
    RequirementDecisionIn,
    RequirementIn,
    RequirementItemOut,
    RequirementOut,
    RequirementStatusHistoryOut,
    RequirementUpdateIn,
)
from ..security.permissions import (
    DEV_DATA_CLEANUP, REQUIREMENT_APPROVE, REQUIREMENT_CREATE, REQUIREMENT_DELETE, REQUIREMENT_READ, branch_scope_id,
)
from ..services import requirement_service
from ..audit.service import record as audit_record
from ..services.force_delete import assert_dev_environment, force_delete_row
from ..services.requirement_service import (
    RequirementPermissionError,
    RequirementReferenceError,
    RequirementServiceError,
    RequirementStateError,
)

router = APIRouter(prefix="/api/v1/requirements", tags=["requirements"])

_READ_ACCESS = require_any_permission(REQUIREMENT_CREATE, REQUIREMENT_READ, REQUIREMENT_APPROVE)


def _get_or_404(db: Session, requirement_id: str) -> Requirement:
    requirement = db.get(Requirement, requirement_id)
    if requirement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found")
    return requirement


def _has_broad_visibility(current_user: CurrentUser) -> bool:
    """Manager/Admin (requirement.approve) and read-only roles (Inventory Manager,
    Viewer, requirement.read) see every requirement. A user holding only
    requirement.create (Hospital TL / Unit Head) is scoped to their own — same
    own-vs-broad split as Incidents' INCIDENT_MANAGE check."""
    return REQUIREMENT_APPROVE in current_user.permissions or REQUIREMENT_READ in current_user.permissions


def _enforce_own_scope(requirement: Requirement, current_user: CurrentUser) -> None:
    """F11: the branch check runs BEFORE the broad-visibility check, so a branch-scoped
    role granted requirement.read/approve/fulfill still cannot reach another branch's
    requirement by id. Mirrors the same precedence in list_requirements()."""
    branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if branch_id is not None:
        if requirement.branch_id != branch_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found")
        return
    if _has_broad_visibility(current_user):
        return
    if requirement.requester_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found")


def _enforce_can_edit(requirement: Requirement, current_user: CurrentUser) -> None:
    """Only the requester themselves, or a holder of requirement.approve (manager/admin
    override), may edit/submit/cancel a requirement they didn't raise."""
    if REQUIREMENT_APPROVE in current_user.permissions:
        return
    if requirement.requester_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found")


def _map_service_error(exc: RequirementServiceError) -> HTTPException:
    if isinstance(exc, RequirementReferenceError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, RequirementStateError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, RequirementPermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("", response_model=Page[RequirementOut])
def list_requirements(
    page: int = 1, page_size: int = 25,
    search: str | None = None,
    branch_id: str | None = None,
    department_id: str | None = None,
    requester_id: str | None = None,
    manager_id: str | None = None,
    status_filter: str | None = None,
    priority: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_READ_ACCESS),
):
    query = db.query(Requirement)

    exact_filters = {
        "branch_id": branch_id, "department_id": department_id,
        "requester_id": requester_id, "manager_id": manager_id, "priority": priority,
    }
    if status_filter:
        exact_filters["status"] = status_filter
    for field, value in exact_filters.items():
        if value is not None:
            query = query.filter(getattr(Requirement, field) == value)

    if search:
        like = f"%{search}%"
        query = query.filter(or_(Requirement.reason.ilike(like), Requirement.request_number.ilike(like)))

    if created_after:
        query = query.filter(Requirement.created_at >= datetime.fromisoformat(created_after))
    if created_before:
        query = query.filter(Requirement.created_at <= datetime.fromisoformat(created_before))

    # F11: branch scope is evaluated BEFORE _has_broad_visibility, not inside it.
    # Previously a branch role granted requirement.read/approve/fulfill from the Role &
    # Permissions screen skipped the branch filter entirely and read every branch's
    # requirements — "this role lacks the permission" was doing the isolation work.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        query = query.filter(Requirement.branch_id == scoped_branch_id)
    elif not _has_broad_visibility(current_user):
        query = query.filter(Requirement.requester_id == current_user.id)

    total = query.count()
    items = query.order_by(Requirement.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=RequirementOut, status_code=status.HTTP_201_CREATED)
def create_requirement(payload: RequirementIn, db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(require_permission(REQUIREMENT_CREATE))):
    if db.get(Branch, payload.branch_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
    # Branch-scoping hardening pass — root cause of the "Requirement not found" bug:
    # payload.branch_id was accepted from the client with no check that it matched the
    # UNIT_HEAD/TL requester's own assigned branch (User.hospital_scope_id). A caller
    # could create a requirement tagged to another branch, get a 201 back, then
    # immediately 404 on GET /{id} because _enforce_own_scope only ever shows them
    # their own branch's requirements. Rejecting the mismatch up front (rather than
    # silently overriding it) makes a tampered/stale client payload fail loudly at
    # create time instead of surfacing as a confusing "not found" one request later.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and payload.branch_id != scoped_branch_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only raise a requirement for your own assigned branch.",
        )
    requester_role = current_user.roles[0] if current_user.roles else None
    try:
        return requirement_service.create_requirement(
            db, payload, requester_id=current_user.id, requester_role=requester_role,
        )
    except RequirementServiceError as exc:
        raise _map_service_error(exc)


@router.get("/{requirement_id}", response_model=RequirementOut)
def get_requirement(requirement_id: str, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(_READ_ACCESS)):
    requirement = _get_or_404(db, requirement_id)
    _enforce_own_scope(requirement, current_user)
    return requirement


@router.get("/{requirement_id}/items", response_model=list[RequirementItemOut])
def list_requirement_items(requirement_id: str, db: Session = Depends(get_db),
                            current_user: CurrentUser = Depends(_READ_ACCESS)):
    requirement = _get_or_404(db, requirement_id)
    _enforce_own_scope(requirement, current_user)
    return (
        db.query(RequirementItem)
        .filter(RequirementItem.requirement_id == requirement_id)
        .order_by(RequirementItem.created_at)
        .all()
    )


@router.get("/{requirement_id}/history", response_model=list[RequirementStatusHistoryOut])
def get_requirement_history(requirement_id: str, db: Session = Depends(get_db),
                             current_user: CurrentUser = Depends(_READ_ACCESS)):
    requirement = _get_or_404(db, requirement_id)
    _enforce_own_scope(requirement, current_user)
    return (
        db.query(RequirementStatusHistory)
        .filter(RequirementStatusHistory.requirement_id == requirement_id)
        .order_by(RequirementStatusHistory.created_at)
        .all()
    )


@router.patch("/{requirement_id}", response_model=RequirementOut)
def update_requirement(requirement_id: str, payload: RequirementUpdateIn, db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(require_any_permission(REQUIREMENT_CREATE, REQUIREMENT_APPROVE))):
    requirement = _get_or_404(db, requirement_id)
    _enforce_can_edit(requirement, current_user)
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and payload.branch_id is not None and payload.branch_id != scoped_branch_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit a requirement to stay on your own assigned branch.",
        )
    try:
        return requirement_service.update_requirement(db, requirement, payload, user_id=current_user.id)
    except RequirementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/{requirement_id}/submit", response_model=RequirementOut)
def submit_requirement(requirement_id: str, db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(require_any_permission(REQUIREMENT_CREATE, REQUIREMENT_APPROVE))):
    requirement = _get_or_404(db, requirement_id)
    _enforce_can_edit(requirement, current_user)
    try:
        return requirement_service.submit_requirement(db, requirement, user_id=current_user.id)
    except RequirementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/{requirement_id}/approve", response_model=RequirementOut)
def approve_requirement(requirement_id: str, payload: RequirementDecisionIn = RequirementDecisionIn(),
                         db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(require_permission(REQUIREMENT_APPROVE))):
    requirement = _get_or_404(db, requirement_id)
    try:
        return requirement_service.approve_requirement(db, requirement, payload, manager_id=current_user.id)
    except RequirementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/{requirement_id}/reject", response_model=RequirementOut)
def reject_requirement(requirement_id: str, payload: RequirementDecisionIn = RequirementDecisionIn(),
                        db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(require_permission(REQUIREMENT_APPROVE))):
    requirement = _get_or_404(db, requirement_id)
    try:
        return requirement_service.reject_requirement(db, requirement, payload, manager_id=current_user.id)
    except RequirementServiceError as exc:
        raise _map_service_error(exc)


@router.delete("/{requirement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_requirement(requirement_id: str, force: bool = False, db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(require_permission(REQUIREMENT_DELETE))):
    """Final admin/inventory/vendor/dashboard polish pass — Part 7: Super Admin-only
    hard delete, gated on requirement.delete (SUPER_ADMIN via ALL_PERMISSIONS, no other
    default role). Self-approval blocking in approve_requirement/reject_requirement is
    untouched by this endpoint. Only a DRAFT requirement with no linked
    FulfillmentRequest can go through the normal path here — any other status must use
    /cancel instead.

    ``force=true`` (also gated on DEV_DATA_CLEANUP, same convention as every other
    force-delete) bypasses both the status and reference restrictions via the generic
    FK-graph cascade (force_delete_row) — including any linked FulfillmentRequest and
    its own items/history."""
    requirement = _get_or_404(db, requirement_id)
    if force:
        if DEV_DATA_CLEANUP not in current_user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Force-delete requires Super Admin (dev.data_cleanup permission)",
            )
        assert_dev_environment()
        deleted_count = force_delete_row(db, "requirements", requirement.id)
        audit_record(
            db, user_id=current_user.id, action="FORCE_DELETE", entity="requirement", entity_id=requirement_id,
            new_value={"cascaded_row_count": deleted_count},
        )
        db.commit()
        return None
    try:
        requirement_service.delete_requirement(db, requirement, user_id=current_user.id)
    except RequirementServiceError as exc:
        raise _map_service_error(exc)
    return None


@router.post("/{requirement_id}/cancel", response_model=RequirementOut)
def cancel_requirement(requirement_id: str, payload: RequirementCancelIn = RequirementCancelIn(),
                        db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(require_any_permission(REQUIREMENT_CREATE, REQUIREMENT_APPROVE))):
    requirement = _get_or_404(db, requirement_id)
    _enforce_can_edit(requirement, current_user)
    try:
        return requirement_service.cancel_requirement(db, requirement, payload, user_id=current_user.id)
    except RequirementServiceError as exc:
        raise _map_service_error(exc)
