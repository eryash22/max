from datetime import date

from pydantic import BaseModel, field_validator, model_validator

FREQUENCIES = ["MONTHLY", "QUARTERLY", "HALF_YEARLY", "YEARLY", "CUSTOM"]
DUE_STATUSES = ["NOT_SCHEDULED", "OVERDUE", "DUE", "UPCOMING"]
# Final hardening pass — matches the frontend's existing PM_RESULT_OPTIONS dropdown
# (frontend/types/index.ts), the real vocabulary already in use; not invented here.
# PM records are create-only (no update endpoint, see routers/pm.py), so this can
# validate strictly with no "preserve an unchanged legacy value" carve-out needed.
PM_RESULT_OPTIONS = ["PASS", "FAIL", "NOT_APPLICABLE"]


def _validate_iso_date(value: str | None) -> str | None:
    if value is None:
        return value
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("must be an ISO date string (YYYY-MM-DD)")
    return value


class PMScheduleIn(BaseModel):
    asset_id: str
    frequency: str | None = None
    custom_interval_days: int | None = None
    next_due_date: str | None = None
    is_active: bool = True

    @field_validator("frequency")
    @classmethod
    def _valid_frequency(cls, value: str | None) -> str | None:
        if value is not None and value not in FREQUENCIES:
            raise ValueError(f"frequency must be one of {', '.join(FREQUENCIES)}")
        return value

    @field_validator("next_due_date")
    @classmethod
    def _valid_next_due_date(cls, value: str | None) -> str | None:
        return _validate_iso_date(value)

    @field_validator("custom_interval_days")
    @classmethod
    def _positive_interval(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("custom_interval_days must be positive")
        return value

    @model_validator(mode="after")
    def _custom_requires_interval(self) -> "PMScheduleIn":
        if self.frequency == "CUSTOM" and not self.custom_interval_days:
            raise ValueError("custom_interval_days is required when frequency is CUSTOM")
        return self


class PMScheduleUpdateIn(BaseModel):
    frequency: str | None = None
    custom_interval_days: int | None = None
    next_due_date: str | None = None
    is_active: bool | None = None

    @field_validator("frequency")
    @classmethod
    def _valid_frequency(cls, value: str | None) -> str | None:
        if value is not None and value not in FREQUENCIES:
            raise ValueError(f"frequency must be one of {', '.join(FREQUENCIES)}")
        return value

    @field_validator("next_due_date")
    @classmethod
    def _valid_next_due_date(cls, value: str | None) -> str | None:
        return _validate_iso_date(value)

    @field_validator("custom_interval_days")
    @classmethod
    def _positive_interval(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("custom_interval_days must be positive")
        return value


class PMScheduleOut(BaseModel):
    id: str
    asset_id: str
    frequency: str | None = None
    custom_interval_days: int | None = None
    next_due_date: str | None = None
    is_active: bool
    due_status: str

    model_config = {"from_attributes": True}


class PMRecordIn(BaseModel):
    schedule_id: str | None = None
    asset_id: str
    engineer_id: str | None = None
    # Workflow consolidation pass — optional back-reference when this record was created
    # from an Incident's "Maintenance Request" (mirrors ServiceVisitIn.incident_id).
    incident_id: str | None = None
    performed_date: str | None = None
    checklist: dict | None = None
    parts_replaced: str | None = None
    electrical_safety_result: str | None = None
    functional_check_result: str | None = None
    calibration_status: str | None = None
    next_due_date: str | None = None
    supervisor_approved_by: str | None = None
    supervisor_approved_at: str | None = None
    notes: str | None = None

    @field_validator("performed_date")
    @classmethod
    def _valid_performed_date(cls, value: str | None) -> str | None:
        return _validate_iso_date(value)

    @field_validator("next_due_date")
    @classmethod
    def _valid_next_due_date(cls, value: str | None) -> str | None:
        return _validate_iso_date(value)

    @field_validator("electrical_safety_result", "functional_check_result", "calibration_status")
    @classmethod
    def _valid_result(cls, value: str | None) -> str | None:
        if value is not None and value not in PM_RESULT_OPTIONS:
            raise ValueError(f"must be one of {', '.join(PM_RESULT_OPTIONS)}")
        return value


class PMRecordOut(BaseModel):
    id: str
    schedule_id: str | None = None
    asset_id: str
    engineer_id: str | None = None
    incident_id: str | None = None
    performed_date: str | None = None
    checklist: dict | None = None
    parts_replaced: str | None = None
    electrical_safety_result: str | None = None
    functional_check_result: str | None = None
    calibration_status: str | None = None
    next_due_date: str | None = None
    supervisor_approved_by: str | None = None
    supervisor_approved_at: str | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}
