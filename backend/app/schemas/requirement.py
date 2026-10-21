from datetime import date, datetime

from pydantic import BaseModel, field_validator, model_validator

VALID_STATUSES = ["DRAFT", "SUBMITTED", "PENDING_MANAGER_APPROVAL", "APPROVED", "REJECTED", "CANCELLED"]
TERMINAL_STATUSES = ["APPROVED", "REJECTED", "CANCELLED"]
PRIORITIES = ["LOW", "MEDIUM", "HIGH", "URGENT"]
ITEM_TYPES = ["DEVICE", "DEVICE_MODEL", "CONSUMABLE", "SPARE_PART", "OTHER", "CONDEMNATION"]

# item_type -> which single reference field it requires (None = no reference required,
# a plain free-text description is enough for "OTHER").
# CONDEMNATION references asset_id (a real, already-installed physical Asset) —
# never device_model_id, which is a catalog/type reference for requesting new stock.
ITEM_TYPE_REFERENCE_FIELD = {
    "DEVICE": "device_model_id",
    "DEVICE_MODEL": "device_model_id",
    "CONSUMABLE": "consumable_id",
    "SPARE_PART": "inventory_item_id",
    "OTHER": None,
    "CONDEMNATION": "asset_id",
}


class RequirementItemIn(BaseModel):
    item_type: str
    device_model_id: str | None = None
    inventory_item_id: str | None = None
    consumable_id: str | None = None
    asset_id: str | None = None
    description: str | None = None
    requested_quantity: int
    unit: str | None = None
    remarks: str | None = None

    @field_validator("item_type")
    @classmethod
    def _valid_item_type(cls, value: str) -> str:
        if value not in ITEM_TYPES:
            raise ValueError(f"item_type must be one of {', '.join(ITEM_TYPES)}")
        return value

    @field_validator("requested_quantity")
    @classmethod
    def _positive_quantity(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("requested_quantity must be greater than 0")
        return value

    @model_validator(mode="after")
    def _reference_matches_type(self) -> "RequirementItemIn":
        required_field = ITEM_TYPE_REFERENCE_FIELD.get(self.item_type)
        refs = {
            "device_model_id": self.device_model_id,
            "inventory_item_id": self.inventory_item_id,
            "consumable_id": self.consumable_id,
            "asset_id": self.asset_id,
        }
        if required_field is None:
            if any(refs.values()):
                raise ValueError("item_type OTHER must not reference a device model/inventory item/consumable")
            if not self.description or not self.description.strip():
                raise ValueError("description is required when item_type is OTHER")
            return self
        if not refs[required_field]:
            raise ValueError(f"{required_field} is required when item_type is {self.item_type}")
        other_refs = {k: v for k, v in refs.items() if k != required_field}
        if any(other_refs.values()):
            raise ValueError(f"item_type {self.item_type} must only reference {required_field}")
        return self


class RequirementItemOut(BaseModel):
    id: str
    requirement_id: str
    item_type: str
    device_model_id: str | None = None
    inventory_item_id: str | None = None
    consumable_id: str | None = None
    asset_id: str | None = None
    description: str | None = None
    requested_quantity: int
    unit: str | None = None
    remarks: str | None = None

    model_config = {"from_attributes": True}


class RequirementIn(BaseModel):
    branch_id: str
    department_id: str | None = None
    priority: str = "MEDIUM"
    required_by_date: str | None = None
    reason: str
    items: list[RequirementItemIn]

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("reason must not be blank")
        return value

    @field_validator("priority")
    @classmethod
    def _valid_priority(cls, value: str) -> str:
        if value not in PRIORITIES:
            raise ValueError(f"priority must be one of {', '.join(PRIORITIES)}")
        return value

    @field_validator("required_by_date")
    @classmethod
    def _valid_required_by_date(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError("required_by_date must be a valid ISO date (YYYY-MM-DD)")
        if parsed < datetime.utcnow().date():
            raise ValueError("required_by_date cannot be in the past")
        return value

    @field_validator("items")
    @classmethod
    def _at_least_one_item(cls, value: list[RequirementItemIn]) -> list[RequirementItemIn]:
        if not value:
            raise ValueError("a requirement must contain at least one item")
        return value


class RequirementUpdateIn(BaseModel):
    """Only usable while the requirement is still DRAFT. Full replace of provided fields;
    `items`, if provided, replaces the entire item set (simplest, avoids partial-item-patch
    ambiguity for a domain this size)."""

    branch_id: str | None = None
    department_id: str | None = None
    priority: str | None = None
    required_by_date: str | None = None
    reason: str | None = None
    items: list[RequirementItemIn] | None = None

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("reason must not be blank")
        return value

    @field_validator("priority")
    @classmethod
    def _valid_priority(cls, value: str | None) -> str | None:
        if value is not None and value not in PRIORITIES:
            raise ValueError(f"priority must be one of {', '.join(PRIORITIES)}")
        return value

    @field_validator("required_by_date")
    @classmethod
    def _valid_required_by_date(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError("required_by_date must be a valid ISO date (YYYY-MM-DD)")
        if parsed < datetime.utcnow().date():
            raise ValueError("required_by_date cannot be in the past")
        return value

    @field_validator("items")
    @classmethod
    def _items_not_empty_when_given(cls, value: list[RequirementItemIn] | None) -> list[RequirementItemIn] | None:
        if value is not None and not value:
            raise ValueError("a requirement must contain at least one item")
        return value


class RequirementDecisionIn(BaseModel):
    remarks: str | None = None


class RequirementCancelIn(BaseModel):
    remarks: str | None = None


class RequirementOut(BaseModel):
    id: str
    request_number: str
    branch_id: str
    department_id: str | None = None
    requester_id: str
    requester_role: str | None = None
    priority: str
    required_by_date: str | None = None
    reason: str
    status: str
    manager_id: str | None = None
    manager_decision: str | None = None
    manager_decision_at: str | None = None
    manager_remarks: str | None = None
    created_at: datetime
    updated_at: datetime
    items: list[RequirementItemOut] = []

    model_config = {"from_attributes": True}


class RequirementStatusHistoryOut(BaseModel):
    id: str
    requirement_id: str
    from_status: str | None = None
    to_status: str
    changed_by: str | None = None
    notes: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
