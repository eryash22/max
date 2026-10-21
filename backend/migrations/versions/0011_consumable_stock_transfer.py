"""Consumables workflow — central-to-branch stock transfer + audit ledger.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-21

Two changes, both additive/schema-widening (no data rewrite, no existing row
touched):

1. Adds `consumable_transactions` — the ledger `Consumable` never had (unlike
   `InventoryItem`'s `InventoryTransaction`), documented as a known gap in
   PROJECT_STATUS.md. Same `create_all(checkfirst=True)` approach as every prior
   table-only migration in this project (0002/0003/0004/...).

2. Relaxes `consumables.item_code` from globally-unique to unique-per-branch
   (`UniqueConstraint("item_code", "branch_id")`). `Consumable.branch_id IS NULL`
   is the central/hub pool; a non-NULL `branch_id` is that branch's own on-hand
   quantity, created the first time central stock is transferred to it
   (`inventory_service.get_or_create_branch_consumable`). Under the old
   globally-unique constraint the same catalog item could never have both a
   central row and a branch row, which blocked exactly the "central stock
   decreases, branch stock increases" flow the consumables workflow requires.
   The old constraint's name is looked up via reflection rather than
   hard-coded, since it was Postgres-assigned (no explicit `name=` was given
   in the original model).
"""
from alembic import op
import sqlalchemy as sa

from app import models  # noqa: F401 ensures every table (incl. the new one) is registered
from app.database import Base

# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

NEW_TABLES = ["consumable_transactions"]


def upgrade() -> None:
    bind = op.get_bind()

    inspector = sa.inspect(bind)
    existing_ucs = inspector.get_unique_constraints("consumables")
    for uc in existing_ucs:
        if uc["column_names"] == ["item_code"]:
            op.drop_constraint(uc["name"], "consumables", type_="unique")
    # Hosting-deploy pass — on a database migrated straight from byte-empty to head,
    # an earlier create_all may have already created this constraint from today's
    # models (see 0005's note).
    if not any(uc["name"] == "uq_consumables_item_code_branch" for uc in existing_ucs):
        op.create_unique_constraint("uq_consumables_item_code_branch", "consumables", ["item_code", "branch_id"])

    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in NEW_TABLES if name in Base.metadata.tables]
    Base.metadata.drop_all(bind=bind, tables=tables, checkfirst=True)

    op.drop_constraint("uq_consumables_item_code_branch", "consumables", type_="unique")
    op.create_unique_constraint("consumables_item_code_key", "consumables", ["item_code"])
