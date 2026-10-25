from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from ..database import get_db
from ..dependencies.auth import require_permission
from ..exports.writer import EXPORT_FORMATS, build_export_response
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
    IncidentBulkDeleteBlockedOut,
    IncidentBulkDeletePreviewOut,
    IncidentBulkDeleteResultOut,
    IncidentBulkSelectionIn,
    IncidentIn,
    IncidentOut,
    IncidentReplaceAllIssueOut,
    IncidentReplaceAllPreviewOut,
    IncidentReplaceAllResultOut,
    IncidentStatusChangeIn,
    IncidentStatusHistoryOut,
    IncidentUpdateIn,
)
from ..security.permissions import DEV_DATA_CLEANUP, INCIDENT_CREATE, INCIDENT_MANAGE, INCIDENT_READ, branch_scope_id
from ..services import asset_lifecycle_service, procurement_service
from ..audit.service import record as audit_record
from ..services.export_service import MAX_EXPORT_ROWS, record_export_audit
from ..services.force_delete import assert_dev_environment, force_delete_row
from ..services.incident_service import (
    IncidentHasBlockingDependentsError,
    IncidentServiceError,
    assign_incident,
    change_status,
    create_incident,
    delete_incident_safe,
    incident_blocking_dependents,
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


# branch/vendor/device sort stays client-side (page-only) — each needs the
# outerjoin chain below, and combining ORDER BY on a joined column with this
# query's plain .distinct() hits Postgres's "ORDER BY expressions must appear
# in select list" restriction for SELECT DISTINCT. date/status/severity are
# plain Incident columns, no join needed, no such restriction, and fix the
# actual confirmed bug (old-dated imports invisible under newest-first).
_SORT_FIELDS = {"date", "status", "severity"}


def _incident_filter_query(
    db: Session, *, search: str | None, branch_id: str | None, status_filter: str | None,
    severity: str | None, vendor_id: str | None,
) -> Query:
    """The filter subset shared by export_incidents and the bulk-delete endpoints —
    'every incident matching the current filter' needs to mean exactly the same
    thing in all three places, or a bulk-delete could touch rows the preview never
    showed. list_incidents keeps its own fuller filter set (department/location/
    asset/engineer/date-range) inline since nothing else needs those."""
    query = db.query(Incident)
    if branch_id is not None:
        query = query.filter(Incident.branch_id == branch_id)
    if severity is not None:
        query = query.filter(Incident.severity == severity)
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
        if statuses:
            query = query.filter(Incident.status.in_(statuses))

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
                    Incident.description.ilike(like), Incident.cer_number.ilike(like),
                    Incident.indent_number.ilike(like), Incident.po_number.ilike(like),
                    Incident.device_name.ilike(like), Incident.manufacturer.ilike(like),
                    Incident.vendor_name.ilike(like), Incident.remark.ilike(like),
                    DeviceModel.name.ilike(like), Branch.name.ilike(like), Vendor.name.ilike(like),
                )
            )
        if vendor_id:
            query = query.filter(DeviceModelVendor.vendor_id == vendor_id)
    return query


# Defense-in-depth for destructive bulk operations (see _resolve_bulk_selection
# below): a branch-scoped role (UNIT_HEAD/TL) that has been granted incident.manage
# (a real possibility — role permissions are DB-driven, see
# test_branch_scope_hardening.py) is still clamped to its own branch, even though
# incident.manage itself has no built-in branch restriction elsewhere in this
# router. Unlike list_incidents' own scoping, this does not special-case
# incident.manage as "therefore global" — deleting data warrants a stricter
# default than reading it.


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
    sort_by: str = "date",
    sort_dir: str = "desc",
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(INCIDENT_READ)),
):
    """Priority is represented by `severity` (LOW/MEDIUM/HIGH/CRITICAL) — there is no
    separate priority column; the incident tables from Phase B are not being redesigned
    for this without a concrete blocker, and severity already serves that purpose.

    `sort_by`/`sort_dir` (security/completeness pass): previously this endpoint always
    returned newest-first with no way to ask for anything else — the frontend's sort
    dropdown only re-sorted the 25 rows already on the current page, so a bulk import
    of historical (often much older) incidents could land entirely on a late page and
    look like it "didn't import" even though every row saved correctly. Real
    server-side sorting fixes that at the source."""
    if sort_by not in _SORT_FIELDS:
        sort_by = "date"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"

    query = db.query(Incident)

    exact_filters = {
        "branch_id": branch_id, "department_id": department_id, "location_id": location_id,
        "severity": severity, "asset_id": asset_id, "assigned_engineer_id": assigned_engineer_id,
    }
    for field, value in exact_filters.items():
        if value is not None:
            query = query.filter(getattr(Incident, field) == value)
    if status_filter:
        # Comma-separated list of statuses — lets the Incidents list filter by
        # several statuses at once (e.g. "OPEN,WORK_IN_PROGRESS") instead of only
        # one; a single value still works exactly as before.
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
        if statuses:
            query = query.filter(Incident.status.in_(statuses))

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
                    Incident.device_name.ilike(like),
                    Incident.manufacturer.ilike(like),
                    Incident.vendor_name.ilike(like),
                    Incident.remark.ilike(like),
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
    sort_column = {"date": Incident.created_at, "status": Incident.status, "severity": Incident.severity}[sort_by]
    order = sort_column.asc() if sort_dir == "asc" else sort_column.desc()
    items = query.order_by(order).offset((page - 1) * page_size).limit(page_size).all()
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


_INCIDENT_STATUS_EXPORT_LABELS = {
    "OPEN": "Open",
    "WORK_IN_PROGRESS": "WIP",
    "TO_BE_CONDEMNED": "To Be Condemned",
    "OPERATIONAL": "Closed",
    "CANCELLED": "Cancelled",
}


@router.get("/export")
def export_incidents(
    format: str = "xlsx", search: str | None = None, branch_id: str | None = None,
    status_filter: str | None = None, severity: str | None = None, vendor_id: str | None = None,
    db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(INCIDENT_READ)),
):
    """Headers/order/sheet name match the user's own `incident_history_template.xlsx`
    exactly (sheet name "Sheet" is openpyxl's own default for build_export_response,
    needs no extra code) — an export can be edited in Excel and re-imported cleanly.
    Reuses list_incidents' filter-building (`_incident_filter_query`) and its own
    branch-scoping rather than a second query-building path — a branch-restricted
    caller can only ever export their own branch's rows, same as they can only list
    them. `device_name`/`manufacturer`/`vendor_name` (migration 0029) are this
    historical data's own free-text source values — exported as stored, never
    re-derived from a live Asset link (most imported incidents have none)."""
    if format not in EXPORT_FORMATS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"format must be one of {', '.join(EXPORT_FORMATS)}")

    query = _incident_filter_query(
        db, search=search, branch_id=branch_id, status_filter=status_filter, severity=severity, vendor_id=vendor_id,
    )
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if INCIDENT_MANAGE not in current_user.permissions:
        if scoped_branch_id is not None:
            query = query.filter(Incident.branch_id == scoped_branch_id)
        else:
            query = query.filter(Incident.reported_by == current_user.id)

    total = query.count()
    rows_q = query.order_by(Incident.created_at.desc()).limit(MAX_EXPORT_ROWS).all()

    headers = [
        "Item Name", "Date", "Unit*", "Make", "Vendor", "Issue Description*",
        "Cer No", "Indent No", "Po No", "Po Closure Date", "Status",
        "Po Cost - Excl. Gst", "Po Gst %", "Po Amount",
        "Vendor Estimate Cost - Excl. Gst", "Vendor Estimate Gst %", "Vendor Estimate Cost - Incl. Gst",
        "Remark",
    ]
    rows = []
    for incident in rows_q:
        branch = db.get(Branch, incident.branch_id) if incident.branch_id else None
        rows.append([
            incident.device_name, incident.created_at.isoformat() if incident.created_at else None,
            branch.name if branch else None, incident.manufacturer, incident.vendor_name, incident.description,
            incident.cer_number, incident.indent_number, incident.po_number, incident.po_closure_date,
            _INCIDENT_STATUS_EXPORT_LABELS.get(incident.status, incident.status),
            incident.po_cost_excl_gst, incident.po_gst_percent, incident.po_amount,
            incident.vendor_estimate_cost_excl_gst, incident.vendor_estimate_gst_percent,
            incident.vendor_estimate_cost_incl_gst, incident.remark,
        ])

    record_export_audit(
        db, user_id=current_user.id, module="incidents",
        filters={"search": search, "branch_id": branch_id, "status_filter": status_filter,
                 "severity": severity, "vendor_id": vendor_id},
        format=format, record_count=len(rows), truncated=total > MAX_EXPORT_ROWS,
    )
    return build_export_response(headers=headers, rows=rows, format=format, filename_stem="incidents_export")


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
    """Real (non-force) delete: refuses with 409 if a genuine procurement record
    (Quotation/Indent/PurchaseOrder/Invoice) still references this incident —
    otherwise deletes it and its own owned history/escalation/attachment rows,
    detaching (not deleting) any linked service visit/PM/calibration record. See
    delete_incident_safe (services/incident_service.py) for the exact rule.

    ``force=true`` (Super Admin only, gated on DEV_DATA_CLEANUP) remains the
    unconditional escape hatch, purging the incident and *everything* that hangs
    off it — including real procurement records — via the generic FK-graph cascade
    (force_delete_row). Use the safe path above for routine cleanup."""
    incident = _get_or_404(db, incident_id)
    _enforce_own_scope(incident, current_user)
    if force:
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
        return

    try:
        before = {"description": incident.description, "branch_id": incident.branch_id, "status": incident.status}
        delete_incident_safe(db, incident)
    except IncidentHasBlockingDependentsError as exc:
        db.rollback()
        detail = "; ".join(f"{len(ids)} {label}(s)" for label, ids in exc.blockers)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete — still referenced by {detail}. Resolve those records first.",
        )
    audit_record(db, user_id=current_user.id, action="DELETE", entity="incident", entity_id=incident_id, old_value=before)
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


# ---- Bulk delete ----
# Explicit, user-initiated cleanup — never run automatically by any import. Both
# endpoints accept the same IncidentBulkSelectionIn shape (explicit id list, or
# "every row matching these filters") so the real delete always acts on exactly
# what the preview showed.

def _resolve_bulk_selection(db: Session, current_user: CurrentUser, payload: IncidentBulkSelectionIn) -> list[Incident]:
    # The scope clamp must OVERRIDE a caller-supplied branch_id, not be ANDed with
    # it — list_incidents does the same override (never an additional filter) for
    # exactly this reason: AND-ing "branch_id == their_choice" with
    # "branch_id == their_actual_scope" is unsatisfiable whenever those differ,
    # which would silently select zero rows instead of correctly clamping to the
    # caller's own branch.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)

    if payload.incident_ids:
        query = db.query(Incident).filter(Incident.id.in_(payload.incident_ids))
        if scoped_branch_id is not None:
            query = query.filter(Incident.branch_id == scoped_branch_id)
    elif payload.select_all_matching_filter:
        effective_branch_id = scoped_branch_id if scoped_branch_id is not None else payload.branch_id
        query = _incident_filter_query(
            db, search=payload.search, branch_id=effective_branch_id, status_filter=payload.status_filter,
            severity=payload.severity, vendor_id=payload.vendor_id,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either incident_ids or select_all_matching_filter=true",
        )
    return query.all()


@router.post("/bulk-delete/preview", response_model=IncidentBulkDeletePreviewOut)
def preview_bulk_delete_incidents(payload: IncidentBulkSelectionIn, db: Session = Depends(get_db),
                                   current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE))):
    incidents = _resolve_bulk_selection(db, current_user, payload)
    blocked = []
    for incident in incidents:
        blockers = incident_blocking_dependents(db, incident.id)
        if blockers:
            blocked.append(IncidentBulkDeleteBlockedOut(
                incident_id=incident.id, description=incident.description,
                blocked_by="; ".join(f"{len(ids)} {label}(s)" for label, ids in blockers),
            ))
    return IncidentBulkDeletePreviewOut(
        total_selected=len(incidents), deletable_count=len(incidents) - len(blocked), blocked=blocked,
    )


@router.post("/bulk-delete", response_model=IncidentBulkDeleteResultOut)
def bulk_delete_incidents(payload: IncidentBulkSelectionIn, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE))):
    """Best-effort per incident (deliberate choice, matching this codebase's existing
    bulk pattern — see qr.py's bulk_generate_qr): deletes every selected incident with
    no blocking procurement dependent, skips and reports the rest by id+reason rather
    than failing the whole selection over one blocked row. One audit entry for the
    whole batch (actor, affected count and ids), not one per row."""
    incidents = _resolve_bulk_selection(db, current_user, payload)
    deleted_ids: list[str] = []
    skipped: list[IncidentBulkDeleteBlockedOut] = []
    for incident in incidents:
        try:
            delete_incident_safe(db, incident)
            deleted_ids.append(incident.id)
        except IncidentHasBlockingDependentsError as exc:
            detail = "; ".join(f"{len(ids)} {label}(s)" for label, ids in exc.blockers)
            skipped.append(IncidentBulkDeleteBlockedOut(
                incident_id=incident.id, description=incident.description, blocked_by=detail,
            ))

    if deleted_ids:
        audit_record(
            db, user_id=current_user.id, action="BULK_DELETE", entity="incident",
            new_value={"deleted_count": len(deleted_ids), "deleted_ids": deleted_ids,
                       "skipped_count": len(skipped)},
        )
    db.commit()
    return IncidentBulkDeleteResultOut(deleted=len(deleted_ids), skipped=len(skipped), skipped_incidents=skipped)


# ---- Replace all incidents from Excel ----
# Deliberately separate from the Import Center's Append flow (see
# services/import_service.py's incident section) — this is a destructive
# delete-then-insert, not an upsert, and reuses the exact same parsing/validation
# code (imports/parser.py, imports/validators.py, the "incident history"
# SheetConfig) rather than a second pipeline. Never runs without an explicit,
# freshly-confirmed user action from the live app.

def _parse_incident_workbook_for_preview(db: Session, file_bytes: bytes, filename: str):
    """Parses an uploaded workbook with the exact same code path Append mode uses
    (parse_workbook + map_row + validate_row + the incident-specific validation —
    imported lazily to avoid a circular import, see import_service.py's own
    incident section for what these do), without creating an ImportJob row — this
    is a pure dry run, no writes. Returns (valid_mapped_rows, issues) where issues
    is [(row_number, field, severity, message), ...], the same vocabulary the
    Import Center's own preview already uses."""
    import tempfile

    from ..imports.mapping import SHEET_CONFIGS
    from ..imports.parser import parse_csv, parse_workbook
    from ..imports.validators import business_key as _bk, map_row, validate_row
    from ..services.import_service import _validate_incident_row

    config = SHEET_CONFIGS["incident history"]
    suffix = Path(filename).suffix.lower() if filename else ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)
    try:
        if suffix == ".csv":
            parsed_sheets = parse_csv(tmp_path, display_name=Path(filename).stem)
        else:
            parsed_sheets = parse_workbook(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    sheet = next((s for s in parsed_sheets if s.config and s.config.entity_type == "incident"), None)
    valid_rows: list[dict] = []
    issues: list[tuple[int, str | None, str, str]] = []
    if sheet is None:
        issues.append((0, None, "ERROR", "No 'incident history'-shaped sheet found in this workbook"))
        return valid_rows, issues

    seen_keys: set[str] = set()
    for parsed_row in sheet.rows:
        mapped = map_row(config, parsed_row.raw)
        row_issues = validate_row(config, mapped)
        if not any(sev == "ERROR" for _, _, sev in row_issues):
            row_issues = row_issues + _validate_incident_row(db, mapped, parsed_row.formula_fields)
        has_error = any(sev == "ERROR" for _, _, sev in row_issues)
        for field, message, severity in row_issues:
            issues.append((parsed_row.row_number, field, severity, message))
        key = _bk(config, mapped)
        if key:
            if key in seen_keys:
                issues.append((parsed_row.row_number, None, "INFO",
                                "Same branch + description + date as an earlier row in this file — "
                                "will still be imported as its own, separate incident"))
            seen_keys.add(key)
        if not has_error:
            valid_rows.append(mapped)
    return valid_rows, issues


@router.post("/replace-all/preview", response_model=IncidentReplaceAllPreviewOut)
async def preview_replace_all_incidents(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE)),
):
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A file is required")
    file_bytes = await file.read()
    valid_rows, issues = _parse_incident_workbook_for_preview(db, file_bytes, file.filename)

    current_count = db.query(Incident).count()
    blocking: list[IncidentBulkDeleteBlockedOut] = []
    if current_count:
        for incident in db.query(Incident).all():
            blockers = incident_blocking_dependents(db, incident.id)
            if blockers:
                blocking.append(IncidentBulkDeleteBlockedOut(
                    incident_id=incident.id, description=incident.description,
                    blocked_by="; ".join(f"{len(ids)} {label}(s)" for label, ids in blockers),
                ))

    invalid_count = len({row_number for row_number, _, severity, _ in issues if severity == "ERROR"})
    return IncidentReplaceAllPreviewOut(
        current_incident_count=current_count,
        incoming_valid_rows=len(valid_rows),
        incoming_invalid_rows=invalid_count,
        issues=[IncidentReplaceAllIssueOut(row_number=r, field=f, severity=s, message=m) for r, f, s, m in issues],
        blocking_existing_incidents=blocking,
    )


@router.post("/replace-all", response_model=IncidentReplaceAllResultOut)
async def replace_all_incidents(
    confirm_replace: bool = False,
    expected_current_count: int = -1,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE)),
):
    """Atomic: either every existing incident is replaced by every valid row in the
    uploaded workbook, or nothing changes at all. Refuses up front (no deletes) if
    any existing incident is still referenced by a real procurement record, or if
    `expected_current_count` doesn't match the live count (the confirmation went
    stale — something changed between preview and confirm)."""
    if not confirm_replace:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="confirm_replace must be true")
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A file is required")

    current_count = db.query(Incident).count()
    if expected_current_count != current_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Stale confirmation — current incident count is {current_count}, "
                   f"expected {expected_current_count}. Re-run the preview and confirm again.",
        )

    file_bytes = await file.read()
    valid_rows, _issues = _parse_incident_workbook_for_preview(db, file_bytes, file.filename)

    existing_incidents = db.query(Incident).all()
    for incident in existing_incidents:
        blockers = incident_blocking_dependents(db, incident.id)
        if blockers:
            db.rollback()
            detail = "; ".join(f"{len(ids)} {label}(s)" for label, ids in blockers)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot replace — incident {incident.id} is still referenced by {detail}. "
                       f"Nothing was changed. Resolve those records first.",
            )

    deleted = 0
    for incident in existing_incidents:
        delete_incident_safe(db, incident)
        deleted += 1

    # Reuses the exact same field-resolution logic Append mode's _import_incident
    # uses (status mapping, date coercion, branch lookup, the update-fields list) —
    # one source of truth for "how a mapped row becomes an Incident", imported
    # lazily to avoid a circular import between this router and import_service.
    from ..services.import_service import (
        _INCIDENT_STATUS_MAP, _INCIDENT_UPDATE_FIELDS, _coerce_incident_date, _resolve_branch_id_by_name_ci,
    )

    inserted = 0
    for mapped in valid_rows:
        branch_id = _resolve_branch_id_by_name_ci(db, mapped.get("branch_code"))
        if branch_id is None:
            continue
        status_raw = mapped.get("status")
        new_status = _INCIDENT_STATUS_MAP.get(str(status_raw).strip().lower()) if status_raw else None
        incident = Incident(branch_id=branch_id, description=mapped.get("description"), status=new_status or "OPEN")
        new_date = _coerce_incident_date(mapped.get("incident_date"))
        if new_date is not None:
            incident.created_at = new_date
        for field_name in _INCIDENT_UPDATE_FIELDS:
            value = mapped.get(field_name)
            if value is not None:
                setattr(incident, field_name, value)
        db.add(incident)
        inserted += 1

    audit_record(
        db, user_id=current_user.id, action="REPLACE_ALL", entity="incident",
        new_value={"deleted": deleted, "inserted": inserted, "source_file": file.filename},
    )
    db.commit()
    return IncidentReplaceAllResultOut(deleted=deleted, inserted=inserted)
