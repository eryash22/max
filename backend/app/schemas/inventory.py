from datetime import date

from pydantic import BaseModel, field_validator, model_validator

# `transaction_type` on `InventoryTransaction` is a plain String column (no DB-level
# enum) — this is the API-layer vocabulary Module 06A introduces. STOCK_IN/STOCK_OUT/
# ADJUSTMENT/TRANSFER match the Phase 0 stock_movements template's "Movement Type"
# values (see work/phase0-spreadsheets/build_phase0_templates.mjs).
TRANSACTION_TYPES = ["STOCK_IN", "STOCK_OUT", "ADJUSTMENT", "TRANSFER"]

STOCK_STATUSES = ["OUT_OF_STOCK", "LOW_STOCK", "NORMAL", "OVERSTOCK"]
BATCH_EXPIRY_STATUSES = ["NOT_SET", "EXPIRED", "NEAR_EXPIRY", "OK"]

# Window used to classify a batch/transaction as "NEAR_EXPIRY" rather than "OK",
# matching the 30-day lead-time window used for contract EXPIRING_SOON (Module 05A).
NEAR_EXPIRY_WINDOW_DAYS = 30

# `InventoryLocation.kind` has no DB-level enum either (plain String, default
# "WAREHOUSE") — this is the Module 13 API-layer vocabulary, chosen to match
# ARCHITECTURE.md's "warehouses, bins" and DATABASE_DESIGN.md's logical-model
# wording for this domain (nothing invented beyond what those two words imply).
LOCATION_KINDS = ["WAREHOUSE", "BIN", "STORE", "OTHER"]


class InventoryLocationIn(BaseModel):
    branch_id: str
    code: str | None = None
    name: str
    kind: str = "WAREHOUSE"

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("name must not be blank")
        return value

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, value: str) -> str:
        if value not in LOCATION_KINDS:
            raise ValueError(f"kind must be one of {', '.join(LOCATION_KINDS)}")
        return value


class InventoryLocationUpdateIn(BaseModel):
    branch_id: str | None = None
    code: str | None = None
    name: str | None = None
    kind: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("name must not be blank")
        return value

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, value: str | None) -> str | None:
        if value is not None and value not in LOCATION_KINDS:
            raise ValueError(f"kind must be one of {', '.join(LOCATION_KINDS)}")
        return value


class InventoryLocationOut(BaseModel):
    id: str
    branch_id: str
    code: str | None = None
    name: str
    kind: str

    model_config = {"from_attributes": True}


class InventoryItemIn(BaseModel):
    item_code: str
    name: str
    category: str | None = None
    unit: str | None = None
    min_level: int | None = None
    max_level: int | None = None
    reorder_level: int | None = None
    unit_cost: float | None = None
    supplier: str | None = None
    is_consumable: bool = False

    @field_validator("item_code", "name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("min_level", "max_level", "reorder_level")
    @classmethod
    def _non_negative_level(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("must not be negative")
        return value

    @field_validator("unit_cost")
    @classmethod
    def _non_negative_cost(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("unit_cost must not be negative")
        return value


class InventoryItemUpdateIn(BaseModel):
    name: str | None = None
    category: str | None = None
    unit: str | None = None
    min_level: int | None = None
    max_level: int | None = None
    reorder_level: int | None = None
    unit_cost: float | None = None
    supplier: str | None = None
    is_consumable: bool | None = None

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("min_level", "max_level", "reorder_level")
    @classmethod
    def _non_negative_level(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("must not be negative")
        return value

    @field_validator("unit_cost")
    @classmethod
    def _non_negative_cost(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("unit_cost must not be negative")
        return value


class InventoryItemOut(BaseModel):
    id: str
    item_code: str
    name: str
    category: str | None = None
    unit: str | None = None
    min_level: int | None = None
    max_level: int | None = None
    reorder_level: int | None = None
    unit_cost: float | None = None
    supplier: str | None = None
    is_consumable: bool
    # current_stock/stock_status are Optional (not split into a separate stock-free
    # schema) so the same InventoryItemOut shape serves both full inventory.read
    # callers (always populated) and inventory.picker_read callers, whose
    # stock/quantity fields are set to None server-side in
    # inventory_service.strip_stock_fields() before this schema validates — TL/Unit
    # Head must never receive real quantities in this response.
    current_stock: int | None = None
    stock_status: str | None = None

    model_config = {"from_attributes": True}


# Suggested vocabulary for the consumable category picker (Phase 3 of the
# consumables workflow) — deliberately NOT enforced server-side: unlike
# `LOCATION_KINDS`/`TRANSACTION_TYPES` (small, closed, API-invented vocabularies),
# consumable categories are a real hospital-procurement taxonomy that will keep
# growing, and existing/imported `Consumable.category` free text must not start
# failing validation. The frontend renders this list plus a free-text fallback.
CONSUMABLE_CATEGORIES = [
    "Physiotherapy Consumables",
    "ICU Consumables",
    "Respiratory Care Consumables",
    "Diagnostic Consumables",
    "Infection Control",
    "Infusion & IV Consumables",
    "General Biomedical Consumables",
]

CONSUMABLE_TRANSACTION_TYPES = TRANSACTION_TYPES


class ConsumableIn(BaseModel):
    item_code: str
    name: str
    branch_id: str | None = None
    category: str | None = None
    manufacturer: str | None = None
    compatible_model_number: str | None = None
    unit: str | None = None
    opening_quantity: int | None = None
    min_level: int | None = None
    reorder_level: int | None = None
    expiry_date: str | None = None
    supplier: str | None = None
    vendor_id: str | None = None
    status: str = "ACTIVE"
    data_source: str | None = None
    classification_status: str | None = None

    @field_validator("item_code", "name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("opening_quantity", "min_level", "reorder_level")
    @classmethod
    def _non_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("must not be negative")
        return value


class ConsumableUpdateIn(BaseModel):
    """Partial update — a consumable's identity (`item_code`/`branch_id`) is fixed at
    creation; only descriptive/threshold/status fields can change afterwards."""

    name: str | None = None
    category: str | None = None
    manufacturer: str | None = None
    compatible_model_number: str | None = None
    unit: str | None = None
    min_level: int | None = None
    reorder_level: int | None = None
    expiry_date: str | None = None
    supplier: str | None = None
    vendor_id: str | None = None
    status: str | None = None

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("min_level", "reorder_level")
    @classmethod
    def _non_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("must not be negative")
        return value


class ConsumableOut(ConsumableIn):
    """`opening_quantity` (inherited from `ConsumableIn`) already holds this row's
    live on-hand quantity — mutated in place on fulfillment completion, same
    convention the Phase-1 domain established. `stock_status` is the one new
    computed field, mirroring `InventoryItemOut.stock_status`."""

    id: str
    environment: str
    # Optional, same reasoning as InventoryItemOut.stock_status: inventory.picker_read
    # callers (TL/Unit Head) get opening_quantity/min_level/reorder_level/stock_status
    # all stripped to None by inventory_service.strip_stock_fields() before this
    # validates — they must never see central/branch consumable quantities.
    stock_status: str | None = None

    model_config = {"from_attributes": True}


class ConsumableTransactionOut(BaseModel):
    id: str
    consumable_id: str
    transaction_type: str
    quantity: int
    source_branch_id: str | None = None
    destination_branch_id: str | None = None
    requirement_id: str | None = None
    reference: str | None = None
    performed_by: str | None = None
    created_at: str

    model_config = {"from_attributes": True}


class InventoryTransactionIn(BaseModel):
    item_id: str
    location_id: str | None = None
    transaction_type: str
    quantity: int
    batch_number: str | None = None
    expiry_date: str | None = None
    reference: str | None = None

    @field_validator("transaction_type")
    @classmethod
    def _valid_type(cls, value: str) -> str:
        if value not in TRANSACTION_TYPES:
            raise ValueError(f"transaction_type must be one of {', '.join(TRANSACTION_TYPES)}")
        return value

    @field_validator("quantity")
    @classmethod
    def _valid_quantity(cls, value: int) -> int:
        # ADJUSTMENT may be signed (a correction can increase or decrease stock) but
        # must not be a no-op zero; every other type is a magnitude and must be positive.
        if value == 0:
            raise ValueError("quantity must not be zero")
        return value

    @field_validator("expiry_date")
    @classmethod
    def _valid_expiry_date(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def _quantity_sign_by_type(self) -> "InventoryTransactionIn":
        # Only ADJUSTMENT may be signed (a correction can raise or lower stock).
        # STOCK_IN/STOCK_OUT/TRANSFER quantities are magnitudes and must be positive.
        if self.transaction_type != "ADJUSTMENT" and self.quantity < 0:
            raise ValueError(f"quantity must be positive for {self.transaction_type}")
        return self


class InventoryTransactionOut(BaseModel):
    id: str
    item_id: str
    location_id: str | None = None
    transaction_type: str
    quantity: int
    batch_number: str | None = None
    expiry_date: str | None = None
    expiry_status: str
    reference: str | None = None
    performed_by: str | None = None
    created_at: str

    model_config = {"from_attributes": True}


class SparePartCompatibilityIn(BaseModel):
    device_model_id: str
    notes: str | None = None


class SparePartCompatibilityOut(BaseModel):
    id: str
    inventory_item_id: str
    device_model_id: str
    notes: str | None = None

    model_config = {"from_attributes": True}
