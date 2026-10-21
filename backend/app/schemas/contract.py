from pydantic import BaseModel, EmailStr, field_validator

CONTRACT_TYPES = ["AMC", "CMC", "WARRANTY"]
CONTRACT_STATUSES = ["ACTIVE", "EXPIRED", "RENEWAL_PENDING", "CANCELLED", "UNKNOWN"]
EXPIRY_STATUSES = ["NOT_SET", "EXPIRED", "EXPIRING_SOON", "UPCOMING", "ACTIVE"]


class ContractIn(BaseModel):
    contract_type: str
    vendor_id: str | None = None
    asset_ids: list[str] = []
    contract_number: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    pm_frequency: str | None = None
    calibration_frequency: str | None = None
    response_time_hours: int | None = None
    resolution_time_hours: int | None = None
    contract_value: float | None = None
    renewal_date: str | None = None
    contact_person: str | None = None
    contact_phone: str | None = None
    contact_email: EmailStr | None = None
    coverage_notes: str | None = None
    status: str = "UNKNOWN"

    @field_validator("contract_type")
    @classmethod
    def _valid_type(cls, value: str) -> str:
        if value not in CONTRACT_TYPES:
            raise ValueError(f"contract_type must be one of {', '.join(CONTRACT_TYPES)}")
        return value

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str) -> str:
        if value not in CONTRACT_STATUSES:
            raise ValueError(f"status must be one of {', '.join(CONTRACT_STATUSES)}")
        return value

    @field_validator("response_time_hours", "resolution_time_hours")
    @classmethod
    def _positive_hours(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("must be positive")
        return value

    @field_validator("contract_value")
    @classmethod
    def _non_negative_value(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("contract_value must not be negative")
        return value


class ContractUpdateIn(BaseModel):
    vendor_id: str | None = None
    contract_number: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    pm_frequency: str | None = None
    calibration_frequency: str | None = None
    response_time_hours: int | None = None
    resolution_time_hours: int | None = None
    contract_value: float | None = None
    renewal_date: str | None = None
    contact_person: str | None = None
    contact_phone: str | None = None
    contact_email: EmailStr | None = None
    coverage_notes: str | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str | None) -> str | None:
        if value is not None and value not in CONTRACT_STATUSES:
            raise ValueError(f"status must be one of {', '.join(CONTRACT_STATUSES)}")
        return value

    @field_validator("response_time_hours", "resolution_time_hours")
    @classmethod
    def _positive_hours(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("must be positive")
        return value

    @field_validator("contract_value")
    @classmethod
    def _non_negative_value(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("contract_value must not be negative")
        return value


class ContractOut(BaseModel):
    id: str
    contract_type: str
    vendor_id: str | None = None
    asset_ids: list[str] = []
    contract_number: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    pm_frequency: str | None = None
    calibration_frequency: str | None = None
    response_time_hours: int | None = None
    resolution_time_hours: int | None = None
    contract_value: float | None = None
    renewal_date: str | None = None
    contact_person: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    coverage_notes: str | None = None
    status: str
    expiry_status: str
    environment: str
