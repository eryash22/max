from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class ReviewItem(BaseModel):
    """Data-quality queue entry. Points at a source record without merging or fabricating data."""

    __tablename__ = "review_items"

    source_type: Mapped[str] = mapped_column(String)
    source_id: Mapped[str] = mapped_column(String)
    reason_code: Mapped[str] = mapped_column(String)
    detail: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="OPEN")
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(String, nullable=True)
    import_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
