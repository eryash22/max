from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel

# Module 21 — Procurement (BME incident -> technical inspection -> AMC/CMC/Warranty
# check -> [fully covered: vendor service, no quotation] / [partial or not covered:
# vendor quotation -> Manager approval -> Indent/CR -> PO -> vendor visit -> BME
# repair -> invoice -> Inventory Manager verification -> financial closure]).
#
# Reuses (does not duplicate): Incident, Asset, Vendor, Contract coverage
# (services/asset_lifecycle_service.get_incident_coverage), ServiceVisit (vendor visit
# scheduling/outcome — see models/incident.py, extended here with purchase_order_id
# only), Document/DocumentLink (attachments via entity_type/entity_id), AuditLog,
# Notification/NotificationTemplate.
#
# quotation.status values: DRAFT, SUBMITTED, APPROVED, REJECTED, CANCELLED.
# quotation.coverage_status values: NOT_COVERED, PARTIALLY_COVERED. "FULLY_COVERED" is
# a decision, never a stored state — creation is blocked before any row is written
# (services/procurement_service.py) since a fully covered repair never becomes a
# quotation at all, it goes straight to vendor service under the existing contract.
# indent.status values: DRAFT, SUBMITTED, COMPLETED — no second approval gate; the
# Manager decision already happened at the quotation stage. CANCELLED added (Batch F)
# for the DRAFT/SUBMITTED archive path — see routers/procurement.py:cancel_indent.
# purchase_order.status values (six states, no extra approval state):
# DRAFT, ISSUED, ACKNOWLEDGED, IN_PROGRESS, DELIVERED, COMPLETED. CANCELLED added
# (Batch F) for the DRAFT/ISSUED archive path — see
# routers/procurement.py:cancel_purchase_order. Never a PO_TRANSITIONS target (that
# dict models only the forward DRAFT->...->COMPLETED workflow); cancellation is a
# separate action, same as Quotation's cancel vs submit/approve/reject.
# invoice.verification_status values: PENDING, VERIFIED, REJECTED.


class Quotation(BaseModel):
    __tablename__ = "quotations"

    quotation_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), index=True)
    quote_date: Mapped[str | None] = mapped_column(String, nullable=True)
    valid_until: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="DRAFT", index=True)
    coverage_status: Mapped[str] = mapped_column(String, default="NOT_COVERED")
    coverage_note: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_at: Mapped[str | None] = mapped_column(String, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    total: Mapped[float] = mapped_column(Float, default=0.0)

    lines: Mapped[list["QuotationLine"]] = relationship(back_populates="quotation", cascade="all, delete-orphan")


class QuotationLine(BaseModel):
    __tablename__ = "quotation_lines"

    quotation_id: Mapped[str] = mapped_column(ForeignKey("quotations.id"), index=True)
    description: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    taxes: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    # Per-line coverage: whether this repair line is covered by the asset's active
    # AMC/CMC/Warranty contract. Drives the fully/partially/not-covered decision and
    # which lines proceed to procurement vs the contract/vendor-service path.
    covered: Mapped[bool] = mapped_column(Boolean, default=False)

    quotation: Mapped["Quotation"] = relationship(back_populates="lines")


class Indent(BaseModel):
    """Indent / Change Request — one entity, no per-CR-type subclassing (per spec).
    Raised by Procurement Officer against an APPROVED quotation's uncovered lines."""

    __tablename__ = "indents"

    indent_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    quotation_id: Mapped[str] = mapped_column(ForeignKey("quotations.id"), index=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), index=True)
    requester_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String, default="DRAFT", index=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)


class PurchaseOrder(BaseModel):
    __tablename__ = "purchase_orders"

    po_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    indent_id: Mapped[str] = mapped_column(ForeignKey("indents.id"), index=True)
    quotation_id: Mapped[str] = mapped_column(ForeignKey("quotations.id"), index=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), index=True)
    issue_date: Mapped[str | None] = mapped_column(String, nullable=True)
    expected_vendor_date: Mapped[str | None] = mapped_column(String, nullable=True)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String, default="DRAFT", index=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)

    lines: Mapped[list["PurchaseOrderLine"]] = relationship(back_populates="purchase_order", cascade="all, delete-orphan")


class PurchaseOrderLine(BaseModel):
    __tablename__ = "purchase_order_lines"

    purchase_order_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    quotation_line_id: Mapped[str | None] = mapped_column(ForeignKey("quotation_lines.id"), nullable=True)
    description: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)

    purchase_order: Mapped["PurchaseOrder"] = relationship(back_populates="lines")


class Invoice(BaseModel):
    __tablename__ = "invoices"

    invoice_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    purchase_order_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), index=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    invoice_date: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    recorded_by: Mapped[str | None] = mapped_column(String, nullable=True)
    recorded_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # Financial closure — independent of Incident technical closure (BME owns that via
    # the existing Incident workflow; never hard-blocked on this).
    verification_status: Mapped[str] = mapped_column(String, default="PENDING", index=True)
    verified_by: Mapped[str | None] = mapped_column(String, nullable=True)
    verified_at: Mapped[str | None] = mapped_column(String, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String, nullable=True)
