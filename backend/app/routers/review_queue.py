from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.device import BranchDeviceAssignment
from ..models.review import ReviewItem
from ..repositories.crud import paginate
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.review import ReviewActionIn, ReviewItemOut
from ..security.permissions import REVIEW_READ, REVIEW_RESOLVE, branch_scope_id

router = APIRouter(prefix="/api/v1/review-queue", tags=["review-queue"])

# Branch-scoping hardening pass — Review Queue was the one module with zero branch
# scoping at all (every other domain already reuses branch_scope_id, see
# RBAC_MATRIX.md's "Branch-scoped visibility" section). ReviewItem is a generic
# data-quality queue (source_type/source_id points at whatever record triggered it);
# in practice the only source_type ever written is "branch_device_assignment" (see
# services/import_service.py), which itself carries a real branch_id — so branch is
# resolved by looking that row up, not by inventing a new column on ReviewItem. Any
# other source_type has no known branch relationship, so a branch-scoped caller sees
# none of those rows rather than risk leaking one — same "hide what can't be proven
# same-branch" discipline as every other scoped endpoint in this codebase.
_BRANCH_RESOLVABLE_SOURCE_TYPES: dict[str, type] = {
    "branch_device_assignment": BranchDeviceAssignment,
}


def _review_item_branch_id(db: Session, item: ReviewItem) -> str | None:
    model = _BRANCH_RESOLVABLE_SOURCE_TYPES.get(item.source_type)
    if model is None:
        return None
    row = db.get(model, item.source_id)
    return getattr(row, "branch_id", None) if row is not None else None

_ACTION_STATUS = {
    "accept": "ACCEPTED",
    "reject": "REJECTED",
    "resolve": "RESOLVED",
    "defer": "DEFERRED",
}

# Statuses that must never be changed by a plain "accept/reject" bulk action;
# they require an explicit resolve with notes recording how verification happened.
PROTECTED_REASON_CODES = {"LOCATION_ASSET_VERIFICATION_REQUIRED"}


@router.get("", response_model=Page[ReviewItemOut])
def list_review_items(page: int = 1, page_size: int = 25, status_filter: str | None = None,
                       reason_code: str | None = None, source_type: str | None = None,
                       db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(REVIEW_READ))):
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        query = db.query(ReviewItem).join(
            BranchDeviceAssignment,
            (ReviewItem.source_type == "branch_device_assignment")
            & (ReviewItem.source_id == BranchDeviceAssignment.id),
        ).filter(BranchDeviceAssignment.branch_id == scoped_branch_id)
        if reason_code:
            query = query.filter(ReviewItem.reason_code == reason_code)
        if status_filter:
            query = query.filter(ReviewItem.status == status_filter)
        total = query.count()
        items = query.order_by(ReviewItem.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return Page(items=items, total=total, page=page, page_size=page_size)

    filters = {"reason_code": reason_code, "source_type": source_type}
    if status_filter:
        filters["status"] = status_filter
    items, total = paginate(db, ReviewItem, page=page, page_size=page_size, **filters)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/{review_item_id}/action", response_model=ReviewItemOut)
def act_on_review_item(review_item_id: str, payload: ReviewActionIn, db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(require_permission(REVIEW_RESOLVE))):
    item = db.get(ReviewItem, review_item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and _review_item_branch_id(db, item) != scoped_branch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")
    if payload.action not in _ACTION_STATUS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid action")
    if item.reason_code in PROTECTED_REASON_CODES and payload.action == "accept" and not payload.notes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This item requires verification notes before it can be accepted.",
        )

    before = {"status": item.status}
    item.status = _ACTION_STATUS[payload.action]
    item.resolved_by = current_user.id
    item.resolution_notes = payload.notes

    audit_record(
        db, user_id=current_user.id, action=f"REVIEW_{payload.action.upper()}", entity="review_item",
        entity_id=item.id, old_value=before, new_value={"status": item.status, "notes": payload.notes},
    )
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{review_item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_review_item(review_item_id: str, db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(require_permission(REVIEW_RESOLVE))):
    """Review Queue delete — a ReviewItem is only a queue entry pointing at whatever
    record actually triggered it (see _BRANCH_RESOLVABLE_SOURCE_TYPES above); deleting
    it removes the flag, never the underlying business record (e.g. the
    BranchDeviceAssignment it references stays exactly as it is), so this is always a
    safe hard delete — there is no history/audit chain hanging off ReviewItem itself.
    Same REVIEW_RESOLVE permission and branch-scope check as act_on_review_item, so
    this doesn't grant any new capability beyond what resolving an item already does."""
    item = db.get(ReviewItem, review_item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and _review_item_branch_id(db, item) != scoped_branch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")

    audit_record(db, user_id=current_user.id, action="DELETE", entity="review_item", entity_id=item.id,
                 old_value={"status": item.status, "source_type": item.source_type, "source_id": item.source_id})
    db.delete(item)
    db.commit()
