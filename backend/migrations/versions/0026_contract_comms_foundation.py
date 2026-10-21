"""Communication foundation: channel-agnostic auto-send + notification dedup key.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-07

Two additive changes that the contract events (and every future event family) need:

1. `notification_templates.whatsapp_auto_send` -> `auto_send`. Introduced one revision
   ago as WhatsApp-only; automatic delivery is now equally meaningful for EMAIL, and a
   rule already carries exactly one `channel`, so a second per-channel flag would be
   redundant and could contradict it. Renamed rather than added alongside so there is
   never a "which flag wins" question. Values are preserved by the rename.

2. `notifications.dedup_key` — a nullable, indexed idempotency key. Event dispatch that
   fires on a mutation is naturally idempotent (the mutation happens once), but the
   contract expiry scan is designed to be run daily by Windows Task Scheduler and must
   not re-notify the same contract for the same reminder window every day. The scan
   writes a stable key (contract id + event + reminder window) and skips anything
   already present. Nullable because every mutation-driven notification leaves it unset
   and keeps its existing behaviour.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("notification_templates", "whatsapp_auto_send", new_column_name="auto_send")
    op.add_column("notifications", sa.Column("dedup_key", sa.String(), nullable=True))
    op.create_index("ix_notifications_dedup_key", "notifications", ["dedup_key"])


def downgrade() -> None:
    op.drop_index("ix_notifications_dedup_key", table_name="notifications")
    op.drop_column("notifications", "dedup_key")
    op.alter_column("notification_templates", "auto_send", new_column_name="whatsapp_auto_send")
