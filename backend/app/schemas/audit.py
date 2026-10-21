from datetime import datetime

from pydantic import BaseModel


class AuditLogOut(BaseModel):
    id: str
    user_id: str | None = None
    action: str
    entity: str
    entity_id: str | None = None
    old_value: dict | None = None
    new_value: dict | None = None
    import_job_id: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
