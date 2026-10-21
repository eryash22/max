"""Step 10G — advanced theme customization & dark-mode readability fix.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-31

Adds one nullable JSON column, `advanced_tokens`, to the existing single-row
`theme_config` table. Carries the expanded surface/text/border/interactive/
sidebar/table/badge token set (see schemas/theme.py AdvancedThemeTokens) —
kept as one JSON object rather than ~20 new String columns, since every field
is still strictly `#RRGGBB`-validated at the API layer before it is ever
written here. Nullable so the existing single row (if any) migrates cleanly;
the API always returns a fully-populated object by falling back to the
built-in Clinical Blue defaults for any missing/absent key.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "advanced_tokens" not in {c["name"] for c in sa.inspect(bind).get_columns("theme_config")}:
        op.add_column("theme_config", sa.Column("advanced_tokens", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("theme_config", "advanced_tokens")
