from sqlalchemy.orm import Session

from ..audit.service import record as audit_record

# Sane cap for this project's demo-scale data (same discipline as every other module's
# documented "fine at current demo data volume, would need real pagination/streaming to
# scale further" caveat — e.g. Module 05A's contracts list, Module 11's failure-summary
# list). A capped export is truncated, never silently wrong: `record_export_audit` and
# the router both surface `truncated`/`record_count` so the caller can tell.
MAX_EXPORT_ROWS = 5000


def record_export_audit(
    db: Session, *, user_id: str | None, module: str, filters: dict, format: str, record_count: int, truncated: bool,
) -> None:
    """Every export is audited the same way a write would be — user, module, filters,
    format, timestamp (via AuditLog.created_at), and record count — so "who exported
    what, filtered how" is always reconstructable, matching this project's append-only
    audit-log discipline (see AGENTS.md) applied to reads that leave the system, not
    just writes."""
    audit_record(
        db, user_id=user_id, action="EXPORT", entity=f"{module}_export",
        new_value={
            "module": module,
            "format": format,
            "filters": {k: v for k, v in filters.items() if v is not None},
            "record_count": record_count,
            "truncated": truncated,
        },
    )
    db.commit()
