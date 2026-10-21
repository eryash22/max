"""Add Module 18A — Asset Lifecycle + AMC/CMC + Maintenance columns to the existing
assets/service_visits tables.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-21

No new tables: AMC/CMC/Warranty coverage and current vendor are always derived live
from the existing Module 05A contract tables (see services/asset_lifecycle_service.py),
never duplicated onto `assets`. Maintenance history reuses PMRecord/CalibrationRecord/
ServiceVisit — only `service_visits` gains the two fields (`issue`, `downtime_hours`)
that had no existing home. Real `op.add_column()` calls are required (see 0005's note)
since both tables already exist and are populated.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assets") as batch_op:
        batch_op.add_column(sa.Column("operational_since", sa.String(), nullable=True))

    with op.batch_alter_table("service_visits") as batch_op:
        batch_op.add_column(sa.Column("issue", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("downtime_hours", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("service_visits") as batch_op:
        batch_op.drop_column("downtime_hours")
        batch_op.drop_column("issue")

    with op.batch_alter_table("assets") as batch_op:
        batch_op.drop_column("operational_since")
