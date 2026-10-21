from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel

# status values: PENDING_ALLOCATION, ALLOCATED, UNDER_PROCESS, PACKED, OUT_FOR_DELIVERY,
# DELIVERED, COMPLETED, CANCELLED.
#
# Module 17A — Inventory Fulfillment & Delivery Workflow. Executes an APPROVED
# Requirement (Module 16A): availability check -> allocation/reservation -> pack ->
# dispatch -> delivery -> inventory stock update -> completion. One FulfillmentRequest
# per Requirement (1:1, enforced by the unique requirement_id below); one FulfillmentItem
# per RequirementItem, carrying allocated/delivered quantities for spare
# parts/consumables/other, or a set of FulfillmentItemAsset links for serialized
# devices. Stock/asset commitment (InventoryTransaction rows, Consumable quantity
# deduction, Asset relocation) only happens at the COMPLETED transition — allocation
# before that is a reservation only, computed by subtracting other active fulfillments'
# allocated quantities from real availability (see services/fulfillment_service.py).


class FulfillmentRequest(BaseModel):
    __tablename__ = "fulfillment_requests"

    fulfillment_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String, default="PENDING_ALLOCATION", index=True)
    source_location_id: Mapped[str | None] = mapped_column(ForeignKey("inventory_locations.id"), nullable=True)
    inventory_manager_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    expected_delivery_date: Mapped[str | None] = mapped_column(String, nullable=True)
    actual_delivery_date: Mapped[str | None] = mapped_column(String, nullable=True)
    delivered_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    delivery_remarks: Mapped[str | None] = mapped_column(String, nullable=True)
    cancel_remarks: Mapped[str | None] = mapped_column(String, nullable=True)

    items: Mapped[list["FulfillmentItem"]] = relationship(back_populates="fulfillment", cascade="all, delete-orphan")


class FulfillmentItem(BaseModel):
    """One row per RequirementItem being fulfilled. `allocated_quantity` covers
    non-serialized items (SPARE_PART/CONSUMABLE/OTHER); serialized DEVICE/DEVICE_MODEL
    items are tracked individually via `assets` instead, with allocated_quantity kept
    in sync (== number of linked FulfillmentItemAsset rows) for a consistent quantity
    display across item types."""

    __tablename__ = "fulfillment_items"

    fulfillment_id: Mapped[str] = mapped_column(ForeignKey("fulfillment_requests.id"), index=True)
    requirement_item_id: Mapped[str] = mapped_column(ForeignKey("requirement_items.id"), index=True)
    allocated_quantity: Mapped[int] = mapped_column(Integer, default=0)
    delivered_quantity: Mapped[int] = mapped_column(Integer, default=0)

    fulfillment: Mapped["FulfillmentRequest"] = relationship(back_populates="items")
    assets: Mapped[list["FulfillmentItemAsset"]] = relationship(
        back_populates="fulfillment_item", cascade="all, delete-orphan"
    )


class FulfillmentItemAsset(BaseModel):
    """Links a specific serialized Asset to a FulfillmentItem (DEVICE/DEVICE_MODEL item
    types only). Presence of a non-delivered row for an asset_id, on a fulfillment whose
    status is not CANCELLED, is what makes that asset unavailable to any other
    fulfillment — see fulfillment_service.compute_asset_availability."""

    __tablename__ = "fulfillment_item_assets"

    fulfillment_item_id: Mapped[str] = mapped_column(ForeignKey("fulfillment_items.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)

    fulfillment_item: Mapped["FulfillmentItem"] = relationship(back_populates="assets")


class FulfillmentStatusHistory(BaseModel):
    """Every state transition a fulfillment goes through. Mirrors
    RequirementStatusHistory's shape/conventions."""

    __tablename__ = "fulfillment_status_history"

    fulfillment_id: Mapped[str] = mapped_column(ForeignKey("fulfillment_requests.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String, nullable=True)
    to_status: Mapped[str] = mapped_column(String)
    changed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
