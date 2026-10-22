"""Composed dashboard aggregation (F1 + F2).

Before this module the Dashboard issued roughly `37 + 8 x branches` HTTP requests per
load — every KPI was a separate `GET /<list>?page=1&page_size=1` whose only purpose was
reading `.total` off the pagination envelope, and the branch table added eight more per
branch. For an eight-branch Super Admin that is ~101 requests.

Everything here is a `GROUP BY` over a table the caller is already allowed to read, so
the whole dashboard becomes one request. Two rules this module exists to enforce:

1. **One canonical open-incident definition.** The old client-side
   `total - RESOLVED - CLOSED` counted CANCELLED incidents as open. Open/closed is now
   decided once, from `schemas.incident.OPEN_STATUSES`.

2. **Permission and branch scope decided server-side.** A section is omitted from the
   response entirely (not zeroed) when the caller lacks its permission, so an
   unauthorized metric is never serialised — the frontend renders what it is given
   rather than deciding what to hide. Branch-scoped callers (UNIT_HEAD/TL) are pinned
   to `branch_scope_id()` and cannot widen it via the `branch_id` cross-filter.
"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.device import Asset
from ..models.fulfillment import FulfillmentRequest
from ..models.hospital import Branch
from ..models.incident import Incident
from ..models.maintenance import CalibrationSchedule, PMSchedule
from ..models.requirement import Requirement
from ..models.review import ReviewItem
from ..schemas.auth import CurrentUser
from ..schemas.incident import OPEN_STATUSES as INCIDENT_OPEN_STATUSES
from ..schemas.requirement import VALID_STATUSES as REQUIREMENT_STATUSES
from ..schemas.fulfillment import FULFILLMENT_STATUSES
from ..security.permissions import (
    ASSET_READ,
    AUDIT_READ,
    CALIBRATION_READ,
    CONTRACT_READ,
    INCIDENT_READ,
    INVENTORY_READ,
    PM_READ,
    PROCUREMENT_READ,
    REQUIREMENT_APPROVE,
    REQUIREMENT_CREATE,
    REQUIREMENT_FULFILL,
    REQUIREMENT_READ,
    REVIEW_READ,
    branch_scope_id,
)
from . import asset_lifecycle_service


def _counts_by(db: Session, column, *filters) -> dict[str, int]:
    """`GROUP BY column` -> {value: count}. One query per breakdown, replacing the one
    request-per-status pattern the frontend used."""
    query = db.query(column, func.count()).group_by(column)
    for f in filters:
        query = query.filter(f)
    return {str(value): count for value, count in query.all() if value is not None}


def _zero_filled(counts: dict[str, int], vocabulary) -> dict[str, int]:
    """Statuses with no rows still belong in a chart's axis, so every known status is
    present. Any unexpected value already in the data is preserved rather than dropped."""
    out = {status: counts.get(status, 0) for status in vocabulary}
    for key, value in counts.items():
        if key not in out:
            out[key] = value
    return out


def resolve_scope(current_user: CurrentUser, requested_branch_id: str | None) -> tuple[str | None, str]:
    """Decide the branch this dashboard is computed for.

    A branch-scoped role is pinned to its own branch and the requested `branch_id` is
    ignored outright — this is what stops the Power BI-style branch cross-filter from
    becoming a way to read another branch. A privileged caller may use `branch_id` as a
    genuine cross-filter, or omit it for the organisation-wide view."""
    scoped = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped is not None:
        return scoped, "BRANCH"
    if requested_branch_id:
        return requested_branch_id, "ORGANIZATION"
    return None, "ORGANIZATION"


def _period_bounds(period: str, year: int | None, month: int | None):
    """Flexible replacement for the old fixed-financial-year window. `period` is
    "year" (all of `year`, defaulting to the current year), "month" (`year`/`month`,
    defaulting to the current month) or "overall" (no bound — every incident).
    Returns (start, end) naive datetimes, or (None, None) for "overall"."""
    from datetime import datetime

    today = datetime.utcnow()
    if period == "year":
        y = year or today.year
        return datetime(y, 1, 1), datetime(y + 1, 1, 1)
    if period == "month":
        y = year or today.year
        m = month or today.month
        start = datetime(y, m, 1)
        end = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)
        return start, end
    return None, None


def _incident_section(
    db: Session,
    branch_id: str | None,
    *,
    period: str = "overall",
    period_year: int | None = None,
    period_month: int | None = None,
) -> dict:
    base = [Incident.branch_id == branch_id] if branch_id else []
    by_status = _zero_filled(_counts_by(db, Incident.status, *base), _incident_vocabulary())
    by_severity = _counts_by(db, Incident.severity, *base)

    open_filter = Incident.status.in_(INCIDENT_OPEN_STATUSES)
    open_total = db.query(func.count(Incident.id)).filter(open_filter, *base).scalar() or 0
    critical_open = (
        db.query(func.count(Incident.id))
        .filter(open_filter, Incident.severity == "CRITICAL", *base)
        .scalar()
        or 0
    )
    period_start, period_end = _period_bounds(period, period_year, period_month)
    period_filters = list(base)
    if period_start is not None:
        period_filters += [Incident.created_at >= period_start, Incident.created_at < period_end]
    period_total = db.query(func.count(Incident.id)).filter(*period_filters).scalar() or 0

    po_spending_incl_gst = db.query(func.coalesce(func.sum(Incident.po_amount), 0)).filter(*base).scalar() or 0
    po_spending_excl_gst = (
        db.query(func.coalesce(func.sum(Incident.po_cost_excl_gst), 0)).filter(*base).scalar() or 0
    )

    return {
        "total": sum(by_status.values()),
        "open": open_total,
        "critical_open": critical_open,
        "by_status": by_status,
        "by_severity": by_severity,
        "open_statuses": list(INCIDENT_OPEN_STATUSES),
        "to_be_condemned": by_status.get("TO_BE_CONDEMNED", 0),
        "period": period,
        "period_year": period_year,
        "period_month": period_month,
        "period_total": period_total,
        "po_spending_incl_gst": float(po_spending_incl_gst),
        "po_spending_excl_gst": float(po_spending_excl_gst),
    }


def _incident_vocabulary():
    from ..schemas.incident import VALID_STATUSES

    return VALID_STATUSES


def _requirement_section(db: Session, branch_id: str | None) -> dict:
    base = [Requirement.branch_id == branch_id] if branch_id else []
    by_status = _zero_filled(_counts_by(db, Requirement.status, *base), REQUIREMENT_STATUSES)
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "pending_approval": by_status.get("PENDING_MANAGER_APPROVAL", 0),
        "submitted": by_status.get("SUBMITTED", 0),
    }


def _fulfillment_section(db: Session, branch_id: str | None) -> dict:
    query = db.query(FulfillmentRequest.status, func.count()).join(
        Requirement, FulfillmentRequest.requirement_id == Requirement.id
    )
    if branch_id:
        query = query.filter(Requirement.branch_id == branch_id)
    counts = {str(status): count for status, count in query.group_by(FulfillmentRequest.status).all() if status}
    by_status = _zero_filled(counts, FULFILLMENT_STATUSES)
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "pending_allocation": by_status.get("PENDING_ALLOCATION", 0),
        "under_process": by_status.get("UNDER_PROCESS", 0),
        "out_for_delivery": by_status.get("OUT_FOR_DELIVERY", 0),
    }


def _asset_section(db: Session, branch_id: str | None) -> dict:
    """Lifecycle counts come from the existing branch-scoped
    asset_lifecycle_service.compute_dashboard_summary() rather than being recomputed —
    that function is already the canonical owner of the lifecycle vocabulary."""
    summary = asset_lifecycle_service.compute_dashboard_summary(db, branch_id=branch_id)
    base = [Asset.branch_id == branch_id] if branch_id else []
    total = db.query(func.count(Asset.id)).filter(*base).scalar() or 0
    return {
        "total": total,
        "by_status": _counts_by(db, Asset.status, *base),
        "operational": summary.total_operational,
        "under_maintenance": summary.under_maintenance,
        "under_repair": summary.under_repair,
        "condemned": summary.condemned,
        "retired": summary.retired,
        "amc_active": summary.amc_active,
        "amc_expired": summary.amc_expired,
        "cmc_active": summary.cmc_active,
        "cmc_expired": summary.cmc_expired,
        "warranty_active": summary.warranty_active,
        "warranty_expired": summary.warranty_expired,
        "upcoming_amc_expiry": summary.upcoming_amc_expiry,
        "upcoming_cmc_expiry": summary.upcoming_cmc_expiry,
        "recent_maintenance": [entry.model_dump() for entry in summary.recent_maintenance],
    }


def _maintenance_section(db: Session, branch_id: str | None, *, can_pm: bool, can_calibration: bool) -> dict:
    """PM and calibration are gated independently — a role can hold one without the
    other, and the old dashboard surfaced a raw 403 when it assumed otherwise."""
    from ..services.pm_service import DUE_SOON_WINDOW_DAYS, _today

    from datetime import timedelta

    today = _today().isoformat()
    soon = (_today() + timedelta(days=DUE_SOON_WINDOW_DAYS)).isoformat()

    def _due_counts(model):
        query = db.query(model)
        if branch_id:
            query = query.join(Asset, Asset.id == model.asset_id).filter(Asset.branch_id == branch_id)
        overdue = query.filter(model.next_due_date.isnot(None), model.next_due_date < today).count()
        due = query.filter(
            model.next_due_date.isnot(None), model.next_due_date >= today, model.next_due_date <= soon
        ).count()
        return {"due": due, "overdue": overdue}

    section: dict = {}
    if can_pm:
        section["pm"] = _due_counts(PMSchedule)
    if can_calibration:
        section["calibration"] = _due_counts(CalibrationSchedule)
    return section


def _review_section(db: Session, branch_id: str | None) -> dict:
    """Review items reach a branch through BranchDeviceAssignment, the same join
    routers/review_queue.py already uses — no second definition of "my branch's review
    items"."""
    from ..models.device import BranchDeviceAssignment

    query = db.query(ReviewItem)
    if branch_id:
        query = query.join(
            BranchDeviceAssignment,
            (ReviewItem.source_type == "branch_device_assignment")
            & (ReviewItem.source_id == BranchDeviceAssignment.id),
        ).filter(BranchDeviceAssignment.branch_id == branch_id)
    total = query.count()
    open_items = query.filter(ReviewItem.status == "OPEN").count()
    return {"total": total, "open": open_items}


def _branch_rows(db: Session, current_user: CurrentUser, *, can_incident: bool, can_requirement: bool) -> list[dict]:
    """The branch comparison table, as three grouped queries instead of eight requests
    per branch. Branch-scoped callers get only their own row (a one-row comparison is
    still correct, and the frontend presents it as branch context rather than a
    league table)."""
    scoped = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    branch_query = db.query(Branch)
    if scoped is not None:
        branch_query = branch_query.filter(Branch.id == scoped)
    branches = branch_query.order_by(Branch.name).all()
    if not branches:
        return []

    branch_ids = [b.id for b in branches]
    assets = dict(
        db.query(Asset.branch_id, func.count())
        .filter(Asset.branch_id.in_(branch_ids))
        .group_by(Asset.branch_id)
        .all()
    )
    operational = dict(
        db.query(Asset.branch_id, func.count())
        .filter(Asset.branch_id.in_(branch_ids), func.upper(Asset.status) == "OPERATIONAL")
        .group_by(Asset.branch_id)
        .all()
    )
    incidents: dict = {}
    total_incidents: dict = {}
    to_be_condemned: dict = {}
    po_spend: dict = {}
    if can_incident:
        incidents = dict(
            db.query(Incident.branch_id, func.count())
            .filter(Incident.branch_id.in_(branch_ids), Incident.status.in_(INCIDENT_OPEN_STATUSES))
            .group_by(Incident.branch_id)
            .all()
        )
        total_incidents = dict(
            db.query(Incident.branch_id, func.count())
            .filter(Incident.branch_id.in_(branch_ids))
            .group_by(Incident.branch_id)
            .all()
        )
        to_be_condemned = dict(
            db.query(Incident.branch_id, func.count())
            .filter(Incident.branch_id.in_(branch_ids), Incident.status == "TO_BE_CONDEMNED")
            .group_by(Incident.branch_id)
            .all()
        )
        po_spend = dict(
            db.query(Incident.branch_id, func.coalesce(func.sum(Incident.po_amount), 0))
            .filter(Incident.branch_id.in_(branch_ids))
            .group_by(Incident.branch_id)
            .all()
        )
    requirements: dict = {}
    if can_requirement:
        requirements = dict(
            db.query(Requirement.branch_id, func.count())
            .filter(
                Requirement.branch_id.in_(branch_ids),
                Requirement.status.in_(["SUBMITTED", "PENDING_MANAGER_APPROVAL"]),
            )
            .group_by(Requirement.branch_id)
            .all()
        )

    return [
        {
            "id": b.id,
            "name": b.name,
            "status": b.status,
            "is_central_warehouse": bool(getattr(b, "is_central_warehouse", False)),
            "devices": assets.get(b.id, 0),
            "operational": operational.get(b.id, 0),
            # None means "you may not see this column"; a real zero means "none in this
            # branch". Defaulting a missing GROUP BY key to 0 keeps those distinct — the
            # frontend renders None as an em dash.
            "open_incidents": incidents.get(b.id, 0) if can_incident else None,
            "total_incidents": total_incidents.get(b.id, 0) if can_incident else None,
            "to_be_condemned": to_be_condemned.get(b.id, 0) if can_incident else None,
            "po_spend_incl_gst": float(po_spend.get(b.id, 0)) if can_incident else None,
            "pending_requirements": requirements.get(b.id, 0) if can_requirement else None,
        }
        for b in branches
    ]


def compute_dashboard(
    db: Session,
    current_user: CurrentUser,
    *,
    branch_id: str | None = None,
    period: str = "overall",
    period_year: int | None = None,
    period_month: int | None = None,
) -> dict:
    """The whole dashboard in one response, shaped by the caller's permissions."""
    perms = current_user.permissions
    scope_branch_id, scope_kind = resolve_scope(current_user, branch_id)

    can_asset = ASSET_READ in perms
    can_incident = INCIDENT_READ in perms
    can_requirement = any(p in perms for p in (REQUIREMENT_READ, REQUIREMENT_CREATE, REQUIREMENT_APPROVE))
    can_fulfillment = can_requirement or REQUIREMENT_FULFILL in perms
    can_inventory = INVENTORY_READ in perms
    can_contract = CONTRACT_READ in perms
    can_pm = PM_READ in perms
    can_calibration = CALIBRATION_READ in perms
    can_review = REVIEW_READ in perms
    can_procurement = PROCUREMENT_READ in perms
    can_audit = AUDIT_READ in perms

    result: dict = {
        "branch_id": scope_branch_id,
        "scope": scope_kind,
        "can_choose_branch": scope_kind == "ORGANIZATION",
        "sections": [],
    }

    if can_asset:
        result["assets"] = _asset_section(db, scope_branch_id)
        result["sections"].append("assets")
    if can_incident:
        result["incidents"] = _incident_section(
            db, scope_branch_id, period=period, period_year=period_year, period_month=period_month
        )
        result["sections"].append("incidents")
    if can_requirement:
        result["requirements"] = _requirement_section(db, scope_branch_id)
        result["sections"].append("requirements")
    if can_fulfillment:
        result["fulfillments"] = _fulfillment_section(db, scope_branch_id)
        result["sections"].append("fulfillments")
    if can_pm or can_calibration:
        result["maintenance"] = _maintenance_section(
            db, scope_branch_id, can_pm=can_pm, can_calibration=can_calibration
        )
        result["sections"].append("maintenance")
    if can_review:
        result["review"] = _review_section(db, scope_branch_id)
        result["sections"].append("review")
    if can_inventory:
        # Central warehouse device stock is physical Asset rows grouped by DeviceModel,
        # CONDEMNED/RETIRED already excluded by the shared eligibility predicate. Gated
        # on inventory.read exactly like GET /assets/central-device-stock, so a
        # picker-only role never receives quantities.
        result["central_device_stock"] = asset_lifecycle_service.compute_central_device_stock(db)
        result["sections"].append("central_device_stock")
    if can_asset:
        result["branches"] = _branch_rows(
            db, current_user, can_incident=can_incident, can_requirement=can_requirement
        )
        result["sections"].append("branches")

    result["capabilities"] = {
        "asset": can_asset,
        "incident": can_incident,
        "requirement": can_requirement,
        "fulfillment": can_fulfillment,
        "inventory": can_inventory,
        "contract": can_contract,
        "pm": can_pm,
        "calibration": can_calibration,
        "review": can_review,
        "procurement": can_procurement,
        "audit": can_audit,
    }
    return result
