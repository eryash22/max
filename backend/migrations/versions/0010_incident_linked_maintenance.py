"""Workflow consolidation pass — add nullable incident_id back-references to
pm_records and calibration_records.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-21

`ServiceVisit.incident_id` already existed; PMRecord/CalibrationRecord did not have the
equivalent column, so an Incident raised as a "Maintenance/Calibration Request" had no
structural way to hand off into a real PM/Calibration record. Schema-only, additive,
nullable — no data rewrite, every existing row is untouched.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Hosting-deploy pass — checked first in case an earlier create_all already
    # created this column/FK from today's models (see 0005's note).
    bind = op.get_bind()

    pm_cols = {c["name"] for c in sa.inspect(bind).get_columns("pm_records")}
    pm_fks = {fk["name"] for fk in sa.inspect(bind).get_foreign_keys("pm_records") if fk["name"]}
    with op.batch_alter_table("pm_records") as batch_op:
        if "incident_id" not in pm_cols:
            batch_op.add_column(sa.Column("incident_id", sa.String(), nullable=True))
        if "fk_pm_records_incident_id" not in pm_fks:
            batch_op.create_foreign_key("fk_pm_records_incident_id", "incidents", ["incident_id"], ["id"])

    cal_cols = {c["name"] for c in sa.inspect(bind).get_columns("calibration_records")}
    cal_fks = {fk["name"] for fk in sa.inspect(bind).get_foreign_keys("calibration_records") if fk["name"]}
    with op.batch_alter_table("calibration_records") as batch_op:
        if "incident_id" not in cal_cols:
            batch_op.add_column(sa.Column("incident_id", sa.String(), nullable=True))
        if "fk_calibration_records_incident_id" not in cal_fks:
            batch_op.create_foreign_key("fk_calibration_records_incident_id", "incidents", ["incident_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("calibration_records") as batch_op:
        batch_op.drop_constraint("fk_calibration_records_incident_id", type_="foreignkey")
        batch_op.drop_column("incident_id")

    with op.batch_alter_table("pm_records") as batch_op:
        batch_op.drop_constraint("fk_pm_records_incident_id", type_="foreignkey")
        batch_op.drop_column("incident_id")
