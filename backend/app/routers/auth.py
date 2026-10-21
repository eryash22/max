from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import get_current_user
from ..models.identity import User
from ..schemas.auth import CurrentUser, LoginRequest, TokenResponse
from ..schemas.user import UserContactUpdate, UserOut
from ..services.auth_service import authenticate, issue_token

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate(db, payload.email, payload.password)
    if user is None:
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
