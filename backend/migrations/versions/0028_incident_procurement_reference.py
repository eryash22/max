"""Incident lightweight procurement reference fields.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-21

Adds plain reference/number fields to `incidents` for CER/Indent/PO numbers and
cost figures (with and without GST), plus a PO closure date. Deliberately NOT a
real Quotation/Indent/PurchaseOrder chain (see Module 21's procurement module for
that full approval workflow) — these are display/spend-total fields for
historical and lightweight record-keeping, all nullable and purely additive.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

NEW_COLUMNS = [
    ("cer_number", sa.String()),
    ("indent_number", sa.String()),
    ("po_number", sa.String()),
    ("po_closure_date", sa.String()),
    ("po_cost_excl_gst", sa.Float()),
    ("po_gst_percent", sa.Float()),
    ("po_amount", sa.Float()),
    ("vendor_estimate_cost_excl_gst", sa.Float()),
    ("vendor_estimate_gst_percent", sa.Float()),
    ("vendor_estimate_cost_incl_gst", sa.Float()),
]


def upgrade() -> None:
    # Hosting-deploy pass — same reasoning as 0005's note: a database migrated
    # straight from byte-empty to head may already have these columns from an
    # earlier unfiltered create_all.
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("incidents")}
    with op.batch_alter_table("incidents") as batch_op:
        for name, col_type in NEW_COLUMNS:
            if name not in existing:
                batch_op.add_column(sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("incidents") as batch_op:
        for name, _ in reversed(NEW_COLUMNS):
            batch_op.drop_column(name)
