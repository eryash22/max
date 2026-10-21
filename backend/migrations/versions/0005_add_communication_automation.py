"""Add Module 17B — WhatsApp + Email Communication Automation columns to the existing
Module 10 notification_templates/notifications tables.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-21

Unlike 0001-0004 (which only ever added brand-new tables, so a plain
`Base.metadata.create_all(checkfirst=True)` was sufficient), this migration extends two
EXISTING, already-populated tables with new columns. `create_all` does not alter existing
tables, so real `op.add_column()`/`op.drop_column()` calls are required here — verified
against the live Postgres dev database (migrated through 0004) before writing this file.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_templates") as batch_op:
        batch_op.add_column(sa.Column("sender_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("sender_email", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("sender_whatsapp_number", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("receiver_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("receiver_email", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("receiver_whatsapp_number", sa.String(), nullable=True))

    with op.batch_alter_table("notifications") as batch_op:
        batch_op.add_column(sa.Column("related_requirement_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("related_fulfillment_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("rendered_subject", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("rendered_body", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_notifications_related_requirement_id", "requirements", ["related_requirement_id"], ["id"]
        )
        batch_op.create_foreign_key(
            "fk_notifications_related_fulfillment_id", "fulfillment_requests", ["related_fulfillment_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.drop_constraint("fk_notifications_related_fulfillment_id", type_="foreignkey")
        batch_op.drop_constraint("fk_notifications_related_requirement_id", type_="foreignkey")
        batch_op.drop_column("rendered_body")
        batch_op.drop_column("rendered_subject")
        batch_op.drop_column("related_fulfillment_id")
        batch_op.drop_column("related_requirement_id")

    with op.batch_alter_table("notification_templates") as batch_op:
        batch_op.drop_column("receiver_whatsapp_number")
        batch_op.drop_column("receiver_email")
        batch_op.drop_column("receiver_name")
        batch_op.drop_column("sender_whatsapp_number")
        batch_op.drop_column("sender_email")
        batch_op.drop_column("sender_name")
