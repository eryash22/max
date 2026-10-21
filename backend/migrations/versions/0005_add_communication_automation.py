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


def _existing_columns(bind, table_name: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table_name)}


def _existing_fk_names(bind, table_name: str) -> set[str]:
    return {fk["name"] for fk in sa.inspect(bind).get_foreign_keys(table_name) if fk["name"]}


def upgrade() -> None:
    # Hosting-deploy pass — on a database migrated from byte-empty straight to head,
    # migration 0002's Base.metadata.create_all(checkfirst=True) already creates
    # notification_templates/notifications with every column these models hold
    # *today*, including the ones this migration was written to add — so these
    # add_column/create_foreign_key calls would fail with a duplicate-column/
    # duplicate-constraint error on a fresh database. On a database that was
    # migrated incrementally over time (up through 0004 before these columns
    # existed on the models), 0002's create_all never retroactively adds columns to
    # an already-existing table, so this migration is still exactly what adds them
    # there. Checking first makes this correct for both cases rather than assuming
    # either history.
    bind = op.get_bind()

    template_cols = _existing_columns(bind, "notification_templates")
    with op.batch_alter_table("notification_templates") as batch_op:
        for col_name in (
            "sender_name", "sender_email", "sender_whatsapp_number",
            "receiver_name", "receiver_email", "receiver_whatsapp_number",
        ):
            if col_name not in template_cols:
                batch_op.add_column(sa.Column(col_name, sa.String(), nullable=True))

    notification_cols = _existing_columns(bind, "notifications")
    notification_fks = _existing_fk_names(bind, "notifications")
    with op.batch_alter_table("notifications") as batch_op:
        for col_name in ("related_requirement_id", "related_fulfillment_id", "rendered_subject", "rendered_body"):
            if col_name not in notification_cols:
                batch_op.add_column(sa.Column(col_name, sa.String(), nullable=True))
        if "fk_notifications_related_requirement_id" not in notification_fks:
            batch_op.create_foreign_key(
                "fk_notifications_related_requirement_id", "requirements", ["related_requirement_id"], ["id"]
            )
        if "fk_notifications_related_fulfillment_id" not in notification_fks:
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
