from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel

# status values: DRAFT, SUBMITTED, PENDING_MANAGER_APPROVAL, APPROVED, REJECTED, CANCELLED.
# priority values: LOW, MEDIUM, HIGH, URGENT.
# item_type values: DEVICE, DEVICE_MODEL, CONSUMABLE, SPARE_PART, OTHER, CONDEMNATION.
#
# Module 16A — Hospital Requirement & Approval Workflow (backend/domain foundation only).
# Covers only the requirement-raise -> manager-approve/reject stages. Inventory
# fulfillment (availability check, allocation/reservation, pack, dispatch, delivery,
# inventory stock update) is Module 17A — see models/fulfillment.py,
# services/fulfillment_service.py and PROJECT_STATUS.md.


class Requirement(BaseModel):
    __tablename__ = "requirements"

    request_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"), index=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    requester_role: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[str] = mapped_column(String, default="MEDIUM")
    required_by_date: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="DRAFT", index=True)
    manager_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    manager_decision: Mapped[str | None] = mapped_column(String, nullable=True)
    manager_decision_at: Mapped[str | None] = mapped_column(String, nullable=True)
    manager_remarks: Mapped[str | None] = mapped_column(String, nullable=True)

    items: Mapped[list["RequirementItem"]] = relationship(back_populates="requirement", cascade="all, delete-orphan")


class RequirementItem(BaseModel):
    """A single requested line item. Not forced into one device-specific field — an item
    references exactly one of device_model_id / inventory_item_id / consumable_id /
    asset_id depending on item_type, so the domain can grow to cover devices, spare
    parts, consumables, free-text 'other' requests, and condemnation of a specific
    already-installed asset without new tables.

    asset_id (business-workflow audit, condemnation gap) is distinct from
    device_model_id: DEVICE/DEVICE_MODEL items reference a catalog/type (DeviceModel)
    when requesting new stock, while a CONDEMNATION item references one real,
    already-installed physical Asset to be retired — never a DeviceModel."""

    __tablename__ = "requirement_items"

    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), index=True)
    item_type: Mapped[str] = mapped_column(String)
    device_model_id: Mapped[str | None] = mapped_column(ForeignKey("device_models.id"), nullable=True)
    inventory_item_id: Mapped[str | None] = mapped_column(ForeignKey("inventory_items.id"), nullable=True)
    consumable_id: Mapped[str | None] = mapped_column(ForeignKey("consumables.id"), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    requested_quantity: Mapped[int] = mapped_column(Integer)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String, nullable=True)

    requirement: Mapped["Requirement"] = relationship(back_populates="items")


class RequirementStatusHistory(BaseModel):
    """Every state transition a requirement goes through — the approval audit trail
    Phase 5/8 require. Mirrors IncidentStatusHistory's shape/conventions."""

    __tablename__ = "requirement_status_history"

    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String, nullable=True)
    to_status: Mapped[str] = mapped_column(String)
    changed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
