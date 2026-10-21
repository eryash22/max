"""Central Department Master + branch assignment.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-05

Departments (Physiotherapy, ICU, ...) were physically duplicated per branch --
two branches both offering "Physiotherapy" meant two unrelated `departments`
rows with no shared identity. This adds a `department_masters` catalog (same
shape as the existing DeviceCategory -> DeviceModel/Asset split: one global
definition, many branch-scoped instances referencing it) and a nullable
`departments.department_master_id` FK.

Purely additive at the schema level -- no existing `departments` row is
deleted, recreated, or has its id/branch_id changed. Requirement.department_id,
Incident.department_id, Asset.department_id, User.department_id,
Location.department_id and AssetHistory.department_id all continue to point at
`departments.id` exactly as before; this migration only adds a new column to
that table and backfills it.

Data backfill: existing `departments` rows are grouped by normalized
(trimmed, case-folded) name. One `department_masters` row is created per
distinct group, and every `departments` row in that group is linked to it via
`department_master_id`. `departments.name`/`.code` are also renormalized to
the master's canonical value at this point (trimming stray whitespace such as
the pre-existing "Physiotherapy " row), matching the sync-on-write behavior
the application now enforces going forward (see routers/hospitals.py).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import orm

from app.utils.ids import new_id

# revision identifiers, used by Alembic.
revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Hosting-deploy pass — a database migrated straight from byte-empty to head may
    # already have this table/column/index from an earlier unfiltered create_all
    # (see 0005's note). The backfill itself is always safe to run either way: on a
    # genuinely fresh database there are zero `departments` rows at this point in
    # history regardless (no migration inserts department data), so it's a no-op.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("department_masters"):
        op.create_table(
            "department_masters",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("updated_by", sa.String(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
            sa.UniqueConstraint("code", name="uq_department_masters_code"),
            sa.UniqueConstraint("name", name="uq_department_masters_name"),
        )

    if "department_master_id" not in {c["name"] for c in inspector.get_columns("departments")}:
        op.add_column(
            "departments",
            sa.Column("department_master_id", sa.String(), sa.ForeignKey("department_masters.id"), nullable=True),
        )
    if "ix_departments_department_master_id" not in {ix["name"] for ix in inspector.get_indexes("departments")}:
        op.create_index("ix_departments_department_master_id", "departments", ["department_master_id"])

    _backfill_department_masters()


def _backfill_department_masters() -> None:
    bind = op.get_bind()
    session = orm.Session(bind=bind)
    try:
        department_masters = sa.table(
            "department_masters",
            sa.column("id", sa.String), sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime),
            sa.column("version", sa.Integer), sa.column("code", sa.String), sa.column("name", sa.String),
            sa.column("status", sa.String),
        )
        departments = sa.table(
            "departments",
            sa.column("id", sa.String), sa.column("name", sa.String), sa.column("code", sa.String),
            sa.column("department_master_id", sa.String),
        )

        rows = session.execute(sa.select(departments.c.id, departments.c.name, departments.c.code)).all()

        groups: dict[str, dict] = {}
        for row_id, name, code in rows:
            key = (name or "").strip().lower()
            group = groups.setdefault(key, {"display_name": (name or "").strip(), "code": None, "row_ids": []})
            if not group["code"] and code:
                group["code"] = code.strip()
            group["row_ids"].append(row_id)

        now = sa.func.now()
        for group in groups.values():
            if not group["display_name"]:
                continue  # a department with no name at all cannot get a master name either
            master_id = new_id()
            session.execute(
                sa.insert(department_masters).values(
                    id=master_id, created_at=now, updated_at=now, version=1,
                    code=group["code"], name=group["display_name"], status="ACTIVE",
                )
            )
            session.execute(
                sa.update(departments)
                .where(departments.c.id.in_(group["row_ids"]))
                .values(department_master_id=master_id, name=group["display_name"], code=group["code"])
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def downgrade() -> None:
    op.drop_index("ix_departments_department_master_id", table_name="departments")
    with op.batch_alter_table("departments") as batch_op:
        batch_op.drop_column("department_master_id")
    op.drop_table("department_masters")
