from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.business_impact import BusinessImpactAssumption
from ..models.device import Asset
from ..models.hospital import Branch
from ..models.incident import Incident
from ..repositories.crud import paginate
from ..schemas.auth import CurrentUser
from ..schemas.business_impact import (
    BusinessImpactAssumptionIn,
    BusinessImpactAssumptionOut,
    FailureSummaryOut,
    IncidentImpactEstimateOut,
)
from ..schemas.common import Page
from ..security.permissions import BUSINESS_IMPACT_READ, HOSPITAL_WRITE, branch_scope_id
from ..services.business_impact_service import compute_failure_summary, estimate_incident_impact, get_assumption_or_404

assumptions_router = APIRouter(prefix="/api/v1/business-impact", tags=["business-impact"])
analytics_router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def _get_or_404(db: Session, model, entity_id: str, label: str):
    obj = db.get(model, entity_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return obj


def _enforce_branch_scope(current_user: CurrentUser, record_branch_id: str | None, label: str) -> None:
    """UNIT_HEAD/TL hold business_impact.read (security/permissions.py) so they can see
    financial-impact context for their own branch's incidents/assets — but nothing
    here previously checked branch_id at all, so a branch-scoped caller could read
    another branch's revenue-per-hour assumptions, incident impact estimates, or
    failure-rate analytics by id, the same commercially-sensitive cross-branch leak
    incidents.py/devices.py already close for the underlying records. A record with
    no branch_id (an org-wide assumption) is never branch-specific, so it's left
    visible."""
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and record_branch_id is not None and record_branch_id != scoped_branch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")


# ---- Business impact assumptions ----
# Write-gated on HOSPITAL_WRITE rather than a dedicated business_impact.write code, since
# none was pre-seeded in security/permissions.py (only business_impact.read exists — verified
# before writing any code, same check performed for Modules 08/09/10's reused permissions).
# Assumption records are a hospital-level financial policy input, not case-by-case data, which
# matches RBAC_MATRIX.md's "Manage organization... global policies" row (Full: Admin, No:
# Engineer) — the same grant pattern HOSPITAL_WRITE already has. See PROJECT_STATUS.md.

@assumptions_router.get("/assumptions", response_model=Page[BusinessImpactAssumptionOut])
def list_assumptions(page: int = 1, page_size: int = 25, search: str | None = None, branch_id: str | None = None,
                      device_category: str | None = None, is_active: bool | None = None,
                      db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(require_permission(BUSINESS_IMPACT_READ))):
    # A branch-scoped caller (UNIT_HEAD/TL) only ever sees their own branch's
    # assumptions, regardless of the branch_id filter passed in — same convention as
    # routers/devices.py:list_assets.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        branch_id = scoped_branch_id
    items, total = paginate(db, BusinessImpactAssumption, page=page, page_size=page_size, search=search,
                             search_fields=["name"], branch_id=branch_id, device_category=device_category, is_active=is_active)
    return Page(items=items, total=total, page=page, page_size=page_size)


@assumptions_router.get("/assumptions/{assumption_id}", response_model=BusinessImpactAssumptionOut)
def get_assumption(assumption_id: str, db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(require_permission(BUSINESS_IMPACT_READ))):
    assumption = get_assumption_or_404(db, assumption_id)
    _enforce_branch_scope(current_user, assumption.branch_id, "Assumption")
    return assumption


@assumptions_router.post("/assumptions", response_model=BusinessImpactAssumptionOut, status_code=status.HTTP_201_CREATED)
def create_assumption(payload: BusinessImpactAssumptionIn, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    if payload.branch_id is not None:
        _get_or_404(db, Branch, payload.branch_id, "Branch")
    obj = BusinessImpactAssumption(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="business_impact_assumption", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@assumptions_router.patch("/assumptions/{assumption_id}", response_model=BusinessImpactAssumptionOut)
def update_assumption(assumption_id: str, payload: BusinessImpactAssumptionIn, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(HOSPITAL_WRITE))):
    obj = get_assumption_or_404(db, assumption_id)
    if payload.branch_id is not None:
        _get_or_404(db, Branch, payload.branch_id, "Branch")
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    for key, value in payload.model_dump().items():
        setattr(obj, key, value)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="business_impact_assumption", entity_id=obj.id, old_value=before, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@assumptions_router.get("/incidents/{incident_id}/estimate", response_model=IncidentImpactEstimateOut)
def get_incident_impact_estimate(incident_id: str, db: Session = Depends(get_db),
                                  current_user: CurrentUser = Depends(require_permission(BUSINESS_IMPACT_READ))):
    incident = _get_or_404(db, Incident, incident_id, "Incident")
    _enforce_branch_scope(current_user, incident.branch_id, "Incident")
    return estimate_incident_impact(db, incident)


# ---- Failure analytics (MTBF/MTTR/repeat-failure) ----
# Computed on the fly from Incident rows — nothing is stored, matching the PM/Calibration
# due_status and Contracts expiry_status precedent of exposing a derived field rather than
# a persisted one. Gated on BUSINESS_IMPACT_READ (the same read code covers both halves of
# this module's "Business Impact & Analytics" scope, since no separate analytics.read code
# was pre-seeded either).

@analytics_router.get("/failure-summary/{asset_id}", response_model=FailureSummaryOut)
def get_failure_summary(asset_id: str, db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(require_permission(BUSINESS_IMPACT_READ))):
    asset = _get_or_404(db, Asset, asset_id, "Asset")
    _enforce_branch_scope(current_user, asset.branch_id, "Asset")
    return compute_failure_summary(db, asset_id)


@analytics_router.get("/failure-summary", response_model=Page[FailureSummaryOut])
def list_failure_summaries(page: int = 1, page_size: int = 25, branch_id: str | None = None,
                            repeat_failure_only: bool = False, db: Session = Depends(get_db),
                            current_user: CurrentUser = Depends(require_permission(BUSINESS_IMPACT_READ))):
    # Same branch-scoping convention as list_assumptions above — a branch-scoped
    # caller can never widen this via the branch_id query param.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        branch_id = scoped_branch_id
    asset_ids = [row[0] for row in db.query(Incident.asset_id).filter(Incident.asset_id.isnot(None)).distinct().all()]
    assets_by_id = {a.id: a for a in db.query(Asset).filter(Asset.id.in_(asset_ids)).all()} if asset_ids else {}
    if branch_id:
        asset_ids = [aid for aid in asset_ids if assets_by_id.get(aid) and assets_by_id[aid].branch_id == branch_id]

    summaries = [compute_failure_summary(db, aid) for aid in asset_ids]
    if repeat_failure_only:
        summaries = [s for s in summaries if s["repeat_failure"]]
    summaries.sort(key=lambda s: s["incident_count"], reverse=True)

    total = len(summaries)
    start = (page - 1) * page_size
    page_items = summaries[start:start + page_size]
    return Page(items=page_items, total=total, page=page, page_size=page_size)
