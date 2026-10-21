"""Security hardening pass — invalidate existing sessions on password reset.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-04

Adds `token_version` (int, default 0) to `users`. Every issued JWT now carries a
`tv` claim equal to the user's `token_version` at issue time
(services/auth_service.issue_token); dependencies/auth.get_current_user rejects
any token whose `tv` no longer matches the current row. An admin-driven password
reset (routers/users.py::reset_user_password) increments the column, so tokens
issued before the reset stop being accepted immediately instead of remaining
valid for up to ACCESS_TOKEN_EXPIRE_MINUTES more.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "token_version" not in {c["name"] for c in sa.inspect(bind).get_columns("users")}:
        op.add_column(
            "users",
            sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("users", "token_version")
