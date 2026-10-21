"""Add a generic contact phone number to users.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-07

`users.whatsapp_number` already exists but is specifically the WhatsApp delivery
address consumed by communication_service (`_preference_blocks_channel`), so it
cannot double as a general contact number -- a Manager/Unit Head/TL commonly has a
desk or mobile number that is not the WhatsApp one, and overloading the field would
silently change who WhatsApp messages reach.

Purely additive and nullable: no existing row is rewritten, and `whatsapp_number`
is left exactly as it is.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "phone")
