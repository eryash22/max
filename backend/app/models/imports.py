from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class ImportJob(BaseModel):
    __tablename__ = "import_jobs"

    file_name: Mapped[str] = mapped_column(String)
    file_checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    data_type: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="UPLOADED")
    sheet_names: Mapped[list | None] = mapped_column(JSON, nullable=True)
    column_mapping: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String, nullable=True)

    rows: Mapped[list["ImportRow"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    errors: Mapped[list["ImportError"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    approvals: Mapped[list["ImportApproval"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class ImportRow(BaseModel):
    __tablename__ = "import_rows"

    job_id: Mapped[str] = mapped_column(ForeignKey("import_jobs.id"))
    sheet_name: Mapped[str] = mapped_column(String)
    row_number: Mapped[int] = mapped_column(Integer)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    mapped_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    business_key: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome: Mapped[str] = mapped_column(String, default="PENDING")
    entity_type: Mapped[str | None] = mapped_column(String, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String, nullable=True)
    is_duplicate_candidate: Mapped[bool] = mapped_column(Boolean, default=False)

    job: Mapped["ImportJob"] = relationship(back_populates="rows")


class ImportError(BaseModel):
    __tablename__ = "import_errors"

    job_id: Mapped[str] = mapped_column(ForeignKey("import_jobs.id"))
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(String, nullable=True)
    field: Mapped[str | None] = mapped_column(String, nullable=True)
    severity: Mapped[str] = mapped_column(String, default="ERROR")
    message: Mapped[str] = mapped_column(String)

    job: Mapped["ImportJob"] = relationship(back_populates="errors")


class ImportApproval(BaseModel):
    __tablename__ = "import_approvals"

    job_id: Mapped[str] = mapped_column(ForeignKey("import_jobs.id"))
    approved_by: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String, default="APPROVED")
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    job: Mapped["ImportJob"] = relationship(back_populates="approvals")
