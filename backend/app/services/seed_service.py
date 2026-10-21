from sqlalchemy.orm import Session

from ..models.identity import Permission, Role, RolePermission
from ..security.permissions import ALL_PERMISSIONS, DEFAULT_ROLE_LABELS, DEFAULT_ROLE_PERMISSIONS


def ensure_roles_and_permissions(db: Session) -> None:
    """Idempotently create the baseline permission catalog and default role bundles.

    Safe to call on every startup; it never removes grants an admin already changed
    beyond the default set, it only fills in what is missing.
    """
    permission_by_code: dict[str, Permission] = {
        p.code: p for p in db.query(Permission).all()
    }
    for code in ALL_PERMISSIONS:
        if code not in permission_by_code:
            perm = Permission(code=code)
            db.add(perm)
            db.flush()
            permission_by_code[code] = perm

    role_by_code: dict[str, Role] = {r.code: r for r in db.query(Role).all()}
    for role_code, permission_codes in DEFAULT_ROLE_PERMISSIONS.items():
        role = role_by_code.get(role_code)
        if role is None:
            role = Role(code=role_code, name=DEFAULT_ROLE_LABELS[role_code], is_system=True)
            db.add(role)
            db.flush()
            role_by_code[role_code] = role

        existing_permission_ids = {rp.permission_id for rp in role.permissions}
        for permission_code in permission_codes:
            permission = permission_by_code[permission_code]
            if permission.id not in existing_permission_ids:
                db.add(RolePermission(role_id=role.id, permission_id=permission.id))

    db.commit()
