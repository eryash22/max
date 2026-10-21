from datetime import date, datetime

from pydantic import BaseModel, field_validator

FULFILLMENT_STATUSES = [
    "PENDING_ALLOCATION", "ALLOCATED", "UNDER_PROCESS", "PACKED", "OUT_FOR_DELIVERY",
    "DELIVERED", "COMPLETED", "CANCELLED",
]
TERMINAL_FULFILLMENT_STATUSES = ["COMPLETED", "CANCELLED"]
CANCELLABLE_FULFILLMENT_STATUSES = ["PENDING_ALLOCATION", "ALLOCATED", "UNDER_PROCESS", "PACKED"]

# Serialized item types are tracked individually via FulfillmentItemAsset (asset_ids);
# every other item type is tracked by quantity only — see models/fulfillment.py.
SERIALIZED_ITEM_TYPES = ["DEVICE", "DEVICE_MODEL"]


def _valid_iso_date(value: str | None) -> str | None:
    if value is not None:
        date.fromisoformat(value)
    return value


class FulfillmentCreateIn(BaseModel):
    requirement_id: str
    source_location_id: str | None = None
    expected_delivery_date: str | None = None

    @field_validator("expected_delivery_date")
    @classmethod
    def _valid_expected_delivery_date(cls, value: str | None) -> str | None:
        return _valid_iso_date(value)


class FulfillmentAllocationItemIn(BaseModel):
    """Exactly one of `quantity` (SPARE_PART/CONSUMABLE/OTHER) or `asset_ids`
    (DEVICE/DEVICE_MODEL) is expected, matching the referenced RequirementItem's
    item_type — enforced in the service layer, where the item_type is known."""

    requirement_item_id: str
    quantity: int | None = None
    asset_ids: list[str] | None = None

    @field_validator("quantity")
    @classmethod
    def _non_negative_quantity(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("quantity must not be negative")
        return value


class FulfillmentAllocateIn(BaseModel):
    source_location_id: str | None = None
    expected_delivery_date: str | None = None
    items: list[FulfillmentAllocationItemIn]

    @field_validator("expected_delivery_date")
    @classmethod
    def _valid_expected_delivery_date(cls, value: str | None) -> str | None:
        return _valid_iso_date(value)

    @field_validator("items")
    @classmethod
    def _at_least_one_item(cls, value: list[FulfillmentAllocationItemIn]) -> list[FulfillmentAllocationItemIn]:
        if not value:
            raise ValueError("at least one item allocation is required")
        return value


class FulfillmentDeliverIn(BaseModel):
    actual_delivery_date: str | None = None
    delivery_remarks: str | None = None

    @field_validator("actual_delivery_date")
    @classmethod
    def _valid_actual_delivery_date(cls, value: str | None) -> str | None:
        return _valid_iso_date(value)


class FulfillmentCancelIn(BaseModel):
    remarks: str | None = None


class FulfillmentItemAssetOut(BaseModel):
    id: str
    fulfillment_item_id: str
    asset_id: str
    delivered: bool

    model_config = {"from_attributes": True}


class FulfillmentItemOut(BaseModel):
    id: str
    fulfillment_id: str
    requirement_item_id: str
    item_type: str
    device_model_id: str | None = None
    inventory_item_id: str | None = None
    consumable_id: str | None = None
    description: str | None = None
    requested_quantity: int
    allocated_quantity: int
    delivered_quantity: int
    pending_quantity: int
    available_quantity: int | None = None
    unit: str | None = None
    assets: list[FulfillmentItemAssetOut] = []


class FulfillmentOut(BaseModel):
    id: str
    fulfillment_number: str
    requirement_id: str
    status: str
    source_location_id: str | None = None
    inventory_manager_id: str | None = None
    expected_delivery_date: str | None = None
    actual_delivery_date: str | None = None
    delivered_by_id: str | None = None
    delivery_remarks: str | None = None
    cancel_remarks: str | None = None
    created_at: datetime
    updated_at: datetime
    items: list[FulfillmentItemOut] = []


class FulfillmentStatusHistoryOut(BaseModel):
    id: str
    fulfillment_id: str
    from_status: str | None = None
    to_status: str
    changed_by: str | None = None
    notes: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
