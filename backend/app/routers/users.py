from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.escalation import EscalationEvent
from ..models.fulfillment import FulfillmentRequest
from ..models.hospital import Branch, BranchTL, Department
from ..models.identity import Role, User, UserRole
from ..models.incident import Incident, ServiceVisit
from ..models.maintenance import PMRecord
from ..models.procurement import Indent
from ..models.requirement import Requirement
from ..models.theme import ThemeConfig
from ..schemas.auth import CurrentUser
from ..schemas.user import UserCreate, UserOut, UserPasswordUpdate, UserUpdate
from ..security.password import hash_password
from ..security.permissions import DEV_DATA_CLEANUP, SUPER_ADMIN, USERS_MANAGE
from ..services.force_delete import assert_dev_environment, force_delete_row

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _assert_can_assign_roles(current_user: CurrentUser, role_codes: list[str]) -> None:
    """USERS_MANAGE lets an admin create/edit users and pick their role_codes freely
    (e.g. a scoped "user admin" role granted USERS_MANAGE but not ROLES_MANAGE) — but
    granting the SUPER_ADMIN role code itself is a privilege escalation to the one
    role with every permission in the system (security/permissions.py ALL_PERMISSIONS),
    so it must never be assignable by someone who isn't already a Super Admin,
    including to themselves. Every other role_code stays USERS_MANAGE-only, matching
    the existing behavior."""
    if SUPER_ADMIN in role_codes and SUPER_ADMIN not in current_user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a Super Admin can assign the Super Admin role.",
        )


def _to_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        is_active=user.is_active,
        hospital_scope_id=user.hospital_scope_id,
        phone=user.phone,
        whatsapp_number=user.whatsapp_number,
        department_id=user.department_id,
        notification_preference=user.notification_preference,
        roles=[ur.role.code for ur in user.roles],
    )


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_permission(USERS_MANAGE)),
):
    return [_to_out(u) for u in db.query(User).all()]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(USERS_MANAGE)),
):
    email = payload.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    _assert_can_assign_roles(current_user, payload.role_codes)

    if payload.hospital_scope_id is not None and db.get(Branch, payload.hospital_scope_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown branch")

    user = User(
        email=email,
        name=payload.name,
        hashed_password=hash_password(payload.password),
        phone=payload.phone,
        hospital_scope_id=payload.hospital_scope_id,
    )
    db.add(user)
    db.flush()

    for code in payload.role_codes:
        role = db.query(Role).filter(Role.code == code).first()
        if role is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown role code: {code}")
        db.add(UserRole(user_id=user.id, role_id=role.id))

    audit_record(
        db, user_id=current_user.id, action="CREATE", entity="user", entity_id=user.id,
        new_value={"email": email, "role_codes": payload.role_codes},
    )
    db.commit()
    db.refresh(user)
    return _to_out(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(USERS_MANAGE)),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Self-protection: a Super Admin editing their own row through this admin endpoint
    # must not be able to lock themselves out — get_current_user 401s the very next
    # request for an inactive user (dependencies/auth.py), so this would otherwise be
    # an accidental, immediate self-lockout with no recovery path but another admin.
    if user_id == current_user.id and payload.is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )

    # hospital_scope_id and phone are included here (they previously weren't): branch is
    # a security-sensitive field — it is exactly what branch_scope_id() reads to decide
    # what a UNIT_HEAD/TL can see — so a change to it must be reconstructable from the
    # audit trail, not silently applied.
    before = {
        "name": user.name, "email": user.email, "is_active": user.is_active,
        "phone": user.phone, "whatsapp_number": user.whatsapp_number,
        "hospital_scope_id": user.hospital_scope_id,
        "department_id": user.department_id, "notification_preference": user.notification_preference,
        "role_codes": sorted(ur.role.code for ur in user.roles),
    }
    if payload.name is not None:
        user.name = payload.name
    if payload.email is not None:
        new_email = payload.email.lower()
        if new_email != user.email:
            if db.query(User).filter(User.email == new_email, User.id != user.id).first():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
            user.email = new_email
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.hospital_scope_id is not None:
        # Branch decides this user's entire data scope, so a forged/unknown id must be
        # rejected with a clear 400 here rather than reaching the database and surfacing
        # as a 500 from the foreign key.
        if db.get(Branch, payload.hospital_scope_id) is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown branch")
        user.hospital_scope_id = payload.hospital_scope_id
    if payload.phone is not None:
        user.phone = payload.phone
    if payload.whatsapp_number is not None:
        user.whatsapp_number = payload.whatsapp_number
    if payload.department_id is not None:
        if db.get(Department, payload.department_id) is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown department")
        user.department_id = payload.department_id
    if payload.notification_preference is not None:
        user.notification_preference = payload.notification_preference
    if payload.role_codes is not None:
        _assert_can_assign_roles(current_user, payload.role_codes)
        db.query(UserRole).filter(UserRole.user_id == user.id).delete()
        for code in payload.role_codes:
            role = db.query(Role).filter(Role.code == code).first()
            if role is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown role code: {code}")
            db.add(UserRole(user_id=user.id, role_id=role.id))

    audit_record(
        db, user_id=current_user.id, action="UPDATE", entity="user", entity_id=user.id,
        old_value=before,
        new_value={
            "name": user.name, "email": user.email, "is_active": user.is_active,
            "phone": user.phone, "whatsapp_number": user.whatsapp_number,
            "hospital_scope_id": user.hospital_scope_id,
            "department_id": user.department_id, "notification_preference": user.notification_preference,
            "role_codes": sorted(payload.role_codes) if payload.role_codes is not None else before["role_codes"],
        },
    )
    db.commit()
    db.refresh(user)
    return _to_out(user)


@router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_user_password(
    user_id: str,
    payload: UserPasswordUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(USERS_MANAGE)),
):
    """Super-Admin password reset. Never returns the password (204, no body) and never
    puts it in the audit payload — only the fact that a reset happened is recorded."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.hashed_password = hash_password(payload.new_password)
    # Invalidate every token issued before this reset (see dependencies/auth.py) —
    # otherwise a token an attacker already holds keeps working until it naturally
    # expires, defeating the point of the reset.
    user.token_version += 1
    audit_record(
        db, user_id=current_user.id, action="PASSWORD_RESET", entity="user", entity_id=user.id,
    )
    db.commit()


def _user_reference_count(db: Session, user_id: str) -> int:
    """How many rows across the schema currently reference this User, other than its
    own UserRole assignments (cascade-deleted with the user via User.roles'
    cascade="all, delete-orphan" — a role assignment isn't independent history, so it
    never blocks a delete). Zero means the user can be hard-deleted; any other count
    means it must be deactivated instead — same reference-check-then-hard-delete-or-
    reject pattern as routers/vendors.py:_vendor_reference_count and friends."""
    counts = [
        db.query(Branch).filter(Branch.unit_head_user_id == user_id).count(),
        db.query(BranchTL).filter(BranchTL.user_id == user_id).count(),
        db.query(EscalationEvent).filter(EscalationEvent.notified_user_id == user_id).count(),
        db.query(FulfillmentRequest).filter(FulfillmentRequest.inventory_manager_id == user_id).count(),
        db.query(FulfillmentRequest).filter(FulfillmentRequest.delivered_by_id == user_id).count(),
        db.query(Requirement).filter(Requirement.requester_id == user_id).count(),
        db.query(Requirement).filter(Requirement.manager_id == user_id).count(),
        db.query(PMRecord).filter(PMRecord.engineer_id == user_id).count(),
        db.query(ThemeConfig).filter(ThemeConfig.updated_by_user_id == user_id).count(),
        db.query(Incident).filter(Incident.reported_by == user_id).count(),
        db.query(Incident).filter(Incident.assigned_engineer_id == user_id).count(),
        db.query(ServiceVisit).filter(ServiceVisit.engineer_id == user_id).count(),
        db.query(Indent).filter(Indent.requester_id == user_id).count(),
    ]
    return sum(counts)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(USERS_MANAGE)),
):
    """Hard delete only when zero rows reference this user; otherwise 409, directing
    the caller to deactivate instead. A Super Admin can never delete their own
    currently authenticated account, force or not — that's a hard block, not a
    deactivate-instead redirect, since there's no "deactivate yourself" recovery
    either. ``force=true`` (also gated on DEV_DATA_CLEANUP, same convention as every
    other force-delete in this app) bypasses the reference guard via the generic
    FK-graph cascade for a clearly-disposable QA user that's still referenced."""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account.",
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if force:
        if DEV_DATA_CLEANUP not in current_user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Force-delete requires Super Admin (dev.data_cleanup permission)",
            )
        assert_dev_environment()
        before = {"email": user.email, "name": user.name}
        deleted_count = force_delete_row(db, "users", user.id)
        audit_record(
            db, user_id=current_user.id, action="FORCE_DELETE", entity="user", entity_id=user_id,
            old_value=before, new_value={"cascaded_row_count": deleted_count},
        )
        db.commit()
        return

    in_use = _user_reference_count(db, user_id)
    if in_use > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User is referenced by {in_use} record(s) and cannot be deleted — deactivate it instead",
        )
    before = {"email": user.email, "name": user.name}
    audit_record(db, user_id=current_user.id, action="DELETE", entity="user", entity_id=user.id, old_value=before)
    db.delete(user)
    db.commit()
