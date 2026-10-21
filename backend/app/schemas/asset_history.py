from datetime import datetime

from pydantic import BaseModel, field_validator

# Matches the event_type vocabulary documented in models/asset_history.py's module docstring.
EVENT_TYPES = [
    "STATUS_CHANGE",
    "LOCATION_CHANGE",
    "DEPARTMENT_CHANGE",
    "BRANCH_TRANSFER",
    "OWNERSHIP_CHANGE",
    "LIFECYCLE_TRANSITION",
]


class AssetHistoryIn(BaseModel):
    event_type: str
    from_value: str | None = None
    to_value: str | None = None
    branch_id: str | None = None
    department_id: str | None = None
    location_id: str | None = None
    notes: str | None = None

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        if value not in EVENT_TYPES:
            raise ValueError(f"event_type must be one of {EVENT_TYPES}")
        return value


class AssetHistoryOut(AssetHistoryIn):
    id: str
    asset_id: str
    changed_by: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
