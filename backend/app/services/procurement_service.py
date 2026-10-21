"""Module 21 — Procurement.

BME device incident -> technical inspection -> AMC/CMC/Warranty check (reuses
asset_lifecycle_service.get_incident_coverage; nothing duplicated onto Quotation/
Indent/PurchaseOrder/Invoice) -> fully covered: vendor service under the existing
contract, a quotation is never created for it -> partially/not covered: Quotation
-> Manager approval -> Indent/CR -> Purchase Order -> vendor visit (ServiceVisit,
reused as-is) -> BME repair -> Invoice -> Inventory Manager verification -> PO
COMPLETED (financial closure). Technical closure (BME, existing Incident workflow)
and financial closure (this module) are independent — nothing here ever blocks or
reads Incident.status.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..models.device import Asset
from ..models.incident import Incident
from ..models.procurement import Indent, Invoice, PurchaseOrder, PurchaseOrderLine, Quotation, QuotationLine
from ..models.vendor import Vendor
from ..schemas.notification import (
    EVENT_INDENT_CREATED,
    EVENT_INVOICE_RECORDED,
    EVENT_INVOICE_VERIFIED,
    EVENT_PO_CREATED,
    EVENT_PROCUREMENT_COMPLETED,
    EVENT_QUOTATION_APPROVED,
    EVENT_QUOTATION_REJECTED,
    EVENT_QUOTATION_SUBMITTED,
)
from ..schemas.procurement import (
    AssetProcurementProvenanceEntryOut,
    IncidentProcurementSummaryOut,
    IndentIn,
    IndentUpdateIn,
    InvoiceIn,
    InvoiceRejectIn,
    InvoiceUpdateIn,
    PurchaseOrderIn,
    PurchaseOrderLineIn,
    PurchaseOrderUpdateIn,
    QuotationDecisionIn,
    QuotationIn,
    QuotationLineIn,
    QuotationRejectIn,
    QuotationUpdateIn,
)
from . import asset_lifecycle_service
from .communication_service import dispatch_procurement_event_safe

PO_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"ISSUED"},
    "ISSUED": {"ACKNOWLEDGED"},
    "ACKNOWLEDGED": {"IN_PROGRESS"},
    "IN_PROGRESS": {"DELIVERED"},
    "DELIVERED": {"COMPLETED"},
    "COMPLETED": set(),
}


class ProcurementServiceError(Exception):
    """Generic 422-mappable validation failure."""


class ProcurementReferenceError(ProcurementServiceError):
    """A referenced entity (incident/asset/vendor/quotation/indent/PO) does not
    exist — 404-mappable."""


class ProcurementStateError(ProcurementServiceError):
    """An action was attempted from a status that does not allow it — 409-mappable."""


class ProcurementPermissionError(ProcurementServiceError):
    """A business rule (self-approval, self-verification) blocks the action —
    403-mappable."""


class ProcurementCoverageError(ProcurementServiceError):
    """The asset is fully covered under an active AMC/CMC/Warranty contract — a
    quotation must never be created for it; the repair goes through vendor service
    under the contract instead. 422-mappable."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_number(db: Session, model, field, prefix: str) -> str:
    count = db.query(model).count()
    column = getattr(model, field)
    for attempt in range(count + 1, count + 1000):
        candidate = f"{prefix}-{attempt:06d}"
        if db.query(model).filter(column == candidate).first() is None:
            return candidate
    raise ProcurementServiceError(f"unable to allocate a {prefix} number")


# ---- Quotation ----

def _price_lines_and_coverage(
    db: Session, *, asset_id: str, lines: list[QuotationLineIn], coverage_note: str | None,
) -> tuple[str, float, list[dict]]:
    """Shared by create_quotation and update_quotation so an edited quotation can
    never drift from the exact fully/partially/not-covered creation rule. Returns
    (coverage_status, total, priced_lines) or raises ProcurementCoverageError /
    ProcurementServiceError — never writes anything itself."""
    coverage = asset_lifecycle_service.get_incident_coverage(db, asset_id)
    asset_covered = coverage.coverage_status != "NOT_COVERED"
    lines_covered = [line.covered for line in lines]

    if asset_covered and all(lines_covered):
        raise ProcurementCoverageError(
            "This asset is fully covered under an active "
            f"{coverage.coverage_status} contract — route this repair through vendor "
            "service under the contract; a procurement quotation must not be created."
        )

    if asset_covered and any(lines_covered):
        coverage_status = "PARTIALLY_COVERED"
        if not coverage_note or not coverage_note.strip():
            raise ProcurementServiceError("coverage_note is required for a partially covered repair")
    else:
        coverage_status = "NOT_COVERED"

    total = 0.0
    priced_lines = []
    for line in lines:
        # NOT_COVERED quotations proceed to procurement in full — force every line
        # uncovered so downstream indent/PO amounts are never silently short, even if
        # the caller sent covered=True on an asset with no active coverage at all.
        covered = line.covered if asset_covered else False
        line_total = round(line.quantity * line.unit_price + line.taxes, 2)
        total += line_total
        priced_lines.append({
            "description": line.description, "quantity": line.quantity, "unit_price": line.unit_price,
            "taxes": line.taxes, "total": line_total, "covered": covered,
        })
    return coverage_status, round(total, 2), priced_lines


def create_quotation(db: Session, payload: QuotationIn, *, user_id: str) -> Quotation:
    incident = db.get(Incident, payload.incident_id)
    if incident is None:
        raise ProcurementReferenceError("Incident not found")
    asset = db.get(Asset, payload.asset_id)
    if asset is None:
        raise ProcurementReferenceError("Asset not found")
    vendor = db.get(Vendor, payload.vendor_id)
    if vendor is None:
        raise ProcurementReferenceError("Vendor not found")

    coverage_status, total, priced_lines = _price_lines_and_coverage(
        db, asset_id=payload.asset_id, lines=payload.lines, coverage_note=payload.coverage_note,
    )

    quotation = Quotation(
        quotation_number=_next_number(db, Quotation, "quotation_number", "QUO"),
        incident_id=payload.incident_id,
        asset_id=payload.asset_id,
        vendor_id=payload.vendor_id,
        quote_date=payload.quote_date,
        valid_until=payload.valid_until,
        status="DRAFT",
        coverage_status=coverage_status,
        coverage_note=payload.coverage_note,
        created_by=user_id,
        total=total,
    )
    db.add(quotation)
    db.flush()

    for line in priced_lines:
        db.add(QuotationLine(quotation_id=quotation.id, **line))

    audit_record(
        db, user_id=user_id, action="CREATE", entity="quotation", entity_id=quotation.id,
        new_value={
            "incident_id": payload.incident_id, "asset_id": payload.asset_id, "vendor_id": payload.vendor_id,
            "coverage_status": coverage_status, "coverage_note": payload.coverage_note, "total": quotation.total,
        },
    )
    db.commit()
    db.refresh(quotation)
    return quotation


def update_quotation(db: Session, quotation: Quotation, payload: QuotationUpdateIn, *, user_id: str) -> Quotation:
    """Only a DRAFT quotation may be edited — see QuotationUpdateIn docstring."""
    if quotation.status != "DRAFT":
        raise ProcurementStateError(f"a {quotation.status} quotation cannot be edited — only DRAFT can")

    changes = payload.model_dump(exclude_unset=True, exclude={"lines"})
    before = {
        "quote_date": quotation.quote_date, "valid_until": quotation.valid_until,
        "coverage_note": quotation.coverage_note, "total": quotation.total, "coverage_status": quotation.coverage_status,
    }
    for field, value in changes.items():
        setattr(quotation, field, value)

    if payload.lines is not None:
        coverage_note = payload.coverage_note if "coverage_note" in changes else quotation.coverage_note
        coverage_status, total, priced_lines = _price_lines_and_coverage(
            db, asset_id=quotation.asset_id, lines=payload.lines, coverage_note=coverage_note,
        )
        for line in list(quotation.lines):
            db.delete(line)
        db.flush()
        for line in priced_lines:
            db.add(QuotationLine(quotation_id=quotation.id, **line))
        quotation.coverage_status = coverage_status
        quotation.total = total

    audit_record(db, user_id=user_id, action="UPDATE", entity="quotation", entity_id=quotation.id,
                 old_value=before, new_value={**changes, "total": quotation.total, "coverage_status": quotation.coverage_status})
    db.commit()
    db.refresh(quotation)
    return quotation


def submit_quotation(db: Session, quotation: Quotation, *, user_id: str) -> Quotation:
    if quotation.status != "DRAFT":
        raise ProcurementStateError("only a DRAFT quotation can be submitted")
    quotation.status = "SUBMITTED"
    audit_record(db, user_id=user_id, action="SUBMIT", entity="quotation", entity_id=quotation.id,
                 old_value={"status": "DRAFT"}, new_value={"status": "SUBMITTED"})
    db.commit()
    db.refresh(quotation)
    dispatch_procurement_event_safe(db, EVENT_QUOTATION_SUBMITTED, entity_type="quotation", entity=quotation, user_id=user_id)
    return quotation


def cancel_quotation(db: Session, quotation: Quotation, *, user_id: str) -> Quotation:
    if quotation.status not in ("DRAFT", "SUBMITTED"):
        raise ProcurementStateError("only a DRAFT or SUBMITTED quotation can be cancelled")
    before = {"status": quotation.status}
    quotation.status = "CANCELLED"
    audit_record(db, user_id=user_id, action="CANCEL", entity="quotation", entity_id=quotation.id,
                 old_value=before, new_value={"status": "CANCELLED"})
    db.commit()
    db.refresh(quotation)
    return quotation


def approve_quotation(db: Session, quotation: Quotation, payload: QuotationDecisionIn, *, manager_id: str) -> Quotation:
    if quotation.status != "SUBMITTED":
        raise ProcurementStateError("only a SUBMITTED quotation can be approved")
    if quotation.created_by == manager_id:
        raise ProcurementPermissionError("a user cannot approve a quotation they created themselves")

    quotation.status = "APPROVED"
    quotation.decided_by = manager_id
    quotation.decided_at = _now_iso()
    audit_record(db, user_id=manager_id, action="APPROVE", entity="quotation", entity_id=quotation.id,
                 old_value={"status": "SUBMITTED"}, new_value={"status": "APPROVED", "remarks": payload.remarks})
    db.commit()
    db.refresh(quotation)
    dispatch_procurement_event_safe(db, EVENT_QUOTATION_APPROVED, entity_type="quotation", entity=quotation, user_id=manager_id)
    return quotation


def reject_quotation(db: Session, quotation: Quotation, payload: QuotationRejectIn, *, manager_id: str) -> Quotation:
    if quotation.status != "SUBMITTED":
        raise ProcurementStateError("only a SUBMITTED quotation can be rejected")
    if quotation.created_by == manager_id:
        raise ProcurementPermissionError("a user cannot reject a quotation they created themselves")

    quotation.status = "REJECTED"
    quotation.decided_by = manager_id
    quotation.decided_at = _now_iso()
    quotation.rejection_reason = payload.rejection_reason
    audit_record(db, user_id=manager_id, action="REJECT", entity="quotation", entity_id=quotation.id,
                 old_value={"status": "SUBMITTED"}, new_value={"status": "REJECTED", "rejection_reason": payload.rejection_reason})
    db.commit()
    db.refresh(quotation)
    dispatch_procurement_event_safe(db, EVENT_QUOTATION_REJECTED, entity_type="quotation", entity=quotation, user_id=manager_id)
    return quotation


# ---- Indent / CR ----

def create_indent(db: Session, payload: IndentIn, *, user_id: str) -> Indent:
    quotation = db.get(Quotation, payload.quotation_id)
    if quotation is None:
        raise ProcurementReferenceError("Quotation not found")
    if quotation.status != "APPROVED":
        raise ProcurementStateError("an indent can only be raised against an APPROVED quotation")

    uncovered_lines = [line for line in quotation.lines if not line.covered]
    if not uncovered_lines:
        raise ProcurementServiceError(
            "quotation has no uncovered lines to procure — covered lines go the contract/vendor-service path"
        )
    amount = round(sum(line.total for line in uncovered_lines), 2)

    indent = Indent(
        indent_number=_next_number(db, Indent, "indent_number", "IND"),
        quotation_id=quotation.id, incident_id=quotation.incident_id, asset_id=quotation.asset_id,
        vendor_id=quotation.vendor_id, requester_id=user_id, amount=amount, status="DRAFT",
        notes=payload.notes,
    )
    db.add(indent)
    db.flush()
    audit_record(db, user_id=user_id, action="CREATE", entity="indent", entity_id=indent.id,
                 new_value={"quotation_id": quotation.id, "amount": amount})
    db.commit()
    db.refresh(indent)
    return indent


def submit_indent(db: Session, indent: Indent, *, user_id: str) -> Indent:
    """DRAFT -> SUBMITTED. No second approval gate here — the Manager decision
    already happened at the quotation stage; SUBMITTED just means the Procurement
    Officer has finished preparing the indent and it is ready for PO creation."""
    if indent.status != "DRAFT":
        raise ProcurementStateError("only a DRAFT indent can be submitted")
    indent.status = "SUBMITTED"
    audit_record(db, user_id=user_id, action="SUBMIT", entity="indent", entity_id=indent.id,
                 old_value={"status": "DRAFT"}, new_value={"status": "SUBMITTED"})
    db.commit()
    db.refresh(indent)
    dispatch_procurement_event_safe(db, EVENT_INDENT_CREATED, entity_type="indent", entity=indent, user_id=user_id)
    return indent


def _complete_indent(db: Session, indent: Indent, *, user_id: str) -> None:
    if indent.status == "COMPLETED":
        return
    before = {"status": indent.status}
    indent.status = "COMPLETED"
    audit_record(db, user_id=user_id, action="UPDATE", entity="indent", entity_id=indent.id,
                 old_value=before, new_value={"status": "COMPLETED"})


def update_indent(db: Session, indent: Indent, payload: IndentUpdateIn, *, user_id: str) -> Indent:
    """Only a DRAFT or SUBMITTED indent may be edited — see IndentUpdateIn
    docstring. `notes` is the only editable field."""
    if indent.status not in ("DRAFT", "SUBMITTED"):
        raise ProcurementStateError(f"a {indent.status} indent cannot be edited")
    changes = payload.model_dump(exclude_unset=True)
    before = {"notes": indent.notes}
    for field, value in changes.items():
        setattr(indent, field, value)
    audit_record(db, user_id=user_id, action="UPDATE", entity="indent", entity_id=indent.id,
                 old_value=before, new_value=changes)
    db.commit()
    db.refresh(indent)
    return indent


def cancel_indent(db: Session, indent: Indent, *, user_id: str) -> Indent:
    """Batch F — archive path for an indent that should never proceed to a Purchase
    Order (mirrors cancel_quotation). Only DRAFT/SUBMITTED indents are cancellable —
    once a PO has been created the indent is auto-completed (_complete_indent) and a
    downstream reference exists, so cancellation is no longer meaningful; the PO
    itself should be cancelled instead (see cancel_purchase_order)."""
    if indent.status not in ("DRAFT", "SUBMITTED"):
        raise ProcurementStateError("only a DRAFT or SUBMITTED indent can be cancelled")
    before = {"status": indent.status}
    indent.status = "CANCELLED"
    audit_record(db, user_id=user_id, action="CANCEL", entity="indent", entity_id=indent.id,
                 old_value=before, new_value={"status": "CANCELLED"})
    db.commit()
    db.refresh(indent)
    return indent


# ---- Purchase Order ----

def create_purchase_order(db: Session, payload: PurchaseOrderIn, *, user_id: str) -> PurchaseOrder:
    indent = db.get(Indent, payload.indent_id)
    if indent is None:
        raise ProcurementReferenceError("Indent not found")
    if indent.status != "SUBMITTED":
        raise ProcurementStateError("a purchase order can only be created from a SUBMITTED indent")
    quotation = db.get(Quotation, indent.quotation_id)

    po = PurchaseOrder(
        po_number=_next_number(db, PurchaseOrder, "po_number", "PO"),
        indent_id=indent.id, quotation_id=indent.quotation_id, incident_id=indent.incident_id,
        asset_id=indent.asset_id, vendor_id=indent.vendor_id, issue_date=payload.issue_date,
        expected_vendor_date=payload.expected_vendor_date, status="DRAFT", created_by=user_id, total=0.0,
    )
    db.add(po)
    db.flush()

    source_lines = payload.lines
    total = 0.0
    if source_lines:
        for line in source_lines:
            line_total = round(line.quantity * line.unit_price, 2)
            total += line_total
            db.add(PurchaseOrderLine(
                purchase_order_id=po.id, quotation_line_id=line.quotation_line_id, description=line.description,
                quantity=line.quantity, unit_price=line.unit_price, total=line_total,
            ))
    else:
        # Default: mirror the quotation's uncovered lines one-for-one — the amount the
        # indent was raised for.
        uncovered = [line for line in (quotation.lines if quotation else []) if not line.covered]
        for line in uncovered:
            total += line.total
            db.add(PurchaseOrderLine(
                purchase_order_id=po.id, quotation_line_id=line.id, description=line.description,
                quantity=line.quantity, unit_price=line.unit_price, total=line.total,
            ))
    po.total = round(total, 2)

    audit_record(db, user_id=user_id, action="CREATE", entity="purchase_order", entity_id=po.id,
                 new_value={"indent_id": indent.id, "quotation_id": indent.quotation_id, "total": po.total})
    _complete_indent(db, indent, user_id=user_id)
    db.commit()
    db.refresh(po)
    dispatch_procurement_event_safe(db, EVENT_PO_CREATED, entity_type="purchase_order", entity=po, user_id=user_id)
    return po


def update_purchase_order(db: Session, po: PurchaseOrder, payload: PurchaseOrderUpdateIn, *, user_id: str) -> PurchaseOrder:
    """Only a DRAFT purchase order may be edited — see PurchaseOrderUpdateIn
    docstring."""
    if po.status != "DRAFT":
        raise ProcurementStateError(f"a {po.status} purchase order cannot be edited — only DRAFT can")

    changes = payload.model_dump(exclude_unset=True, exclude={"lines"})
    before = {"issue_date": po.issue_date, "expected_vendor_date": po.expected_vendor_date, "total": po.total}
    for field, value in changes.items():
        setattr(po, field, value)

    if payload.lines is not None:
        total = 0.0
        for line in list(po.lines):
            db.delete(line)
        db.flush()
        for line in payload.lines:
            line_total = round(line.quantity * line.unit_price, 2)
            total += line_total
            db.add(PurchaseOrderLine(
                purchase_order_id=po.id, quotation_line_id=line.quotation_line_id, description=line.description,
                quantity=line.quantity, unit_price=line.unit_price, total=line_total,
            ))
        po.total = round(total, 2)

    audit_record(db, user_id=user_id, action="UPDATE", entity="purchase_order", entity_id=po.id,
                 old_value=before, new_value={**changes, "total": po.total})
    db.commit()
    db.refresh(po)
    return po


def cancel_purchase_order(db: Session, po: PurchaseOrder, *, user_id: str) -> PurchaseOrder:
    """Batch F — archive path for a purchase order (mirrors cancel_quotation /
    cancel_indent). Only DRAFT/ISSUED POs are cancellable — once the vendor has
    ACKNOWLEDGED it, real-world fulfillment work is underway and the PO must run its
    course through the normal DELIVERED/COMPLETED lifecycle instead. Never a
    PO_TRANSITIONS target; this is a separate action, not a forward-workflow step."""
    if po.status not in ("DRAFT", "ISSUED"):
        raise ProcurementStateError("only a DRAFT or ISSUED purchase order can be cancelled")
    before = {"status": po.status}
    po.status = "CANCELLED"
    audit_record(db, user_id=user_id, action="CANCEL", entity="purchase_order", entity_id=po.id,
                 old_value=before, new_value={"status": "CANCELLED"})
    db.commit()
    db.refresh(po)
    return po


def update_po_status(db: Session, po: PurchaseOrder, to_status: str, *, user_id: str) -> PurchaseOrder:
    allowed = PO_TRANSITIONS.get(po.status, set())
    if to_status not in allowed:
        raise ProcurementStateError(f"cannot move purchase order from {po.status} to {to_status}")
    from_status = po.status
    po.status = to_status
    action = "SEND" if to_status == "ISSUED" else "UPDATE"
    audit_record(db, user_id=user_id, action=action, entity="purchase_order", entity_id=po.id,
                 old_value={"status": from_status}, new_value={"status": to_status})
    db.commit()
    db.refresh(po)
    return po


# ---- Invoice ----

def record_invoice(db: Session, payload: InvoiceIn, *, user_id: str) -> Invoice:
    po = db.get(PurchaseOrder, payload.purchase_order_id)
    if po is None:
        raise ProcurementReferenceError("Purchase order not found")

    invoice = Invoice(
        invoice_number=_next_number(db, Invoice, "invoice_number", "INV"),
        purchase_order_id=po.id, vendor_id=po.vendor_id, incident_id=po.incident_id, asset_id=po.asset_id,
        invoice_date=payload.invoice_date, amount=payload.amount, recorded_by=user_id, recorded_at=_now_iso(),
        verification_status="PENDING",
    )
    db.add(invoice)
    db.flush()
    audit_record(db, user_id=user_id, action="CREATE", entity="invoice", entity_id=invoice.id,
                 new_value={"purchase_order_id": po.id, "amount": payload.amount})
    db.commit()
    db.refresh(invoice)
    dispatch_procurement_event_safe(db, EVENT_INVOICE_RECORDED, entity_type="invoice", entity=invoice, user_id=user_id)
    return invoice


def update_invoice(db: Session, invoice: Invoice, payload: InvoiceUpdateIn, *, user_id: str) -> Invoice:
    """Only a PENDING invoice may be edited — see InvoiceUpdateIn docstring."""
    if invoice.verification_status != "PENDING":
        raise ProcurementStateError(f"a {invoice.verification_status} invoice cannot be edited")
    changes = payload.model_dump(exclude_unset=True)
    before = {"invoice_date": invoice.invoice_date, "amount": invoice.amount}
    for field, value in changes.items():
        setattr(invoice, field, value)
    audit_record(db, user_id=user_id, action="UPDATE", entity="invoice", entity_id=invoice.id,
                 old_value=before, new_value=changes)
    db.commit()
    db.refresh(invoice)
    return invoice


def verify_invoice(db: Session, invoice: Invoice, *, user_id: str) -> Invoice:
    """Financial closure. Independent of Incident technical closure — never touches
    Incident.status. PO becomes COMPLETED here (financial procurement closure)."""
    if invoice.verification_status != "PENDING":
        raise ProcurementStateError("only a PENDING invoice can be verified")
    if invoice.recorded_by == user_id:
        raise ProcurementPermissionError("a user cannot verify an invoice they recorded themselves")

    invoice.verification_status = "VERIFIED"
    invoice.verified_by = user_id
    invoice.verified_at = _now_iso()
    audit_record(db, user_id=user_id, action="VERIFY", entity="invoice", entity_id=invoice.id,
                 old_value={"verification_status": "PENDING"}, new_value={"verification_status": "VERIFIED"})

    po = db.get(PurchaseOrder, invoice.purchase_order_id)
    if po is not None and po.status != "COMPLETED":
        before = {"status": po.status}
        po.status = "COMPLETED"
        audit_record(db, user_id=user_id, action="UPDATE", entity="purchase_order", entity_id=po.id,
                     old_value=before, new_value={"status": "COMPLETED"})

    db.commit()
    db.refresh(invoice)
    dispatch_procurement_event_safe(db, EVENT_INVOICE_VERIFIED, entity_type="invoice", entity=invoice, user_id=user_id)
    if po is not None:
        db.refresh(po)
        dispatch_procurement_event_safe(db, EVENT_PROCUREMENT_COMPLETED, entity_type="purchase_order", entity=po, user_id=user_id)
    return invoice


def reject_invoice(db: Session, invoice: Invoice, payload: InvoiceRejectIn, *, user_id: str) -> Invoice:
    if invoice.verification_status != "PENDING":
        raise ProcurementStateError("only a PENDING invoice can be rejected")
    if invoice.recorded_by == user_id:
        raise ProcurementPermissionError("a user cannot reject an invoice they recorded themselves")

    invoice.verification_status = "REJECTED"
    invoice.verified_by = user_id
    invoice.verified_at = _now_iso()
    invoice.rejection_reason = payload.rejection_reason
    audit_record(db, user_id=user_id, action="REJECT", entity="invoice", entity_id=invoice.id,
                 old_value={"verification_status": "PENDING"},
                 new_value={"verification_status": "REJECTED", "rejection_reason": payload.rejection_reason})
    db.commit()
    db.refresh(invoice)
    return invoice


# ---- Incident / Asset provenance summaries ----
#
# Read-only rollups computed fresh on every read (never stored), same discipline as
# asset_lifecycle_service.get_incident_coverage — no procurement fields are ever
# duplicated onto Incident or Asset.

def get_incident_procurement_summary(db: Session, incident_id: str) -> IncidentProcurementSummaryOut:
    quotations = db.query(Quotation).filter(Quotation.incident_id == incident_id).order_by(Quotation.created_at).all()
    indents = db.query(Indent).filter(Indent.incident_id == incident_id).order_by(Indent.created_at).all()
    purchase_orders = (
        db.query(PurchaseOrder).filter(PurchaseOrder.incident_id == incident_id).order_by(PurchaseOrder.created_at).all()
    )
    invoices = db.query(Invoice).filter(Invoice.incident_id == incident_id).order_by(Invoice.created_at).all()

    # Financial closure: every non-cancelled PO raised for this incident has reached
    # COMPLETED (invoice recorded + Inventory Manager verified). Independent of, and
    # never blocking, Incident technical closure — see module docstring.
    financially_closed = bool(purchase_orders) and all(po.status == "COMPLETED" for po in purchase_orders)

    return IncidentProcurementSummaryOut(
        quotations=list(quotations), indents=list(indents), purchase_orders=list(purchase_orders),
        invoices=list(invoices), financially_closed=financially_closed,
    )


def list_asset_procurement_provenance(db: Session, asset_id: str) -> list[AssetProcurementProvenanceEntryOut]:
    quotations = db.query(Quotation).filter(Quotation.asset_id == asset_id).order_by(Quotation.created_at.desc()).all()
    entries: list[AssetProcurementProvenanceEntryOut] = []
    for quotation in quotations:
        indent = db.query(Indent).filter(Indent.quotation_id == quotation.id).first()
        po = db.query(PurchaseOrder).filter(PurchaseOrder.quotation_id == quotation.id).first()
        invoice = db.query(Invoice).filter(Invoice.purchase_order_id == po.id).first() if po else None
        entries.append(AssetProcurementProvenanceEntryOut(
            quotation_id=quotation.id, quotation_number=quotation.quotation_number, quotation_status=quotation.status,
            coverage_status=quotation.coverage_status, incident_id=quotation.incident_id,
            indent_id=indent.id if indent else None, indent_number=indent.indent_number if indent else None,
            purchase_order_id=po.id if po else None, po_number=po.po_number if po else None,
            po_status=po.status if po else None,
            invoice_id=invoice.id if invoice else None, invoice_number=invoice.invoice_number if invoice else None,
            invoice_verification_status=invoice.verification_status if invoice else None,
        ))
    return entries
