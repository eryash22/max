from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models.audit import AuditLog


def _json_safe(value: Any) -> Any:
    """Recursively convert datetime/date values (e.g. from a raw column-snapshot dict
    such as `{c.name: getattr(obj, c.name) for c in obj.__table__.columns}`) into
    JSON-serializable strings so writes to the JSON old_value/new_value columns never fail."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def record(
    db: Session,
    *,
    user_id: str | None,
    action: str,
    entity: str,
    entity_id: str | None = None,
    old_value: dict | None = None,
    new_value: dict | None = None,
    import_job_id: str | None = None,
    request_ip: str | None = None,
) -> AuditLog:
    """Append-only audit trail write. Never update or delete existing rows."""
    log = AuditLog(
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        old_value=_json_safe(old_value),
        new_value=_json_safe(new_value),
        import_job_id=import_job_id,
        request_ip=request_ip,
    )
    db.add(log)
    db.flush()
    return log
