"""Branch-scoping hardening pass — UNIT_HEAD/TL device catalog access swap.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-04

Data-only fix, no schema change: removes `device.read` from the `UNIT_HEAD` and
`TL` roles' `role_permissions` and grants `device.read_picker` instead. Same
precedent as 0012's HOSPITAL_UNIT_MANAGER inventory.read -> inventory.picker_read
swap: `ensure_roles_and_permissions()` (app/services/seed_service.py) is
additive-only by design ("never removes grants an admin already changed beyond
the default set") — it cannot fix a role that already has a grant which no
longer belongs in its default bundle (security/permissions.py). `device.read`
grants full Device Catalog admin visibility (internal notes, data source,
catalog-match/model-information review status, known-quantity planning figure,
plus create/archive actions gated on device.write) — UNIT_HEAD/TL must not see
the global catalog, they only need to pick a device model by category when
raising a requirement, which `device.read_picker` covers (see
routers/devices.py list_categories/list_device_models).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import orm

from app.utils.ids import new_id

# revision identifiers, used by Alembic.
revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

DEVICE_READ = "device.read"
DEVICE_READ_PICKER = "device.read_picker"
SCOPED_ROLES = ["UNIT_HEAD", "TL"]


def upgrade() -> None:
    bind = op.get_bind()
    session = orm.Session(bind=bind)
    try:
        for role_code in SCOPED_ROLES:
            _swap_device_read_for_picker(session, role_code)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _swap_device_read_for_picker(session: orm.Session, role_code: str) -> None:
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.String), sa.column("code", sa.String),
        sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime), sa.column("version", sa.Integer),
    )
    roles = sa.table("roles", sa.column("id", sa.String), sa.column("code", sa.String))
    role_permissions = sa.table(
        "role_permissions",
        sa.column("id", sa.String), sa.column("role_id", sa.String), sa.column("permission_id", sa.String),
        sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime), sa.column("version", sa.Integer),
    )

    role_row = session.execute(sa.select(roles.c.id).where(roles.c.code == role_code)).first()
    if role_row is None:
        # Fresh DB — role doesn't exist yet, the next ensure_roles_and_permissions()
        # call will seed it correctly from the (already-fixed) permissions.py defaults.
        return
    role_id = role_row[0]

    now = sa.func.now()

    picker_perm = session.execute(
        sa.select(permissions.c.id).where(permissions.c.code == DEVICE_READ_PICKER)
    ).first()
    if picker_perm is None:
        picker_perm_id = new_id()
        session.execute(
            sa.insert(permissions).values(
                id=picker_perm_id, code=DEVICE_READ_PICKER, created_at=now, updated_at=now, version=1,
            )
        )
    else:
        picker_perm_id = picker_perm[0]

    read_perm = session.execute(
        sa.select(permissions.c.id).where(permissions.c.code == DEVICE_READ)
    ).first()
    if read_perm is not None:
        session.execute(
            sa.delete(role_permissions).where(
                role_permissions.c.role_id == role_id,
                role_permissions.c.permission_id == read_perm[0],
            )
        )

    already_granted = session.execute(
        sa.select(role_permissions.c.id).where(
            role_permissions.c.role_id == role_id,
            role_permissions.c.permission_id == picker_perm_id,
        )
    ).first()
    if already_granted is None:
        session.execute(
            sa.insert(role_permissions).values(
                id=new_id(), role_id=role_id, permission_id=picker_perm_id,
                created_at=now, updated_at=now, version=1,
            )
        )


def downgrade() -> None:
    # Permission-grant swap is intentionally not reversed — same one-directional
    # data-backfill precedent as 0012 (restoring DEVICE_READ on UNIT_HEAD/TL would
    # reintroduce the Device Catalog access leak this migration exists to close).
    pass
