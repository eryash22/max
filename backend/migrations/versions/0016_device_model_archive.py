"""Final admin/inventory/vendor/dashboard polish pass — Part 2: device catalog
archive/delete.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-28

Adds `device_models.is_active` (default True) so a `DeviceModel` with no referencing
`Asset` rows can be hard-deleted, while one that is still referenced is soft-archived
instead (`is_active=False`) — mirrors `Vendor.status`'s activate/deactivate toggle.
Purely additive: every existing row defaults to `is_active=True` (unchanged, still
visible everywhere it is today).
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "is_active" not in {c["name"] for c in sa.inspect(bind).get_columns("device_models")}:
        op.add_column(
            "device_models",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    with op.batch_alter_table("device_models") as batch_op:
        batch_op.drop_column("is_active")
