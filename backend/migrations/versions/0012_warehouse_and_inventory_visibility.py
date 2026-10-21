"""Business-workflow audit — central warehouse flag + Hospital TL/Unit Head
inventory-visibility fix.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-22

Two independent changes:

1. Adds `branches.is_central_warehouse` (bool, default false). The business audit
   found no way to distinguish the one central biomedical warehouse ("MAX @ HOME,
   Okhla Phase 2") from an ordinary hospital branch — `InventoryLocation.branch_id`
   is a required FK to `branches`, so per the brief's own instruction ("If the
   current data model supports locations, use the existing location architecture"),
   the central warehouse is represented as a `Branch` row with this flag set, not a
   new top-level entity. Does not touch existing branch rows beyond the new column
   default; the actual "MAX @ HOME" row is created by application data (import or
   an admin creating/flagging a branch), not guessed here — no branch in the live
   dev DB matches that name (confirmed via repo-wide search before writing this).

2. Data-only fix, no schema change: removes `inventory.read` from the
   `HOSPITAL_UNIT_MANAGER` role's `role_permissions` and grants `inventory.picker_read`
   instead. `ensure_roles_and_permissions()` (app/services/seed_service.py) is
   additive-only by design ("never removes grants an admin already changed beyond
   the default set") — it cannot fix a role that already has a grant which no longer
   belongs in its default bundle. That bundle change (permissions.py) is a business
   audit finding: TL/Unit Head must never see central/branch stock quantities, but
   `INVENTORY_READ` returned exactly that from `GET /inventory/items` and
   `GET /consumables`. `inventory.picker_read` is inserted first if the permission
   catalog row doesn't exist yet — this migration can run before
   `ensure_roles_and_permissions()` first executes on the next app startup.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import orm

from app.utils.ids import new_id

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

INVENTORY_READ = "inventory.read"
INVENTORY_PICKER_READ = "inventory.picker_read"
HOSPITAL_UNIT_MANAGER = "HOSPITAL_UNIT_MANAGER"


def upgrade() -> None:
    bind = op.get_bind()

    # Hosting-deploy pass — checked first in case an earlier create_all already
    # created this column from today's models (see 0005's note).
    if "is_central_warehouse" not in {c["name"] for c in sa.inspect(bind).get_columns("branches")}:
        op.add_column(
            "branches",
            sa.Column("is_central_warehouse", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    session = orm.Session(bind=bind)
    try:
        _fix_hospital_unit_manager_inventory_grant(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _fix_hospital_unit_manager_inventory_grant(session: orm.Session) -> None:
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

    role_row = session.execute(sa.select(roles.c.id).where(roles.c.code == HOSPITAL_UNIT_MANAGER)).first()
    if role_row is None:
        # Fresh DB — role doesn't exist yet, the next ensure_roles_and_permissions()
        # call will seed it correctly from the (already-fixed) permissions.py defaults.
        return
    role_id = role_row[0]

    now = sa.func.now()

    picker_perm = session.execute(
        sa.select(permissions.c.id).where(permissions.c.code == INVENTORY_PICKER_READ)
    ).first()
    if picker_perm is None:
        picker_perm_id = new_id()
        session.execute(
            sa.insert(permissions).values(
                id=picker_perm_id, code=INVENTORY_PICKER_READ, created_at=now, updated_at=now, version=1,
            )
        )
    else:
        picker_perm_id = picker_perm[0]

    read_perm = session.execute(
        sa.select(permissions.c.id).where(permissions.c.code == INVENTORY_READ)
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
    op.drop_column("branches", "is_central_warehouse")
    # Permission-grant swap is intentionally not reversed — same one-directional
    # data-backfill precedent as 0008 (restoring INVENTORY_READ on
    # HOSPITAL_UNIT_MANAGER would reintroduce the stock-visibility leak this
    # migration exists to close).
