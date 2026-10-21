"""Final hardening pass — real branch user hierarchy (Unit Head / TL).

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-27

Adds branches.unit_head_user_id (nullable FK to users — the branch's real Unit
Head) and a new branch_tls join table (many real TL users per branch). Purely
additive: the legacy free-text Branch.unit_head/Branch.tl columns are untouched
and keep their existing values, used only as a display fallback where no real
user assignment exists yet. No existing row is rewritten or deleted.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "branches",
        sa.Column("unit_head_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_branches_unit_head_user_id", "branches", ["unit_head_user_id"])

    op.create_table(
        "branch_tls",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("branch_id", sa.String(), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
    )
    op.create_index("ix_branch_tls_branch_id", "branch_tls", ["branch_id"])
    op.create_index("ix_branch_tls_user_id", "branch_tls", ["user_id"])
    op.create_unique_constraint("uq_branch_tls_branch_user", "branch_tls", ["branch_id", "user_id"])


def downgrade() -> None:
    op.drop_table("branch_tls")
    op.drop_index("ix_branches_unit_head_user_id", table_name="branches")
    with op.batch_alter_table("branches") as batch_op:
        batch_op.drop_column("unit_head_user_id")
