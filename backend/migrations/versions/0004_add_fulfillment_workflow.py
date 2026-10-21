"""Add Module 17A — Inventory Fulfillment & Delivery Workflow domain:
fulfillment_requests, fulfillment_items, fulfillment_item_assets,
fulfillment_status_history.

Revision ID: 0004_add_fulfillment_workflow
Revises: 0003_add_requirement_workflow
Create Date: 2026-08-21

Same authoring constraint as 0001/0002/0003: no live PostgreSQL instance was
available while writing this migration, so `alembic revision --autogenerate` could
not be run. DDL is generated from the current `app.models` metadata rather than
hand-typed `op.create_table()` calls, for the same reason given in those revisions —
it eliminates any risk of a hand-transcribed column/type mismatch, and is safe for
both a brand-new database and one already at 0003 (create_all(checkfirst=True) skips
existing tables). No existing table (requirements, assets, inventory_items,
consumables, ...) is altered — Module 17A only adds new tables.

Once a live PostgreSQL instance is available, verify this revision with
`alembic check` and prefer `alembic revision --autogenerate` for every migration after
this one, same as documented in 0001/0002/0003 and DATABASE_SETUP.md.
"""
from alembic import op

from app import models  # noqa: F401 ensures every table is registered
from app.database import Base

# revision identifiers, used by Alembic.
# Short ID (not the descriptive filename) because alembic_version.version_num
# is VARCHAR(32) and the full slug is longer.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

NEW_TABLES = [
    "fulfillment_requests",
    "fulfillment_items",
    "fulfillment_item_assets",
    "fulfillment_status_history",
]


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in NEW_TABLES if name in Base.metadata.tables]
    Base.metadata.drop_all(bind=bind, tables=tables, checkfirst=True)
