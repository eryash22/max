from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel

# event_type values: STATUS_CHANGE, LOCATION_CHANGE, DEPARTMENT_CHANGE, BRANCH_TRANSFER,
# OWNERSHIP_CHANGE, LIFECYCLE_TRANSITION (procurement->...->disposed stages).
# A single polymorphic table avoids three near-identical status/location/ownership
# history tables that would otherwise duplicate the same shape.


class AssetHistory(BaseModel):
    __tablename__ = "asset_history"

    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    event_type: Mapped[str] = mapped_column(String)
    from_value: Mapped[str | None] = mapped_column(String, nullable=True)
    to_value: Mapped[str | None] = mapped_column(String, nullable=True)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"), nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
