"""Device QR Code Management System — token provisioning + the allowlisted public
display-field registry.

Every physical Asset gets at most one stable QR identity: a cryptographically
random, unguessable `qr_token` (never a sequential id, never device information
encoded into it — see models/device.py's comment on Asset.qr_token). The public
scan page resolves a token to a fixed set of allowlisted display fields computed
here; a QR display setting can only toggle/reorder entries from this registry, it
can never name an arbitrary database column (see schemas/qr.py's validation).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

from sqlalchemy.orm import Session

from ..models.device import Asset
from ..models.hospital import Branch, Department, Location
from ..models.qr import DEFAULT_QR_SETTINGS_ID, QrDisplaySettings
from ..services import asset_lifecycle_service

# 128 bits of entropy, URL-safe — not guessable, not sequential, carries no device
# information. ~2 characters of base64 per 1.5 bytes -> token_urlsafe(24) is 32 chars.
_TOKEN_BYTES = 24
_MAX_COLLISION_RETRIES = 5


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _unique_token(db: Session) -> str:
    for _ in range(_MAX_COLLISION_RETRIES):
        token = generate_token()
        if db.query(Asset).filter(Asset.qr_token == token).first() is None:
            return token
    # Astronomically unlikely with 128 bits of entropy; fail loudly rather than risk
    # a silent duplicate if it ever happens.
    raise RuntimeError("Could not allocate a unique QR token after several attempts")


def ensure_qr_token(db: Session, asset: Asset) -> bool:
    """Idempotent provisioning: generates a token only if the asset doesn't already
    have one. Returns True when a new token was generated, False when the asset
    already had one (and nothing changed) — used by both the automatic
    create_asset/create_device hooks and the bulk "generate missing" admin action,
    so rerunning bulk generation is always a no-op for assets that already have a
    token (idempotent per the spec)."""
    if asset.qr_token:
        return False
    asset.qr_token = _unique_token(db)
    asset.qr_active = True
    asset.qr_generated_at = datetime.utcnow()
    return True


def regenerate_token(db: Session, asset: Asset) -> str:
    """Explicit, authorized action only (never automatic) — replaces the token
    unconditionally, so any previously printed label stops resolving immediately
    (the old token is no longer stored anywhere once this commits)."""
    asset.qr_token = _unique_token(db)
    asset.qr_active = True
    asset.qr_generated_at = datetime.utcnow()
    return asset.qr_token


def revoke_token(db: Session, asset: Asset) -> None:
    """Explicit, authorized action only. Keeps the token value in place (for audit
    history) but flips qr_active off — the public endpoint must treat a revoked
    token exactly like an unknown one (safe not-found, never a different error that
    would confirm the token once existed)."""
    asset.qr_active = False


def reactivate_token(db: Session, asset: Asset) -> None:
    """Re-enable a previously revoked token without changing its value (distinct
    from regenerate, which always mints a new value)."""
    asset.qr_active = True


# ---- Allowlisted public field registry -----------------------------------------
# Every key here is the only vocabulary a QrDisplaySettings.field_config entry may
# reference (schemas/qr.py rejects anything else) and the only vocabulary the
# public scan endpoint will ever emit. `resolver` receives the _QrContext built for
# one asset and returns a display string, or None when the information is absent/
# unverifiable — callers must show "Not recorded" (or omit the field), never invent
# a value. This indirection is what keeps the public endpoint from ever being able
# to leak an arbitrary internal field: there is no code path that returns anything
# outside this list.


@dataclass
class QrFieldDef:
    key: str
    label: str
    section: str
    default_visible: bool
    resolver: Callable[["_QrContext"], str | None]


@dataclass
class _QrContext:
    asset: Asset
    branch: Branch | None
    department: Department | None
    location: Location | None
    lifecycle: object  # AssetLifecycleSummaryOut


def _device_name(ctx: _QrContext) -> str | None:
    return ctx.asset.device_model.name if ctx.asset.device_model else None


def _manufacturer(ctx: _QrContext) -> str | None:
    return ctx.asset.device_model.manufacturer if ctx.asset.device_model else None


def _asset_number(ctx: _QrContext) -> str | None:
    return ctx.asset.asset_number or "Asset number not assigned"


def _serial_number(ctx: _QrContext) -> str | None:
    return ctx.asset.serial_number


def _branch_name(ctx: _QrContext) -> str | None:
    return ctx.branch.name if ctx.branch else None


def _department_name(ctx: _QrContext) -> str | None:
    return ctx.department.name if ctx.department else None


def _location_name(ctx: _QrContext) -> str | None:
    if not ctx.location:
        return None
    parts = [p for p in (ctx.location.name, ctx.location.room, ctx.location.bed) if p]
    return " / ".join(parts) if parts else None


def _status(ctx: _QrContext) -> str | None:
    return ctx.asset.status


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:10]).date()
    except ValueError:
        return None


def _in_service_since(ctx: _QrContext) -> str | None:
    return ctx.asset.operational_since or None


def _time_in_operation(ctx: _QrContext) -> str | None:
    """Never falls back to the record's created_at — per spec, the creation date is
    never assumed to be the actual in-service date. Only computed when
    operational_since is a real, parseable date."""
    start = _parse_date(ctx.asset.operational_since)
    if start is None:
        return None
    today = date.today()
    if start > today:
        return None
    months = (today.year - start.year) * 12 + (today.month - start.month)
    if today.day < start.day:
        months -= 1
    months = max(months, 0)
    years, rem_months = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} yr{'s' if years != 1 else ''}")
    if rem_months or not years:
        parts.append(f"{rem_months} mo{'s' if rem_months != 1 else ''}")
    return " ".join(parts)


def _last_updated(ctx: _QrContext) -> str | None:
    return ctx.asset.updated_at.date().isoformat() if ctx.asset.updated_at else None


def _last_pm(ctx: _QrContext) -> str | None:
    return ctx.lifecycle.last_pm_date


def _next_pm(ctx: _QrContext) -> str | None:
    return ctx.lifecycle.next_pm_due_date


def _last_calibration(ctx: _QrContext) -> str | None:
    return ctx.lifecycle.last_calibration_date


def _next_calibration(ctx: _QrContext) -> str | None:
    return ctx.lifecycle.next_calibration_due_date


def _coverage_line(coverage, label: str) -> str | None:
    if coverage is None:
        return None
    expiry = f" (expires {coverage.end_date})" if coverage.end_date else ""
    return f"{label}: {coverage.expiry_status}{expiry}"


def _warranty_status(ctx: _QrContext) -> str | None:
    return _coverage_line(ctx.lifecycle.warranty, "Warranty")


def _amc_status(ctx: _QrContext) -> str | None:
    return _coverage_line(ctx.lifecycle.amc, "AMC")


def _cmc_status(ctx: _QrContext) -> str | None:
    return _coverage_line(ctx.lifecycle.cmc, "CMC")


QR_FIELD_REGISTRY: list[QrFieldDef] = [
    QrFieldDef("device_name", "Device / Model Name", "identity", True, _device_name),
    QrFieldDef("asset_number", "Asset Number", "identity", True, _asset_number),
    QrFieldDef("manufacturer", "Manufacturer", "identity", False, _manufacturer),
    QrFieldDef("serial_number", "Serial Number", "identity", False, _serial_number),
    QrFieldDef("branch_name", "Branch / Hospital", "location", True, _branch_name),
    QrFieldDef("department_name", "Department", "location", True, _department_name),
    QrFieldDef("location_name", "Building / Floor / Location", "location", False, _location_name),
    QrFieldDef("status", "Current Status", "lifecycle", True, _status),
    QrFieldDef("in_service_since", "In Service Since", "lifecycle", True, _in_service_since),
    QrFieldDef("time_in_operation", "Time In Operation", "lifecycle", True, _time_in_operation),
    QrFieldDef("last_updated", "Last Updated", "lifecycle", False, _last_updated),
    QrFieldDef("last_pm_date", "Last Service / PM", "maintenance", False, _last_pm),
    QrFieldDef("next_pm_due_date", "Next PM Due", "maintenance", True, _next_pm),
    QrFieldDef("last_calibration_date", "Last Calibration", "maintenance", False, _last_calibration),
    QrFieldDef("next_calibration_due_date", "Next Calibration Due", "maintenance", True, _next_calibration),
    QrFieldDef("warranty_status", "Warranty", "coverage", True, _warranty_status),
    QrFieldDef("amc_status", "AMC", "coverage", False, _amc_status),
    QrFieldDef("cmc_status", "CMC", "coverage", False, _cmc_status),
]

QR_FIELD_BY_KEY: dict[str, QrFieldDef] = {f.key: f for f in QR_FIELD_REGISTRY}


def default_field_config() -> list[dict]:
    return [{"key": f.key, "enabled": f.default_visible} for f in QR_FIELD_REGISTRY]


def get_or_default_settings(db: Session) -> QrDisplaySettings:
    settings = db.get(QrDisplaySettings, DEFAULT_QR_SETTINGS_ID)
    if settings is None:
        return QrDisplaySettings(id=DEFAULT_QR_SETTINGS_ID, enabled=True, field_config=default_field_config())
    if not settings.field_config:
        settings.field_config = default_field_config()
    return settings


def _build_context(db: Session, asset: Asset) -> _QrContext:
    branch = db.get(Branch, asset.branch_id) if asset.branch_id else None
    department = db.get(Department, asset.department_id) if asset.department_id else None
    location = db.get(Location, asset.location_id) if asset.location_id else None
    lifecycle = asset_lifecycle_service.get_asset_lifecycle_summary(db, asset)
    return _QrContext(asset=asset, branch=branch, department=department, location=location, lifecycle=lifecycle)


def build_public_fields(db: Session, asset: Asset, settings: QrDisplaySettings) -> list[dict]:
    """Returns the ordered, filtered list of {key, label, section, value} the public
    page may render — strictly limited to entries in field_config that are both
    allowlisted (QR_FIELD_BY_KEY) and enabled. Never returns anything else from the
    Asset row."""
    ctx = _build_context(db, asset)
    fields: list[dict] = []
    for entry in settings.field_config or []:
        key = entry.get("key")
        field_def = QR_FIELD_BY_KEY.get(key)
        if field_def is None or not entry.get("enabled"):
            continue
        value = field_def.resolver(ctx)
        fields.append({
            "key": field_def.key,
            "label": field_def.label,
            "section": field_def.section,
            "value": value if value is not None else "Not recorded",
        })
    return fields
