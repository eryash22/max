from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import UUIDPKMixin, TimestampMixin
from ..database import Base


class AuditLog(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"

    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String)
    entity: Mapped[str] = mapped_column(String)
    entity_id: Mapped[str | None] = mapped_column(String, nullable=True)
    old_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    import_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    request_ip: Mapped[str | None] = mapped_column(String, nullable=True)
