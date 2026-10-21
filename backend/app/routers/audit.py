from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.audit import AuditLog
from ..repositories.crud import paginate
from ..schemas.audit import AuditLogOut
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..security.permissions import AUDIT_READ

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut])
def list_audit_logs(page: int = 1, page_size: int = 25, entity: str | None = None, entity_id: str | None = None,
                     db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(AUDIT_READ))):
    items, total = paginate(db, AuditLog, page=page, page_size=page_size, entity=entity, entity_id=entity_id)
    return Page(items=items, total=total, page=page, page_size=page_size)
