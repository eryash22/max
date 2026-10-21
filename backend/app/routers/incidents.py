from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.device import Asset, DeviceModel
from ..models.documents import Document, DocumentLink
from ..models.hospital import Branch
from ..models.incident import Incident, IncidentStatusHistory
from ..models.vendor import DeviceModelVendor, Vendor
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.incident import (
    VALID_STATUSES,
    IncidentAssignIn,
    IncidentAttachmentIn,
    IncidentAttachmentOut,
    IncidentIn,
    IncidentOut,
    IncidentStatusChangeIn,
    IncidentStatusHistoryOut,
    IncidentUpdateIn,
)
from ..security.permissions import DEV_DATA_CLEANUP, INCIDENT_CREATE, INCIDENT_MANAGE, INCIDENT_READ, branch_scope_id
from ..services import asset_lifecycle_service, procurement_service
from ..audit.service import record as audit_record
from ..services.force_delete import assert_dev_environment, force_delete_row
from ..services.incident_service import (
    IncidentServiceError,
    assign_incident,
    change_status,
    create_incident,
    update_incident,
)

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])

_ATTACHMENT_ENTITY_TYPE = "incident"


def _get_or_404(db: Session, incident_id: str) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return incident


def _enforce_own_scope(incident: Incident, current_user: CurrentUser) -> None:
    """Doctor/Staff hold incident.read without incident.manage — RBAC_MATRIX.md scopes
    them to their own reported incidents only, not every incident in the system.
    UNIT_HEAD/TL (real branch user hierarchy, final hardening pass) are scoped to
    their whole assigned branch instead — see branch_scope_id."""
    if INCIDENT_MANAGE in current_user.permissions:
        return
    branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if branch_id is not None:
        if incident.branch_id != branch_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
        return
    if incident.reported_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")


@router.get("", response_model=Page[IncidentOut])
def list_incidents(
    page: int = 1, page_size: int = 25,
    search: str | None = None,
    branch_id: str | None = None,
    department_id: str | None = None,
    location_id: str | None = None,
    status_filter: str | None = None,
    severity: str | None = None,
    asset_id: str | None = None,
    assigned_engineer_id: str | None = None,
    vendor_id: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(INCIDENT_READ)),
):
    """Priority is represented by `severity` (LOW/MEDIUM/HIGH/CRITICAL) — there is no
    separate priority column; the incident tables from Phase B are not being redesigned
    for this without a concrete blocker, and severity already serves that purpose."""
    query = db.query(Incident)

    exact_filters = {
        "branch_id": branch_id, "department_id": department_id, "location_id": location_id,
        "severity": severity, "asset_id": asset_id, "assigned_engineer_id": assigned_engineer_id,
    }
    if status_filter:
        exact_filters["status"] = status_filter
    for field, value in exact_filters.items():
        if value is not None:
            query = query.filter(getattr(Incident, field) == value)

    # Device name, branch and vendor aren't stored on Incident directly (Vendor via
    # the device model's primary/alternative mapping, same as
    # qr_service.primary_vendor_name) — both `search` (matched against every column
    # the list page displays) and the dedicated `vendor_id` filter need these joins,
    # so they share one outerjoin chain instead of duplicating it.
    if search or vendor_id:
        query = (
            query.outerjoin(Asset, Incident.asset_id == Asset.id)
            .outerjoin(DeviceModel, Asset.device_model_id == DeviceModel.id)
            .outerjoin(Branch, Incident.branch_id == Branch.id)
            .outerjoin(DeviceModelVendor, DeviceModelVendor.device_model_id == Asset.device_model_id)
            .outerjoin(Vendor, Vendor.id == DeviceModelVendor.vendor_id)
            .distinct()
        )
        if search:
            like = f"%{search}%"
            query = query.filter(
                or_(
                    Incident.description.ilike(like),
                    Incident.problem_category.ilike(like),
                    Incident.resolution.ilike(like),
                    Incident.cer_number.ilike(like),
                    Incident.indent_number.ilike(like),
                    Incident.po_number.ilike(like),
                    DeviceModel.name.ilike(like),
                    Branch.name.ilike(like),
                    Vendor.name.ilike(like),
                )
            )
        if vendor_id:
            query = query.filter(DeviceModelVendor.vendor_id == vendor_id)

    if created_after:
        query = query.filter(Incident.created_at >= datetime.fromisoformat(created_after))
    if created_before:
        query = query.filter(Incident.created_at <= datetime.fromisoformat(created_before))

    if INCIDENT_MANAGE not in current_user.permissions:
        branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
        if branch_id is not None:
            query = query.filter(Incident.branch_id == branch_id)
        else:
            query = query.filter(Incident.reported_by == current_user.id)

    total = query.count()
    items = query.order_by(Incident.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def report_incident(payload: IncidentIn, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(INCIDENT_CREATE))):
    if db.get(Branch, payload.branch_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
    # Branch-scoping hardening pass — same "reject a foreign branch_id at create time"
    # fix as requirements.py:create_requirement (see its comment for the full trace of
    # the "Incident not found" bug this closes).
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and payload.branch_id != scoped_branch_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only report an incident for your own assigned branch.",
        )
    if payload.asset_id is not None:
        asset = db.get(Asset, payload.asset_id)
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
        # An incident's asset must actually belong to the incident's own branch —
        # applies to every role, not just branch-scoped ones, so branch_id/asset_id
        # can never silently diverge on the same incident.
        if asset.branch_id != payload.branch_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Selected device does not belong to the selected branch.",
            )
    return create_incident(db, payload, reported_by=current_user.id)


@router.get("/{incident_id}", response_model=IncidentOut)
def get_incident(incident_id: str, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_permission(INCIDENT_READ))):
    incident = _get_or_404(db, incident_id)
    _enforce_own_scope(incident, current_user)
    out = IncidentOut.model_validate(incident)
    # Business-workflow audit — coverage is computed only for the single-incident
    # detail read (not list_incidents), and only when the incident is asset-linked,
    # to avoid an extra query per row on list views. No permission beyond the
    # existing incident.read gate above; read-only, nothing stored on Incident.
    if incident.asset_id:
        out.coverage = asset_lifecycle_service.get_incident_coverage(db, incident.asset_id)
    # Module 21 — Procurement status rollup, same detail-only/no-new-permission
    # discipline as coverage above.
    out.procurement = procurement_service.get_incident_procurement_summary(db, incident.id)
    return out


@router.get("/{incident_id}/history", response_model=list[IncidentStatusHistoryOut])
def get_incident_history(incident_id: str, db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(require_permission(INCIDENT_READ))):
    incident = _get_or_404(db, incident_id)
    _enforce_own_scope(incident, current_user)
    return (
        db.query(IncidentStatusHistory)
        .filter(IncidentStatusHistory.incident_id == incident_id)
        .order_by(IncidentStatusHistory.created_at)
        .all()
    )


@router.patch("/{incident_id}", response_model=IncidentOut)
def patch_incident(incident_id: str, payload: IncidentUpdateIn, db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE))):
    incident = _get_or_404(db, incident_id)
    try:
        return update_incident(db, incident, payload, user_id=current_user.id)
    except IncidentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.delete("/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_incident(incident_id: str, force: bool = False, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE))):
    """No non-force hard delete exists here — CLOSED/CANCELLED (via /status) is the
    normal archive path for an incident. ``force=true`` (Super Admin only, gated on
    DEV_DATA_CLEANUP) is the one escape hatch, purging the incident and everything
    that hangs off it (status history, service visits, its own procurement chain,
    attachments) via the generic FK-graph cascade (force_delete_row)."""
    incident = _get_or_404(db, incident_id)
    if not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Incidents are never hard-deleted directly — close/cancel it instead, or use force-delete (Super Admin).",
        )
    if DEV_DATA_CLEANUP not in current_user.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Force-delete requires Super Admin (dev.data_cleanup permission)",
        )
    assert_dev_environment()
    deleted_count = force_delete_row(db, "incidents", incident.id)
    audit_record(
        db, user_id=current_user.id, action="FORCE_DELETE", entity="incident", entity_id=incident_id,
        new_value={"cascaded_row_count": deleted_count},
    )
    db.commit()


@router.post("/{incident_id}/assign", response_model=IncidentOut)
def assign_incident_route(incident_id: str, payload: IncidentAssignIn, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE))):
    incident = _get_or_404(db, incident_id)
    try:
        return assign_incident(db, incident, payload, user_id=current_user.id)
    except IncidentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/{incident_id}/status", response_model=IncidentOut)
def change_incident_status(incident_id: str, payload: IncidentStatusChangeIn, db: Session = Depends(get_db),
                            current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE))):
    if payload.to_status not in VALID_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                             detail=f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")
    incident = _get_or_404(db, incident_id)
    try:
        return change_status(db, incident, payload, user_id=current_user.id)
    except IncidentServiceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{incident_id}/attachments", response_model=list[IncidentAttachmentOut])
def list_incident_attachments(incident_id: str, db: Session = Depends(get_db),
                               current_user: CurrentUser = Depends(require_permission(INCIDENT_READ))):
    incident = _get_or_404(db, incident_id)
    _enforce_own_scope(incident, current_user)
    return (
        db.query(DocumentLink)
        .filter(DocumentLink.entity_type == _ATTACHMENT_ENTITY_TYPE, DocumentLink.entity_id == incident_id)
        .order_by(DocumentLink.created_at)
        .all()
    )


@router.post("/{incident_id}/attachments", response_model=IncidentAttachmentOut, status_code=status.HTTP_201_CREATED)
def add_incident_attachment(incident_id: str, payload: IncidentAttachmentIn, db: Session = Depends(get_db),
                             current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE))):
    """Links an already-uploaded Document to this incident. Does not accept a raw file —
    document upload/creation belongs to the (not-yet-built) Document Management module;
    this only records the association, matching the schema's polymorphic DocumentLink table."""
    incident = _get_or_404(db, incident_id)
    if db.get(Document, payload.document_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    link = DocumentLink(document_id=payload.document_id, entity_type=_ATTACHMENT_ENTITY_TYPE, entity_id=incident.id)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link
