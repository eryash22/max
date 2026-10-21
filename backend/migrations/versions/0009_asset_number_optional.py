"""Final Operational Gap Pass, Part 1 — make assets.asset_number nullable.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-21

`asset_number` was the last field the Device Master creation flow (Part D of the
restructure consolidation pass) had to auto-generate a fallback value for, since
the column was NOT NULL. This migration drops that constraint so a device can be
created before a physical asset tag/number is assigned, and one attached later via
PATCH. Schema-only, no data rewrite — every existing row already has a real
asset_number and is untouched. The existing unique index/constraint on
asset_number is left as-is: Postgres's standard UNIQUE constraint already treats
multiple NULLs as distinct from one another (not equal to each other), so
uniqueness for every non-NULL value is preserved with no partial-index rework.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assets") as batch_op:
        batch_op.alter_column("asset_number", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    # Reversible only if no NULL asset_number rows exist at downgrade time — same
    # documented caveat this project's other migrations carry for one-directional
    # data states (see 0008's downgrade note).
    with op.batch_alter_table("assets") as batch_op:
        batch_op.alter_column("asset_number", existing_type=sa.String(), nullable=False)
