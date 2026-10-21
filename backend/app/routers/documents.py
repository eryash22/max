from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.device import Asset, DeviceModel
from ..models.documents import Document, DocumentLink, DocumentVersion
from ..models.vendor import Vendor
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.documents import (
    ACCESS_LEVELS,
    APPROVAL_STATUSES,
    DOCUMENT_TYPES,
    ENTITY_TYPES,
    DocumentIn,
    DocumentLinkIn,
    DocumentLinkOut,
    DocumentOut,
    DocumentUpdateIn,
    DocumentVersionOut,
)
from ..audit.service import record as audit_record
from ..models.incident import Incident
from ..security.permissions import DEV_DATA_CLEANUP, DOCUMENT_READ, DOCUMENT_WRITE, branch_scope_id
from ..services.force_delete import assert_dev_environment, force_delete_row
from ..services.document_service import (
    DocumentServiceError,
    create_document,
    create_version,
    delete_document,
    link_document,
    unlink_document,
    update_document,
    with_version_count,
)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def _document_out_of_scope(db: Session, document: Document, scoped_branch_id: str) -> bool:
    """True if this document is provably tied to a branch other than the caller's
    assigned branch — via its own asset_id, or a DocumentLink to an incident in
    another branch — and so must not be visible to a UNIT_HEAD/TL scoped there. A
    document with no branch-derivable link at all (e.g. attached only to a
    device_model/vendor catalog entry) is never blocked; those are global reference
    documents, not branch-owned operational data."""
    if document.asset_id is not None:
        asset = db.get(Asset, document.asset_id)
        if asset is not None and asset.branch_id != scoped_branch_id:
            return True
    linked_incident_ids = [
        row[0] for row in db.query(DocumentLink.entity_id)
        .filter(DocumentLink.document_id == document.id, DocumentLink.entity_type == "incident")
        .all()
    ]
    if linked_incident_ids:
        incidents = db.query(Incident).filter(Incident.id.in_(linked_incident_ids)).all()
        if any(incident.branch_id != scoped_branch_id for incident in incidents):
            return True
    return False


def _enforce_document_branch_scope(db: Session, document: Document, current_user: CurrentUser) -> None:
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and _document_out_of_scope(db, document, scoped_branch_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")


def _get_or_404(db: Session, document_id: str) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


def _get_version_or_404(db: Session, document_id: str, version_id: str) -> DocumentVersion:
    version = db.get(DocumentVersion, version_id)
    if version is None or version.document_id != document_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document version not found")
    return version


def _get_link_or_404(db: Session, document_id: str, link_id: str) -> DocumentLink:
    link = db.get(DocumentLink, link_id)
    if link is None or link.document_id != document_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document link not found")
    return link


def _validate_direct_fks(db: Session, *, device_model_id: str | None, asset_id: str | None,
                          vendor_id: str | None) -> None:
    if device_model_id is not None and db.get(DeviceModel, device_model_id) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Device model not found")
    if asset_id is not None and db.get(Asset, asset_id) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Asset not found")
    if vendor_id is not None and db.get(Vendor, vendor_id) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Vendor not found")


@router.get("", response_model=Page[DocumentOut])
def list_documents(
    page: int = 1, page_size: int = 25,
    search: str | None = None,
    document_type: str | None = None,
    access_level: str | None = None,
    approval_status: str | None = None,
    is_active: bool | None = None,
    asset_id: str | None = None,
    vendor_id: str | None = None,
    device_model_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    expiry_after: str | None = None,
    expiry_before: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(DOCUMENT_READ)),
):
    if document_type is not None and document_type not in DOCUMENT_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                             detail=f"document_type must be one of: {', '.join(DOCUMENT_TYPES)}")
    if access_level is not None and access_level not in ACCESS_LEVELS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                             detail=f"access_level must be one of: {', '.join(ACCESS_LEVELS)}")
    if approval_status is not None and approval_status not in APPROVAL_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                             detail=f"approval_status must be one of: {', '.join(APPROVAL_STATUSES)}")
    if (entity_type is None) != (entity_id is None):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                             detail="entity_type and entity_id must be provided together")
    if entity_type is not None and entity_type not in ENTITY_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                             detail=f"entity_type must be one of: {', '.join(ENTITY_TYPES)}")

    query = db.query(Document)

    exact_filters = {
        "document_type": document_type, "access_level": access_level, "approval_status": approval_status,
        "is_active": is_active, "asset_id": asset_id, "vendor_id": vendor_id, "device_model_id": device_model_id,
    }
    for field, value in exact_filters.items():
        if value is not None:
            query = query.filter(getattr(Document, field) == value)

    if search:
        like = f"%{search}%"
        query = query.filter(or_(Document.title.ilike(like), Document.manufacturer.ilike(like),
                                  Document.model_number.ilike(like)))

    if expiry_after:
        query = query.filter(Document.expiry_date.isnot(None), Document.expiry_date >= expiry_after)
    if expiry_before:
        query = query.filter(Document.expiry_date.isnot(None), Document.expiry_date <= expiry_before)

    if entity_type is not None:
        linked_ids = (
            db.query(DocumentLink.document_id)
            .filter(DocumentLink.entity_type == entity_type, DocumentLink.entity_id == entity_id)
        )
        query = query.filter(Document.id.in_(linked_ids))

    # A branch-scoped caller (UNIT_HEAD/TL) must never see another branch's
    # documents — Document has no branch_id of its own, so exclude anything
    # provably tied elsewhere via asset_id or an "incident" DocumentLink. Global
    # catalog documents (device_model/vendor only, or no link at all) stay visible.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        other_branch_asset_ids = db.query(Asset.id).filter(Asset.branch_id != scoped_branch_id)
        other_branch_incident_ids = db.query(Incident.id).filter(Incident.branch_id != scoped_branch_id)
        blocked_by_incident_link = (
            db.query(DocumentLink.document_id)
            .filter(DocumentLink.entity_type == "incident", DocumentLink.entity_id.in_(other_branch_incident_ids))
        )
        query = query.filter(
            # NOT IN against a NULL asset_id evaluates to NULL (never TRUE) in SQL,
            # which would silently drop every document with no asset link at all —
            # guard it explicitly so those (genuinely global) rows stay visible.
            or_(Document.asset_id.is_(None), ~Document.asset_id.in_(other_branch_asset_ids)),
            ~Document.id.in_(blocked_by_incident_link),
        )

    total = query.count()
    items = query.order_by(Document.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [with_version_count(item) for item in items]
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def register_document(payload: DocumentIn, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(DOCUMENT_WRITE))):
    _validate_direct_fks(db, device_model_id=payload.device_model_id, asset_id=payload.asset_id,
                          vendor_id=payload.vendor_id)
    document = create_document(db, payload, user_id=current_user.id)
    return with_version_count(document)


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_permission(DOCUMENT_READ))):
    document = _get_or_404(db, document_id)
    _enforce_document_branch_scope(db, document, current_user)
    return with_version_count(document)


@router.patch("/{document_id}", response_model=DocumentOut)
def patch_document(document_id: str, payload: DocumentUpdateIn, db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(require_permission(DOCUMENT_WRITE))):
    document = _get_or_404(db, document_id)
    _validate_direct_fks(db, device_model_id=payload.device_model_id, asset_id=payload.asset_id,
                          vendor_id=payload.vendor_id)
    document = update_document(db, document, payload, user_id=current_user.id)
    return with_version_count(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document_route(document_id: str, force: bool = False, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(DOCUMENT_WRITE))):
    """``force=true`` (Super Admin only, gated on DEV_DATA_CLEANUP) bypasses the normal
    link-reference guard via the generic FK-graph cascade (force_delete_row) —
    everything hanging off this document (links, versions) is purged too."""
    document = _get_or_404(db, document_id)
    if force:
        if DEV_DATA_CLEANUP not in current_user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Force-delete requires Super Admin (dev.data_cleanup permission)",
            )
        assert_dev_environment()
        deleted_count = force_delete_row(db, "documents", document.id)
        audit_record(
            db, user_id=current_user.id, action="FORCE_DELETE", entity="document", entity_id=document_id,
            new_value={"cascaded_row_count": deleted_count},
        )
        db.commit()
        return None
    try:
        delete_document(db, document, user_id=current_user.id)
    except DocumentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return None


# ---- Versions / file upload & download ----

@router.get("/{document_id}/versions", response_model=list[DocumentVersionOut])
def list_document_versions(document_id: str, db: Session = Depends(get_db),
                            current_user: CurrentUser = Depends(require_permission(DOCUMENT_READ))):
    document = _get_or_404(db, document_id)
    _enforce_document_branch_scope(db, document, current_user)
    return (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.created_at)
        .all()
    )


# Security hardening pass — document uploads (manuals, certificates, service
# reports, photographs, procurement/invoice attachments — every ENTITY_TYPES
# use case) had no extension or size check at all, unlike the Import Center's
# ALLOWED_EXTENSIONS (routers/imports.py). Allowlist, not denylist, per OWASP file-
# upload guidance — covers every document_type this module actually supports
# without ever accepting an executable/script type (.exe/.sh/.html/.svg/.js/...).
ALLOWED_DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".ppt", ".pptx", ".csv", ".txt",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic",
}
MAX_DOCUMENT_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB — generous for a scanned OEM manual


@router.post("/{document_id}/versions", response_model=DocumentVersionOut, status_code=status.HTTP_201_CREATED)
async def upload_document_version(
    document_id: str,
    version_label: str = Form(...),
    effective_date: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(DOCUMENT_WRITE)),
):
    document = _get_or_404(db, document_id)
    if not version_label or not version_label.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="version_label must not be blank")
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A file is required")

    suffix = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if suffix not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{suffix or '(none)'}' is not allowed. Allowed types: {', '.join(sorted(ALLOWED_DOCUMENT_EXTENSIONS))}",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    if len(file_bytes) > MAX_DOCUMENT_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {MAX_DOCUMENT_UPLOAD_BYTES // (1024 * 1024)} MB upload limit",
        )

    return create_version(db, document, version_label=version_label, effective_date=effective_date,
                           file_bytes=file_bytes, file_name=file.filename, user_id=current_user.id)


@router.get("/{document_id}/download")
def download_active_version(document_id: str, db: Session = Depends(get_db),
                             current_user: CurrentUser = Depends(require_permission(DOCUMENT_READ))):
    document = _get_or_404(db, document_id)
    _enforce_document_branch_scope(db, document, current_user)
    if document.active_version_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document has no uploaded file yet")
    version = _get_version_or_404(db, document_id, document.active_version_id)
    return FileResponse(path=version.file_path, filename=version.file_name)


@router.get("/{document_id}/versions/{version_id}/download")
def download_document_version(document_id: str, version_id: str, db: Session = Depends(get_db),
                               current_user: CurrentUser = Depends(require_permission(DOCUMENT_READ))):
    document = _get_or_404(db, document_id)
    _enforce_document_branch_scope(db, document, current_user)
    version = _get_version_or_404(db, document_id, version_id)
    return FileResponse(path=version.file_path, filename=version.file_name)


# ---- Links (polymorphic entity linkage) ----

@router.get("/{document_id}/links", response_model=list[DocumentLinkOut])
def list_document_links(document_id: str, db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(require_permission(DOCUMENT_READ))):
    document = _get_or_404(db, document_id)
    _enforce_document_branch_scope(db, document, current_user)
    return (
        db.query(DocumentLink)
        .filter(DocumentLink.document_id == document_id)
        .order_by(DocumentLink.created_at)
        .all()
    )


@router.post("/{document_id}/links", response_model=DocumentLinkOut, status_code=status.HTTP_201_CREATED)
def add_document_link(document_id: str, payload: DocumentLinkIn, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(DOCUMENT_WRITE))):
    document = _get_or_404(db, document_id)
    try:
        return link_document(db, document, payload, user_id=current_user.id)
    except DocumentServiceError as exc:
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if detail.endswith("not found") else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=detail)


@router.delete("/{document_id}/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_document_link(document_id: str, link_id: str, db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(require_permission(DOCUMENT_WRITE))):
    link = _get_link_or_404(db, document_id, link_id)
    unlink_document(db, link, user_id=current_user.id)
    return None
