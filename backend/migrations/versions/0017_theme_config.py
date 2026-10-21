"""Final admin/inventory/vendor/dashboard polish pass — Part 11: admin-controlled
theme configuration.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-28

New single-row `theme_config` table, keyed by a fixed string id ("default") rather
than a generated UUID — there is exactly one theme configuration for the whole
deployment, no per-user/per-tenant rows. No existing precedent for a fixed-id
singleton config row exists elsewhere in this codebase (every other table uses the
UUIDPKMixin default), so this is the minimal new shape rather than an invented
parallel pattern: a plain String primary key, application-set to the constant
"default" (see models/theme.py), everything else following the same
created_at/updated_at timestamp convention as every other table.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "theme_config",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("primary_color", sa.String(), nullable=False),
        sa.Column("secondary_color", sa.String(), nullable=False),
        sa.Column("accent_color", sa.String(), nullable=False),
        sa.Column("success_color", sa.String(), nullable=False),
        sa.Column("warning_color", sa.String(), nullable=False),
        sa.Column("danger_color", sa.String(), nullable=False),
        sa.Column("surface_mode", sa.String(), nullable=False),
        sa.Column("preset_name", sa.String(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("theme_config")
