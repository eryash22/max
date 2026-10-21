from pydantic import BaseModel


class ReviewItemOut(BaseModel):
    id: str
    source_type: str
    source_id: str
    reason_code: str
    detail: str | None = None
    status: str
    resolved_by: str | None = None
    resolution_notes: str | None = None
    import_job_id: str | None = None

    model_config = {"from_attributes": True}


class ReviewActionIn(BaseModel):
    action: str  # accept | reject | resolve | defer
    notes: str | None = None
