from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models.business_impact import BusinessImpactAssumption
from ..models.device import Asset, DeviceModel
from ..models.incident import Incident

REPEAT_FAILURE_THRESHOLD = 3


def get_assumption_or_404(db: Session, assumption_id: str) -> BusinessImpactAssumption:
    obj = db.get(BusinessImpactAssumption, assumption_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business impact assumption not found")
    return obj


def _best_matching_assumption(db: Session, branch_id: str | None, device_category: str | None) -> BusinessImpactAssumption | None:
    """Prefers an assumption matching both branch and device category, then branch-only,
    then device-category-only, then a fully generic (both null) assumption — most specific
    configured rate wins, matching how a real finance policy would be applied.
    """
    query = db.query(BusinessImpactAssumption).filter(BusinessImpactAssumption.is_active.is_(True))
    candidates = query.all()

    def score(a: BusinessImpactAssumption) -> int:
        s = 0
        if a.branch_id is not None and a.branch_id == branch_id:
            s += 2
        elif a.branch_id is not None:
            return -1
        if a.device_category is not None and a.device_category == device_category:
            s += 1
        elif a.device_category is not None:
            return -1
        return s

    scored = [(score(a), a) for a in candidates]
    scored = [(s, a) for s, a in scored if s >= 0]
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]


def estimate_incident_impact(db: Session, incident: Incident) -> dict:
    if incident.downtime_hours is None:
        return {
            "incident_id": incident.id, "asset_id": incident.asset_id, "downtime_hours": None,
            "assumption_id": None, "revenue_per_hour": None, "currency": None, "estimated_impact": None,
            "basis": "No downtime_hours recorded on this incident yet.",
        }

    device_category = None
    if incident.asset_id is not None:
        asset = db.get(Asset, incident.asset_id)
        if asset is not None:
            model = db.get(DeviceModel, asset.device_model_id)
            device_category = model.category if model is not None else None

    assumption = _best_matching_assumption(db, incident.branch_id, device_category)
    if assumption is None or assumption.revenue_per_hour is None:
        return {
            "incident_id": incident.id, "asset_id": incident.asset_id, "downtime_hours": incident.downtime_hours,
            "assumption_id": None, "revenue_per_hour": None, "currency": None, "estimated_impact": None,
            "basis": "No matching business_impact_assumption with a revenue_per_hour is configured.",
        }

    estimated_impact = incident.downtime_hours * assumption.revenue_per_hour
    return {
        "incident_id": incident.id, "asset_id": incident.asset_id, "downtime_hours": incident.downtime_hours,
        "assumption_id": assumption.id, "revenue_per_hour": assumption.revenue_per_hour, "currency": assumption.currency,
        "estimated_impact": round(estimated_impact, 2),
        "basis": f"Estimated as downtime_hours x revenue_per_hour from assumption '{assumption.name}'.",
    }


def compute_failure_summary(db: Session, asset_id: str) -> dict:
    incidents = (
        db.query(Incident)
        .filter(Incident.asset_id == asset_id)
        .order_by(Incident.created_at)
        .all()
    )
    incident_count = len(incidents)
    downtimes = [i.downtime_hours for i in incidents if i.downtime_hours is not None]
    total_downtime_hours = round(sum(downtimes), 2) if downtimes else 0.0
    mttr_hours = round(sum(downtimes) / len(downtimes), 2) if downtimes else None

    mtbf_hours = None
    basis_parts = []
    if incident_count >= 2:
        span_seconds = (incidents[-1].created_at - incidents[0].created_at).total_seconds()
        mtbf_hours = round((span_seconds / 3600) / (incident_count - 1), 2) if span_seconds > 0 else None
        basis_parts.append("MTBF = time span between first and last incident / (incident count - 1).")
    else:
        basis_parts.append("MTBF requires at least 2 incidents to compute a time span; insufficient data.")
    if mttr_hours is not None:
        basis_parts.append("MTTR = average downtime_hours across incidents with downtime recorded.")
    else:
        basis_parts.append("MTTR requires at least one incident with downtime_hours recorded; insufficient data.")

    return {
        "asset_id": asset_id,
        "incident_count": incident_count,
        "total_downtime_hours": total_downtime_hours,
        "mttr_hours": mttr_hours,
        "mtbf_hours": mtbf_hours,
        "repeat_failure": incident_count >= REPEAT_FAILURE_THRESHOLD,
        "basis": " ".join(basis_parts),
    }
