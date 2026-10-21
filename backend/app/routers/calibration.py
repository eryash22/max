from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.device import Asset
from ..models.incident import Incident
from ..models.maintenance import CalibrationRecord, CalibrationSchedule
from ..schemas.auth import CurrentUser
from ..schemas.calibration import (
    DUE_STATUSES,
    CalibrationRecordIn,
    CalibrationRecordOut,
    CalibrationScheduleIn,
    CalibrationScheduleOut,
    CalibrationScheduleUpdateIn,
)
from ..schemas.common import Page
from ..security.permissions import CALIBRATION_READ, CALIBRATION_WRITE, branch_scope_id
from ..services.calibration_service import (
    DUE_SOON_WINDOW_DAYS,
    CalibrationServiceError,
    _today,
    compute_due_status,
    create_record,
    create_schedule,
    delete_schedule,
    update_schedule,
)

router = APIRouter(prefix="/api/v1/calibration", tags=["calibration"])


def _get_asset_or_404(db: Session, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return asset


def _get_schedule_or_404(db: Session, schedule_id: str) -> CalibrationSchedule:
    schedule = db.get(CalibrationSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calibration schedule not found")
    return schedule


def _with_due_status(schedule: CalibrationSchedule) -> CalibrationSchedule:
    schedule.due_status = compute_due_status(schedule.next_due_date)
    return schedule


def _enforce_calibration_branch_scope(db: Session, asset_id: str, current_user: CurrentUser, label: str) -> None:
    """Branch isolation for Calibration, enforced through the schedule/record's asset.

    CalibrationSchedule and CalibrationRecord both carry a non-nullable `asset_id` and
    no branch_id, so `Asset.branch_id` is the canonical branch relationship — identical
    reasoning to routers/pm.py:_enforce_pm_branch_scope.

    Enforced in the request path rather than relying on UNIT_HEAD/TL not currently
    holding calibration.read: role permissions are DB-driven and grantable at runtime,
    so the absence of a grant is not a branch-isolation boundary."""
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is None:
        return
    asset = db.get(Asset, asset_id)
    if asset is None or asset.branch_id != scoped_branch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")


# ---- Schedules ----

@router.get("/schedules", response_model=Page[CalibrationScheduleOut])
def list_calibration_schedules(
    page: int = 1, page_size: int = 25,
    asset_id: str | None = None,
    frequency: str | None = None,
    is_active: bool | None = None,
    due_status: str | None = None,
    # Workflow consolidation pass — Branch detail hub's "Calibration due" tile filters
    # schedules via the asset's branch, same join pattern the hub already uses for
    # /assets?branch_id=.
    branch_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(CALIBRATION_READ)),
):
    if due_status is not None and due_status not in DUE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                             detail=f"due_status must be one of: {', '.join(DUE_STATUSES)}")

    # A branch-scoped caller (UNIT_HEAD/TL) only ever sees their own branch's schedules,
    # whatever branch_id they pass — same override convention as service_visits.py.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        branch_id = scoped_branch_id

    query = db.query(CalibrationSchedule)
    exact_filters = {"asset_id": asset_id, "frequency": frequency, "is_active": is_active}
    for field, value in exact_filters.items():
        if value is not None:
            query = query.filter(getattr(CalibrationSchedule, field) == value)
    if branch_id:
        query = query.join(Asset, Asset.id == CalibrationSchedule.asset_id).filter(Asset.branch_id == branch_id)

    today_str = _today().isoformat()
    due_before_str = (_today() + timedelta(days=DUE_SOON_WINDOW_DAYS)).isoformat()
    if due_status == "NOT_SCHEDULED":
        query = query.filter(CalibrationSchedule.next_due_date.is_(None))
    elif due_status == "OVERDUE":
        query = query.filter(CalibrationSchedule.next_due_date.isnot(None), CalibrationSchedule.next_due_date < today_str)
    elif due_status == "DUE":
        query = query.filter(CalibrationSchedule.next_due_date.isnot(None), CalibrationSchedule.next_due_date >= today_str,
                              CalibrationSchedule.next_due_date <= due_before_str)
    elif due_status == "UPCOMING":
        query = query.filter(CalibrationSchedule.next_due_date.isnot(None), CalibrationSchedule.next_due_date > due_before_str)

    total = query.count()
    items = query.order_by(CalibrationSchedule.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [_with_due_status(item) for item in items]
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/schedules", response_model=CalibrationScheduleOut, status_code=status.HTTP_201_CREATED)
def create_calibration_schedule(payload: CalibrationScheduleIn, db: Session = Depends(get_db),
                                 current_user: CurrentUser = Depends(require_permission(CALIBRATION_WRITE))):
    _get_asset_or_404(db, payload.asset_id)
    _enforce_calibration_branch_scope(db, payload.asset_id, current_user, "Asset")
    try:
        schedule = create_schedule(db, payload, user_id=current_user.id)
    except CalibrationServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _with_due_status(schedule)


@router.get("/schedules/{schedule_id}", response_model=CalibrationScheduleOut)
def get_calibration_schedule(schedule_id: str, db: Session = Depends(get_db),
                              current_user: CurrentUser = Depends(require_permission(CALIBRATION_READ))):
    schedule = _get_schedule_or_404(db, schedule_id)
    _enforce_calibration_branch_scope(db, schedule.asset_id, current_user, "Calibration schedule")
    return _with_due_status(schedule)


@router.patch("/schedules/{schedule_id}", response_model=CalibrationScheduleOut)
def patch_calibration_schedule(schedule_id: str, payload: CalibrationScheduleUpdateIn, db: Session = Depends(get_db),
                                current_user: CurrentUser = Depends(require_permission(CALIBRATION_WRITE))):
    schedule = _get_schedule_or_404(db, schedule_id)
    _enforce_calibration_branch_scope(db, schedule.asset_id, current_user, "Calibration schedule")
    try:
        schedule = update_schedule(db, schedule, payload, user_id=current_user.id)
    except CalibrationServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _with_due_status(schedule)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_calibration_schedule(schedule_id: str, db: Session = Depends(get_db),
                                 current_user: CurrentUser = Depends(require_permission(CALIBRATION_WRITE))):
    schedule = _get_schedule_or_404(db, schedule_id)
    _enforce_calibration_branch_scope(db, schedule.asset_id, current_user, "Calibration schedule")
    delete_schedule(db, schedule, user_id=current_user.id)
    return None


# ---- Records ----

@router.get("/records", response_model=Page[CalibrationRecordOut])
def list_calibration_records(
    page: int = 1, page_size: int = 25,
    asset_id: str | None = None,
    schedule_id: str | None = None,
    pass_fail: str | None = None,
    # Workflow consolidation pass — Branch detail hub filters Calibration records via the
    # asset's branch, same join pattern the hub already uses for /assets?branch_id=.
    branch_id: str | None = None,
    performed_after: str | None = None,
    performed_before: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(CALIBRATION_READ)),
):
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        branch_id = scoped_branch_id

    query = db.query(CalibrationRecord)
    exact_filters = {"asset_id": asset_id, "schedule_id": schedule_id, "pass_fail": pass_fail}
    for field, value in exact_filters.items():
        if value is not None:
            query = query.filter(getattr(CalibrationRecord, field) == value)
    if branch_id:
        query = query.join(Asset, Asset.id == CalibrationRecord.asset_id).filter(Asset.branch_id == branch_id)
    if performed_after:
        query = query.filter(CalibrationRecord.performed_date >= performed_after)
    if performed_before:
        query = query.filter(CalibrationRecord.performed_date <= performed_before)

    total = query.count()
    items = query.order_by(CalibrationRecord.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/records", response_model=CalibrationRecordOut, status_code=status.HTTP_201_CREATED)
def create_calibration_record(payload: CalibrationRecordIn, db: Session = Depends(get_db),
                               current_user: CurrentUser = Depends(require_permission(CALIBRATION_WRITE))):
    _get_asset_or_404(db, payload.asset_id)
    _enforce_calibration_branch_scope(db, payload.asset_id, current_user, "Asset")
    schedule = None
    if payload.schedule_id is not None:
        schedule = _get_schedule_or_404(db, payload.schedule_id)
    if payload.incident_id is not None and db.get(Incident, payload.incident_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    try:
        record = create_record(db, payload, schedule, user_id=current_user.id)
    except CalibrationServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return record


@router.get("/records/{record_id}", response_model=CalibrationRecordOut)
def get_calibration_record(record_id: str, db: Session = Depends(get_db),
                            current_user: CurrentUser = Depends(require_permission(CALIBRATION_READ))):
    record = db.get(CalibrationRecord, record_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calibration record not found")
    _enforce_calibration_branch_scope(db, record.asset_id, current_user, "Calibration record")
    return record
