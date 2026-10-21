import io

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..config import settings
from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.device import Asset
from ..models.hospital import Branch
from ..models.qr import DEFAULT_QR_SETTINGS_ID, QrDisplaySettings
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.qr import (
    QrAssetStatusOut, QrBulkGenerateIn, QrBulkGenerateResult, QrConfirmAction, QrFieldConfigItemOut,
    QrFieldDefinitionOut, QrSettingsIn, QrSettingsOut,
)
from ..security.permissions import QR_MANAGE
from ..services import qr_service

router = APIRouter(prefix="/api/v1/qr", tags=["qr"])


def _get_asset_or_404(db: Session, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return asset


def _to_asset_status_out(db: Session, asset: Asset) -> QrAssetStatusOut:
    branch = db.get(Branch, asset.branch_id) if asset.branch_id else None
    return QrAssetStatusOut(
        asset_id=asset.id,
        asset_number=asset.asset_number,
        serial_number=asset.serial_number,
        device_name=asset.device_model.name if asset.device_model else "",
        branch_name=branch.name if branch else None,
        status=asset.status,
        has_qr=bool(asset.qr_token),
        qr_active=asset.qr_active,
        qr_generated_at=asset.qr_generated_at,
    )


def _settings_to_out(settings_row: QrDisplaySettings) -> QrSettingsOut:
    """Appends any registry field missing from the stored config (e.g. added to
    QR_FIELD_REGISTRY after this settings row was last saved) at its registry
    default, so GET always reflects every available field even before an admin has
    explicitly saved it."""
    configured_keys = {entry["key"] for entry in (settings_row.field_config or [])}
    entries = list(settings_row.field_config or [])
    for field_def in qr_service.QR_FIELD_REGISTRY:
        if field_def.key not in configured_keys:
            entries.append({"key": field_def.key, "enabled": field_def.default_visible})

    fields = []
    for order, entry in enumerate(entries):
        field_def = qr_service.QR_FIELD_BY_KEY.get(entry["key"])
        if field_def is None:
            continue
        fields.append(QrFieldConfigItemOut(
            key=field_def.key, enabled=bool(entry.get("enabled")), label=field_def.label,
            section=field_def.section, order=order,
        ))
    return QrSettingsOut(enabled=settings_row.enabled, fields=fields, updated_by_user_id=settings_row.updated_by_user_id)


@router.get("/field-definitions", response_model=list[QrFieldDefinitionOut])
def list_field_definitions(_: CurrentUser = Depends(require_permission(QR_MANAGE))):
    return [
        QrFieldDefinitionOut(key=f.key, label=f.label, section=f.section, default_visible=f.default_visible)
        for f in qr_service.QR_FIELD_REGISTRY
    ]


@router.get("/settings", response_model=QrSettingsOut)
def get_settings(db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(QR_MANAGE))):
    return _settings_to_out(qr_service.get_or_default_settings(db))


@router.put("/settings", response_model=QrSettingsOut)
def update_settings(payload: QrSettingsIn, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(QR_MANAGE))):
    existing = db.get(QrDisplaySettings, DEFAULT_QR_SETTINGS_ID)
    before = {"enabled": existing.enabled, "field_config": existing.field_config} if existing else None
    field_config = [{"key": item.key, "enabled": item.enabled} for item in payload.fields]

    if existing is None:
        row = QrDisplaySettings(
            id=DEFAULT_QR_SETTINGS_ID, enabled=payload.enabled, field_config=field_config,
            updated_by_user_id=current_user.id,
        )
        db.add(row)
    else:
        row = existing
        row.enabled = payload.enabled
        row.field_config = field_config
        row.updated_by_user_id = current_user.id

    # Security-sensitive change (controls what the unauthenticated public page can
    # show) — audited per the spec, same as every other settings write in this
    # codebase (see routers/theme.py's update_theme).
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="qr_display_settings",
                 entity_id=DEFAULT_QR_SETTINGS_ID, old_value=before,
                 new_value={"enabled": payload.enabled, "field_config": field_config})
    db.commit()
    db.refresh(row)
    return _settings_to_out(row)


@router.get("/assets", response_model=Page[QrAssetStatusOut])
def list_qr_assets(
    page: int = 1, page_size: int = 25, search: str | None = None,
    branch_id: str | None = None, status_filter: str | None = None, missing_only: bool = False,
    asset_ids: str | None = None,
    db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(QR_MANAGE)),
):
    """Deliberately includes RETIRED/CONDEMNED assets (no status exclusion anywhere
    in this query) so historical physical devices can still be found and labeled,
    per the spec. `asset_ids` is a comma-separated id list — used by the bulk print
    view to fetch exactly the selected rows regardless of the current page/filters."""
    query = db.query(Asset)
    if asset_ids:
        query = query.filter(Asset.id.in_([a for a in asset_ids.split(",") if a]))
    if branch_id:
        query = query.filter(Asset.branch_id == branch_id)
    if status_filter:
        query = query.filter(Asset.status == status_filter)
    if missing_only:
        query = query.filter(Asset.qr_token.is_(None))
    if search:
        like = f"%{search}%"
        query = query.filter(Asset.asset_number.ilike(like) | Asset.serial_number.ilike(like))

    total = query.count()
    page_query = query.order_by(Asset.created_at.desc())
    if not asset_ids:
        page_query = page_query.offset((page - 1) * page_size).limit(page_size)
    items = page_query.all()
    return Page(items=[_to_asset_status_out(db, a) for a in items], total=total, page=page, page_size=page_size)


@router.post("/assets/{asset_id}/generate", response_model=QrAssetStatusOut)
def generate_qr(asset_id: str, db: Session = Depends(get_db),
                 current_user: CurrentUser = Depends(require_permission(QR_MANAGE))):
    asset = _get_asset_or_404(db, asset_id)
    created = qr_service.ensure_qr_token(db, asset)
    if created:
        audit_record(db, user_id=current_user.id, action="CREATE", entity="asset_qr", entity_id=asset.id,
                     new_value={"qr_token_prefix": asset.qr_token[:8] + "..."})
        db.commit()
        db.refresh(asset)
    return _to_asset_status_out(db, asset)


@router.post("/bulk-generate", response_model=QrBulkGenerateResult)
def bulk_generate_qr(payload: QrBulkGenerateIn, db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(require_permission(QR_MANAGE))):
    query = db.query(Asset)
    if payload.asset_ids:
        query = query.filter(Asset.id.in_(payload.asset_ids))
    assets = query.all()

    generated, already_existing, failed, failed_ids = 0, 0, 0, []
    for asset in assets:
        try:
            if qr_service.ensure_qr_token(db, asset):
                generated += 1
            else:
                already_existing += 1
        except Exception:
            failed += 1
            failed_ids.append(asset.id)

    if generated:
        audit_record(db, user_id=current_user.id, action="BULK_CREATE", entity="asset_qr",
                     new_value={"generated": generated, "already_existing": already_existing, "failed": failed})
        db.commit()
    return QrBulkGenerateResult(generated=generated, already_existing=already_existing, failed=failed, failed_asset_ids=failed_ids)


@router.post("/assets/{asset_id}/regenerate", response_model=QrAssetStatusOut)
def regenerate_qr(asset_id: str, payload: QrConfirmAction, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(QR_MANAGE))):
    if not payload.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Regenerating a QR code invalidates any previously printed label for this asset — confirm to proceed.",
        )
    asset = _get_asset_or_404(db, asset_id)
    old_prefix = (asset.qr_token[:8] + "...") if asset.qr_token else None
    new_token = qr_service.regenerate_token(db, asset)
    audit_record(db, user_id=current_user.id, action="REGENERATE", entity="asset_qr", entity_id=asset.id,
                 old_value={"qr_token_prefix": old_prefix}, new_value={"qr_token_prefix": new_token[:8] + "..."})
    db.commit()
    db.refresh(asset)
    return _to_asset_status_out(db, asset)


@router.post("/assets/{asset_id}/revoke", response_model=QrAssetStatusOut)
def revoke_qr(asset_id: str, payload: QrConfirmAction, db: Session = Depends(get_db),
              current_user: CurrentUser = Depends(require_permission(QR_MANAGE))):
    if not payload.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Revoking this QR code immediately stops any previously printed label from working — confirm to proceed.",
        )
    asset = _get_asset_or_404(db, asset_id)
    qr_service.revoke_token(db, asset)
    audit_record(db, user_id=current_user.id, action="REVOKE", entity="asset_qr", entity_id=asset.id)
    db.commit()
    db.refresh(asset)
    return _to_asset_status_out(db, asset)


@router.post("/assets/{asset_id}/reactivate", response_model=QrAssetStatusOut)
def reactivate_qr(asset_id: str, db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(require_permission(QR_MANAGE))):
    asset = _get_asset_or_404(db, asset_id)
    if not asset.qr_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset has no QR identity to reactivate")
    qr_service.reactivate_token(db, asset)
    audit_record(db, user_id=current_user.id, action="REACTIVATE", entity="asset_qr", entity_id=asset.id)
    db.commit()
    db.refresh(asset)
    return _to_asset_status_out(db, asset)


@router.get("/assets/{asset_id}/image")
def get_qr_image(asset_id: str, db: Session = Depends(get_db),
                  _: CurrentUser = Depends(require_permission(QR_MANAGE))):
    asset = _get_asset_or_404(db, asset_id)
    if not asset.qr_token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset has no QR identity yet")
    scan_url = f"{settings.frontend_base_url.rstrip('/')}/scan/device/{asset.qr_token}"
    img = qrcode.make(scan_url)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")
