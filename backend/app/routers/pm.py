from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.device import Asset
from ..models.identity import User
from ..models.incident import Incident
from ..models.maintenance import PMRecord, PMSchedule
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.pm import (
    DUE_STATUSES,
    PMRecordIn,
    PMRecordOut,
    PMScheduleIn,
    PMScheduleOut,
    PMScheduleUpdateIn,
)
from ..security.permissions import PM_READ, PM_WRITE, branch_scope_id
from ..services.pm_service import (
    DUE_SOON_WINDOW_DAYS,
    PMServiceError,
    _today,
    compute_due_status,
    create_record,
    create_schedule,
    delete_schedule,
    update_schedule,
)

router = APIRouter(prefix="/api/v1/pm", tags=["preventive-maintenance"])


def _get_asset_or_404(db: Session, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return asset


def _get_schedule_or_404(db: Session, schedule_id: str) -> PMSchedule:
    schedule = db.get(PMSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PM schedule not found")
    return schedule


def _with_due_status(schedule: PMSchedule) -> PMSchedule:
    schedule.due_status = compute_due_status(schedule.next_due_date)
    return schedule


def _enforce_pm_branch_scope(db: Session, asset_id: str, current_user: CurrentUser, label: str) -> None:
    """Branch isolation for PM, enforced through the asset the schedule/record hangs off.

    PMSchedule and PMRecord both carry a non-nullable `asset_id` and no branch_id of
    their own, so `Asset.branch_id` is the canonical branch relationship — the same
    join routers/service_visits.py already uses for ServiceVisit.

    Why this exists even though UNIT_HEAD/TL don't currently hold pm.read: role
    permissions are DB-driven and a Super Admin can grant pm.read to any role from the
    Role & Permissions screen at any time. "This role happens to lack the permission"
    is not a branch-isolation boundary, so the scope is enforced in the request path
    itself rather than relying on the current grant table."""
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is None:
        return
    asset = db.get(Asset, asset_id)
    if asset is None or asset.branch_id != scoped_branch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")


# ---- Schedules ----

@router.get("/schedules", response_model=Page[PMScheduleOut])
def list_pm_schedules(
    page: int = 1, page_size: int = 25,
    asset_id: str | None = None,
    frequency: str | None = None,
    is_active: bool | None = None,
    due_status: str | None = None,
    # Workflow consolidation pass — Branch detail hub's "PM due" tile filters schedules
    # via the asset's branch, same join pattern the hub already uses for /assets?branch_id=.
    branch_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(PM_READ)),
):
    if due_status is not None and due_status not in DUE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                             detail=f"due_status must be one of: {', '.join(DUE_STATUSES)}")

    # A branch-scoped caller (UNIT_HEAD/TL) only ever sees their own branch's schedules,
    # whatever branch_id they pass — same override convention as service_visits.py.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        branch_id = scoped_branch_id

    query = db.query(PMSchedule)
    exact_filters = {"asset_id": asset_id, "frequency": frequency, "is_active": is_active}
    for field, value in exact_filters.items():
        if value is not None:
            query = query.filter(getattr(PMSchedule, field) == value)
    if branch_id:
        query = query.join(Asset, Asset.id == PMSchedule.asset_id).filter(Asset.branch_id == branch_id)

    today_str = _today().isoformat()
    due_before_str = (_today() + timedelta(days=DUE_SOON_WINDOW_DAYS)).isoformat()
    if due_status == "NOT_SCHEDULED":
        query = query.filter(PMSchedule.next_due_date.is_(None))
    elif due_status == "OVERDUE":
        query = query.filter(PMSchedule.next_due_date.isnot(None), PMSchedule.next_due_date < today_str)
    elif due_status == "DUE":
        query = query.filter(PMSchedule.next_due_date.isnot(None), PMSchedule.next_due_date >= today_str,
                              PMSchedule.next_due_date <= due_before_str)
    elif due_status == "UPCOMING":
        query = query.filter(PMSchedule.next_due_date.isnot(None), PMSchedule.next_due_date > due_before_str)

    total = query.count()
    items = query.order_by(PMSchedule.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [_with_due_status(item) for item in items]
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/schedules", response_model=PMScheduleOut, status_code=status.HTTP_201_CREATED)
def create_pm_schedule(payload: PMScheduleIn, db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(require_permission(PM_WRITE))):
    _get_asset_or_404(db, payload.asset_id)
    _enforce_pm_branch_scope(db, payload.asset_id, current_user, "Asset")
    try:
        schedule = create_schedule(db, payload, user_id=current_user.id)
    except PMServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _with_due_status(schedule)


@router.get("/schedules/{schedule_id}", response_model=PMScheduleOut)
def get_pm_schedule(schedule_id: str, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(PM_READ))):
    schedule = _get_schedule_or_404(db, schedule_id)
    _enforce_pm_branch_scope(db, schedule.asset_id, current_user, "PM schedule")
    return _with_due_status(schedule)


@router.patch("/schedules/{schedule_id}", response_model=PMScheduleOut)
def patch_pm_schedule(schedule_id: str, payload: PMScheduleUpdateIn, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(PM_WRITE))):
    schedule = _get_schedule_or_404(db, schedule_id)
    _enforce_pm_branch_scope(db, schedule.asset_id, current_user, "PM schedule")
    try:
        schedule = update_schedule(db, schedule, payload, user_id=current_user.id)
    except PMServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _with_due_status(schedule)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pm_schedule(schedule_id: str, db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(require_permission(PM_WRITE))):
    schedule = _get_schedule_or_404(db, schedule_id)
    _enforce_pm_branch_scope(db, schedule.asset_id, current_user, "PM schedule")
    delete_schedule(db, schedule, user_id=current_user.id)
    return None


# ---- Records ----

@router.get("/records", response_model=Page[PMRecordOut])
def list_pm_records(
    page: int = 1, page_size: int = 25,
    asset_id: str | None = None,
    schedule_id: str | None = None,
    engineer_id: str | None = None,
    # Workflow consolidation pass — Branch detail hub filters PM records via the asset's
    # branch, same join pattern the hub already uses for /assets?branch_id=.
    branch_id: str | None = None,
    performed_after: str | None = None,
    performed_before: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(PM_READ)),
):
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        branch_id = scoped_branch_id

    query = db.query(PMRecord)
    exact_filters = {"asset_id": asset_id, "schedule_id": schedule_id, "engineer_id": engineer_id}
    for field, value in exact_filters.items():
        if value is not None:
            query = query.filter(getattr(PMRecord, field) == value)
    if branch_id:
        query = query.join(Asset, Asset.id == PMRecord.asset_id).filter(Asset.branch_id == branch_id)
    if performed_after:
        query = query.filter(PMRecord.performed_date >= performed_after)
    if performed_before:
        query = query.filter(PMRecord.performed_date <= performed_before)

    total = query.count()
    items = query.order_by(PMRecord.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/records", response_model=PMRecordOut, status_code=status.HTTP_201_CREATED)
def create_pm_record(payload: PMRecordIn, db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(require_permission(PM_WRITE))):
    _get_asset_or_404(db, payload.asset_id)
    _enforce_pm_branch_scope(db, payload.asset_id, current_user, "Asset")
    schedule = None
    if payload.schedule_id is not None:
        schedule = _get_schedule_or_404(db, payload.schedule_id)
    if payload.engineer_id is not None and db.get(User, payload.engineer_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Engineer not found")
    if payload.incident_id is not None and db.get(Incident, payload.incident_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    try:
        record = create_record(db, payload, schedule, user_id=current_user.id)
    except PMServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return record


@router.get("/records/{record_id}", response_model=PMRecordOut)
def get_pm_record(record_id: str, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(PM_READ))):
    record = db.get(PMRecord, record_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PM record not found")
    _enforce_pm_branch_scope(db, record.asset_id, current_user, "PM record")
    return record
