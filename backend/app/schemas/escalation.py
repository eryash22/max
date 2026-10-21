from pydantic import BaseModel, field_validator

# Vocabulary taken verbatim from models/escalation.py's module docstring — nothing invented.
TRIGGER_TYPES = ["TIME_BASED", "SEVERITY_BASED", "CLINICAL_RISK", "REPEATED_FAILURE", "UNRESOLVED"]
SEVERITY_THRESHOLDS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class EscalationPolicyIn(BaseModel):
    name: str
    trigger_type: str
    severity_threshold: str | None = None
    device_criticality_threshold: str | None = None
    is_active: bool = True

    @field_validator("trigger_type")
    @classmethod
    def validate_trigger_type(cls, value: str) -> str:
        if value not in TRIGGER_TYPES:
            raise ValueError(f"trigger_type must be one of {TRIGGER_TYPES}")
        return value

    @field_validator("severity_threshold")
    @classmethod
    def validate_severity_threshold(cls, value: str | None) -> str | None:
        if value is not None and value not in SEVERITY_THRESHOLDS:
            raise ValueError(f"severity_threshold must be one of {SEVERITY_THRESHOLDS}")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value


class EscalationPolicyOut(EscalationPolicyIn):
    id: str

    model_config = {"from_attributes": True}


class EscalationLevelIn(BaseModel):
    level_number: int
    role_code: str | None = None
    wait_minutes: int | None = None

    @field_validator("level_number")
    @classmethod
    def validate_level_number(cls, value: int) -> int:
        if value < 1:
            raise ValueError("level_number must be >= 1")
        return value

    @field_validator("wait_minutes")
    @classmethod
    def validate_wait_minutes(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("wait_minutes must be >= 0")
        return value


class EscalationLevelOut(EscalationLevelIn):
    id: str
    policy_id: str

    model_config = {"from_attributes": True}


class EscalationEventIn(BaseModel):
    incident_id: str
    policy_id: str
    level_number: int
    notified_user_id: str | None = None
    notes: str | None = None


class EscalationEventOut(EscalationEventIn):
    id: str

    model_config = {"from_attributes": True}
