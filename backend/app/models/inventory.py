from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class InventoryLocation(BaseModel):
    __tablename__ = "inventory_locations"

    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="WAREHOUSE")


class InventoryItem(BaseModel):
    __tablename__ = "inventory_items"

    item_code: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    min_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reorder_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    supplier: Mapped[str | None] = mapped_column(String, nullable=True)
    is_consumable: Mapped[bool] = mapped_column(Boolean, default=False)

    transactions: Mapped[list["InventoryTransaction"]] = relationship(back_populates="item")


class SparePartCompatibility(BaseModel):
    """Which inventory items (spare parts) are known-compatible with which device model."""

    __tablename__ = "spare_part_compatibility"

    inventory_item_id: Mapped[str] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    device_model_id: Mapped[str] = mapped_column(ForeignKey("device_models.id"), index=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)


class Consumable(BaseModel):
    """A consumable stock row for one location: `branch_id IS NULL` is the central/
    hub pool an Inventory Manager stocks directly; a non-NULL `branch_id` is that
    branch's own on-hand quantity, created on demand the first time central stock is
    transferred to it (see `inventory_service.get_or_create_branch_consumable`).
    `item_code` therefore is NOT globally unique any more — the same catalog item can
    have one central row plus one row per branch it has ever received stock at."""

    __tablename__ = "consumables"
    __table_args__ = (UniqueConstraint("item_code", "branch_id", name="uq_consumables_item_code_branch"),)

    item_code: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String, nullable=True)
    compatible_model_number: Mapped[str | None] = mapped_column(String, nullable=True)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    opening_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reorder_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expiry_date: Mapped[str | None] = mapped_column(String, nullable=True)
    # `supplier` (free text) predates this FK and is kept for rows imported before a
    # clean Vendor relationship existed. `vendor_id` is the one the New Inventory Item
    # form now offers (a dropdown from Vendor master, matching how Device already
    # works) — see routers/inventory.py and frontend/app/inventory/new/page.tsx.
    supplier: Mapped[str | None] = mapped_column(String, nullable=True)
    vendor_id: Mapped[str | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    data_source: Mapped[str | None] = mapped_column(String, nullable=True)
    classification_status: Mapped[str | None] = mapped_column(String, nullable=True)
    environment: Mapped[str] = mapped_column(String, default="DEVELOPMENT / DEMO DATA")


class InventoryTransaction(BaseModel):
    __tablename__ = "inventory_transactions"

    item_id: Mapped[str] = mapped_column(ForeignKey("inventory_items.id"))
    location_id: Mapped[str | None] = mapped_column(ForeignKey("inventory_locations.id"), nullable=True)
    transaction_type: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer)
    batch_number: Mapped[str | None] = mapped_column(String, nullable=True)
    expiry_date: Mapped[str | None] = mapped_column(String, nullable=True)
    reference: Mapped[str | None] = mapped_column(String, nullable=True)
    performed_by: Mapped[str | None] = mapped_column(String, nullable=True)

    item: Mapped["InventoryItem"] = relationship(back_populates="transactions")


class ConsumableTransaction(BaseModel):
    """Ledger row for every `Consumable.opening_quantity` mutation — mirrors
    `InventoryTransaction`'s role for `InventoryItem`, which has no equivalent for
    consumables (a documented gap, see PROJECT_STATUS.md). `source_branch_id`/
    `destination_branch_id` are both nullable (NULL = the central/hub pool, matching
    `Consumable.branch_id`'s own convention): a central-to-branch fulfillment sets
    both (a TRANSFER), a branch consuming its own stock sets only `source_branch_id`
    (an OUT), and a manual stock-in sets only `destination_branch_id` (an IN)."""

    __tablename__ = "consumable_transactions"

    consumable_id: Mapped[str] = mapped_column(ForeignKey("consumables.id"), index=True)
    transaction_type: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer)
    source_branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    destination_branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    requirement_id: Mapped[str | None] = mapped_column(ForeignKey("requirements.id"), nullable=True)
    reference: Mapped[str | None] = mapped_column(String, nullable=True)
    performed_by: Mapped[str | None] = mapped_column(String, nullable=True)
