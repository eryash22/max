"""Batch E, Part 20 — Service Visit Type configuration.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-29

Adds a new `service_visit_types` table (id/name/is_active/created_at/updated_at/
created_by/updated_by/version — same BaseModel shape as `DeviceCategory`,
models/device.py) and seeds it with the five values already documented as the
informal `ServiceVisit.visit_type` convention (PM, CALIBRATION, BREAKDOWN,
INSTALLATION, OTHER — see models/incident.py's `ServiceVisit.visit_type` comment),
all active.

Purely additive: `service_visits.visit_type` itself is NOT touched by this
migration — no column type change, no FK, no backfill, no row updated or nulled.
Existing/historical `ServiceVisit` rows (including any with a free-text value
outside the seeded five) are completely unaffected and can never fail this
migration. New/edited ServiceVisit rows are validated against the active names in
this table at the API layer only (see routers/service_visits.py), never via a DB
constraint, precisely so history is never at risk.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import orm

from app.utils.ids import new_id

# revision identifiers, used by Alembic.
revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

SEEDED_VISIT_TYPES = ["PM", "CALIBRATION", "BREAKDOWN", "INSTALLATION", "OTHER"]


def upgrade() -> None:
    op.create_table(
        "service_visit_types",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    bind = op.get_bind()
    session = orm.Session(bind=bind)
    try:
        _seed_visit_types(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _seed_visit_types(session: orm.Session) -> None:
    service_visit_types = sa.table(
        "service_visit_types",
        sa.column("id", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("version", sa.Integer),
        sa.column("name", sa.String),
        sa.column("is_active", sa.Boolean),
    )
    now = sa.func.now()
    for name in SEEDED_VISIT_TYPES:
        session.execute(
            sa.insert(service_visit_types).values(
                id=new_id(), created_at=now, updated_at=now, version=1, name=name, is_active=True,
            )
        )


def downgrade() -> None:
    op.drop_table("service_visit_types")
