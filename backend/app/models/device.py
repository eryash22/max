from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class DeviceCategory(BaseModel):
    __tablename__ = "device_categories"

    code: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String, unique=True)

    device_models: Mapped[list["DeviceModel"]] = relationship(back_populates="category_rel")


class DeviceModel(BaseModel):
    __tablename__ = "device_models"

    category_id: Mapped[str | None] = mapped_column(ForeignKey("device_categories.id"), nullable=True)
    name: Mapped[str] = mapped_column(String)
    manufacturer: Mapped[str | None] = mapped_column(String, nullable=True)
    model_number: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String)
    record_type: Mapped[str] = mapped_column(String, default="DEVICE")
    model_status: Mapped[str | None] = mapped_column(String, nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String, nullable=True)
    criticality_default: Mapped[str | None] = mapped_column(String, nullable=True)
    known_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    data_source: Mapped[str | None] = mapped_column(String, nullable=True)
    model_information_status: Mapped[str] = mapped_column(String, default="MODEL_NOT_PROVIDED")
    catalog_match_status: Mapped[str] = mapped_column(String, default="UNMATCHED")
    environment: Mapped[str] = mapped_column(String, default="DEVELOPMENT / DEMO DATA")
    # Final admin/inventory/vendor/dashboard polish pass — Part 2: device catalog
    # archive/delete. A model with zero referencing Assets can be hard-deleted;
    # otherwise it is soft-archived (is_active=False) instead — see
    # routers/devices.py's _device_model_in_use()/archive/activate endpoints. Every
    # existing row defaults to True (unchanged visibility).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    category_rel: Mapped["DeviceCategory | None"] = relationship(back_populates="device_models")
    assets: Mapped[list["Asset"]] = relationship(back_populates="device_model")


class Asset(BaseModel):
    __tablename__ = "assets"

    # Module 18A — `status` has never had an enforced vocabulary (verified: existing
    # tests/imports/seed data already use free-text values like "Pending", "In Service",
    # "Decommissioned", "OPERATIONAL" that don't share one casing convention), so this
    # module does NOT add a validator that would reject any of them — see
    # schemas/asset_lifecycle.py's LIFECYCLE_STATUSES for the new recommended (not
    # DB-enforced) vocabulary: PROCUREMENT/RECEIVED/INSTALLED/OPERATIONAL/
    # UNDER_MAINTENANCE/UNDER_REPAIR/AMC/CMC/WARRANTY/CONDEMNED/RETIRED. AMC/CMC/warranty
    # coverage is never stored here — it's always derived live from the existing
    # Warranty/AmcContract/CmcContract tables (see services/asset_lifecycle_service.py)
    # to avoid a second, driftable source of truth. Current vendor is derived the same
    # way (from whichever contract is currently active) rather than duplicated as a
    # column here, for the same reason.
    # Final Operational Gap Pass, Part 1 — asset_number is optional: a device can be
    # added before a physical asset tag/number exists and one attached later via
    # edit. `unique=True` still enforces uniqueness for every non-NULL value;
    # Postgres's standard UNIQUE constraint treats multiple NULLs as distinct from
    # each other, so this needs no partial/conditional index.
    asset_number: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String, nullable=True)
    device_model_id: Mapped[str] = mapped_column(ForeignKey("device_models.id"))
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="Pending")
    criticality: Mapped[str | None] = mapped_column(String, nullable=True)
    purchase_date: Mapped[str | None] = mapped_column(String, nullable=True)
    installation_date: Mapped[str | None] = mapped_column(String, nullable=True)
    operational_since: Mapped[str | None] = mapped_column(String, nullable=True)
    environment: Mapped[str] = mapped_column(String, default="DEVELOPMENT / DEMO DATA")

    # Device QR Code Management System — one stable, cryptographically random,
    # unguessable public token per physical Asset (never a sequential id, never
    # encoded device information). Provisioned automatically on create (see
    # services/qr_service.ensure_qr_token, called from create_asset/create_device/
    # the import path) and stays unchanged across normal updates, status changes,
    # branch transfers and contract renewals — only an explicit regenerate action
    # replaces it. qr_active=False means the token is revoked: the public scan
    # endpoint must treat it as not-found even though the row (and token) still
    # exists, so a revoked label can never start working again by accident.
    qr_token: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    qr_active: Mapped[bool] = mapped_column(Boolean, default=True)
    qr_generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    device_model: Mapped["DeviceModel"] = relationship(back_populates="assets")


class BranchDeviceAssignment(BaseModel):
    __tablename__ = "branch_device_assignments"

    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    device_model_id: Mapped[str | None] = mapped_column(ForeignKey("device_models.id"), nullable=True)
    source_name: Mapped[str] = mapped_column(String)
    known_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignment_status: Mapped[str] = mapped_column(String, default="PENDING_ASSET_IDENTIFICATION")
    inventory_mapping_status: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_identification_status: Mapped[str | None] = mapped_column(String, nullable=True)
    data_quality_flag: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    environment: Mapped[str] = mapped_column(String, default="DEVELOPMENT / DEMO DATA")
