from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel

# Configurable inputs only — never a source of "actual" financial figures. Any dashboard
# reading these must label output as Estimated/Projected, never Actual, per AGENTS.md.


class BusinessImpactAssumption(BaseModel):
    __tablename__ = "business_impact_assumptions"

    name: Mapped[str] = mapped_column(String)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    device_category: Mapped[str | None] = mapped_column(String, nullable=True)
    revenue_per_hour: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String, default="INR")
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
