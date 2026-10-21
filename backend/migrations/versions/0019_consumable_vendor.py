"""Final non-AI cleanup pass — Consumable vendor relationship.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-30

Adds a nullable `vendor_id` FK column to `consumables`, mirroring how `Asset`
already links to a vendor via its device model. `supplier` (free text) is kept
as-is for existing/historical rows — nothing is backfilled or migrated off it —
this is purely additive so the New Inventory Item form can offer a Vendor-master
dropdown for consumables the same way it already does for devices, per the "do not
allow random free-text supplier names where the vendor relationship is supported"
requirement.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "vendor_id" not in {c["name"] for c in inspector.get_columns("consumables")}:
        op.add_column("consumables", sa.Column("vendor_id", sa.String(), nullable=True))
    fk_names = {fk["name"] for fk in inspector.get_foreign_keys("consumables") if fk["name"]}
    if "fk_consumables_vendor_id_vendors" not in fk_names:
        op.create_foreign_key(
            "fk_consumables_vendor_id_vendors", "consumables", "vendors", ["vendor_id"], ["id"]
        )


def downgrade() -> None:
    op.drop_constraint("fk_consumables_vendor_id_vendors", "consumables", type_="foreignkey")
    op.drop_column("consumables", "vendor_id")
