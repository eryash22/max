import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.identity import User
from ..security.tokens import decode_access_token
from ..schemas.auth import CurrentUser

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if credentials is None:
        raise _unauthorized()
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise _unauthorized("Invalid or expired token")

    user_id = payload.get("sub")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("User not found or inactive")
    if payload.get("tv", 0) != user.token_version:
        # Password was reset (or the account was otherwise force-logged-out) after this
        # token was issued — a stolen/leftover token must stop working immediately
        # rather than staying valid for up to ACCESS_TOKEN_EXPIRE_MINUTES more.
        raise _unauthorized("Invalid or expired token")

    role_codes = [ur.role.code for ur in user.roles]
    permission_codes = sorted({
        rp.permission.code
        for ur in user.roles
        for rp in ur.role.permissions
    })

    return CurrentUser(
        id=user.id,
        email=user.email,
        name=user.name,
        is_active=user.is_active,
        roles=role_codes,
        permissions=permission_codes,
        hospital_scope_id=user.hospital_scope_id,
        whatsapp_number=user.whatsapp_number,
        department_id=user.department_id,
        notification_preference=user.notification_preference,
    )


def require_permission(*required: str):
    def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        missing = [p for p in required if p not in current_user.permissions]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission(s): {', '.join(missing)}",
            )
        return current_user

    return dependency


def require_any_permission(*allowed: str):
    """Like require_permission, but grants access if the caller holds ANY one of the
    listed permission codes rather than requiring all of them. Used where several
    distinct roles (e.g. requirement creator vs. read-only vs. approver) all need
    access to the same read endpoint but hold different permission codes."""

    def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not any(p in current_user.permissions for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission(s): one of {', '.join(allowed)}",
            )
        return current_user

    return dependency
