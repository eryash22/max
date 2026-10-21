from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel

# frequency values: MONTHLY, QUARTERLY, HALF_YEARLY, YEARLY, CUSTOM (with custom_interval_days set).


class PMSchedule(BaseModel):
    __tablename__ = "pm_schedules"

    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    frequency: Mapped[str | None] = mapped_column(String, nullable=True)
    custom_interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_due_date: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PMRecord(BaseModel):
    __tablename__ = "pm_records"

    schedule_id: Mapped[str | None] = mapped_column(ForeignKey("pm_schedules.id"), nullable=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    engineer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Workflow consolidation pass — mirrors ServiceVisit.incident_id (models/incident.py):
    # an Incident raised as a "Maintenance Request" can hand off into a real PMRecord
    # without duplicating PM's own model/workflow inside Incident.
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    performed_date: Mapped[str | None] = mapped_column(String, nullable=True)
    checklist: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parts_replaced: Mapped[str | None] = mapped_column(String, nullable=True)
    electrical_safety_result: Mapped[str | None] = mapped_column(String, nullable=True)
    functional_check_result: Mapped[str | None] = mapped_column(String, nullable=True)
    calibration_status: Mapped[str | None] = mapped_column(String, nullable=True)
    next_due_date: Mapped[str | None] = mapped_column(String, nullable=True)
    supervisor_approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    supervisor_approved_at: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)


class CalibrationSchedule(BaseModel):
    __tablename__ = "calibration_schedules"

    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    frequency: Mapped[str | None] = mapped_column(String, nullable=True)
    custom_interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_due_date: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CalibrationRecord(BaseModel):
    __tablename__ = "calibration_records"

    schedule_id: Mapped[str | None] = mapped_column(ForeignKey("calibration_schedules.id"), nullable=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    # Workflow consolidation pass — mirrors ServiceVisit.incident_id (models/incident.py):
    # an Incident raised as a "Calibration Request" can hand off into a real
    # CalibrationRecord without duplicating Calibration's own model/workflow inside Incident.
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    performed_date: Mapped[str | None] = mapped_column(String, nullable=True)
    certificate_number: Mapped[str | None] = mapped_column(String, nullable=True)
    calibration_agency: Mapped[str | None] = mapped_column(String, nullable=True)
    reference_standard: Mapped[str | None] = mapped_column(String, nullable=True)
    result: Mapped[str | None] = mapped_column(String, nullable=True)
    pass_fail: Mapped[str | None] = mapped_column(String, nullable=True)
    next_due_date: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
