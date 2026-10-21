"""Add Module 19 — Operational Integration & Automation columns/table.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-21

Extends two EXISTING, already-populated tables (`notifications`, `users`) with new
columns via real `op.add_column()`/`batch_alter_table` calls (same reasoning as 0005/
0006 — `create_all` does not alter existing tables), and creates one brand-new table
(`communication_provider_configs`) via `Base.metadata.create_all(checkfirst=True)`,
same unfiltered pattern as 0002 — checkfirst skips every table that already exists
(users/notifications included), so only the new table is actually created.
"""
from alembic import op
import sqlalchemy as sa

from app import models  # noqa: F401 ensures every table is registered
from app.database import Base

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Hosting-deploy pass — same reasoning as 0005/0006: on a database migrated
    # straight from byte-empty to head, an earlier create_all already creates these
    # columns from today's models, so unconditional add_column/create_foreign_key
    # would fail with a duplicate error. Checking first keeps this correct for an
    # incrementally-migrated database too.
    bind = op.get_bind()

    notification_cols = {c["name"] for c in sa.inspect(bind).get_columns("notifications")}
    with op.batch_alter_table("notifications") as batch_op:
        for col_name in ("whatsapp_status", "whatsapp_opened_at", "whatsapp_confirmed_by", "whatsapp_provider"):
            if col_name not in notification_cols:
                batch_op.add_column(sa.Column(col_name, sa.String(), nullable=True))

    user_cols = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    user_fks = {fk["name"] for fk in sa.inspect(bind).get_foreign_keys("users") if fk["name"]}
    with op.batch_alter_table("users") as batch_op:
        if "whatsapp_number" not in user_cols:
            batch_op.add_column(sa.Column("whatsapp_number", sa.String(), nullable=True))
        if "department_id" not in user_cols:
            batch_op.add_column(sa.Column("department_id", sa.String(), nullable=True))
        if "notification_preference" not in user_cols:
            batch_op.add_column(
                sa.Column("notification_preference", sa.String(), nullable=False, server_default="EMAIL")
            )
        if "fk_users_department_id" not in user_fks:
            batch_op.create_foreign_key("fk_users_department_id", "departments", ["department_id"], ["id"])

    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    if "communication_provider_configs" in Base.metadata.tables:
        Base.metadata.drop_all(bind=bind, tables=[Base.metadata.tables["communication_provider_configs"]], checkfirst=True)

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("fk_users_department_id", type_="foreignkey")
        batch_op.drop_column("notification_preference")
        batch_op.drop_column("department_id")
        batch_op.drop_column("whatsapp_number")

    with op.batch_alter_table("notifications") as batch_op:
        batch_op.drop_column("whatsapp_provider")
        batch_op.drop_column("whatsapp_confirmed_by")
        batch_op.drop_column("whatsapp_opened_at")
        batch_op.drop_column("whatsapp_status")
