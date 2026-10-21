"""Module 21 — Procurement (Quotation, Indent/CR, Purchase Order, Invoice).

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-25

BME device incident -> technical inspection -> AMC/CMC/Warranty check (reuses
services/asset_lifecycle_service.get_incident_coverage; nothing duplicated onto
these tables) -> fully covered: vendor service only, no quotation is ever created;
partially/not covered: Quotation -> Manager approval -> Indent/CR -> Purchase
Order -> vendor visit (ServiceVisit, extended with a nullable purchase_order_id
below) -> BME repair -> Invoice -> Inventory Manager verification -> PO
COMPLETED (financial closure, independent of Incident technical closure).

Adds six new tables (quotations, quotation_lines, indents, purchase_orders,
purchase_order_lines, invoices) and one nullable FK column
(service_visits.purchase_order_id) for the vendor-visit link. Additive only —
no existing table's data is rewritten.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def _base_columns():
    return [
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    # Hosting-deploy pass — a database migrated straight from byte-empty to head may
    # already have every table/column/index below from an earlier unfiltered
    # create_all (see 0005's note). `_ct`/`_ix` skip anything that already exists
    # rather than assuming either migration history.
    bind = op.get_bind()
    if sa.inspect(bind).has_table("quotations"):
        return  # the whole module's tables/columns either all exist already or none do
    op.create_table(
        "quotations",
        *_base_columns(),
        sa.Column("quotation_number", sa.String(), nullable=False),
        sa.Column("incident_id", sa.String(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("asset_id", sa.String(), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("vendor_id", sa.String(), sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("quote_date", sa.String(), nullable=True),
        sa.Column("valid_until", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="DRAFT"),
        sa.Column("coverage_status", sa.String(), nullable=False, server_default="NOT_COVERED"),
        sa.Column("coverage_note", sa.String(), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decided_at", sa.String(), nullable=True),
        sa.Column("rejection_reason", sa.String(), nullable=True),
        sa.Column("total", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_index("ix_quotations_quotation_number", "quotations", ["quotation_number"], unique=True)
    op.create_index("ix_quotations_incident_id", "quotations", ["incident_id"])
    op.create_index("ix_quotations_asset_id", "quotations", ["asset_id"])
    op.create_index("ix_quotations_vendor_id", "quotations", ["vendor_id"])
    op.create_index("ix_quotations_status", "quotations", ["status"])

    op.create_table(
        "quotation_lines",
        *_base_columns(),
        sa.Column("quotation_id", sa.String(), sa.ForeignKey("quotations.id"), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("taxes", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("covered", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_quotation_lines_quotation_id", "quotation_lines", ["quotation_id"])

    op.create_table(
        "indents",
        *_base_columns(),
        sa.Column("indent_number", sa.String(), nullable=False),
        sa.Column("quotation_id", sa.String(), sa.ForeignKey("quotations.id"), nullable=False),
        sa.Column("incident_id", sa.String(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("asset_id", sa.String(), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("vendor_id", sa.String(), sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("requester_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="DRAFT"),
        sa.Column("notes", sa.String(), nullable=True),
    )
    op.create_index("ix_indents_indent_number", "indents", ["indent_number"], unique=True)
    op.create_index("ix_indents_quotation_id", "indents", ["quotation_id"])
    op.create_index("ix_indents_incident_id", "indents", ["incident_id"])
    op.create_index("ix_indents_asset_id", "indents", ["asset_id"])
    op.create_index("ix_indents_vendor_id", "indents", ["vendor_id"])
    op.create_index("ix_indents_status", "indents", ["status"])

    op.create_table(
        "purchase_orders",
        *_base_columns(),
        sa.Column("po_number", sa.String(), nullable=False),
        sa.Column("indent_id", sa.String(), sa.ForeignKey("indents.id"), nullable=False),
        sa.Column("quotation_id", sa.String(), sa.ForeignKey("quotations.id"), nullable=False),
        sa.Column("incident_id", sa.String(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("asset_id", sa.String(), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("vendor_id", sa.String(), sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("issue_date", sa.String(), nullable=True),
        sa.Column("expected_vendor_date", sa.String(), nullable=True),
        sa.Column("total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="DRAFT"),
    )
    op.create_index("ix_purchase_orders_po_number", "purchase_orders", ["po_number"], unique=True)
    op.create_index("ix_purchase_orders_indent_id", "purchase_orders", ["indent_id"])
    op.create_index("ix_purchase_orders_quotation_id", "purchase_orders", ["quotation_id"])
    op.create_index("ix_purchase_orders_incident_id", "purchase_orders", ["incident_id"])
    op.create_index("ix_purchase_orders_asset_id", "purchase_orders", ["asset_id"])
    op.create_index("ix_purchase_orders_vendor_id", "purchase_orders", ["vendor_id"])
    op.create_index("ix_purchase_orders_status", "purchase_orders", ["status"])

    op.create_table(
        "purchase_order_lines",
        *_base_columns(),
        sa.Column("purchase_order_id", sa.String(), sa.ForeignKey("purchase_orders.id"), nullable=False),
        sa.Column("quotation_line_id", sa.String(), sa.ForeignKey("quotation_lines.id"), nullable=True),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_index("ix_purchase_order_lines_purchase_order_id", "purchase_order_lines", ["purchase_order_id"])

    op.create_table(
        "invoices",
        *_base_columns(),
        sa.Column("invoice_number", sa.String(), nullable=False),
        sa.Column("purchase_order_id", sa.String(), sa.ForeignKey("purchase_orders.id"), nullable=False),
        sa.Column("vendor_id", sa.String(), sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("incident_id", sa.String(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("asset_id", sa.String(), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("invoice_date", sa.String(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("recorded_by", sa.String(), nullable=True),
        sa.Column("recorded_at", sa.String(), nullable=True),
        sa.Column("verification_status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("verified_by", sa.String(), nullable=True),
        sa.Column("verified_at", sa.String(), nullable=True),
        sa.Column("rejection_reason", sa.String(), nullable=True),
    )
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"], unique=True)
    op.create_index("ix_invoices_purchase_order_id", "invoices", ["purchase_order_id"])
    op.create_index("ix_invoices_vendor_id", "invoices", ["vendor_id"])
    op.create_index("ix_invoices_incident_id", "invoices", ["incident_id"])
    op.create_index("ix_invoices_asset_id", "invoices", ["asset_id"])
    op.create_index("ix_invoices_verification_status", "invoices", ["verification_status"])

    op.add_column(
        "service_visits",
        sa.Column("purchase_order_id", sa.String(), sa.ForeignKey("purchase_orders.id"), nullable=True),
    )
    op.create_index("ix_service_visits_purchase_order_id", "service_visits", ["purchase_order_id"])


def downgrade() -> None:
    op.drop_index("ix_service_visits_purchase_order_id", table_name="service_visits")
    with op.batch_alter_table("service_visits") as batch_op:
        batch_op.drop_column("purchase_order_id")

    op.drop_table("invoices")
    op.drop_table("purchase_order_lines")
    op.drop_table("purchase_orders")
    op.drop_table("indents")
    op.drop_table("quotation_lines")
    op.drop_table("quotations")
