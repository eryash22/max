from sqlalchemy import Boolean, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampMixin
from ..database import Base

# Device QR Code Management System — single-row, server-side display configuration
# for the public QR scan page, same singleton shape as models/theme.py's ThemeConfig
# (exactly one row, always at this fixed id — see routers/qr.py, which always reads/
# writes db.get(QrDisplaySettings, DEFAULT_QR_SETTINGS_ID)). Not a BaseModel (UUID PK
# + audit/version mixins) for the same reason ThemeConfig isn't: there's only ever
# one row for the whole deployment.
DEFAULT_QR_SETTINGS_ID = "default"


class QrDisplaySettings(TimestampMixin, Base):
    __tablename__ = "qr_display_settings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=DEFAULT_QR_SETTINGS_ID)
    # Global on/off switch for the public scan page. When False, every token's public
    # page reports "unavailable" instead of device data — distinct from an individual
    # asset's qr_active (per-asset revoke), see routers/qr_public.py.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Ordered list of {"key": <allowlisted field key>, "enabled": bool} — validated
    # against services/qr_service.QR_FIELD_REGISTRY (the fixed allowlist) by
    # schemas/qr.py before ever reaching this column, so a caller can never store an
    # arbitrary database column name or expression here. List order is display order.
    field_config: Mapped[list | None] = mapped_column(JSON, nullable=True)
    updated_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
