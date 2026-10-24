from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import get_current_user
from ..models.identity import User
from ..schemas.auth import CurrentUser, LoginRequest, TokenResponse
from ..schemas.user import UserContactUpdate, UserOut
from ..services.auth_service import authenticate, issue_token
from ..services.rate_limiter import RateLimitExceeded, check, record_failure

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    """Render terminates TLS and proxies every request through its own edge —
    the one and only hop in front of this app — so the leftmost address in
    X-Forwarded-For is the real client IP, not something an end user can forge
    past Render's edge. Falls back to the raw socket peer for local/dev runs
    where no proxy (and so no header) is present."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip_key = f"ip:{_client_ip(request)}"
    # Keyed on the raw submitted email (lowercased), not a resolved user id —
    # this limiter must not reveal whether an account exists, and has to work
    # the same way whether or not the email turns out to be real.
    identity_key = f"id:{payload.email.strip().lower()}"
    try:
        check([ip_key, identity_key])
    except RateLimitExceeded as exc:
        # HTTPException's own `headers=` is what FastAPI's exception handler
        # actually sends — mutating the injected `response` object has no effect
        # once a raised exception replaces the normal return path.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again shortly.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc

    user = authenticate(db, payload.email, payload.password)
    if user is None:
        # Only a failed attempt counts toward the limit — a successful login
        # (however many are made) is never throttled.
        record_failure([ip_key, identity_key])
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    audit_record(db, user_id=user.id, action="LOGIN", entity="user", entity_id=user.id)
    db.commit()
    return TokenResponse(access_token=issue_token(user))


@router.get("/me", response_model=CurrentUser)
def me(current_user: CurrentUser = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UserContactUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Module 19 — self-service contact-field update. Always operates on
    current_user.id (no user_id path param), so there is structurally no code path
    from here to another user's row — this is what makes "normal users must not
    alter another user's communication identity" true without an extra permission
    check. Admin-on-behalf-of-anyone editing stays on PATCH /api/v1/users/{id}
    (USERS_MANAGE-gated, routers/users.py)."""
    user = db.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    before = {
        "whatsapp_number": user.whatsapp_number, "department_id": user.department_id,
        "notification_preference": user.notification_preference,
    }
    if payload.whatsapp_number is not None:
        user.whatsapp_number = payload.whatsapp_number
    if payload.department_id is not None:
        user.department_id = payload.department_id
    if payload.notification_preference is not None:
        user.notification_preference = payload.notification_preference

    audit_record(
        db, user_id=user.id, action="UPDATE", entity="user", entity_id=user.id,
        old_value=before,
        new_value={
            "whatsapp_number": user.whatsapp_number, "department_id": user.department_id,
            "notification_preference": user.notification_preference,
        },
    )
    db.commit()
    db.refresh(user)
    return UserOut(
        id=user.id, email=user.email, name=user.name, is_active=user.is_active,
        hospital_scope_id=user.hospital_scope_id, whatsapp_number=user.whatsapp_number,
        department_id=user.department_id, notification_preference=user.notification_preference,
        roles=[ur.role.code for ur in user.roles],
    )
