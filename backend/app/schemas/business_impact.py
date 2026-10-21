from pydantic import BaseModel, field_validator


class BusinessImpactAssumptionIn(BaseModel):
    name: str
    branch_id: str | None = None
    device_category: str | None = None
    revenue_per_hour: float | None = None
    currency: str = "INR"
    notes: str | None = None
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value

    @field_validator("revenue_per_hour")
    @classmethod
    def validate_revenue_per_hour(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("revenue_per_hour must be >= 0")
        return value


class BusinessImpactAssumptionOut(BusinessImpactAssumptionIn):
    id: str

    model_config = {"from_attributes": True}


class IncidentImpactEstimateOut(BaseModel):
    """Always an estimate, never an actual figure — see AGENTS.md.

    `assumption_id` is null and `estimated_impact` is null when no matching
    assumption is configured; the response still explains why via `basis`.
    """

    incident_id: str
    asset_id: str | None
    downtime_hours: float | None
    assumption_id: str | None
    revenue_per_hour: float | None
    currency: str | None
    estimated_impact: float | None
    basis: str


class FailureSummaryOut(BaseModel):
    """Computed on the fly from Incident rows — never stored. Always Estimated/Projected,
    per AGENTS.md's requirement that AI/dashboards never present derived figures as Actual.
    """

    asset_id: str
    incident_count: int
    total_downtime_hours: float
    mttr_hours: float | None
    mtbf_hours: float | None
    repeat_failure: bool
    basis: str
