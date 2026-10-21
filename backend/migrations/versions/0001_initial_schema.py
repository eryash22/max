"""Initial Phase 1 schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-17

This migration creates every Phase 1 table directly from the current
SQLAlchemy model metadata (app.models) rather than a hand-typed sequence of
op.create_table() calls. There is no live PostgreSQL instance available in
the environment that authored this migration to run `alembic revision
--autogenerate` against, so generating DDL straight from the models is the
only way to guarantee the migration matches the models exactly, with zero
risk of a hand-transcribed column/type mismatch.

Once this has been applied once, ALL subsequent schema changes must go
through `alembic revision --autogenerate -m "..."` in the normal way — do
not keep hand-writing create_all-based migrations after this one.
"""
from alembic import op

from app.database import Base
from app import models  # noqa: F401 ensures every table is registered

# revision identifiers, used by Alembic.
# Short IDs (not the descriptive filename) because alembic_version.version_num
# is VARCHAR(32); "0001_initial_schema" fits, but the 0002 slug did not.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
