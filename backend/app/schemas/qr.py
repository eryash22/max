from datetime import datetime

from pydantic import BaseModel, field_validator

from ..services.qr_service import QR_FIELD_BY_KEY


class QrFieldDefinitionOut(BaseModel):
    key: str
    label: str
    section: str
    default_visible: bool


class QrFieldConfigItem(BaseModel):
    key: str
    enabled: bool

    @field_validator("key")
    @classmethod
    def _must_be_allowlisted(cls, value: str) -> str:
        # The one place that stops a Super Admin (or a forged request) from ever
        # storing an arbitrary database column/expression as a public display
        # field — only keys already defined in qr_service.QR_FIELD_REGISTRY are
        # accepted, matching the spec's "only permit recognized, allowlisted
        # fields" requirement.
        if value not in QR_FIELD_BY_KEY:
            raise ValueError(f"Unknown QR display field: {value}")
        return value


class QrSettingsIn(BaseModel):
    enabled: bool
    fields: list[QrFieldConfigItem]

    @field_validator("fields")
    @classmethod
    def _no_duplicate_keys(cls, value: list[QrFieldConfigItem]) -> list[QrFieldConfigItem]:
        keys = [item.key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate field keys in QR display configuration")
        return value


class QrFieldConfigItemOut(QrFieldConfigItem):
    label: str
    section: str
    order: int


class QrSettingsOut(BaseModel):
    enabled: bool
    fields: list[QrFieldConfigItemOut]
    updated_by_user_id: str | None = None


class QrAssetStatusOut(BaseModel):
    asset_id: str
    asset_number: str | None = None
    serial_number: str | None = None
    device_name: str
    branch_name: str | None = None
    vendor_name: str | None = None
    status: str
    has_qr: bool
    qr_active: bool
    qr_generated_at: datetime | None = None


class QrBulkGenerateIn(BaseModel):
    asset_ids: list[str] | None = None


class QrBulkGenerateResult(BaseModel):
    generated: int
    already_existing: int
    failed: int
    failed_asset_ids: list[str] = []


class QrConfirmAction(BaseModel):
    confirm: bool = False


class QrPublicFieldOut(BaseModel):
    key: str
    label: str
    section: str
    value: str


class QrPublicPageOut(BaseModel):
    enabled: bool
    fields: list[QrPublicFieldOut] = []
