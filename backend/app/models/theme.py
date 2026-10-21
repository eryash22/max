from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampMixin
from ..database import Base

# Final admin/inventory/vendor/dashboard polish pass — Part 11: admin-controlled
# theme. Exactly one row exists, always at this fixed id — see routers/theme.py,
# which always reads/writes db.get(ThemeConfig, DEFAULT_THEME_ID).
DEFAULT_THEME_ID = "default"


class ThemeConfig(TimestampMixin, Base):
    """Single-row, server-side theme configuration ("Store theme configuration
    centrally," per spec). Deliberately not a `BaseModel` (UUID PK + audit/version
    mixins) — there is exactly one row for the whole deployment, always at
    `DEFAULT_THEME_ID`, so a generated UUID primary key would add nothing; this is
    the minimal shape (plain String PK, the existing TimestampMixin for
    created_at/updated_at) rather than an invented parallel singleton pattern."""

    __tablename__ = "theme_config"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=DEFAULT_THEME_ID)
    primary_color: Mapped[str] = mapped_column(String)
    secondary_color: Mapped[str] = mapped_column(String)
    accent_color: Mapped[str] = mapped_column(String)
    success_color: Mapped[str] = mapped_column(String)
    warning_color: Mapped[str] = mapped_column(String)
    danger_color: Mapped[str] = mapped_column(String)
    surface_mode: Mapped[str] = mapped_column(String, default="light")
    preset_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # Step 10G — the expanded surface/text/border/interactive/sidebar/table/badge
    # token set. Stored as one JSON object (validated against a fixed-field Pydantic
    # model in schemas/theme.py, never arbitrary keys) rather than ~20 more String
    # columns, since every field is still strictly `#RRGGBB`-validated before it ever
    # reaches this column — this is a storage-shape choice, not a validation gap.
    advanced_tokens: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
