import calendar
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..models.maintenance import PMRecord, PMSchedule
from ..schemas.notification import EVENT_PM_DUE, EVENT_PM_OVERDUE
from ..schemas.pm import PMRecordIn, PMScheduleIn, PMScheduleUpdateIn
from .communication_service import dispatch_maintenance_event_safe

# Window used to classify a schedule as "DUE" (due soon) rather than "UPCOMING".
DUE_SOON_WINDOW_DAYS = 7

_FREQUENCY_MONTHS = {"MONTHLY": 1, "QUARTERLY": 3, "HALF_YEARLY": 6, "YEARLY": 12}


class PMServiceError(Exception):
    pass


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _add_months(base: date, months: int) -> date:
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def compute_next_due_date(from_date: date, frequency: str | None, custom_interval_days: int | None) -> str | None:
    """Calendar-aware due-date projection. MONTHLY/QUARTERLY/HALF_YEARLY/YEARLY add
    whole months (with day-of-month clamped for short months); CUSTOM adds a fixed
    number of days. No frequency means no due date can be projected."""
    if frequency is None:
        return None
    if frequency == "CUSTOM":
        if not custom_interval_days:
            raise PMServiceError("custom_interval_days is required when frequency is CUSTOM")
        return (from_date + timedelta(days=custom_interval_days)).isoformat()
    months = _FREQUENCY_MONTHS.get(frequency)
    if months is None:
        raise PMServiceError(f"Unknown frequency: {frequency}")
    return _add_months(from_date, months).isoformat()


def compute_due_status(next_due_date: str | None, *, today: date | None = None) -> str:
    if not next_due_date:
        return "NOT_SCHEDULED"
    today = today or _today()
    due = date.fromisoformat(next_due_date)
    if due < today:
        return "OVERDUE"
    if due <= today + timedelta(days=DUE_SOON_WINDOW_DAYS):
        return "DUE"
    return "UPCOMING"


def _validate_custom_interval(frequency: str | None, custom_interval_days: int | None) -> None:
    if frequency == "CUSTOM" and not custom_interval_days:
        raise PMServiceError("custom_interval_days is required when frequency is CUSTOM")


def _dispatch_due_status_event(db: Session, schedule: PMSchedule, *, user_id: str) -> None:
    """Fires PM_DUE/PM_OVERDUE against the schedule's freshly (re)computed due_status.
    Never called for UPCOMING/NOT_SCHEDULED — those are not actionable events."""
    status = compute_due_status(schedule.next_due_date)
    if status == "DUE":
        dispatch_maintenance_event_safe(db, EVENT_PM_DUE, entity_type="pm_schedule", schedule=schedule, due_status=status, user_id=user_id)
    elif status == "OVERDUE":
        dispatch_maintenance_event_safe(db, EVENT_PM_OVERDUE, entity_type="pm_schedule", schedule=schedule, due_status=status, user_id=user_id)


def create_schedule(db: Session, payload: PMScheduleIn, *, user_id: str) -> PMSchedule:
    _validate_custom_interval(payload.frequency, payload.custom_interval_days)
    data = payload.model_dump()
    if data["next_due_date"] is None and data["frequency"]:
        data["next_due_date"] = compute_next_due_date(_today(), data["frequency"], data["custom_interval_days"])

    schedule = PMSchedule(**data)
    db.add(schedule)
    db.flush()
    audit_record(db, user_id=user_id, action="CREATE", entity="pm_schedule", entity_id=schedule.id, new_value=data)
    db.commit()
    db.refresh(schedule)
    _dispatch_due_status_event(db, schedule, user_id=user_id)
    return schedule


def update_schedule(db: Session, schedule: PMSchedule, payload: PMScheduleUpdateIn, *, user_id: str) -> PMSchedule:
    before = {c.name: getattr(schedule, c.name) for c in schedule.__table__.columns}
    changes = payload.model_dump(exclude_unset=True)

    final_frequency = changes.get("frequency", schedule.frequency)
    final_interval = changes.get("custom_interval_days", schedule.custom_interval_days)
    _validate_custom_interval(final_frequency, final_interval)

    for field, value in changes.items():
        setattr(schedule, field, value)

    audit_record(db, user_id=user_id, action="UPDATE", entity="pm_schedule", entity_id=schedule.id,
                 old_value=before, new_value=changes)
    db.commit()
    db.refresh(schedule)
    if "next_due_date" in changes:
        _dispatch_due_status_event(db, schedule, user_id=user_id)
    return schedule


def delete_schedule(db: Session, schedule: PMSchedule, *, user_id: str) -> None:
    before = {c.name: getattr(schedule, c.name) for c in schedule.__table__.columns}
    audit_record(db, user_id=user_id, action="DELETE", entity="pm_schedule", entity_id=schedule.id, old_value=before)
    db.delete(schedule)
    db.commit()


def create_record(db: Session, payload: PMRecordIn, schedule: PMSchedule | None, *, user_id: str) -> PMRecord:
    data = payload.model_dump()
    if data["performed_date"] is None:
        data["performed_date"] = _today().isoformat()

    if data["next_due_date"] is None and schedule is not None and schedule.frequency:
        data["next_due_date"] = compute_next_due_date(
            date.fromisoformat(data["performed_date"]), schedule.frequency, schedule.custom_interval_days
        )

    record = PMRecord(**data)
    db.add(record)
    db.flush()
    audit_record(db, user_id=user_id, action="CREATE", entity="pm_record", entity_id=record.id, new_value=data)

    if schedule is not None and record.next_due_date:
        schedule_before = {"next_due_date": schedule.next_due_date}
        schedule.next_due_date = record.next_due_date
        audit_record(db, user_id=user_id, action="UPDATE", entity="pm_schedule", entity_id=schedule.id,
                     old_value=schedule_before, new_value={"next_due_date": schedule.next_due_date})

    db.commit()
    db.refresh(record)
    if schedule is not None and record.next_due_date:
        db.refresh(schedule)
        _dispatch_due_status_event(db, schedule, user_id=user_id)
    return record
