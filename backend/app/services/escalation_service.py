from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models.escalation import EscalationLevel, EscalationPolicy


def get_policy_or_404(db: Session, policy_id: str) -> EscalationPolicy:
    policy = db.get(EscalationPolicy, policy_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escalation policy not found")
    return policy


def validate_level_number_exists(db: Session, policy_id: str, level_number: int) -> None:
    """An EscalationEvent must reference a real rung on the policy's escalation chain.

    No dedicated blocker forces the event to also validate wait_minutes/role_code —
    level_number is the only field EscalationEvent actually stores from the chain.
    """
    exists = (
        db.query(EscalationLevel)
        .filter(EscalationLevel.policy_id == policy_id, EscalationLevel.level_number == level_number)
        .first()
    )
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"level_number {level_number} is not configured on this policy",
        )
