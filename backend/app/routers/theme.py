from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import get_current_user, require_permission
from ..models.theme import DEFAULT_THEME_ID, ThemeConfig
from ..schemas.auth import CurrentUser
from ..schemas.theme import ThemeConfigIn, ThemeConfigOut
from ..security.permissions import SETTINGS_MANAGE
from ..theming import derive_default_advanced_tokens

router = APIRouter(prefix="/api/v1/theme", tags=["theme"])

# Clinical Blue — the built-in default, applied the first time GET /theme is called
# before any admin has ever saved a configuration. Not persisted until PUT is called;
# see get_theme() below.
_DEFAULT_BASE_COLORS = {
    "primary_color": "#2563EB",
    "secondary_color": "#0EA5E9",
    "accent_color": "#7C3AED",
    "success_color": "#16A34A",
    "warning_color": "#D97706",
    "danger_color": "#DC2626",
}
_DEFAULT_THEME = {
    **_DEFAULT_BASE_COLORS,
    "surface_mode": "light",
    "preset_name": "Clinical Blue",
    "advanced_tokens": derive_default_advanced_tokens(_DEFAULT_BASE_COLORS, "light"),
}


def _get_or_default(db: Session) -> ThemeConfig:
    config = db.get(ThemeConfig, DEFAULT_THEME_ID)
    if config is None:
        return ThemeConfig(id=DEFAULT_THEME_ID, **_DEFAULT_THEME)
    if config.advanced_tokens is None:
        # Step 10G shipped after this row was saved — fill in derived defaults
        # in-memory only (not persisted) so every GET always returns a complete,
        # always-defined token set rather than letting any field fall back to an
        # undefined CSS variable.
        config.advanced_tokens = derive_default_advanced_tokens(
            {c: getattr(config, c) for c in _DEFAULT_BASE_COLORS}, config.surface_mode
        )
    return config


@router.get("", response_model=ThemeConfigOut)
def get_theme(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)):
    """Open to any authenticated user — no special permission — per the spec's
    'non-admin users should only consume' requirement. Returns the built-in default
    (never persisted) until an admin has saved a configuration at least once."""
    return _get_or_default(db)


@router.put("", response_model=ThemeConfigOut)
def update_theme(payload: ThemeConfigIn, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_permission(SETTINGS_MANAGE))):
    """SETTINGS_MANAGE only (SUPER_ADMIN by default). Colors are already validated as
    strict `#RRGGBB` hex by ThemeConfigIn's field validators (422 otherwise, never
    stored) — see schemas/theme.py."""
    existing = db.get(ThemeConfig, DEFAULT_THEME_ID)
    before = (
        {c.name: getattr(existing, c.name) for c in existing.__table__.columns} if existing is not None else None
    )
    data = payload.model_dump()

    if existing is None:
        config = ThemeConfig(id=DEFAULT_THEME_ID, updated_by_user_id=current_user.id, **data)
        db.add(config)
    else:
        config = existing
        for field, value in data.items():
            setattr(config, field, value)
        config.updated_by_user_id = current_user.id

    audit_record(db, user_id=current_user.id, action="UPDATE", entity="theme_config", entity_id=DEFAULT_THEME_ID,
                 old_value=before, new_value={**data, "updated_by_user_id": current_user.id})
    db.commit()
    db.refresh(config)
    return config
