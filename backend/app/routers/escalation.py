from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.identity import User
from ..models.escalation import EscalationEvent, EscalationLevel, EscalationPolicy
from ..models.incident import Incident
from ..repositories.crud import paginate
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.escalation import (
    EscalationEventIn,
    EscalationEventOut,
    EscalationLevelIn,
    EscalationLevelOut,
    EscalationPolicyIn,
    EscalationPolicyOut,
)
from ..security.permissions import INCIDENT_MANAGE, INCIDENT_READ
from ..services.escalation_service import get_policy_or_404, validate_level_number_exists

router = APIRouter(prefix="/api/v1/escalation", tags=["escalation"])


def _get_or_404(db: Session, model, entity_id: str, label: str):
    obj = db.get(model, entity_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return obj


# ---- Escalation policies ----

@router.get("/policies", response_model=Page[EscalationPolicyOut])
def list_policies(page: int = 1, page_size: int = 25, search: str | None = None,
                   trigger_type: str | None = None, is_active: bool | None = None,
                   db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(INCIDENT_READ))):
    items, total = paginate(db, EscalationPolicy, page=page, page_size=page_size, search=search,
                             search_fields=["name"], trigger_type=trigger_type, is_active=is_active)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/policies/{policy_id}", response_model=EscalationPolicyOut)
def get_policy(policy_id: str, db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(INCIDENT_READ))):
    return get_policy_or_404(db, policy_id)


@router.post("/policies", response_model=EscalationPolicyOut, status_code=status.HTTP_201_CREATED)
def create_policy(payload: EscalationPolicyIn, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE))):
    if db.query(EscalationPolicy).filter(EscalationPolicy.name == payload.name).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An escalation policy with this name already exists")
    obj = EscalationPolicy(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="escalation_policy", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/policies/{policy_id}", response_model=EscalationPolicyOut)
def update_policy(policy_id: str, payload: EscalationPolicyIn, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE))):
    obj = get_policy_or_404(db, policy_id)
    duplicate = db.query(EscalationPolicy).filter(
        EscalationPolicy.name == payload.name, EscalationPolicy.id != policy_id
    ).first()
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An escalation policy with this name already exists")
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    for key, value in payload.model_dump().items():
        setattr(obj, key, value)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="escalation_policy", entity_id=obj.id, old_value=before, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


# ---- Escalation levels (ordered chain within a policy) ----

@router.get("/policies/{policy_id}/levels", response_model=list[EscalationLevelOut])
def list_levels(policy_id: str, db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(INCIDENT_READ))):
    get_policy_or_404(db, policy_id)
    return (
        db.query(EscalationLevel)
        .filter(EscalationLevel.policy_id == policy_id)
        .order_by(EscalationLevel.level_number)
        .all()
    )


@router.post("/policies/{policy_id}/levels", response_model=EscalationLevelOut, status_code=status.HTTP_201_CREATED)
def create_level(policy_id: str, payload: EscalationLevelIn, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE))):
    get_policy_or_404(db, policy_id)
    if db.query(EscalationLevel).filter(
        EscalationLevel.policy_id == policy_id, EscalationLevel.level_number == payload.level_number
    ).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This policy already has a level with this level_number")

    obj = EscalationLevel(policy_id=policy_id, **payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="escalation_level", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/policies/{policy_id}/levels/{level_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_level(policy_id: str, level_id: str, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE))):
    get_policy_or_404(db, policy_id)
    obj = db.query(EscalationLevel).filter(EscalationLevel.id == level_id, EscalationLevel.policy_id == policy_id).first()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escalation level not found")
    before = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    db.delete(obj)
    audit_record(db, user_id=current_user.id, action="DELETE", entity="escalation_level", entity_id=level_id, old_value=before)
    db.commit()


# ---- Escalation events (firing log) ----

@router.get("/events", response_model=Page[EscalationEventOut])
def list_events(page: int = 1, page_size: int = 25, incident_id: str | None = None, policy_id: str | None = None,
                 notified_user_id: str | None = None, db: Session = Depends(get_db),
                 _: CurrentUser = Depends(require_permission(INCIDENT_READ))):
    items, total = paginate(db, EscalationEvent, page=page, page_size=page_size,
                             incident_id=incident_id, policy_id=policy_id, notified_user_id=notified_user_id)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/events", response_model=EscalationEventOut, status_code=status.HTTP_201_CREATED)
def create_event(payload: EscalationEventIn, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_permission(INCIDENT_MANAGE))):
    _get_or_404(db, Incident, payload.incident_id, "Incident")
    get_policy_or_404(db, payload.policy_id)
    validate_level_number_exists(db, payload.policy_id, payload.level_number)
    if payload.notified_user_id is not None:
        _get_or_404(db, User, payload.notified_user_id, "User")

    obj = EscalationEvent(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="escalation_event", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return obj
