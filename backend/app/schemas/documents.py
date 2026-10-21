from datetime import date

from pydantic import BaseModel, field_validator

# document_type is reference data on the Document model (no DB enum) — see
# models/documents.py's module docstring for the authoritative list.
DOCUMENT_TYPES = [
    "OEM_MANUAL", "SERVICE_MANUAL", "USER_MANUAL", "SOP", "PM_CHECKLIST",
    "CALIBRATION_CERTIFICATE", "AMC_CONTRACT", "CMC_CONTRACT", "WARRANTY_DOCUMENT",
    "SERVICE_REPORT", "PURCHASE_DOCUMENT", "INVOICE", "TRAINING_DOCUMENT",
    "DEVICE_PHOTOGRAPH", "INCIDENT_PHOTOGRAPH",
]
ACCESS_LEVELS = ["PUBLIC", "INTERNAL", "RESTRICTED"]
APPROVAL_STATUSES = ["PENDING", "APPROVED", "REJECTED"]

# Entity types that a Document can be linked to via the polymorphic DocumentLink table,
# matching the entities that actually exist in the Phase B schema (see models/*.py).
# "incident" matches the entity_type string already used by routers/incidents.py's own
# attachment endpoints, so links created there are visible through this module too.
ENTITY_TYPES = [
    "incident", "asset", "pm_schedule", "pm_record", "calibration_schedule",
    "calibration_record", "vendor", "amc_contract", "cmc_contract", "warranty",
    "inventory_item",
    # Final hardening pass — extends the existing polymorphic DocumentLink table to
    # the Module 16A/17A/21 workflow entities it predates; no new document subsystem.
    "requirement", "quotation", "indent", "purchase_order", "invoice", "service_visit",
]


def _validate_iso_date(value: str | None) -> str | None:
    if value is None:
        return value
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("must be an ISO date string (YYYY-MM-DD)")
    return value


class DocumentIn(BaseModel):
    title: str
    document_type: str
    manufacturer: str | None = None
    model_number: str | None = None
    device_model_id: str | None = None
    asset_id: str | None = None
    vendor_id: str | None = None
    effective_date: str | None = None
    expiry_date: str | None = None
    access_level: str = "INTERNAL"
    is_active: bool = True

    @field_validator("title")
    @classmethod
    def _non_blank_title(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("document_type")
    @classmethod
    def _valid_document_type(cls, value: str) -> str:
        if value not in DOCUMENT_TYPES:
            raise ValueError(f"document_type must be one of {', '.join(DOCUMENT_TYPES)}")
        return value

    @field_validator("access_level")
    @classmethod
    def _valid_access_level(cls, value: str) -> str:
        if value not in ACCESS_LEVELS:
            raise ValueError(f"access_level must be one of {', '.join(ACCESS_LEVELS)}")
        return value

    @field_validator("effective_date")
    @classmethod
    def _valid_effective_date(cls, value: str | None) -> str | None:
        return _validate_iso_date(value)

    @field_validator("expiry_date")
    @classmethod
    def _valid_expiry_date(cls, value: str | None) -> str | None:
        return _validate_iso_date(value)


class DocumentUpdateIn(BaseModel):
    title: str | None = None
    document_type: str | None = None
    manufacturer: str | None = None
    model_number: str | None = None
    device_model_id: str | None = None
    asset_id: str | None = None
    vendor_id: str | None = None
    effective_date: str | None = None
    expiry_date: str | None = None
    access_level: str | None = None
    approval_status: str | None = None
    is_active: bool | None = None

    @field_validator("title")
    @classmethod
    def _non_blank_title(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("document_type")
    @classmethod
    def _valid_document_type(cls, value: str | None) -> str | None:
        if value is not None and value not in DOCUMENT_TYPES:
            raise ValueError(f"document_type must be one of {', '.join(DOCUMENT_TYPES)}")
        return value

    @field_validator("access_level")
    @classmethod
    def _valid_access_level(cls, value: str | None) -> str | None:
        if value is not None and value not in ACCESS_LEVELS:
            raise ValueError(f"access_level must be one of {', '.join(ACCESS_LEVELS)}")
        return value

    @field_validator("approval_status")
    @classmethod
    def _valid_approval_status(cls, value: str | None) -> str | None:
        if value is not None and value not in APPROVAL_STATUSES:
            raise ValueError(f"approval_status must be one of {', '.join(APPROVAL_STATUSES)}")
        return value

    @field_validator("effective_date")
    @classmethod
    def _valid_effective_date(cls, value: str | None) -> str | None:
        return _validate_iso_date(value)

    @field_validator("expiry_date")
    @classmethod
    def _valid_expiry_date(cls, value: str | None) -> str | None:
        return _validate_iso_date(value)


class DocumentVersionOut(BaseModel):
    id: str
    document_id: str
    version_label: str
    file_name: str
    effective_date: str | None = None
    processing_status: str

    model_config = {"from_attributes": True}


class DocumentOut(BaseModel):
    id: str
    title: str
    document_type: str
    manufacturer: str | None = None
    model_number: str | None = None
    device_model_id: str | None = None
    asset_id: str | None = None
    vendor_id: str | None = None
    active_version_id: str | None = None
    effective_date: str | None = None
    expiry_date: str | None = None
    uploaded_by: str | None = None
    access_level: str
    approval_status: str
    approved_by: str | None = None
    is_active: bool
    version_count: int = 0

    model_config = {"from_attributes": True}


class DocumentLinkIn(BaseModel):
    entity_type: str
    entity_id: str

    @field_validator("entity_type")
    @classmethod
    def _valid_entity_type(cls, value: str) -> str:
        if value not in ENTITY_TYPES:
            raise ValueError(f"entity_type must be one of {', '.join(ENTITY_TYPES)}")
        return value

    @field_validator("entity_id")
    @classmethod
    def _non_blank_entity_id(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("entity_id must not be blank")
        return value


class DocumentLinkOut(BaseModel):
    id: str
    document_id: str
    entity_type: str
    entity_id: str

    model_config = {"from_attributes": True}
