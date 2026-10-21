from datetime import date

from pydantic import BaseModel, field_validator

# Batch E, Part 20 — this list is now only the historical/display fallback (used by
# ServiceVisitOut consumers and older callers). Actual acceptance of a `visit_type` on
# create/update is enforced against the active `ServiceVisitType` rows in the database
# (see routers/service_visits.py's `_valid_active_visit_type`), not this constant —
# these five remain the seeded defaults (migration 0018) but an admin can add more.
VISIT_TYPES = ["PM", "CALIBRATION", "BREAKDOWN", "INSTALLATION", "OTHER"]


class ServiceVisitTypeIn(BaseModel):
    name: str


class ServiceVisitTypeOut(BaseModel):
    id: str
    name: str
    is_active: bool

    model_config = {"from_attributes": True}


def _validate_iso_date(value: str | None) -> str | None:
    if value is None:
        return value
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("must be an ISO date string (YYYY-MM-DD)")
    return value


class ServiceVisitIn(BaseModel):
    incident_id: str | None = None
    asset_id: str
    vendor_id: str | None = None
    engineer_id: str | None = None
    visit_type: str | None = None
    visit_date: str | None = None
    summary: str | None = None
    parts_used: str | None = None
    cost: float | None = None
    next_action: str | None = None
    issue: str | None = None
    downtime_hours: float | None = None
    # Module 21 — Procurement. Set when this visit is the vendor visit scheduled
    # against a Purchase Order; reuses ServiceVisit as-is (no duplicate table).
    purchase_order_id: str | None = None

    # visit_type is intentionally NOT validated here — the active list is dynamic
    # (admin-managed `ServiceVisitType` rows, Batch E Part 20), so acceptance is
    # checked against the database in routers/service_visits.py instead of against a
    # static constant.

    @field_validator("visit_date")
    @classmethod
    def _valid_visit_date(cls, value: str | None) -> str | None:
        return _validate_iso_date(value)

    @field_validator("cost")
    @classmethod
    def _non_negative_cost(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("cost must not be negative")
        return value

    @field_validator("downtime_hours")
    @classmethod
    def _non_negative_downtime(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("downtime_hours must not be negative")
        return value


class ServiceVisitUpdateIn(BaseModel):
    incident_id: str | None = None
    vendor_id: str | None = None
    engineer_id: str | None = None
    visit_type: str | None = None
    visit_date: str | None = None
    summary: str | None = None
    parts_used: str | None = None
    cost: float | None = None
    next_action: str | None = None
    issue: str | None = None
    downtime_hours: float | None = None
    purchase_order_id: str | None = None

    # visit_type is intentionally NOT validated here — the active list is dynamic
    # (admin-managed `ServiceVisitType` rows, Batch E Part 20), so acceptance is
    # checked against the database in routers/service_visits.py instead of against a
    # static constant.

    @field_validator("visit_date")
    @classmethod
    def _valid_visit_date(cls, value: str | None) -> str | None:
        return _validate_iso_date(value)

    @field_validator("cost")
    @classmethod
    def _non_negative_cost(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("cost must not be negative")
        return value

    @field_validator("downtime_hours")
    @classmethod
    def _non_negative_downtime(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("downtime_hours must not be negative")
        return value


class ServiceVisitOut(BaseModel):
    id: str
    incident_id: str | None = None
    asset_id: str
    vendor_id: str | None = None
    engineer_id: str | None = None
    visit_type: str | None = None
    visit_date: str | None = None
    summary: str | None = None
    parts_used: str | None = None
    cost: float | None = None
    next_action: str | None = None
    issue: str | None = None
    downtime_hours: float | None = None
    purchase_order_id: str | None = None

    model_config = {"from_attributes": True}
