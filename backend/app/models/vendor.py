from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class Vendor(BaseModel):
    __tablename__ = "vendors"

    name: Mapped[str] = mapped_column(String, unique=True)
    vendor_type: Mapped[str | None] = mapped_column(String, nullable=True)  # OEM, DISTRIBUTOR, SERVICE, OTHER
    is_oem: Mapped[bool] = mapped_column(Boolean, default=False)
    is_authorized_distributor: Mapped[bool] = mapped_column(Boolean, default=False)
    is_service_vendor: Mapped[bool] = mapped_column(Boolean, default=False)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    website: Mapped[str | None] = mapped_column(String, nullable=True)
    support_coverage: Mapped[str | None] = mapped_column(String, nullable=True)
    response_sla_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    environment: Mapped[str] = mapped_column(String, default="DEVELOPMENT / DEMO DATA")


class VendorContact(BaseModel):
    __tablename__ = "vendor_contacts"

    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class DeviceModelVendor(BaseModel):
    """Primary/alternative vendor mapping for a device model. `rank` 0 = primary, 1-3 = alternatives."""

    __tablename__ = "device_model_vendors"

    device_model_id: Mapped[str] = mapped_column(ForeignKey("device_models.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
