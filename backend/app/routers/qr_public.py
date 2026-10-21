from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.device import Asset
from ..schemas.qr import QrPublicPageOut
from ..services import qr_service

# Device QR Code Management System — the public, read-only scan landing page's API.
# Deliberately has NO auth dependency anywhere in this router (mirrors
# routers/system.py's health endpoints, the one other unauthenticated-by-design
# surface in this codebase): a staff member scans a printed label with an ordinary
# phone camera and must see the device's allowlisted information without logging
# in. Never returns anything beyond services/qr_service.build_public_fields'
# allowlisted set — no internal id, no password/token fields, no vendor contact
# details, no purchase cost, no raw Asset/DeviceModel rows.
router = APIRouter(prefix="/api/v1/public/qr", tags=["qr-public"])


@router.get("/{token}", response_model=QrPublicPageOut)
def get_public_qr_page(token: str, db: Session = Depends(get_db)):
    # Deliberately does not log `token` anywhere here (no logging call at all on
    # this path) — avoids the full public token ever landing in ordinary
    # application logs, per the spec. Uvicorn's own access log line for this
    # request is outside this application's control, same as it is for every
    # other endpoint.
    asset = db.query(Asset).filter(Asset.qr_token == token).first()
    if asset is None or not asset.qr_active:
        # Same response whether the token is unknown or was revoked/disabled — an
        # attacker probing tokens must not be able to distinguish "never existed"
        # from "existed but was revoked".
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="QR code not found")

    settings_row = qr_service.get_or_default_settings(db)
    if not settings_row.enabled:
        return QrPublicPageOut(enabled=False, fields=[])

    fields = qr_service.build_public_fields(db, asset, settings_row)
    return QrPublicPageOut(enabled=True, fields=fields)
