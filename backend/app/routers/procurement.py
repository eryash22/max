"""Module 21 — Procurement API.

Quotation -> Manager approval -> Indent/CR -> Purchase Order -> Invoice ->
Inventory Manager verification. See services/procurement_service.py for the full
business-rule writeup (fully/partially/not-covered handling, segregation of
duties, financial-vs-technical closure independence).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import require_any_permission, require_permission
from ..models.device import Asset
from ..models.documents import Document, DocumentLink
from ..models.incident import ServiceVisit
from ..models.procurement import Indent, Invoice, PurchaseOrder, Quotation
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.incident import IncidentAttachmentIn, IncidentAttachmentOut
from ..schemas.procurement import (
    IndentIn,
    IndentOut,
    IndentUpdateIn,
    InvoiceIn,
    InvoiceOut,
    InvoiceRejectIn,
    InvoiceUpdateIn,
    PurchaseOrderIn,
    PurchaseOrderOut,
    PurchaseOrderStatusIn,
    PurchaseOrderUpdateIn,
    QuotationDecisionIn,
    QuotationIn,
    QuotationOut,
    QuotationRejectIn,
    QuotationUpdateIn,
)
from ..security.permissions import (
    INVOICE_APPROVE,
    INVOICE_RECORD,
    PO_MANAGE,
    PROCUREMENT_MANAGE,
    PROCUREMENT_READ,
    QUOTATION_APPROVE,
    QUOTATION_CREATE,
    QUOTATION_READ,
    branch_scope_id,
)
from ..services import procurement_service
from ..services.procurement_service import (
    ProcurementCoverageError,
    ProcurementPermissionError,
    ProcurementReferenceError,
    ProcurementServiceError,
    ProcurementStateError,
)

router = APIRouter(prefix="/api/v1/procurement", tags=["procurement"])

_QUOTATION_READ_ACCESS = require_any_permission(QUOTATION_CREATE, QUOTATION_READ, QUOTATION_APPROVE, PROCUREMENT_READ)
_PROCUREMENT_READ_ACCESS = require_any_permission(PROCUREMENT_MANAGE, PO_MANAGE, INVOICE_RECORD, INVOICE_APPROVE, PROCUREMENT_READ)


def _map_service_error(exc: ProcurementServiceError) -> HTTPException:
    if isinstance(exc, ProcurementReferenceError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ProcurementStateError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, ProcurementPermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ProcurementCoverageError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


def _scope_procurement_query(query, model, current_user: CurrentUser):
    """Restrict a procurement list to the caller's branch when they are branch-scoped.

    Quotation, Indent, PurchaseOrder and Invoice each carry a non-nullable `asset_id`
    (see models/procurement.py) and no branch_id of their own, so `Asset.branch_id` is
    the canonical branch relationship for all four — the same asset-join convention
    routers/service_visits.py and routers/pm.py use.

    Enforced regardless of which procurement permission the caller holds: role
    permissions are DB-driven and a Super Admin can grant procurement.read (or
    quotation.read, po.manage, invoice.record) to a branch role at runtime, so the
    current grant table is not a branch-isolation boundary."""
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is None:
        return query
    return query.join(Asset, Asset.id == model.asset_id).filter(Asset.branch_id == scoped_branch_id)


def _enforce_procurement_branch_scope(db: Session, row, current_user: CurrentUser, label: str) -> None:
    """Direct-ID counterpart of _scope_procurement_query — a branch-scoped caller must
    not reach another branch's quotation/indent/PO/invoice by id."""
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is None:
        return
    asset = db.get(Asset, row.asset_id)
    if asset is None or asset.branch_id != scoped_branch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")


def _get_or_404(db: Session, model, entity_id: str, label: str):
    obj = db.get(model, entity_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return obj


def _attachments_router(entity_type: str, url_segment: str, model, read_dep, write_dep):
    @router.get(f"/{url_segment}/{{entity_id}}/attachments", response_model=list[IncidentAttachmentOut],
                name=f"list_{entity_type}_attachments")
    def list_attachments(entity_id: str, db: Session = Depends(get_db), _: CurrentUser = Depends(read_dep)):
        _get_or_404(db, model, entity_id, entity_type)
        return (
            db.query(DocumentLink)
            .filter(DocumentLink.entity_type == entity_type, DocumentLink.entity_id == entity_id)
            .order_by(DocumentLink.created_at)
            .all()
        )

    @router.post(f"/{url_segment}/{{entity_id}}/attachments", response_model=IncidentAttachmentOut,
                 status_code=status.HTTP_201_CREATED, name=f"add_{entity_type}_attachment")
    def add_attachment(entity_id: str, payload: IncidentAttachmentIn, db: Session = Depends(get_db),
                        _: CurrentUser = Depends(write_dep)):
        entity = _get_or_404(db, model, entity_id, entity_type)
        if db.get(Document, payload.document_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        link = DocumentLink(document_id=payload.document_id, entity_type=entity_type, entity_id=entity.id)
        db.add(link)
        db.commit()
        db.refresh(link)
        return link


# ---- Quotations ----

@router.get("/quotations", response_model=Page[QuotationOut])
def list_quotations(page: int = 1, page_size: int = 25, incident_id: str | None = None, asset_id: str | None = None,
                     vendor_id: str | None = None, status_filter: str | None = None,
                     db: Session = Depends(get_db), current_user: CurrentUser = Depends(_QUOTATION_READ_ACCESS)):
    query = db.query(Quotation)
    query = _scope_procurement_query(query, Quotation, current_user)
    if incident_id:
        query = query.filter(Quotation.incident_id == incident_id)
    if asset_id:
        query = query.filter(Quotation.asset_id == asset_id)
    if vendor_id:
        query = query.filter(Quotation.vendor_id == vendor_id)
    if status_filter:
        query = query.filter(Quotation.status == status_filter)
    total = query.count()
    items = query.order_by(Quotation.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/quotations", response_model=QuotationOut, status_code=status.HTTP_201_CREATED)
def create_quotation(payload: QuotationIn, db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(require_permission(QUOTATION_CREATE))):
    try:
        return procurement_service.create_quotation(db, payload, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.get("/quotations/{quotation_id}", response_model=QuotationOut)
def get_quotation(quotation_id: str, db: Session = Depends(get_db), current_user: CurrentUser = Depends(_QUOTATION_READ_ACCESS)):
    row = _get_or_404(db, Quotation, quotation_id, "Quotation")
    _enforce_procurement_branch_scope(db, row, current_user, "Quotation")
    return row


@router.patch("/quotations/{quotation_id}", response_model=QuotationOut)
def update_quotation(quotation_id: str, payload: QuotationUpdateIn, db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(require_permission(QUOTATION_CREATE))):
    """Same permission as create (data-entry, not approval) — only a DRAFT
    quotation may be edited, see procurement_service.update_quotation."""
    quotation = _get_or_404(db, Quotation, quotation_id, "Quotation")
    try:
        return procurement_service.update_quotation(db, quotation, payload, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/quotations/{quotation_id}/submit", response_model=QuotationOut)
def submit_quotation(quotation_id: str, db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(require_permission(QUOTATION_CREATE))):
    quotation = _get_or_404(db, Quotation, quotation_id, "Quotation")
    try:
        return procurement_service.submit_quotation(db, quotation, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/quotations/{quotation_id}/cancel", response_model=QuotationOut)
def cancel_quotation(quotation_id: str, db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(require_permission(QUOTATION_CREATE))):
    quotation = _get_or_404(db, Quotation, quotation_id, "Quotation")
    try:
        return procurement_service.cancel_quotation(db, quotation, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/quotations/{quotation_id}/approve", response_model=QuotationOut)
def approve_quotation(quotation_id: str, payload: QuotationDecisionIn = QuotationDecisionIn(),
                       db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(QUOTATION_APPROVE))):
    quotation = _get_or_404(db, Quotation, quotation_id, "Quotation")
    try:
        return procurement_service.approve_quotation(db, quotation, payload, manager_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/quotations/{quotation_id}/reject", response_model=QuotationOut)
def reject_quotation(quotation_id: str, payload: QuotationRejectIn, db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(require_permission(QUOTATION_APPROVE))):
    quotation = _get_or_404(db, Quotation, quotation_id, "Quotation")
    try:
        return procurement_service.reject_quotation(db, quotation, payload, manager_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


def _quotation_reference_count(db: Session, quotation_id: str) -> int:
    """Batch F: how many rows reference this Quotation — an Indent or a Purchase
    Order raised against it. Zero means it can be hard-deleted; any other count means
    it must be cancelled instead — same reference-check-then-hard-delete-or-reject
    pattern as routers/vendors.py:_vendor_reference_count /
    routers/hospitals.py:_branch_reference_count."""
    counts = [
        db.query(Indent).filter(Indent.quotation_id == quotation_id).count(),
        db.query(PurchaseOrder).filter(PurchaseOrder.quotation_id == quotation_id).count(),
    ]
    return sum(counts)


def _delete_attachments(db: Session, entity_type: str, entity_id: str) -> None:
    """Removes any DocumentLink rows pointing at an entity being hard-deleted, so a
    deleted quotation/indent/PO/invoice never leaves an orphaned link behind. Never a
    blocker on its own — same as contracts.py/vendors.py, which don't count Document
    attachments as in-use references either; the underlying Document rows themselves
    are untouched."""
    db.query(DocumentLink).filter(DocumentLink.entity_type == entity_type, DocumentLink.entity_id == entity_id).delete()


@router.delete("/quotations/{quotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_quotation(quotation_id: str, db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(require_permission(QUOTATION_CREATE))):
    """Batch F: hard delete only when the quotation is still DRAFT/SUBMITTED (not yet
    decided) and zero rows reference it; otherwise 409, directing the caller to cancel
    (if still cancellable) or explaining the decision already stands."""
    quotation = _get_or_404(db, Quotation, quotation_id, "Quotation")
    cancellable = quotation.status in ("DRAFT", "SUBMITTED")
    in_use = _quotation_reference_count(db, quotation_id)
    if in_use > 0 or not cancellable:
        if not cancellable:
            detail = (
                f"Quotation is {quotation.status} and cannot be deleted — its status already reflects "
                "its outcome"
            )
        else:
            detail = f"Quotation is referenced by {in_use} record(s) and cannot be deleted — cancel it instead"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    before = {c.name: getattr(quotation, c.name) for c in quotation.__table__.columns}
    audit_record(db, user_id=current_user.id, action="DELETE", entity="quotation", entity_id=quotation.id, old_value=before)
    _delete_attachments(db, "quotation", quotation_id)
    db.delete(quotation)
    db.commit()


# ---- Indents / CRs ----

@router.get("/indents", response_model=Page[IndentOut])
def list_indents(page: int = 1, page_size: int = 25, quotation_id: str | None = None, incident_id: str | None = None,
                  status_filter: str | None = None, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(_PROCUREMENT_READ_ACCESS)):
    query = db.query(Indent)
    query = _scope_procurement_query(query, Indent, current_user)
    if quotation_id:
        query = query.filter(Indent.quotation_id == quotation_id)
    if incident_id:
        query = query.filter(Indent.incident_id == incident_id)
    if status_filter:
        query = query.filter(Indent.status == status_filter)
    total = query.count()
    items = query.order_by(Indent.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/indents", response_model=IndentOut, status_code=status.HTTP_201_CREATED)
def create_indent(payload: IndentIn, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(PROCUREMENT_MANAGE))):
    try:
        return procurement_service.create_indent(db, payload, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.get("/indents/{indent_id}", response_model=IndentOut)
def get_indent(indent_id: str, db: Session = Depends(get_db), current_user: CurrentUser = Depends(_PROCUREMENT_READ_ACCESS)):
    row = _get_or_404(db, Indent, indent_id, "Indent")
    _enforce_procurement_branch_scope(db, row, current_user, "Indent")
    return row


@router.patch("/indents/{indent_id}", response_model=IndentOut)
def update_indent(indent_id: str, payload: IndentUpdateIn, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(PROCUREMENT_MANAGE))):
    """Only DRAFT/SUBMITTED — see procurement_service.update_indent."""
    indent = _get_or_404(db, Indent, indent_id, "Indent")
    try:
        return procurement_service.update_indent(db, indent, payload, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/indents/{indent_id}/submit", response_model=IndentOut)
def submit_indent(indent_id: str, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(PROCUREMENT_MANAGE))):
    indent = _get_or_404(db, Indent, indent_id, "Indent")
    try:
        return procurement_service.submit_indent(db, indent, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/indents/{indent_id}/cancel", response_model=IndentOut)
def cancel_indent(indent_id: str, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(PROCUREMENT_MANAGE))):
    indent = _get_or_404(db, Indent, indent_id, "Indent")
    try:
        return procurement_service.cancel_indent(db, indent, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


def _indent_reference_count(db: Session, indent_id: str) -> int:
    """Batch F: how many rows reference this Indent — a Purchase Order raised from
    it. Zero means it can be hard-deleted; any other count means it must be cancelled
    instead."""
    return db.query(PurchaseOrder).filter(PurchaseOrder.indent_id == indent_id).count()


@router.delete("/indents/{indent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_indent(indent_id: str, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(PROCUREMENT_MANAGE))):
    """Batch F: hard delete only when the indent is still DRAFT/SUBMITTED and zero
    rows reference it; otherwise 409, directing the caller to cancel instead. In
    practice a PurchaseOrder reference and a COMPLETED status always coincide (a PO
    auto-completes the indent it was created from — see
    services/procurement_service.py:_complete_indent), so both checks agree."""
    indent = _get_or_404(db, Indent, indent_id, "Indent")
    cancellable = indent.status in ("DRAFT", "SUBMITTED")
    in_use = _indent_reference_count(db, indent_id)
    if in_use > 0 or not cancellable:
        if not cancellable:
            detail = f"Indent is {indent.status} and cannot be deleted — its status already reflects its outcome"
        else:
            detail = f"Indent is referenced by {in_use} record(s) and cannot be deleted — cancel it instead"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    before = {c.name: getattr(indent, c.name) for c in indent.__table__.columns}
    audit_record(db, user_id=current_user.id, action="DELETE", entity="indent", entity_id=indent.id, old_value=before)
    _delete_attachments(db, "indent", indent_id)
    db.delete(indent)
    db.commit()


# ---- Purchase Orders ----

@router.get("/purchase-orders", response_model=Page[PurchaseOrderOut])
def list_purchase_orders(page: int = 1, page_size: int = 25, indent_id: str | None = None,
                          incident_id: str | None = None, vendor_id: str | None = None,
                          status_filter: str | None = None, db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(_PROCUREMENT_READ_ACCESS)):
    query = db.query(PurchaseOrder)
    query = _scope_procurement_query(query, PurchaseOrder, current_user)
    if indent_id:
        query = query.filter(PurchaseOrder.indent_id == indent_id)
    if incident_id:
        query = query.filter(PurchaseOrder.incident_id == incident_id)
    if vendor_id:
        query = query.filter(PurchaseOrder.vendor_id == vendor_id)
    if status_filter:
        query = query.filter(PurchaseOrder.status == status_filter)
    total = query.count()
    items = query.order_by(PurchaseOrder.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/purchase-orders", response_model=PurchaseOrderOut, status_code=status.HTTP_201_CREATED)
def create_purchase_order(payload: PurchaseOrderIn, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(PO_MANAGE))):
    try:
        return procurement_service.create_purchase_order(db, payload, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.get("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
def get_purchase_order(po_id: str, db: Session = Depends(get_db), current_user: CurrentUser = Depends(_PROCUREMENT_READ_ACCESS)):
    row = _get_or_404(db, PurchaseOrder, po_id, "Purchase order")
    _enforce_procurement_branch_scope(db, row, current_user, "Purchase order")
    return row


@router.patch("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
def update_purchase_order(po_id: str, payload: PurchaseOrderUpdateIn, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(PO_MANAGE))):
    """Only DRAFT — see procurement_service.update_purchase_order."""
    po = _get_or_404(db, PurchaseOrder, po_id, "Purchase order")
    try:
        return procurement_service.update_purchase_order(db, po, payload, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/purchase-orders/{po_id}/status", response_model=PurchaseOrderOut)
def update_purchase_order_status(po_id: str, payload: PurchaseOrderStatusIn, db: Session = Depends(get_db),
                                  current_user: CurrentUser = Depends(require_permission(PO_MANAGE))):
    po = _get_or_404(db, PurchaseOrder, po_id, "Purchase order")
    try:
        return procurement_service.update_po_status(db, po, payload.to_status, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/purchase-orders/{po_id}/cancel", response_model=PurchaseOrderOut)
def cancel_purchase_order(po_id: str, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(PO_MANAGE))):
    po = _get_or_404(db, PurchaseOrder, po_id, "Purchase order")
    try:
        return procurement_service.cancel_purchase_order(db, po, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


def _purchase_order_reference_count(db: Session, po_id: str) -> int:
    """Batch F: how many rows reference this Purchase Order — a recorded Invoice or
    a scheduled ServiceVisit. Zero means it can be hard-deleted; any other count means
    it must be cancelled instead."""
    counts = [
        db.query(Invoice).filter(Invoice.purchase_order_id == po_id).count(),
        db.query(ServiceVisit).filter(ServiceVisit.purchase_order_id == po_id).count(),
    ]
    return sum(counts)


@router.delete("/purchase-orders/{po_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_purchase_order(po_id: str, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(PO_MANAGE))):
    """Batch F: hard delete only when the PO is still DRAFT (nothing sent to the
    vendor yet) and zero rows reference it; otherwise 409, directing the caller to
    cancel instead (DRAFT/ISSUED) or explaining that fulfillment is already underway
    (ACKNOWLEDGED and beyond)."""
    po = _get_or_404(db, PurchaseOrder, po_id, "Purchase order")
    deletable = po.status == "DRAFT"
    cancellable = po.status in ("DRAFT", "ISSUED")
    in_use = _purchase_order_reference_count(db, po_id)
    if in_use > 0 or not deletable:
        if cancellable:
            detail = f"Purchase order is {po.status} and cannot be deleted — cancel it instead"
        else:
            detail = (
                f"Purchase order is {po.status} and cannot be deleted or cancelled — fulfillment is already "
                "underway"
            )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    before = {c.name: getattr(po, c.name) for c in po.__table__.columns}
    audit_record(db, user_id=current_user.id, action="DELETE", entity="purchase_order", entity_id=po.id, old_value=before)
    _delete_attachments(db, "purchase_order", po_id)
    db.delete(po)
    db.commit()


# ---- Invoices ----

@router.get("/invoices", response_model=Page[InvoiceOut])
def list_invoices(page: int = 1, page_size: int = 25, purchase_order_id: str | None = None,
                   incident_id: str | None = None, verification_status: str | None = None,
                   vendor_id: str | None = None,
                   db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_any_permission(INVOICE_RECORD, INVOICE_APPROVE, PROCUREMENT_READ))):
    query = db.query(Invoice)
    query = _scope_procurement_query(query, Invoice, current_user)
    if purchase_order_id:
        query = query.filter(Invoice.purchase_order_id == purchase_order_id)
    if incident_id:
        query = query.filter(Invoice.incident_id == incident_id)
    if verification_status:
        query = query.filter(Invoice.verification_status == verification_status)
    if vendor_id:
        # Invoice carries its own vendor_id directly (denormalized from the PO at
        # record time, see models/procurement.py:Invoice) — no join needed. Enables
        # the vendor detail page to show a real invoice history instead of just a
        # link-out (see frontend/app/vendors/[id]/page.tsx).
        query = query.filter(Invoice.vendor_id == vendor_id)
    total = query.count()
    items = query.order_by(Invoice.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/invoices", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def record_invoice(payload: InvoiceIn, db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(require_permission(INVOICE_RECORD))):
    try:
        return procurement_service.record_invoice(db, payload, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: str, db: Session = Depends(get_db),
                 current_user: CurrentUser = Depends(require_any_permission(INVOICE_RECORD, INVOICE_APPROVE, PROCUREMENT_READ))):
    row = _get_or_404(db, Invoice, invoice_id, "Invoice")
    _enforce_procurement_branch_scope(db, row, current_user, "Invoice")
    return row


@router.patch("/invoices/{invoice_id}", response_model=InvoiceOut)
def update_invoice(invoice_id: str, payload: InvoiceUpdateIn, db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(require_permission(INVOICE_RECORD))):
    """Same permission as recording (data-entry) — NOT invoice.approve, preserving
    segregation between who records an invoice and who verifies it. Only a PENDING
    invoice may be edited — see procurement_service.update_invoice."""
    invoice = _get_or_404(db, Invoice, invoice_id, "Invoice")
    try:
        return procurement_service.update_invoice(db, invoice, payload, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/invoices/{invoice_id}/verify", response_model=InvoiceOut)
def verify_invoice(invoice_id: str, db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(require_permission(INVOICE_APPROVE))):
    invoice = _get_or_404(db, Invoice, invoice_id, "Invoice")
    try:
        return procurement_service.verify_invoice(db, invoice, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.post("/invoices/{invoice_id}/reject", response_model=InvoiceOut)
def reject_invoice(invoice_id: str, payload: InvoiceRejectIn, db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(require_permission(INVOICE_APPROVE))):
    invoice = _get_or_404(db, Invoice, invoice_id, "Invoice")
    try:
        return procurement_service.reject_invoice(db, invoice, payload, user_id=current_user.id)
    except ProcurementServiceError as exc:
        raise _map_service_error(exc)


@router.delete("/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(invoice_id: str, db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(require_permission(INVOICE_RECORD))):
    """Batch F: hard delete only when the invoice is still PENDING (not yet decided);
    otherwise 409, directing the caller to reject it instead. No other table has an FK
    onto invoices.id, so this is purely a status gate — VERIFIED financial closure and
    REJECTED are both terminal outcomes that must remain in the audit trail."""
    invoice = _get_or_404(db, Invoice, invoice_id, "Invoice")
    if invoice.verification_status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Invoice is {invoice.verification_status} and cannot be deleted — its outcome already stands",
        )
    before = {c.name: getattr(invoice, c.name) for c in invoice.__table__.columns}
    audit_record(db, user_id=current_user.id, action="DELETE", entity="invoice", entity_id=invoice.id, old_value=before)
    _delete_attachments(db, "invoice", invoice_id)
    db.delete(invoice)
    db.commit()


# ---- Attachments (reuses Document/DocumentLink — no new document subsystem) ----

_attachments_router("quotation", "quotations", Quotation, _QUOTATION_READ_ACCESS, require_permission(QUOTATION_CREATE))
_attachments_router("indent", "indents", Indent, _PROCUREMENT_READ_ACCESS, require_permission(PROCUREMENT_MANAGE))
_attachments_router("purchase_order", "purchase-orders", PurchaseOrder, _PROCUREMENT_READ_ACCESS, require_permission(PO_MANAGE))
_attachments_router("invoice", "invoices", Invoice, require_any_permission(INVOICE_RECORD, INVOICE_APPROVE, PROCUREMENT_READ),
                     require_permission(INVOICE_RECORD))
