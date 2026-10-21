"""Product Restructure / Consolidation Pass — Branch contact fields, MAX Healthcare
single-network backfill, and DeviceCategory canonicalization.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-21

Three independent, additive changes, all safe against the live dev database
(confirmed via the Pass 1 read-only data audit: 1 Branch, 0 HospitalNetwork rows,
6 DeviceModel rows, 0 DeviceCategory rows, no conflicting data):

1. Adds `tl`, `branch_email`, `branch_phone`, `branch_whatsapp` columns to `branches`
   (real `op.add_column()` — the table already exists and may be populated, same
   discipline as 0005/0006). `unit_head` already existed and is unchanged.

2. Data backfill only, no schema change: ensures exactly one `HospitalNetwork` row
   named "MAX Healthcare" exists, then sets `network_id` on every `Branch` row that
   doesn't already reference a network. Idempotent — if a "MAX Healthcare" network
   already exists (e.g. re-running against a database where this migration partially
   applied), it's reused rather than duplicated; branches that already reference some
   other network are left untouched, not overwritten.

3. Data backfill only, no schema change: seeds the canonical `DeviceCategory` list
   (task-provided vocabulary) and backfills `DeviceModel.category_id` for the two
   free-text values confirmed live in Pass 1 (`Diagnostic` -> `Diagnostic`,
   `Physio` -> `Physiotherapy & Rehabilitation`). `DeviceModel.category` (free text)
   is left untouched — it remains the legacy/display source until every consumer is
   verified to read `category_id` instead. Any DeviceModel whose free-text category
   doesn't match one of the two known values is left with `category_id = NULL`
   (nothing is guessed) — per instruction, no NOT NULL/strict constraint is added.

Downgrade removes the four new columns only. The HospitalNetwork/DeviceCategory
backfill is intentionally not reversed (deleting a network/category row could orphan
FKs written by application code after upgrade) — this matches the project's existing
precedent of one-directional data backfills.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import orm

from app.utils.ids import new_id

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

CANONICAL_DEVICE_CATEGORIES = [
    "ICU / Critical Care",
    "Physiotherapy & Rehabilitation",
    "Respiratory Care",
    "Mobility & Patient Transport",
    "Patient Monitoring",
    "Infusion & Syringe Pumps",
    "Surgical",
    "Diagnostic",
    "Electrotherapy",
    "Oxygen Therapy",
    "Laboratory",
    "Other Biomedical Equipment",
]

# Confirmed-live free-text -> canonical name mapping (Pass 1 data audit).
FREE_TEXT_CATEGORY_MAP = {
    "diagnostic": "Diagnostic",
    "physio": "Physiotherapy & Rehabilitation",
}

MAX_HEALTHCARE_NETWORK_NAME = "MAX Healthcare"


def upgrade() -> None:
    bind = op.get_bind()

    # Hosting-deploy pass — same reasoning as 0005/0006/0007: a database migrated
    # straight from byte-empty to head already has these columns from an earlier
    # create_all using today's models, so check first.
    branch_cols = {c["name"] for c in sa.inspect(bind).get_columns("branches")}
    with op.batch_alter_table("branches") as batch_op:
        for col_name in ("tl", "branch_email", "branch_phone", "branch_whatsapp"):
            if col_name not in branch_cols:
                batch_op.add_column(sa.Column(col_name, sa.String(), nullable=True))

    session = orm.Session(bind=bind)
    try:
        _backfill_hospital_network(session)
        _backfill_device_categories(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _backfill_hospital_network(session: orm.Session) -> None:
    hospital_networks = sa.table(
        "hospital_networks",
        sa.column("id", sa.String),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("version", sa.Integer),
    )
    branches = sa.table(
        "branches",
        sa.column("id", sa.String),
        sa.column("network_id", sa.String),
    )

    existing = session.execute(
        sa.select(hospital_networks.c.id).where(hospital_networks.c.name == MAX_HEALTHCARE_NETWORK_NAME)
    ).first()
    if existing:
        network_id = existing[0]
    else:
        network_id = new_id()
        now = sa.func.now()
        session.execute(
            sa.insert(hospital_networks).values(
                id=network_id,
                code="MAX",
                name=MAX_HEALTHCARE_NETWORK_NAME,
                created_at=now,
                updated_at=now,
                version=1,
            )
        )

    session.execute(
        sa.update(branches).where(branches.c.network_id.is_(None)).values(network_id=network_id)
    )


def _backfill_device_categories(session: orm.Session) -> None:
    device_categories = sa.table(
        "device_categories",
        sa.column("id", sa.String),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("version", sa.Integer),
    )
    device_models = sa.table(
        "device_models",
        sa.column("id", sa.String),
        sa.column("category", sa.String),
        sa.column("category_id", sa.String),
    )

    now = sa.func.now()
    name_to_id: dict[str, str] = {}
    for name in CANONICAL_DEVICE_CATEGORIES:
        existing = session.execute(
            sa.select(device_categories.c.id).where(device_categories.c.name == name)
        ).first()
        if existing:
            name_to_id[name] = existing[0]
            continue
        category_id = new_id()
        session.execute(
            sa.insert(device_categories).values(
                id=category_id, code=None, name=name, created_at=now, updated_at=now, version=1,
            )
        )
        name_to_id[name] = category_id

    rows = session.execute(
        sa.select(device_models.c.id, device_models.c.category).where(device_models.c.category_id.is_(None))
    ).all()
    for model_id, category_text in rows:
        if not category_text:
            continue
        canonical_name = FREE_TEXT_CATEGORY_MAP.get(category_text.strip().lower())
        if canonical_name is None:
            continue
        session.execute(
            sa.update(device_models)
            .where(device_models.c.id == model_id)
            .values(category_id=name_to_id[canonical_name])
        )


def downgrade() -> None:
    with op.batch_alter_table("branches") as batch_op:
        batch_op.drop_column("branch_whatsapp")
        batch_op.drop_column("branch_phone")
        batch_op.drop_column("branch_email")
        batch_op.drop_column("tl")
