from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel

# status values: OPEN, WORK_IN_PROGRESS, TO_BE_CONDEMNED, OPERATIONAL,
# CANCELLED (safe "archive" terminal state — see incident_service.ALLOWED_TRANSITIONS).
# severity values: LOW, MEDIUM, HIGH, CRITICAL.


class Incident(BaseModel):
    __tablename__ = "incidents"

    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"), index=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True, index=True)
    reported_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    problem_category: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String, default="MEDIUM")
    clinical_impact: Mapped[str | None] = mapped_column(String, nullable=True)
    patient_care_impact: Mapped[str | None] = mapped_column(String, nullable=True)
    device_status_at_report: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="OPEN")
    assigned_engineer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    assigned_vendor_id: Mapped[str | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String, nullable=True)
    parts_used: Mapped[str | None] = mapped_column(String, nullable=True)
    downtime_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(String, nullable=True)
    corrective_action: Mapped[str | None] = mapped_column(String, nullable=True)
    preventive_action: Mapped[str | None] = mapped_column(String, nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    closed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    closure_approved_by: Mapped[str | None] = mapped_column(String, nullable=True)


class IncidentStatusHistory(BaseModel):
    """Every state transition an incident goes through — the auditable trail Part 3/4 require."""

    __tablename__ = "incident_status_history"

    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String, nullable=True)
    to_status: Mapped[str] = mapped_column(String)
    changed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)


class ServiceVisit(BaseModel):
    """A single engineer/vendor visit or action against an asset — may or may not be tied
    to a formal incident (e.g. routine PM visits are not incidents). Module 18A reuses
    this table (rather than a new "maintenance history" table) as the general-purpose
    maintenance/repair record — `issue`/`downtime_hours` were added here because they
    naturally describe an ad-hoc service/repair visit and have no home on PMRecord/
    CalibrationRecord's routine-schedule shape; see services/asset_lifecycle_service.py's
    unified maintenance-history view across PMRecord/CalibrationRecord/ServiceVisit."""

    __tablename__ = "service_visits"

    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    vendor_id: Mapped[str | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    engineer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    visit_type: Mapped[str | None] = mapped_column(String, nullable=True)  # PM, CALIBRATION, BREAKDOWN, INSTALLATION, OTHER
    visit_date: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    parts_used: Mapped[str | None] = mapped_column(String, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_action: Mapped[str | None] = mapped_column(String, nullable=True)
    issue: Mapped[str | None] = mapped_column(String, nullable=True)
    downtime_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Module 21 — Procurement. Reuses ServiceVisit as-is for a procurement vendor visit
    # rather than a duplicate table; nullable so every pre-existing PM/calibration/
    # breakdown visit is unaffected. Set when a visit is scheduled against a PO.
    purchase_order_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True, index=True)


class ServiceVisitType(BaseModel):
    """Batch E, Part 20 — controlled list of service-visit types, replacing the
    informal PM/CALIBRATION/BREAKDOWN/INSTALLATION/OTHER free-text convention on
    `ServiceVisit.visit_type` (see comment above) with an admin-managed table.

    Deliberately does NOT add a FK/constraint onto `ServiceVisit.visit_type` — that
    column stays a plain nullable string exactly as it was, so no historical row
    (which may carry a free-text value never seeded here) is ever put at risk by this
    migration. New/edited `ServiceVisit` rows are instead validated at the API layer
    (see routers/service_visits.py) against the active names in this table. Follows
    the same minimal BaseModel + unique `name` + `is_active` shape as
    `DeviceCategory`/`DeviceModel.is_active` (models/device.py) — never hard-deleted
    once created, only deactivated, since a type may already be referenced by
    historical ServiceVisit rows."""

    __tablename__ = "service_visit_types"

    name: Mapped[str] = mapped_column(String, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
