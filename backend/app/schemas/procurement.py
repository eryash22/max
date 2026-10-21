from datetime import datetime

from pydantic import BaseModel, field_validator

QUOTATION_STATUSES = ["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "CANCELLED"]
QUOTATION_COVERAGE_STATUSES = ["NOT_COVERED", "PARTIALLY_COVERED"]
INDENT_STATUSES = ["DRAFT", "SUBMITTED", "COMPLETED", "CANCELLED"]
PO_STATUSES = ["DRAFT", "ISSUED", "ACKNOWLEDGED", "IN_PROGRESS", "DELIVERED", "COMPLETED", "CANCELLED"]
INVOICE_VERIFICATION_STATUSES = ["PENDING", "VERIFIED", "REJECTED"]


# ---- Quotation ----

class QuotationLineIn(BaseModel):
    description: str
    quantity: int = 1
    unit_price: float = 0.0
    taxes: float = 0.0
    covered: bool = False

    @field_validator("description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value

    @field_validator("quantity")
    @classmethod
    def _positive_quantity(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("quantity must be positive")
        return value

    @field_validator("unit_price", "taxes")
    @classmethod
    def _non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("must not be negative")
        return value


class QuotationLineOut(BaseModel):
    id: str
    quotation_id: str
    description: str
    quantity: int
    unit_price: float
    taxes: float
    total: float
    covered: bool

    model_config = {"from_attributes": True}


class QuotationIn(BaseModel):
    incident_id: str
    asset_id: str
    vendor_id: str
    quote_date: str | None = None
    valid_until: str | None = None
    coverage_note: str | None = None
    lines: list[QuotationLineIn]

    @field_validator("lines")
    @classmethod
    def _at_least_one_line(cls, value: list[QuotationLineIn]) -> list[QuotationLineIn]:
        if not value:
            raise ValueError("a quotation must contain at least one line")
        return value


class QuotationUpdateIn(BaseModel):
    """Only a DRAFT quotation may be edited (see routers/procurement.py) — once
    SUBMITTED it is pending a Manager decision and must not change under them;
    APPROVED/REJECTED/CANCELLED are decided history. `lines` (if provided) fully
    replaces the existing line set and total/coverage_status are recomputed with
    the exact same logic create_quotation uses, so an edited quotation can never
    drift from the fully-covered/partially-covered creation rule."""

    quote_date: str | None = None
    valid_until: str | None = None
    coverage_note: str | None = None
    lines: list[QuotationLineIn] | None = None

    @field_validator("lines")
    @classmethod
    def _at_least_one_line_if_given(cls, value: list[QuotationLineIn] | None) -> list[QuotationLineIn] | None:
        if value is not None and not value:
            raise ValueError("a quotation must contain at least one line")
        return value


class QuotationDecisionIn(BaseModel):
    remarks: str | None = None


class QuotationRejectIn(BaseModel):
    rejection_reason: str

    @field_validator("rejection_reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rejection_reason must not be blank")
        return value


class QuotationOut(BaseModel):
    id: str
    quotation_number: str
    incident_id: str
    asset_id: str
    vendor_id: str
    quote_date: str | None = None
    valid_until: str | None = None
    status: str
    coverage_status: str
    coverage_note: str | None = None
    created_by: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    rejection_reason: str | None = None
    total: float
    created_at: datetime
    lines: list[QuotationLineOut] = []

    model_config = {"from_attributes": True}


# ---- Indent / CR ----

class IndentIn(BaseModel):
    quotation_id: str
    notes: str | None = None


class IndentUpdateIn(BaseModel):
    """`amount` is derived from the quotation's uncovered lines and structural
    fields (quotation_id/incident_id/asset_id/vendor_id) are fixed at creation —
    `notes` is the only field that is ever safe to edit. Only a DRAFT or SUBMITTED
    indent may be edited (mirrors cancel_indent's own state guard); once a
    Purchase Order exists the indent is auto-COMPLETED and becomes historical."""

    notes: str | None = None


class IndentOut(BaseModel):
    id: str
    indent_number: str
    quotation_id: str
    incident_id: str
    asset_id: str
    vendor_id: str
    requester_id: str | None = None
    amount: float
    status: str
    notes: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---- Purchase Order ----

class PurchaseOrderLineIn(BaseModel):
    quotation_line_id: str | None = None
    description: str
    quantity: int = 1
    unit_price: float = 0.0

    @field_validator("description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value


class PurchaseOrderLineOut(BaseModel):
    id: str
    purchase_order_id: str
    quotation_line_id: str | None = None
    description: str
    quantity: int
    unit_price: float
    total: float

    model_config = {"from_attributes": True}


class PurchaseOrderIn(BaseModel):
    indent_id: str
    issue_date: str | None = None
    expected_vendor_date: str | None = None
    lines: list[PurchaseOrderLineIn] | None = None


class PurchaseOrderUpdateIn(BaseModel):
    """Only a DRAFT purchase order may be edited (see routers/procurement.py) —
    once ISSUED the vendor has received these terms; changing them afterward would
    misrepresent what was actually communicated. Cancel and reissue instead
    (cancel_purchase_order). `lines` (if provided) fully replaces the existing line
    set and `total` is recomputed."""

    issue_date: str | None = None
    expected_vendor_date: str | None = None
    lines: list[PurchaseOrderLineIn] | None = None

    @field_validator("lines")
    @classmethod
    def _lines_not_empty_if_given(cls, value: list[PurchaseOrderLineIn] | None) -> list[PurchaseOrderLineIn] | None:
        if value is not None and not value:
            raise ValueError("a purchase order must contain at least one line")
        return value


class PurchaseOrderStatusIn(BaseModel):
    to_status: str

    @field_validator("to_status")
    @classmethod
    def _valid_status(cls, value: str) -> str:
        if value not in PO_STATUSES:
            raise ValueError(f"to_status must be one of {PO_STATUSES}")
        return value


class PurchaseOrderOut(BaseModel):
    id: str
    po_number: str
    indent_id: str
    quotation_id: str
    incident_id: str
    asset_id: str
    vendor_id: str
    issue_date: str | None = None
    expected_vendor_date: str | None = None
    total: float
    status: str
    created_by: str | None = None
    created_at: datetime
    lines: list[PurchaseOrderLineOut] = []

    model_config = {"from_attributes": True}


# ---- Invoice ----

class InvoiceIn(BaseModel):
    purchase_order_id: str
    invoice_date: str | None = None
    amount: float

    @field_validator("amount")
    @classmethod
    def _positive_amount(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("amount must be positive")
        return value


class InvoiceUpdateIn(BaseModel):
    """Only a PENDING invoice may be edited (see routers/procurement.py) — once
    VERIFIED or REJECTED it is financial closure history and must not change;
    `verification_status`/`verified_by`/`verified_at` are never editable here."""

    invoice_date: str | None = None
    amount: float | None = None

    @field_validator("amount")
    @classmethod
    def _positive_amount_if_given(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("amount must be positive")
        return value


class InvoiceRejectIn(BaseModel):
    rejection_reason: str

    @field_validator("rejection_reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rejection_reason must not be blank")
        return value


class InvoiceOut(BaseModel):
    id: str
    invoice_number: str
    purchase_order_id: str
    vendor_id: str
    incident_id: str
    asset_id: str
    invoice_date: str | None = None
    amount: float
    recorded_by: str | None = None
    recorded_at: str | None = None
    verification_status: str
    verified_by: str | None = None
    verified_at: str | None = None
    rejection_reason: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---- Incident / Asset provenance summaries ----

class IncidentProcurementSummaryOut(BaseModel):
    """Read-only rollup shown on the Incident detail page — no procurement fields are
    duplicated onto Incident; this is computed fresh from Quotation/Indent/
    PurchaseOrder/Invoice at read time, same discipline as IncidentCoverageOut."""

    quotations: list[QuotationOut] = []
    indents: list[IndentOut] = []
    purchase_orders: list[PurchaseOrderOut] = []
    invoices: list[InvoiceOut] = []
    financially_closed: bool = False


class AssetProcurementProvenanceEntryOut(BaseModel):
    """One quotation->indent->PO->invoice chain an asset went through — mirrors
    AssetProvenanceEntryOut's shape/purpose (requirement/fulfillment provenance) for
    the procurement chain, surfaced on the Asset Digital Passport page."""

    quotation_id: str
    quotation_number: str
    quotation_status: str
    coverage_status: str
    incident_id: str
    indent_id: str | None = None
    indent_number: str | None = None
    purchase_order_id: str | None = None
    po_number: str | None = None
    po_status: str | None = None
    invoice_id: str | None = None
    invoice_number: str | None = None
    invoice_verification_status: str | None = None
