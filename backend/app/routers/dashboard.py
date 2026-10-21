"""Composed dashboard endpoint (F2).

One request replaces the ~37 + 8-per-branch count requests the Dashboard used to
issue. Deliberately a single endpoint rather than a family of per-domain summary
endpoints: the page needs all of it at once, and splitting it would recreate the
request fan-out this exists to remove.

Authorization is the same DB-driven RBAC as everywhere else — no permission is
required to *call* it, because it returns only the sections the caller's permissions
already entitle them to (see dashboard_service.compute_dashboard). A caller with no
relevant permission gets an empty `sections` list, not a 403, so the page can render
its own "nothing you can see here" state instead of an error.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies.auth import get_current_user
from ..schemas.auth import CurrentUser
from ..services import dashboard_service

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/summary")
def get_dashboard_summary(
    branch_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """`branch_id` is the Power BI-style branch cross-filter. It is honoured only for
    callers who legitimately have organisation-wide visibility — a UNIT_HEAD/TL is
    pinned to their assigned branch by dashboard_service.resolve_scope() and cannot
    widen scope by passing someone else's branch id."""
    return dashboard_service.compute_dashboard(db, current_user, branch_id=branch_id)
