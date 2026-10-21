"""Device condemnation workflow — RequirementItem.asset_id.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-23

Business-workflow audit (2026-08-22 continued) — approved gap #1: Unit Head/TL
raise a condemnation request against a real, already-installed physical Asset
(not a DeviceModel, which is a catalog/type reference used by DEVICE/DEVICE_MODEL
requirement items). Reuses the existing Requirement/RequirementItem/approval
architecture per instruction rather than a separate Condemnation model — the only
schema gap was that RequirementItem had no way to reference an Asset at all
(only device_model_id/inventory_item_id/consumable_id). Adds a nullable
`asset_id` FK to `requirement_items`, used exclusively by the new `CONDEMNATION`
item_type (see schemas/requirement.py ITEM_TYPE_REFERENCE_FIELD). Additive,
no data rewrite — every existing requirement_items row gets NULL, matching every
other item_type's unused reference columns today.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "asset_id" not in {c["name"] for c in sa.inspect(bind).get_columns("requirement_items")}:
        op.add_column(
            "requirement_items",
            sa.Column("asset_id", sa.String(), sa.ForeignKey("assets.id"), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("requirement_items") as batch_op:
        batch_op.drop_column("asset_id")
