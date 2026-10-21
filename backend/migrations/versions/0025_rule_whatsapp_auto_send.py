"""Per-communication-rule "auto-send via WhatsApp Web" toggle.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-07

WhatsApp notifications produced by a communication rule currently stop at
PENDING_APPROVAL and need two manual actions (approve, then attempt-send through the
browser automation). This adds an opt-in per rule so a specific event can deliver
automatically through the existing WhatsApp Web session, without globally removing
`approval_required` from every notification in the system.

Defaults to FALSE with a server_default so every rule that exists today keeps its
current approval behaviour exactly -- enabling automation is always a deliberate,
per-rule admin decision.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_templates",
        sa.Column("whatsapp_auto_send", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("notification_templates", "whatsapp_auto_send")
