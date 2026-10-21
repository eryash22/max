import os

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


# document_type values (reference data, not enforced by a DB constraint): OEM_MANUAL,
# SERVICE_MANUAL, USER_MANUAL, SOP, PM_CHECKLIST, CALIBRATION_CERTIFICATE, AMC_CONTRACT,
# CMC_CONTRACT, WARRANTY_DOCUMENT, SERVICE_REPORT, PURCHASE_DOCUMENT, INVOICE,
# TRAINING_DOCUMENT, DEVICE_PHOTOGRAPH, INCIDENT_PHOTOGRAPH.
# access_level values: PUBLIC, INTERNAL, RESTRICTED.


class Document(BaseModel):
    __tablename__ = "documents"

    title: Mapped[str] = mapped_column(String)
    document_type: Mapped[str] = mapped_column(String)
    manufacturer: Mapped[str | None] = mapped_column(String, nullable=True)
    model_number: Mapped[str | None] = mapped_column(String, nullable=True)
    device_model_id: Mapped[str | None] = mapped_column(ForeignKey("device_models.id"), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    vendor_id: Mapped[str | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    active_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    effective_date: Mapped[str | None] = mapped_column(String, nullable=True)
    expiry_date: Mapped[str | None] = mapped_column(String, nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String, nullable=True)
    access_level: Mapped[str] = mapped_column(String, default="INTERNAL")
    approval_status: Mapped[str] = mapped_column(String, default="PENDING")
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentVersion(BaseModel):
    __tablename__ = "document_versions"

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    version_label: Mapped[str] = mapped_column(String)
    file_path: Mapped[str] = mapped_column(String)
    effective_date: Mapped[str | None] = mapped_column(String, nullable=True)
    processing_status: Mapped[str] = mapped_column(String, default="UPLOADED")

    document: Mapped["Document"] = relationship(back_populates="versions")

    @property
    def file_name(self) -> str:
        """Display-safe basename derived from the stored server path — never expose
        `file_path` (a server filesystem path) directly to API clients."""
        return os.path.basename(self.file_path)


class DocumentLink(BaseModel):
    """Attach a document to any entity type (incident, PM record, contract, ...) beyond
    the direct device_model/asset/vendor columns on Document, without adding a new
    nullable FK column to Document for every future attachable entity."""

    __tablename__ = "document_links"

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String)
    entity_id: Mapped[str] = mapped_column(String)
