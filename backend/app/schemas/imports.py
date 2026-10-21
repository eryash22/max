from datetime import datetime

from pydantic import BaseModel


class ImportJobOut(BaseModel):
    id: str
    file_name: str
    data_type: str | None = None
    status: str
    sheet_names: list | None = None
    column_mapping: dict | None = None
    summary: dict | None = None
    uploaded_by: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ImportRowOut(BaseModel):
    id: str
    sheet_name: str
    row_number: int
    mapped_data: dict | None = None
    business_key: str | None = None
    outcome: str
    entity_type: str | None = None
    is_duplicate_candidate: bool

    model_config = {"from_attributes": True}


class ImportErrorOut(BaseModel):
    id: str
    row_number: int | None = None
    sheet_name: str | None = None
    field: str | None = None
    severity: str
    message: str

    model_config = {"from_attributes": True}


class ImportPreview(BaseModel):
    job: ImportJobOut
    rows: list[ImportRowOut]
    errors: list[ImportErrorOut]


class ImportApprovalIn(BaseModel):
    decision: str = "APPROVED"
    notes: str | None = None
