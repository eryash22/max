from pydantic import BaseModel, EmailStr, field_validator

VENDOR_TYPES = ["OEM", "DISTRIBUTOR", "SERVICE", "OTHER"]
VENDOR_STATUSES = ["ACTIVE", "INACTIVE"]


class VendorIn(BaseModel):
    name: str
    vendor_type: str | None = None
    is_oem: bool = False
    is_authorized_distributor: bool = False
    is_service_vendor: bool = False
    city: str | None = None
    state: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    website: str | None = None
    support_coverage: str | None = None
    response_sla_hours: int | None = None
    rating: int | None = None
    status: str = "ACTIVE"

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("name must not be blank")
        return value

    @field_validator("vendor_type")
    @classmethod
    def _valid_vendor_type(cls, value: str | None) -> str | None:
        if value is not None and value not in VENDOR_TYPES:
            raise ValueError(f"vendor_type must be one of {', '.join(VENDOR_TYPES)}")
        return value

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str) -> str:
        if value not in VENDOR_STATUSES:
            raise ValueError(f"status must be one of {', '.join(VENDOR_STATUSES)}")
        return value

    @field_validator("response_sla_hours")
    @classmethod
    def _positive_sla(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("response_sla_hours must be positive")
        return value

    @field_validator("rating")
    @classmethod
    def _valid_rating(cls, value: int | None) -> int | None:
        if value is not None and not (1 <= value <= 5):
            raise ValueError("rating must be between 1 and 5")
        return value


class VendorUpdateIn(BaseModel):
    name: str | None = None
    vendor_type: str | None = None
    is_oem: bool | None = None
    is_authorized_distributor: bool | None = None
    is_service_vendor: bool | None = None
    city: str | None = None
    state: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    website: str | None = None
    support_coverage: str | None = None
    response_sla_hours: int | None = None
    rating: int | None = None
    status: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("name must not be blank")
        return value

    @field_validator("vendor_type")
    @classmethod
    def _valid_vendor_type(cls, value: str | None) -> str | None:
        if value is not None and value not in VENDOR_TYPES:
            raise ValueError(f"vendor_type must be one of {', '.join(VENDOR_TYPES)}")
        return value

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str | None) -> str | None:
        if value is not None and value not in VENDOR_STATUSES:
            raise ValueError(f"status must be one of {', '.join(VENDOR_STATUSES)}")
        return value

    @field_validator("response_sla_hours")
    @classmethod
    def _positive_sla(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("response_sla_hours must be positive")
        return value

    @field_validator("rating")
    @classmethod
    def _valid_rating(cls, value: int | None) -> int | None:
        if value is not None and not (1 <= value <= 5):
            raise ValueError("rating must be between 1 and 5")
        return value


class VendorOut(BaseModel):
    id: str
    name: str
    vendor_type: str | None = None
    is_oem: bool
    is_authorized_distributor: bool
    is_service_vendor: bool
    city: str | None = None
    state: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    support_coverage: str | None = None
    response_sla_hours: int | None = None
    rating: int | None = None
    status: str
    environment: str

    model_config = {"from_attributes": True}


class VendorContactIn(BaseModel):
    name: str
    role: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    is_primary: bool = False

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("name must not be blank")
        return value


class VendorContactOut(BaseModel):
    id: str
    vendor_id: str
    name: str
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    is_primary: bool

    model_config = {"from_attributes": True}


class DeviceModelVendorIn(BaseModel):
    device_model_id: str
    rank: int = 0
    notes: str | None = None

    @field_validator("rank")
    @classmethod
    def _rank_range(cls, value: int) -> int:
        if not (0 <= value <= 3):
            raise ValueError("rank must be between 0 (primary) and 3")
        return value


class DeviceModelVendorOut(BaseModel):
    id: str
    device_model_id: str
    vendor_id: str
    rank: int
    notes: str | None = None

    model_config = {"from_attributes": True}


class VendorServiceVisitOut(BaseModel):
    id: str
    incident_id: str | None = None
    asset_id: str
    engineer_id: str | None = None
    visit_type: str | None = None
    visit_date: str | None = None
    summary: str | None = None
    parts_used: str | None = None
    cost: float | None = None
    next_action: str | None = None

    model_config = {"from_attributes": True}
