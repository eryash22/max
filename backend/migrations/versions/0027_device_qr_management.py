"""Device QR Code Management System: stable per-asset QR identity + public display settings.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-17

Adds three purely-additive, nullable columns to `assets` for a cryptographically
random, unguessable public QR token per physical asset (never a sequential id,
never encoded device information — see services/qr_service.py):

- `qr_token` (unique, nullable): NULL means "no QR identity provisioned yet" for
  every existing row; backfilling millions of existing assets with a real token is
  an explicit Super Admin bulk-generate action (routers/qr.py), not something this
  migration invents data for.
- `qr_active` (boolean, default True): per-asset revoke flag. Defaults True so a
  token generated after this migration is live immediately; existing rows with no
  token yet are unaffected either way until they get one.
- `qr_generated_at` (nullable timestamp): when the token was last (re)generated.

Also creates `qr_display_settings`, a singleton table (one row, fixed id) holding
the Super-Admin-configured public-page field visibility/ordering — same shape as
the existing `theme_config` singleton table added by an earlier migration.

Safe for existing databases: no existing Asset row is rewritten or deleted, no
historical operation/QR data is invented, and the new unique index on `qr_token`
permits any number of NULLs (every pre-existing row) without conflict.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Hosting-deploy pass — same reasoning as 0005's note.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    asset_cols = {c["name"] for c in inspector.get_columns("assets")}
    if "qr_token" not in asset_cols:
        op.add_column("assets", sa.Column("qr_token", sa.String(), nullable=True))
    if "qr_active" not in asset_cols:
        op.add_column("assets", sa.Column("qr_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    if "qr_generated_at" not in asset_cols:
        op.add_column("assets", sa.Column("qr_generated_at", sa.DateTime(), nullable=True))
    if "ix_assets_qr_token" not in {ix["name"] for ix in inspector.get_indexes("assets")}:
        op.create_index("ix_assets_qr_token", "assets", ["qr_token"], unique=True)

    if not inspector.has_table("qr_display_settings"):
        op.create_table(
            "qr_display_settings",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("field_config", sa.JSON(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("qr_display_settings")
    op.drop_index("ix_assets_qr_token", table_name="assets")
    op.drop_column("assets", "qr_generated_at")
    op.drop_column("assets", "qr_active")
    op.drop_column("assets", "qr_token")
