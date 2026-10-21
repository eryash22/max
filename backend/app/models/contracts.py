from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel

# Contract status values: ACTIVE, EXPIRED, RENEWAL_PENDING, CANCELLED, UNKNOWN.
# Expiry-window dashboards (7/30/60/90 days) are computed from end_date at query
# time rather than stored, so there is nothing to keep in sync.


class Warranty(BaseModel):
    """Per-asset warranty coverage — one warranty typically covers exactly one asset."""

    __tablename__ = "warranties"

    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    vendor_id: Mapped[str | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    warranty_number: Mapped[str | None] = mapped_column(String, nullable=True)
    start_date: Mapped[str | None] = mapped_column(String, nullable=True)
    end_date: Mapped[str | None] = mapped_column(String, nullable=True)
    coverage_notes: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="UNKNOWN")
    environment: Mapped[str] = mapped_column(String, default="DEVELOPMENT / DEMO DATA")


class AmcContract(BaseModel):
    """Annual Maintenance Contract — typically covers multiple assets, see `amc_contract_assets`."""

    __tablename__ = "amc_contracts"

    contract_number: Mapped[str | None] = mapped_column(String, nullable=True)
    vendor_id: Mapped[str | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    start_date: Mapped[str | None] = mapped_column(String, nullable=True)
    end_date: Mapped[str | None] = mapped_column(String, nullable=True)
    pm_frequency: Mapped[str | None] = mapped_column(String, nullable=True)
    calibration_frequency: Mapped[str | None] = mapped_column(String, nullable=True)
    response_time_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution_time_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contract_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    renewal_date: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="UNKNOWN")
    environment: Mapped[str] = mapped_column(String, default="DEVELOPMENT / DEMO DATA")


class AmcContractAsset(BaseModel):
    __tablename__ = "amc_contract_assets"

    amc_contract_id: Mapped[str] = mapped_column(ForeignKey("amc_contracts.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)


class CmcContract(BaseModel):
    """Comprehensive Maintenance Contract — same shape as AMC, kept as a separate table
    because AMC/CMC have different regulatory and coverage semantics in hospital procurement."""

    __tablename__ = "cmc_contracts"

    contract_number: Mapped[str | None] = mapped_column(String, nullable=True)
    vendor_id: Mapped[str | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    start_date: Mapped[str | None] = mapped_column(String, nullable=True)
    end_date: Mapped[str | None] = mapped_column(String, nullable=True)
    pm_frequency: Mapped[str | None] = mapped_column(String, nullable=True)
    calibration_frequency: Mapped[str | None] = mapped_column(String, nullable=True)
    response_time_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution_time_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contract_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    renewal_date: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="UNKNOWN")
    environment: Mapped[str] = mapped_column(String, default="DEVELOPMENT / DEMO DATA")


class CmcContractAsset(BaseModel):
    __tablename__ = "cmc_contract_assets"

    cmc_contract_id: Mapped[str] = mapped_column(ForeignKey("cmc_contracts.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
