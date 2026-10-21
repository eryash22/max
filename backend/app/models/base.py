from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.ids import new_id


class UUIDPKMixin:
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class AuditFieldsMixin:
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VersionMixin:
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class BaseModel(UUIDPKMixin, TimestampMixin, AuditFieldsMixin, VersionMixin, Base):
    __abstract__ = True
