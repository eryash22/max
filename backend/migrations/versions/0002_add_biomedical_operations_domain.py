"""Add biomedical operations domain: asset history, vendors, contracts, PM/calibration,
incidents, escalation, notifications, business-impact assumptions, document links,
spare-part compatibility.

Revision ID: 0002_add_biomedical_operations_domain
Revises: 0001_initial_schema
Create Date: 2026-08-17

Same authoring constraint as 0001: no live PostgreSQL instance was available while
writing this migration, so `alembic revision --autogenerate` could not be run. DDL is
generated from the current `app.models` metadata rather than hand-typed `op.create_table()`
calls, for the same reason given in 0001 — it eliminates any risk of a hand-transcribed
column/type mismatch across 26 new tables.

This remains safe for both a brand-new database (where 0001's own `create_all` will
already have created every table current at upgrade time, making this revision a no-op
that only advances `alembic_version`) and a database that already applied 0001 before
these models existed (where this revision creates exactly the 26 new tables and leaves
the original 25 untouched, because `create_all(checkfirst=True)` — the default — skips
any table that already exists).

Once a live PostgreSQL instance is available, verify this revision with
`alembic check` and prefer `alembic revision --autogenerate` for every migration after
this one, same as documented in 0001 and DATABASE_SETUP.md.
"""
from alembic import op

from app import models  # noqa: F401 ensures every table is registered
from app.database import Base

# revision identifiers, used by Alembic.
# Short ID (not the descriptive filename) because alembic_version.version_num
# is VARCHAR(32) and the full slug is 37 characters.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NEW_TABLES = [
    "asset_history",
    "vendors",
    "vendor_contacts",
    "device_model_vendors",
    "warranties",
    "amc_contracts",
    "amc_contract_assets",
    "cmc_contracts",
    "cmc_contract_assets",
    "pm_schedules",
    "pm_records",
    "calibration_schedules",
    "calibration_records",
    "incidents",
    "incident_status_history",
    "service_visits",
    "escalation_policies",
    "escalation_levels",
    "escalation_events",
    "notification_templates",
    "notifications",
    "business_impact_assumptions",
    "document_links",
    "spare_part_compatibility",
]


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in NEW_TABLES if name in Base.metadata.tables]
    Base.metadata.drop_all(bind=bind, tables=tables, checkfirst=True)
