from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.identity import Permission, Role, RolePermission
from ..schemas.auth import CurrentUser
from ..schemas.user import PermissionOut, RoleCreate, RoleOut, RoleUpdate
from ..security.permissions import ROLES_MANAGE

router = APIRouter(prefix="/api/v1", tags=["roles"])


def _to_out(role: Role) -> RoleOut:
    return RoleOut(
        id=role.id, code=role.code, name=role.name, description=role.description,
        permissions=[rp.permission.code for rp in role.permissions],
    )


@router.get("/roles", response_model=list[RoleOut])
def list_roles(db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(ROLES_MANAGE))):
    return [_to_out(r) for r in db.query(Role).all()]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(ROLES_MANAGE)),
):
    if db.query(Role).filter(Role.code == payload.code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Role code already exists")

    role = Role(code=payload.code, name=payload.name, description=payload.description)
    db.add(role)
    db.flush()

    for code in payload.permission_codes:
        permission = db.query(Permission).filter(Permission.code == code).first()
        if permission is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown permission code: {code}")
        db.add(RolePermission(role_id=role.id, permission_id=permission.id))

    audit_record(db, user_id=current_user.id, action="CREATE", entity="role", entity_id=role.id, new_value={"code": role.code})
    db.commit()
    db.refresh(role)
    return _to_out(role)


@router.patch("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: str,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(ROLES_MANAGE)),
):
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    before = {"name": role.name, "permissions": [rp.permission.code for rp in role.permissions]}
    if payload.name is not None:
        role.name = payload.name
    if payload.description is not None:
        role.description = payload.description
    if payload.permission_codes is not None:
        db.query(RolePermission).filter(RolePermission.role_id == role.id).delete()
        for code in payload.permission_codes:
            permission = db.query(Permission).filter(Permission.code == code).first()
            if permission is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown permission code: {code}")
            db.add(RolePermission(role_id=role.id, permission_id=permission.id))

    audit_record(
        db, user_id=current_user.id, action="UPDATE", entity="role", entity_id=role.id,
        old_value=before, new_value={"name": role.name},
    )
    db.commit()
    db.refresh(role)
    return _to_out(role)


@router.get("/permissions", response_model=list[PermissionOut])
def list_permissions(db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(ROLES_MANAGE))):
    return db.query(Permission).all()
