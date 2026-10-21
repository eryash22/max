from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..models.device import Asset
from ..models.hospital import Branch
from ..models.identity import User
from ..models.incident import Incident, IncidentStatusHistory
from ..schemas.incident import IncidentAssignIn, IncidentIn, IncidentStatusChangeIn, IncidentUpdateIn
from ..schemas.notification import EVENT_INCIDENT_ASSIGNED, EVENT_INCIDENT_RESOLVED
from .communication_service import dispatch_incident_event_safe

# Business-workflow audit: device issues route to BME for technical repair, never to
# Inventory Manager. assign_incident() previously accepted any user_id as
# assigned_engineer_id with no role check — a soft convention, not an enforced one.
# These are the roles actually allowed to hold INCIDENT_MANAGE (see permissions.py),
# i.e. the roles that can legitimately act as the assigned technical owner.
ENGINEER_ASSIGNABLE_ROLES = {"BIOMEDICAL_ENGINEER", "BIOMEDICAL_ADMIN", "SUPER_ADMIN"}

# Workflow consolidation pass — Incident previously accepted any of the 11 VALID_STATUSES
# from any current status (unlike Requirement/Fulfillment, which both enforce a real
# transition graph). This closes that gap without introducing new status values. CLOSED
# is reachable directly from every non-terminal state (not just RESOLVED/
# PENDING_VERIFICATION) since a real biomedical engineer routinely closes an incident
# outright — duplicate report, false alarm, withdrawn by reporter — without walking it
# through the full diagnosis chain first; existing test coverage
# (test_status_transition_creates_history_and_closes_incident) already exercises
# ACKNOWLEDGED -> CLOSED on that basis.
# Final non-AI cleanup pass — CANCELLED added as a safe "archive" terminal state
# (duplicate report, false alarm, withdrawn by reporter) reachable from every
# still-open, not-yet-resolved status. It preserves the incident and its full status
# history/audit trail (nothing is deleted), matching the "archive instead of destroy"
# principle used elsewhere (branches/devices/vendors/contracts) — there is
# deliberately no DELETE endpoint for incidents; Cancel is the safe equivalent for a
# record that can be linked from asset history, repair, procurement, and audit.
# Not reachable from RESOLVED/PENDING_VERIFICATION (the incident is already
# effectively done — closing is the correct next step, not cancelling) or CLOSED
# (terminal).
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"WORK_IN_PROGRESS", "TO_BE_CONDEMNED", "OPERATIONAL", "CANCELLED"},
    "WORK_IN_PROGRESS": {"TO_BE_CONDEMNED", "OPERATIONAL", "CANCELLED"},
    "TO_BE_CONDEMNED": {"OPERATIONAL", "CANCELLED"},
    "OPERATIONAL": set(),
    "CANCELLED": set(),
}


class IncidentServiceError(Exception):
    """Generic validation failure (e.g. an illegal status transition) — 409-mappable."""
    pass


def create_incident(db: Session, payload: IncidentIn, *, reported_by: str) -> Incident:
    incident = Incident(reported_by=reported_by, status="OPEN", **payload.model_dump())
    db.add(incident)
    db.flush()

    db.add(IncidentStatusHistory(incident_id=incident.id, from_status=None, to_status="OPEN", changed_by=reported_by))
    audit_record(db, user_id=reported_by, action="CREATE", entity="incident", entity_id=incident.id,
                 new_value=payload.model_dump())
    db.commit()
    db.refresh(incident)
    return incident


def update_incident(db: Session, incident: Incident, payload: IncidentUpdateIn, *, user_id: str) -> Incident:
    before = {c.name: getattr(incident, c.name) for c in incident.__table__.columns}
    changes = payload.model_dump(exclude_unset=True)
    if "branch_id" in changes and changes["branch_id"] is not None and db.get(Branch, changes["branch_id"]) is None:
        raise IncidentServiceError("branch_id does not reference a known branch")
    if "asset_id" in changes and changes["asset_id"] is not None and db.get(Asset, changes["asset_id"]) is None:
        raise IncidentServiceError("asset_id does not reference a known asset")
    for field, value in changes.items():
        setattr(incident, field, value)

    audit_record(db, user_id=user_id, action="UPDATE", entity="incident", entity_id=incident.id,
                 old_value=before, new_value=changes)
    db.commit()
    db.refresh(incident)
    return incident


def assign_incident(db: Session, incident: Incident, payload: IncidentAssignIn, *, user_id: str) -> Incident:
    """Dedicated assignment action: sets assignee(s), and — if the incident is still in
    its initial NEW/ACKNOWLEDGED state — advances it to ASSIGNED, with the same history
    + audit trail a manual status change would produce."""
    if payload.assigned_engineer_id is not None:
        engineer = db.get(User, payload.assigned_engineer_id)
        if engineer is None:
            raise IncidentServiceError("assigned_engineer_id does not reference a known user")
        engineer_roles = {ur.role.code for ur in engineer.roles}
        if not engineer_roles & ENGINEER_ASSIGNABLE_ROLES:
            raise IncidentServiceError(
                "assigned_engineer_id must belong to a Biomedical Engineer (or admin) user — "
                "device issues are a BME technical responsibility, not a general assignment"
            )

    before = {"assigned_engineer_id": incident.assigned_engineer_id, "assigned_vendor_id": incident.assigned_vendor_id}
    if payload.assigned_engineer_id is not None:
        incident.assigned_engineer_id = payload.assigned_engineer_id
    if payload.assigned_vendor_id is not None:
        incident.assigned_vendor_id = payload.assigned_vendor_id

    from_status = incident.status
    if incident.status == "OPEN":
        incident.status = "WORK_IN_PROGRESS"
        db.add(IncidentStatusHistory(
            incident_id=incident.id, from_status=from_status, to_status="WORK_IN_PROGRESS",
            changed_by=user_id, notes=payload.notes,
        ))

    audit_record(db, user_id=user_id, action="ASSIGN", entity="incident", entity_id=incident.id,
                 old_value=before, new_value={"assigned_engineer_id": incident.assigned_engineer_id,
                                               "assigned_vendor_id": incident.assigned_vendor_id})
    db.commit()
    db.refresh(incident)
    if incident.assigned_engineer_id:
        dispatch_incident_event_safe(db, EVENT_INCIDENT_ASSIGNED, incident=incident, user_id=user_id)
    return incident


def change_status(db: Session, incident: Incident, payload: IncidentStatusChangeIn, *, user_id: str) -> Incident:
    from_status = incident.status
    if payload.to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise IncidentServiceError(
            f"Cannot move incident from {from_status} to {payload.to_status}"
        )
    incident.status = payload.to_status
    if payload.to_status == "OPERATIONAL":
        incident.closed_by = user_id
        incident.closed_at = datetime.now(timezone.utc).isoformat()

    db.add(IncidentStatusHistory(
        incident_id=incident.id, from_status=from_status, to_status=payload.to_status,
        changed_by=user_id, notes=payload.notes,
    ))
    audit_record(db, user_id=user_id, action="STATUS_CHANGE", entity="incident", entity_id=incident.id,
                 old_value={"status": from_status}, new_value={"status": payload.to_status, "notes": payload.notes})
    db.commit()
    db.refresh(incident)
    if payload.to_status == "OPERATIONAL":
        dispatch_incident_event_safe(db, EVENT_INCIDENT_RESOLVED, incident=incident, user_id=user_id)
    return incident
