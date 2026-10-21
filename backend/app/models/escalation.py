from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel

# trigger_type values: TIME_BASED, SEVERITY_BASED, CLINICAL_RISK, REPEATED_FAILURE, UNRESOLVED.
# Policies are rows, not code — configuring "critical ventilator = immediate escalation" means
# inserting a policy + level rows, not editing a Python if/else chain.


class EscalationPolicy(BaseModel):
    __tablename__ = "escalation_policies"

    name: Mapped[str] = mapped_column(String, unique=True)
    trigger_type: Mapped[str] = mapped_column(String)
    severity_threshold: Mapped[str | None] = mapped_column(String, nullable=True)
    device_criticality_threshold: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class EscalationLevel(BaseModel):
    """Ordered chain of who gets notified for a policy: Level 1 engineer, Level 2 manager, etc."""

    __tablename__ = "escalation_levels"

    policy_id: Mapped[str] = mapped_column(ForeignKey("escalation_policies.id"), index=True)
    level_number: Mapped[int] = mapped_column(Integer)
    role_code: Mapped[str | None] = mapped_column(String, nullable=True)
    wait_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EscalationEvent(BaseModel):
    """Actual firing log — one row each time a policy level triggers for a real incident."""

    __tablename__ = "escalation_events"

    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("escalation_policies.id"))
    level_number: Mapped[int] = mapped_column(Integer)
    notified_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
