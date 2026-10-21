from fastapi import APIRouter, Depends
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ..config import settings
from ..database import Base, engine, get_db
from ..models import identity  # noqa: F401 ensures metadata is populated

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok", "environment": settings.environment, "ai_provider": settings.ai_provider}


@router.get("/system/health")
def system_health(db: Session = Depends(get_db)):
    database_status = "unreachable"
    database_detail = None
    try:
        db.execute(text("SELECT 1"))
        database_status = "connected"
    except Exception as exc:  # pragma: no cover - depends on live DB state
        database_status = "unreachable"
        # This endpoint has no auth dependency (infra/load-balancer health checks
        # can't carry a bearer token) — str(exc) on a DB connectivity failure can
        # include the connection string (host/port/credentials fragments), so only
        # the exception class name is exposed, never the raw message.
        database_detail = type(exc).__name__

    migration_status = "unknown"
    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        expected_tables = set(Base.metadata.tables.keys())
        missing = expected_tables - existing_tables
        migration_status = "up_to_date" if not missing else "pending"
    except Exception:
        migration_status = "unknown"
        missing = None

    return {
        "api": "ok",
        "database": database_status,
        "database_detail": database_detail,
        "migration_status": migration_status,
        "missing_tables": sorted(missing) if migration_status == "pending" and missing else [],
        "ai": "disabled",
        "docker_required": False,
    }
