from datetime import date

from pydantic import BaseModel, field_validator, model_validator

FREQUENCIES = ["MONTHLY", "QUARTERLY", "HALF_YEARLY", "YEARLY", "CUSTOM"]
DUE_STATUSES = ["NOT_SCHEDULED", "OVERDUE", "DUE", "UPCOMING"]
PASS_FAIL_VALUES = ["PASS", "FAIL"]


def _validate_iso_date(value: str | None) -> str | None:
    if value is None:
        return value
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("must be an ISO date string (YYYY-MM-DD)")
    return value


class CalibrationScheduleIn(BaseModel):
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
    def _custom_requires_interval(self) -> "CalibrationScheduleIn":
        if self.frequency == "CUSTOM" and not self.custom_interval_days:
            raise ValueError("custom_interval_days is required when frequency is CUSTOM")
        return self


class CalibrationScheduleUpdateIn(BaseModel):
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


class CalibrationScheduleOut(BaseModel):
    id: str
    asset_id: str
    frequency: str | None = None
    custom_interval_days: int | None = None
    next_due_date: str | None = None
    is_active: bool
    due_status: str

    model_config = {"from_attributes": True}


class CalibrationRecordIn(BaseModel):
    schedule_id: str | None = None
    asset_id: str
    # Workflow consolidation pass — optional back-reference when this record was created
    # from an Incident's "Calibration Request" (mirrors ServiceVisitIn.incident_id).
    incident_id: str | None = None
    performed_date: str | None = None
    certificate_number: str | None = None
    calibration_agency: str | None = None
    reference_standard: str | None = None
    result: str | None = None
    pass_fail: str | None = None
    next_due_date: str | None = None
    notes: str | None = None

    @field_validator("performed_date")
    @classmethod
    def _valid_performed_date(cls, value: str | None) -> str | None:
        return _validate_iso_date(value)

    @field_validator("next_due_date")
    @classmethod
    def _valid_next_due_date(cls, value: str | None) -> str | None:
        return _validate_iso_date(value)

    @field_validator("pass_fail")
    @classmethod
    def _valid_pass_fail(cls, value: str | None) -> str | None:
        if value is not None and value not in PASS_FAIL_VALUES:
            raise ValueError(f"pass_fail must be one of {', '.join(PASS_FAIL_VALUES)}")
        return value


class CalibrationRecordOut(BaseModel):
    id: str
    schedule_id: str | None = None
    asset_id: str
    incident_id: str | None = None
    performed_date: str | None = None
    certificate_number: str | None = None
    calibration_agency: str | None = None
    reference_standard: str | None = None
    result: str | None = None
    pass_fail: str | None = None
    next_due_date: str | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}
