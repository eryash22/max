"""Incident source-text fields (device name, make, vendor name, remark).

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-25

An Incident's device/make/vendor are normally derived from its linked Asset ->
DeviceModel (see routers/incidents.py's export join), but the Incident History
Excel import is historical repair-log data that often has no corresponding Asset
record at all — "Item Name"/"Make"/"Vendor"/"Remark" in that workbook are free
text, not a device/vendor reference. Without a place to store them, an imported
row's values would be silently discarded, which the import's own explicit "never
silently discard a source value" requirement forbids. These four nullable text
columns are purely additive, exactly the same "lightweight reference field, not a
real relational link" pattern 0028 already used for CER/Indent/PO.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

NEW_COLUMNS = [
    ("device_name", sa.String()),
    ("manufacturer", sa.String()),
    ("vendor_name", sa.String()),
    ("remark", sa.String()),
]


def upgrade() -> None:
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
