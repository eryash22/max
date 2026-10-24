from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import require_any_permission, require_permission
from ..exports.writer import EXPORT_FORMATS, build_export_response
from ..models.contracts import AmcContract, AmcContractAsset, CmcContract, CmcContractAsset, Warranty
from ..models.device import DeviceModel
from ..models.documents import Document
from ..models.incident import Incident, ServiceVisit
from ..models.procurement import Indent, Invoice, PurchaseOrder, Quotation
from ..models.vendor import DeviceModelVendor, Vendor, VendorContact
from ..services.force_delete import assert_dev_environment
from ..services.procurement_purge import collect_procurement_chain_ids, delete_procurement_chain
from ..repositories.crud import paginate
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.vendor import (
    DeviceModelVendorIn,
    DeviceModelVendorOut,
    DeviceModelVendorUpdateIn,
    VendorContactIn,
    VendorContactOut,
    VendorContactUpdateIn,
    VendorIn,
    VendorOut,
    VendorServiceVisitOut,
    VendorUpdateIn,
)
from ..security.permissions import DEV_DATA_CLEANUP, VENDOR_READ, VENDOR_READ_LIMITED, VENDOR_WRITE
from ..services.export_service import MAX_EXPORT_ROWS, record_export_audit
from ..services.vendor_service import strip_contact_fields

router = APIRouter(prefix="/api/v1/vendors", tags=["vendors"])

_READ_ACCESS = require_any_permission(VENDOR_READ, VENDOR_READ_LIMITED)


def _get_vendor_or_404(db: Session, vendor_id: str) -> Vendor:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found")
    return vendor


def _has_full_read(current_user: CurrentUser) -> bool:
    return VENDOR_READ in current_user.permissions


def _vendor_out(vendor: Vendor, current_user: CurrentUser) -> dict:
    """Serialize a `Vendor` row, masking contact fields (Part 5 — vendor visibility)
    for callers who hold only `vendor.read_limited`. Always returns a dict (not the
    ORM object) so masking happens before the response_model re-validates it —
    backend-enforced, never relying on the frontend to hide anything."""
    out = VendorOut.model_validate(vendor).model_dump()
    if not _has_full_read(current_user):
        out = strip_contact_fields(out)
    return out


def _vendor_contact_out(contact: VendorContact, current_user: CurrentUser) -> dict:
    out = VendorContactOut.model_validate(contact).model_dump()
    if not _has_full_read(current_user):
        out = strip_contact_fields(out)
    return out


# ---- Vendor CRUD ----

@router.get("", response_model=Page[VendorOut])
def list_vendors(
    page: int = 1, page_size: int = 25,
    search: str | None = None,
    status_filter: str | None = None,
    vendor_type: str | None = None,
    city: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_READ_ACCESS),
):
    filters = {"vendor_type": vendor_type, "city": city, "state": state}
    if status_filter is not None:
        filters["status"] = status_filter
    items, total = paginate(
        db, Vendor, page=page, page_size=page_size, search=search,
        search_fields=["name", "city", "state", "email", "phone"], **filters,
    )
    items = [_vendor_out(v, current_user) for v in items]
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/export")
def export_vendors(
    format: str = "xlsx", search: str | None = None, status_filter: str | None = None,
    vendor_type: str | None = None, city: str | None = None, state: str | None = None,
    db: Session = Depends(get_db), current_user: CurrentUser = Depends(_READ_ACCESS),
):
    """Exports every vendor matching the same filters `GET /api/v1/vendors` accepts —
    not just the current page — gated on the identical read access the list endpoint
    uses (full `vendor.read` or masked `vendor.read_limited`), so a user can never
    export more than they could already read; a `vendor.read_limited` caller's
    exported rows have Email/Phone blanked the same way the list/detail endpoints
    mask them (Part 5 — vendor visibility). See `services/export_service.py` for the
    shared audit/row-cap/response mechanics every module's export endpoint reuses."""
    if format not in EXPORT_FORMATS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"format must be one of {', '.join(EXPORT_FORMATS)}")

    filters = {"vendor_type": vendor_type, "city": city, "state": state}
    if status_filter is not None:
        filters["status"] = status_filter
    items, total = paginate(
        db, Vendor, page=1, page_size=MAX_EXPORT_ROWS, search=search,
        search_fields=["name", "city", "state", "email", "phone"], **filters,
    )

    has_full_read = _has_full_read(current_user)
    headers = [
        "Vendor Name", "Vendor Type", "OEM", "Authorized Distributor", "Service Vendor",
        "City", "State", "Email", "Phone", "Website", "Support Coverage", "SLA Response Hours",
        "Status",
    ]
    rows = [
        [v.name, v.vendor_type, v.is_oem, v.is_authorized_distributor, v.is_service_vendor,
         v.city, v.state, v.email if has_full_read else None, v.phone if has_full_read else None,
         v.website, v.support_coverage, v.response_sla_hours,
         v.status]
        for v in items
    ]

    record_export_audit(
        db, user_id=current_user.id, module="vendors",
        filters={"search": search, "status_filter": status_filter, "vendor_type": vendor_type, "city": city, "state": state},
        format=format, record_count=len(rows), truncated=total > MAX_EXPORT_ROWS,
    )
    return build_export_response(headers=headers, rows=rows, format=format, filename_stem="vendors_export")


@router.post("", response_model=VendorOut, status_code=status.HTTP_201_CREATED)
def create_vendor(payload: VendorIn, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(VENDOR_WRITE))):
    if db.query(Vendor).filter(Vendor.name == payload.name).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Vendor name already exists")

    data = payload.model_dump()
    vendor = Vendor(**data)
    db.add(vendor)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="vendor", entity_id=vendor.id, new_value=data)
    db.commit()
    db.refresh(vendor)
    return vendor


@router.get("/{vendor_id}", response_model=VendorOut)
def get_vendor(vendor_id: str, db: Session = Depends(get_db),
                current_user: CurrentUser = Depends(_READ_ACCESS)):
    vendor = _get_vendor_or_404(db, vendor_id)
    return _vendor_out(vendor, current_user)


@router.patch("/{vendor_id}", response_model=VendorOut)
def update_vendor(vendor_id: str, payload: VendorUpdateIn, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(VENDOR_WRITE))):
    vendor = _get_vendor_or_404(db, vendor_id)
    changes = payload.model_dump(exclude_unset=True)

    if "name" in changes and changes["name"] != vendor.name:
        if db.query(Vendor).filter(Vendor.name == changes["name"]).first():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Vendor name already exists")

    before = {c.name: getattr(vendor, c.name) for c in vendor.__table__.columns}
    for field, value in changes.items():
        setattr(vendor, field, value)

    audit_record(db, user_id=current_user.id, action="UPDATE", entity="vendor", entity_id=vendor.id,
                 old_value=before, new_value=changes)
    db.commit()
    db.refresh(vendor)
    return vendor


def _vendor_reference_count(db: Session, vendor_id: str) -> int:
    """Final non-AI cleanup pass — Batch B: how many rows across the schema currently
    reference this Vendor (contacts, device-model links, AMC/CMC/Warranty contracts,
    procurement records, service visits, incident vendor assignment, documents). Zero
    means the vendor can be hard-deleted; any other count means it must be archived
    (status=INACTIVE via deactivate) instead — same reference-check-then-hard-delete-
    or-reject pattern as routers/devices.py:_device_model_in_use /
    delete_device_model."""
    counts = [
        db.query(VendorContact).filter(VendorContact.vendor_id == vendor_id).count(),
        db.query(DeviceModelVendor).filter(DeviceModelVendor.vendor_id == vendor_id).count(),
        db.query(AmcContract).filter(AmcContract.vendor_id == vendor_id).count(),
        db.query(CmcContract).filter(CmcContract.vendor_id == vendor_id).count(),
        db.query(Warranty).filter(Warranty.vendor_id == vendor_id).count(),
        db.query(Quotation).filter(Quotation.vendor_id == vendor_id).count(),
        db.query(Indent).filter(Indent.vendor_id == vendor_id).count(),
        db.query(PurchaseOrder).filter(PurchaseOrder.vendor_id == vendor_id).count(),
        db.query(Invoice).filter(Invoice.vendor_id == vendor_id).count(),
        db.query(ServiceVisit).filter(ServiceVisit.vendor_id == vendor_id).count(),
        db.query(Incident).filter(Incident.assigned_vendor_id == vendor_id).count(),
        db.query(Document).filter(Document.vendor_id == vendor_id).count(),
    ]
    return sum(counts)


def _force_delete_vendor(db: Session, vendor: Vendor, *, user_id: str) -> None:
    """Super-Admin-only cascade purge (gated on DEV_DATA_CLEANUP, same permission the
    DEV Data Cleanup tool uses to bypass entity delete-guards). Junction/vendor-owned
    rows are deleted outright; rows that belong to another domain and only optionally
    reference this vendor (Document, Incident) are detached instead of destroyed so
    unrelated document/incident history survives. Quotations/Indents/POs/Invoices are
    financial records with a required (NOT NULL) vendor_id, so there is no way to
    detach them — deleting the vendor here also deletes that procurement trail, which
    is exactly what a force-delete is for."""
    vendor_id = vendor.id

    db.query(VendorContact).filter(VendorContact.vendor_id == vendor_id).delete(synchronize_session=False)
    db.query(DeviceModelVendor).filter(DeviceModelVendor.vendor_id == vendor_id).delete(synchronize_session=False)
    db.query(ServiceVisit).filter(ServiceVisit.vendor_id == vendor_id).delete(synchronize_session=False)

    for contract in db.query(AmcContract).filter(AmcContract.vendor_id == vendor_id).all():
        db.query(AmcContractAsset).filter(AmcContractAsset.amc_contract_id == contract.id).delete(synchronize_session=False)
        db.delete(contract)
    for contract in db.query(CmcContract).filter(CmcContract.vendor_id == vendor_id).all():
        db.query(CmcContractAsset).filter(CmcContractAsset.cmc_contract_id == contract.id).delete(synchronize_session=False)
        db.delete(contract)
    db.query(Warranty).filter(Warranty.vendor_id == vendor_id).delete(synchronize_session=False)

    quotation_ids, indent_ids, po_ids, invoice_ids = collect_procurement_chain_ids(
        db,
        quotation_seed=Quotation.vendor_id == vendor_id,
        indent_seed=Indent.vendor_id == vendor_id,
        po_seed=PurchaseOrder.vendor_id == vendor_id,
        invoice_seed=Invoice.vendor_id == vendor_id,
    )
    delete_procurement_chain(db, quotation_ids, indent_ids, po_ids, invoice_ids)

    db.query(Incident).filter(Incident.assigned_vendor_id == vendor_id).update(
        {"assigned_vendor_id": None}, synchronize_session=False
    )
    db.query(Document).filter(Document.vendor_id == vendor_id).update(
        {"vendor_id": None}, synchronize_session=False
    )

    before = {c.name: getattr(vendor, c.name) for c in vendor.__table__.columns}
    audit_record(
        db, user_id=user_id, action="FORCE_DELETE", entity="vendor", entity_id=vendor.id, old_value=before,
    )
    db.delete(vendor)
    db.commit()


@router.delete("/{vendor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vendor(vendor_id: str, force: bool = False, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(VENDOR_WRITE))):
    """Final non-AI cleanup pass — Batch B: hard delete only when zero rows reference
    this vendor; otherwise 409, directing the caller to deactivate (archive) instead.

    ``force=true`` bypasses that guard and cascade-deletes every referencing row (see
    _force_delete_vendor) — gated on DEV_DATA_CLEANUP so only Super Admin can use it,
    same permission the DEV Data Cleanup tool already requires to bypass other
    entities' delete-guards."""
    vendor = _get_vendor_or_404(db, vendor_id)
    if force:
        if DEV_DATA_CLEANUP not in current_user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Force-delete requires Super Admin (dev.data_cleanup permission)",
            )
        assert_dev_environment()
        _force_delete_vendor(db, vendor, user_id=current_user.id)
        return
    in_use = _vendor_reference_count(db, vendor_id)
    if in_use > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Vendor is referenced by {in_use} record(s) and cannot be deleted — deactivate it instead",
        )
    before = {c.name: getattr(vendor, c.name) for c in vendor.__table__.columns}
    audit_record(db, user_id=current_user.id, action="DELETE", entity="vendor", entity_id=vendor.id, old_value=before)
    db.delete(vendor)
    db.commit()


def _set_status(db: Session, vendor: Vendor, new_status: str, *, user_id: str) -> Vendor:
    before = {"status": vendor.status}
    vendor.status = new_status
    audit_record(db, user_id=user_id, action="UPDATE", entity="vendor", entity_id=vendor.id,
                 old_value=before, new_value={"status": new_status})
    db.commit()
    db.refresh(vendor)
    return vendor


@router.post("/{vendor_id}/activate", response_model=VendorOut)
def activate_vendor(vendor_id: str, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(VENDOR_WRITE))):
    vendor = _get_vendor_or_404(db, vendor_id)
    return _set_status(db, vendor, "ACTIVE", user_id=current_user.id)


@router.post("/{vendor_id}/deactivate", response_model=VendorOut)
def deactivate_vendor(vendor_id: str, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(VENDOR_WRITE))):
    vendor = _get_vendor_or_404(db, vendor_id)
    return _set_status(db, vendor, "INACTIVE", user_id=current_user.id)


# ---- Vendor contacts ----

@router.get("/{vendor_id}/contacts", response_model=list[VendorContactOut])
def list_vendor_contacts(vendor_id: str, db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(_READ_ACCESS)):
    _get_vendor_or_404(db, vendor_id)
    contacts = (
        db.query(VendorContact)
        .filter(VendorContact.vendor_id == vendor_id)
        .order_by(VendorContact.created_at.desc())
        .all()
    )
    return [_vendor_contact_out(c, current_user) for c in contacts]


@router.post("/{vendor_id}/contacts", response_model=VendorContactOut, status_code=status.HTTP_201_CREATED)
def create_vendor_contact(vendor_id: str, payload: VendorContactIn, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(VENDOR_WRITE))):
    _get_vendor_or_404(db, vendor_id)
    data = payload.model_dump()
    contact = VendorContact(vendor_id=vendor_id, **data)
    db.add(contact)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="vendor_contact", entity_id=contact.id,
                 new_value={"vendor_id": vendor_id, **data})
    db.commit()
    db.refresh(contact)
    return contact


def _get_vendor_contact_or_404(db: Session, vendor_id: str, contact_id: str) -> VendorContact:
    contact = db.get(VendorContact, contact_id)
    if contact is None or contact.vendor_id != vendor_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendor contact not found")
    return contact


@router.patch("/{vendor_id}/contacts/{contact_id}", response_model=VendorContactOut)
def update_vendor_contact(vendor_id: str, contact_id: str, payload: VendorContactUpdateIn,
                           db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(VENDOR_WRITE))):
    contact = _get_vendor_contact_or_404(db, vendor_id, contact_id)
    before = VendorContactOut.model_validate(contact).model_dump()
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(contact, field, value)
    db.flush()
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="vendor_contact", entity_id=contact.id,
                 old_value=before, new_value=updates)
    db.commit()
    db.refresh(contact)
    return contact


@router.delete("/{vendor_id}/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vendor_contact(vendor_id: str, contact_id: str, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(VENDOR_WRITE))):
    contact = _get_vendor_contact_or_404(db, vendor_id, contact_id)
    before = VendorContactOut.model_validate(contact).model_dump()
    db.delete(contact)
    audit_record(db, user_id=current_user.id, action="DELETE", entity="vendor_contact", entity_id=contact_id,
                 old_value=before)
    db.commit()
    return None


# ---- Vendor <-> device model linkage ----

@router.get("/{vendor_id}/device-models", response_model=list[DeviceModelVendorOut])
def list_vendor_device_models(vendor_id: str, db: Session = Depends(get_db),
                               _: CurrentUser = Depends(_READ_ACCESS)):
    _get_vendor_or_404(db, vendor_id)
    return db.query(DeviceModelVendor).filter(DeviceModelVendor.vendor_id == vendor_id).order_by(DeviceModelVendor.rank).all()


@router.post("/{vendor_id}/device-models", response_model=DeviceModelVendorOut, status_code=status.HTTP_201_CREATED)
def link_vendor_device_model(vendor_id: str, payload: DeviceModelVendorIn, db: Session = Depends(get_db),
                              current_user: CurrentUser = Depends(require_permission(VENDOR_WRITE))):
    _get_vendor_or_404(db, vendor_id)
    if db.get(DeviceModel, payload.device_model_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device model not found")

    data = payload.model_dump()
    link = DeviceModelVendor(vendor_id=vendor_id, **data)
    db.add(link)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="device_model_vendor", entity_id=link.id,
                 new_value={"vendor_id": vendor_id, **data})
    db.commit()
    db.refresh(link)
    return link


def _get_device_model_link_or_404(db: Session, vendor_id: str, link_id: str) -> DeviceModelVendor:
    link = db.get(DeviceModelVendor, link_id)
    if link is None or link.vendor_id != vendor_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device model link not found")
    return link


@router.patch("/{vendor_id}/device-models/{link_id}", response_model=DeviceModelVendorOut)
def update_vendor_device_model(vendor_id: str, link_id: str, payload: DeviceModelVendorUpdateIn,
                                db: Session = Depends(get_db),
                                current_user: CurrentUser = Depends(require_permission(VENDOR_WRITE))):
    link = _get_device_model_link_or_404(db, vendor_id, link_id)
    before = DeviceModelVendorOut.model_validate(link).model_dump()
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(link, field, value)
    db.flush()
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="device_model_vendor", entity_id=link.id,
                 old_value=before, new_value=updates)
    db.commit()
    db.refresh(link)
    return link


@router.delete("/{vendor_id}/device-models/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vendor_device_model(vendor_id: str, link_id: str, db: Session = Depends(get_db),
                                current_user: CurrentUser = Depends(require_permission(VENDOR_WRITE))):
    link = _get_device_model_link_or_404(db, vendor_id, link_id)
    before = DeviceModelVendorOut.model_validate(link).model_dump()
    db.delete(link)
    audit_record(db, user_id=current_user.id, action="DELETE", entity="device_model_vendor", entity_id=link_id,
                 old_value=before)
    db.commit()
    return None


# ---- Vendor service visit history (read-only) ----

@router.get("/{vendor_id}/service-visits", response_model=Page[VendorServiceVisitOut])
def list_vendor_service_visits(vendor_id: str, page: int = 1, page_size: int = 25,
                                db: Session = Depends(get_db),
                                _: CurrentUser = Depends(_READ_ACCESS)):
    _get_vendor_or_404(db, vendor_id)
    query = db.query(ServiceVisit).filter(ServiceVisit.vendor_id == vendor_id)
    total = query.count()
    items = query.order_by(ServiceVisit.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return Page(items=items, total=total, page=page, page_size=page_size)
