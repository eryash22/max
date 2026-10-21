import re
from pathlib import Path

from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..config import settings
from ..models.contracts import AmcContract, CmcContract, Warranty
from ..models.device import Asset
from ..models.documents import Document, DocumentLink, DocumentVersion
from ..models.incident import Incident, ServiceVisit
from ..models.inventory import InventoryItem
from ..models.maintenance import CalibrationRecord, CalibrationSchedule, PMRecord, PMSchedule
from ..models.procurement import Indent, Invoice, PurchaseOrder, Quotation
from ..models.requirement import Requirement
from ..models.vendor import Vendor
from ..schemas.documents import DocumentIn, DocumentLinkIn, DocumentUpdateIn

# entity_type -> model, used to validate a DocumentLink target actually exists before
# writing the link (matches schemas.documents.ENTITY_TYPES exactly).
ENTITY_MODEL_MAP = {
    "incident": Incident,
    "asset": Asset,
    "pm_schedule": PMSchedule,
    "pm_record": PMRecord,
    "calibration_schedule": CalibrationSchedule,
    "calibration_record": CalibrationRecord,
    "vendor": Vendor,
    "amc_contract": AmcContract,
    "cmc_contract": CmcContract,
    "warranty": Warranty,
    "inventory_item": InventoryItem,
    # Final hardening pass:
    "requirement": Requirement,
    "quotation": Quotation,
    "indent": Indent,
    "purchase_order": PurchaseOrder,
    "invoice": Invoice,
    "service_visit": ServiceVisit,
}


class DocumentServiceError(Exception):
    pass


def with_version_count(document: Document) -> Document:
    document.version_count = len(document.versions)
    return document


def create_document(db: Session, payload: DocumentIn, *, user_id: str) -> Document:
    data = payload.model_dump()
    document = Document(**data, uploaded_by=user_id)
    db.add(document)
    db.flush()
    audit_record(db, user_id=user_id, action="CREATE", entity="document", entity_id=document.id, new_value=data)
    db.commit()
    db.refresh(document)
    return document


def update_document(db: Session, document: Document, payload: DocumentUpdateIn, *, user_id: str) -> Document:
    before = {c.name: getattr(document, c.name) for c in document.__table__.columns}
    changes = payload.model_dump(exclude_unset=True)

    if "approval_status" in changes and changes["approval_status"] == "APPROVED":
        changes.setdefault("approved_by", user_id)

    for field, value in changes.items():
        setattr(document, field, value)

    audit_record(db, user_id=user_id, action="UPDATE", entity="document", entity_id=document.id,
                 old_value=before, new_value=changes)
    db.commit()
    db.refresh(document)
    return document


def delete_document(db: Session, document: Document, *, user_id: str) -> None:
    link_count = db.query(DocumentLink).filter(DocumentLink.document_id == document.id).count()
    if link_count:
        raise DocumentServiceError(
            f"Document is linked to {link_count} record(s); remove the link(s) before deleting"
        )

    before = {c.name: getattr(document, c.name) for c in document.__table__.columns}
    audit_record(db, user_id=user_id, action="DELETE", entity="document", entity_id=document.id, old_value=before)
    db.delete(document)
    db.commit()


def _storage_dir() -> Path:
    path = settings.file_storage_path / "documents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_filename(name: str) -> str:
    """Strip any directory components and unsafe characters from a client-supplied
    filename before it is used to build a path on disk, avoiding the path-traversal
    class of bug already flagged (unfixed) in the Import Center's upload handling."""
    base = Path(name).name
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return base or "file"


def create_version(
    db: Session, document: Document, *, version_label: str, effective_date: str | None,
    file_bytes: bytes, file_name: str, user_id: str,
) -> DocumentVersion:
    version = DocumentVersion(
        document_id=document.id, version_label=version_label, effective_date=effective_date,
        file_path="",  # set below once the version id (used in the stored filename) exists
        processing_status="UPLOADED",
    )
    db.add(version)
    db.flush()

    stored_path = _storage_dir() / f"{version.id}_{_safe_filename(file_name)}"
    stored_path.write_bytes(file_bytes)
    version.file_path = str(stored_path)
    document.active_version_id = version.id

    audit_record(db, user_id=user_id, action="CREATE", entity="document_version", entity_id=version.id,
                 new_value={"document_id": document.id, "version_label": version_label, "file_name": file_name})
    db.commit()
    db.refresh(version)
    return version


def link_document(db: Session, document: Document, payload: DocumentLinkIn, *, user_id: str) -> DocumentLink:
    model = ENTITY_MODEL_MAP[payload.entity_type]
    if db.get(model, payload.entity_id) is None:
        raise DocumentServiceError(f"{payload.entity_type} not found")

    existing = (
        db.query(DocumentLink)
        .filter(DocumentLink.document_id == document.id, DocumentLink.entity_type == payload.entity_type,
                DocumentLink.entity_id == payload.entity_id)
        .first()
    )
    if existing is not None:
        raise DocumentServiceError("Document is already linked to this record")

    link = DocumentLink(document_id=document.id, entity_type=payload.entity_type, entity_id=payload.entity_id)
    db.add(link)
    db.flush()
    audit_record(db, user_id=user_id, action="CREATE", entity="document_link", entity_id=link.id,
                 new_value={"document_id": document.id, "entity_type": payload.entity_type, "entity_id": payload.entity_id})
    db.commit()
    db.refresh(link)
    return link


def unlink_document(db: Session, link: DocumentLink, *, user_id: str) -> None:
    before = {"document_id": link.document_id, "entity_type": link.entity_type, "entity_id": link.entity_id}
    audit_record(db, user_id=user_id, action="DELETE", entity="document_link", entity_id=link.id, old_value=before)
    db.delete(link)
    db.commit()
