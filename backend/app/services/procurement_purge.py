from sqlalchemy.orm import Session

from ..models.procurement import Indent, Invoice, PurchaseOrder, PurchaseOrderLine, Quotation, QuotationLine

# Shared by every force-delete cascade that needs to purge the procurement chain
# (Quotation -> Indent -> PurchaseOrder -> Invoice) scoped to some owning record
# (a Vendor or an Asset). Used by routers/vendors.py's and routers/devices.py's
# force-delete paths.


def collect_procurement_chain_ids(db: Session, *, quotation_seed, indent_seed, po_seed, invoice_seed):
    """Transitive closure over the procurement chain, starting from each table's
    directly-scoped rows (the `*_seed` filter conditions, e.g. `Quotation.vendor_id ==
    vendor_id`) and then walking every Quotation<->Indent<->PurchaseOrder<->Invoice FK
    edge in both directions until nothing new is found.

    Real seed/QA data is not always internally consistent about which vendor/asset a
    downstream Indent/PurchaseOrder/Invoice claims (verified live: a PurchaseOrder
    whose own `asset_id` didn't match the Indent it pointed at). Scoping every table
    independently by the same column, as a first cut did, leaves exactly that kind of
    row un-deleted while something still references it — an IntegrityError instead of
    a clean cascade. Walking the actual FK edges instead of trusting each row's own
    scoping column closes that gap."""
    quotation_ids = {r[0] for r in db.query(Quotation.id).filter(quotation_seed)}
    indent_ids = {r[0] for r in db.query(Indent.id).filter(indent_seed)}
    po_ids = {r[0] for r in db.query(PurchaseOrder.id).filter(po_seed)}
    invoice_ids = {r[0] for r in db.query(Invoice.id).filter(invoice_seed)}

    for _ in range(4):
        next_quotation, next_indent, next_po, next_invoice = (
            set(quotation_ids), set(indent_ids), set(po_ids), set(invoice_ids),
        )
        if quotation_ids:
            next_indent |= {r[0] for r in db.query(Indent.id).filter(Indent.quotation_id.in_(quotation_ids))}
            next_po |= {r[0] for r in db.query(PurchaseOrder.id).filter(PurchaseOrder.quotation_id.in_(quotation_ids))}
        if indent_ids:
            next_po |= {r[0] for r in db.query(PurchaseOrder.id).filter(PurchaseOrder.indent_id.in_(indent_ids))}
            next_quotation |= {r[0] for r in db.query(Indent.quotation_id).filter(Indent.id.in_(indent_ids))}
        if po_ids:
            next_invoice |= {r[0] for r in db.query(Invoice.id).filter(Invoice.purchase_order_id.in_(po_ids))}
            next_indent |= {r[0] for r in db.query(PurchaseOrder.indent_id).filter(PurchaseOrder.id.in_(po_ids))}
            next_quotation |= {r[0] for r in db.query(PurchaseOrder.quotation_id).filter(PurchaseOrder.id.in_(po_ids))}

        if (next_quotation, next_indent, next_po, next_invoice) == (quotation_ids, indent_ids, po_ids, invoice_ids):
            break
        quotation_ids, indent_ids, po_ids, invoice_ids = next_quotation, next_indent, next_po, next_invoice

    return quotation_ids, indent_ids, po_ids, invoice_ids


def delete_procurement_chain(db: Session, quotation_ids, indent_ids, po_ids, invoice_ids) -> None:
    """Deletes exactly the id sets `collect_procurement_chain_ids` returned, leaf-first
    (Invoice, then PurchaseOrder+lines, then Indent, then Quotation+lines) so no FK is
    ever violated regardless of how tangled the seed data's cross-references are."""
    if invoice_ids:
        db.query(Invoice).filter(Invoice.id.in_(invoice_ids)).delete(synchronize_session=False)
    if po_ids:
        db.query(PurchaseOrderLine).filter(PurchaseOrderLine.purchase_order_id.in_(po_ids)).delete(synchronize_session=False)
        db.query(PurchaseOrder).filter(PurchaseOrder.id.in_(po_ids)).delete(synchronize_session=False)
    if indent_ids:
        db.query(Indent).filter(Indent.id.in_(indent_ids)).delete(synchronize_session=False)
    if quotation_ids:
        db.query(QuotationLine).filter(QuotationLine.quotation_id.in_(quotation_ids)).delete(synchronize_session=False)
        db.query(Quotation).filter(Quotation.id.in_(quotation_ids)).delete(synchronize_session=False)
