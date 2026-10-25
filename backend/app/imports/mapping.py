"""Known sheet layouts the Import Center understands.

Each entry maps a normalized sheet name to: the entity it stages rows for,
the canonical column -> model-field mapping, which fields are required, and
which field is the stable business key used for insert/update/duplicate
detection. Unknown sheets are still staged (for audit visibility) but are
never written to production tables.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SheetConfig:
    entity_type: str
    column_map: dict[str, str]  # source header (normalized, no '*') -> model field
    required: list[str]  # model field names that must be present
    business_key_fields: list[str] = field(default_factory=list)
    # When true, parser.parse_workbook does a second (slower) pass over this
    # sheet's cells with formulas intact, so a formula-containing cell can be
    # flagged in the import preview instead of its cached value being trusted
    # silently — see ParsedRow.formula_fields. Opt-in per sheet (default
    # False) so the extra pass only happens where it's actually needed.
    detect_formulas: bool = False


def _norm(header: str) -> str:
    return header.strip().rstrip("*").strip().lower()


SHEET_CONFIGS: dict[str, SheetConfig] = {
    "hospital master": SheetConfig(
        entity_type="branch",
        column_map={
            "hospital code": "code",
            "hospital name": "name",
            "status": "status",
            "address": "address",
            "city": "city",
            "state": "state",
            "region": "region",
            "unit head": "unit_head",
            "inventory mapping status": "inventory_mapping_status",
            "environment": "environment",
        },
        required=["name", "status"],
        business_key_fields=["code", "name"],
    ),
    "device model master": SheetConfig(
        entity_type="device_model",
        column_map={
            "manufacturer": "manufacturer",
            "model number": "model_number",
            "device name": "name",
            "category": "category",
            "model status": "model_status",
            "firmware version": "firmware_version",
            "criticality default": "criticality_default",
            "known quantity": "known_quantity",
            "notes": "notes",
            "data source": "data_source",
            "model information status": "model_information_status",
            "catalog match status": "catalog_match_status",
            "environment": "environment",
        },
        required=["name", "category"],
        business_key_fields=["manufacturer", "model_number", "name"],
    ),
    "consumables": SheetConfig(
        entity_type="consumable",
        column_map={
            "item code": "item_code",
            "item name": "name",
            "category": "category",
            "manufacturer": "manufacturer",
            "compatible model number": "compatible_model_number",
            "unit": "unit",
            "opening quantity": "opening_quantity",
            "minimum stock": "min_level",
            "reorder level": "reorder_level",
            "status": "status",
            "data source": "data_source",
            "classification status": "classification_status",
            "environment": "environment",
        },
        required=["name", "category"],
        business_key_fields=["item_code", "name"],
    ),
    "device master": SheetConfig(
        entity_type="asset",
        column_map={
            "asset number": "asset_number",
            "device name": "device_name",
            "category": "category",
            "manufacturer": "manufacturer",
            "model number": "model_number",
            "serial number": "serial_number",
            "hospital code": "hospital_code",
            "department code": "department_code",
            "location code": "location_code",
            "status": "status",
            "purchase date": "purchase_date",
            "installation date": "installation_date",
            "operational date": "operational_date",
            "criticality": "criticality",
        },
        required=["asset_number", "manufacturer", "model_number", "hospital_code", "status"],
        business_key_fields=["asset_number"],
    ),
    "branch device assignments": SheetConfig(
        entity_type="branch_device_assignment",
        column_map={
            "assignment id": "external_id",
            "branch name": "branch_name",
            "catalog record name": "catalog_record_name",
            "manufacturer": "manufacturer",
            "model number": "model_number",
            "known quantity": "known_quantity",
            "assignment status": "assignment_status",
            "inventory mapping status": "inventory_mapping_status",
            "asset identification status": "asset_identification_status",
            "source name": "source_name",
            "data quality flag": "data_quality_flag",
            "notes": "notes",
            "environment": "environment",
        },
        required=["branch_name", "assignment_status", "source_name"],
        business_key_fields=["branch_name", "source_name"],
    ),
    # ---- Module 14: Vendor / Contract / PM / Calibration sheets ----
    # Column headers taken verbatim from work/phase0-spreadsheets/build_phase0_templates.mjs
    # (the authoritative Phase 0 template source). Several template columns have no
    # matching field on the Phase C models built in Modules 02-05 (e.g. "OEM Relationship",
    # "Procedure Document Version", "Assigned Role", "Scope"/"Exclusions") — those are
    # simply omitted from column_map so they stay visible in raw_data for audit but are
    # never fabricated onto a column that doesn't exist. See PROJECT_STATUS.md Module 14.
    "vendor master": SheetConfig(
        entity_type="vendor",
        column_map={
            "vendor id": "external_id",
            "vendor name": "name",
            "vendor category": "vendor_type",
            "status": "status",
            "email": "email",
            "phone": "phone",
            "city": "city",
            "sla response hours": "response_sla_hours",
        },
        required=["name", "vendor_type", "status"],
        business_key_fields=["name"],
    ),
    "vendor contacts": SheetConfig(
        entity_type="vendor_contact",
        column_map={
            "vendor id": "vendor_external_id",
            "contact name": "name",
            "email": "email",
            "phone": "phone",
            "role": "role",
        },
        required=["name", "vendor_external_id"],
        business_key_fields=["vendor_external_id", "name"],
    ),
    "pm schedule": SheetConfig(
        entity_type="pm_schedule",
        column_map={
            "asset number": "asset_number",
            "frequency": "frequency",
            "next due date": "next_due_date",
            "status": "status",
        },
        required=["asset_number", "frequency"],
        business_key_fields=["asset_number"],
    ),
    "pm records": SheetConfig(
        entity_type="pm_record",
        column_map={
            "asset number": "asset_number",
            "performed date": "performed_date",
            "result": "result",
            "performed by": "performed_by",
            "next due date": "next_due_date",
            "notes": "notes",
        },
        required=["asset_number", "performed_date"],
        business_key_fields=["asset_number", "performed_date"],
    ),
    "calibration schedule": SheetConfig(
        entity_type="calibration_schedule",
        column_map={
            "asset number": "asset_number",
            "frequency": "frequency",
            "next due date": "next_due_date",
            "status": "status",
        },
        required=["asset_number", "frequency"],
        business_key_fields=["asset_number"],
    ),
    "calibration records": SheetConfig(
        entity_type="calibration_record",
        column_map={
            "asset number": "asset_number",
            "performed date": "performed_date",
            "result": "result",
            "certificate number": "certificate_number",
            "provider/vendor id": "calibration_agency",
            "next due date": "next_due_date",
            "notes": "notes",
        },
        required=["asset_number", "performed_date"],
        business_key_fields=["asset_number", "performed_date"],
    ),
    "amc contracts": SheetConfig(
        entity_type="amc_contract",
        column_map={
            "contract number": "contract_number",
            "asset number": "asset_number",
            "vendor id": "vendor_external_id",
            "start date": "start_date",
            "end date": "end_date",
            "status": "status",
            "sla response hours": "response_time_hours",
            "cost": "contract_value",
        },
        required=["contract_number", "asset_number"],
        business_key_fields=["contract_number"],
    ),
    "cmc contracts": SheetConfig(
        entity_type="cmc_contract",
        column_map={
            "contract number": "contract_number",
            "asset number": "asset_number",
            "vendor id": "vendor_external_id",
            "start date": "start_date",
            "end date": "end_date",
            "status": "status",
            "sla response hours": "response_time_hours",
            "cost": "contract_value",
        },
        required=["contract_number", "asset_number"],
        business_key_fields=["contract_number"],
    ),
    "warranty": SheetConfig(
        entity_type="warranty",
        column_map={
            "warranty id": "warranty_number",
            "asset number": "asset_number",
            "vendor id": "vendor_external_id",
            "start date": "start_date",
            "end date": "end_date",
            "status": "status",
            "terms": "coverage_notes",
        },
        required=["asset_number"],
        business_key_fields=["warranty_number", "asset_number"],
    ),
    # ---- Import & Export Enhancement: remaining Part A datasets ----
    # Column headers for "department master"/"location master" are verbatim from
    # build_phase0_templates.mjs, same as every sheet above. "inventory locations",
    # "spare parts", "service history", and "document metadata" have no Phase 0
    # template (their tables were built in Modules 12/13/07, after Phase 0) — headers
    # below are newly defined for this task, following the same naming convention as
    # their siblings, not sourced from a pre-existing file.
    "department master": SheetConfig(
        entity_type="department",
        column_map={
            "department code": "code",
            "department name": "name",
            "hospital code": "hospital_code",
            "status": "status",
        },
        required=["name", "hospital_code"],
        business_key_fields=["hospital_code", "code", "name"],
    ),
    "location master": SheetConfig(
        entity_type="location",
        column_map={
            "location code": "code",
            "location name": "name",
            "hospital code": "hospital_code",
            "department code": "department_code",
            "room/bed": "room",
            "status": "status",
        },
        required=["name", "hospital_code", "department_code"],
        business_key_fields=["hospital_code", "department_code", "code", "name"],
    ),
    "inventory locations": SheetConfig(
        entity_type="inventory_location",
        column_map={
            "location code": "code",
            "location name": "name",
            "hospital code": "hospital_code",
            "kind": "kind",
        },
        required=["name", "hospital_code"],
        business_key_fields=["hospital_code", "code", "name"],
    ),
    "spare parts": SheetConfig(
        entity_type="inventory_item",
        column_map={
            "part number": "item_code",
            "part name": "name",
            "category": "category",
            "manufacturer": "supplier",
            "unit": "unit",
            "minimum stock": "min_level",
            "reorder level": "reorder_level",
        },
        required=["name"],
        business_key_fields=["item_code", "name"],
    ),
    "service history": SheetConfig(
        entity_type="service_visit",
        column_map={
            "asset number": "asset_number",
            "event date": "visit_date",
            "service type": "visit_type",
            "complaint": "complaint",
            "root cause": "root_cause",
            "action taken": "action_taken",
            "parts used": "parts_used",
            "vendor id": "vendor_external_id",
            "downtime hours": "downtime_hours",
            "cost": "cost",
            "resolution status": "resolution_status",
        },
        required=["asset_number"],
        business_key_fields=["asset_number", "visit_date"],
    ),
    # Incident history — matches the user's own `incident_history_template.xlsx`
    # exactly: 18 columns (`Unit*`/`Issue Description*` required, the trailing `*`
    # stripped by `_norm()` same as every other required-marker header in this
    # file), sheet tab literally named "Sheet" — parser.parse_workbook falls back
    # to header-overlap matching (best_fit_config) for any .xlsx sheet whose tab
    # name doesn't match a known config, so a generically-named tab like this one
    # routes correctly without the upload needing to be renamed first.
    # detect_formulas=True: a formula cell (e.g. a stray cross-row SUM() left in a
    # per-incident Po Cost/Po Amount cell) must be flagged in the import preview,
    # never silently trusted — see import_service.py's incident row handling and
    # parser.py's _formula_fields_by_row.
    "incident history": SheetConfig(
        entity_type="incident",
        column_map={
            "item name": "device_name",
            "date": "incident_date",
            "unit": "branch_code",
            "make": "manufacturer",
            "vendor": "vendor_name",
            "issue description": "description",
            "cer no": "cer_number",
            "indent no": "indent_number",
            "po no": "po_number",
            "po closure date": "po_closure_date",
            "status": "status",
            "po cost - excl. gst": "po_cost_excl_gst",
            "po gst %": "po_gst_percent",
            "po amount": "po_amount",
            "vendor estimate cost - excl. gst": "vendor_estimate_cost_excl_gst",
            "vendor estimate gst %": "vendor_estimate_gst_percent",
            "vendor estimate cost - incl. gst": "vendor_estimate_cost_incl_gst",
            "remark": "remark",
        },
        required=["branch_code", "description"],
        business_key_fields=["branch_code", "description", "incident_date"],
        detect_formulas=True,
    ),
    "document metadata": SheetConfig(
        entity_type="document",
        column_map={
            "document title": "title",
            "document type": "document_type",
            "manufacturer": "manufacturer",
            "model number": "model_number",
            "asset number": "asset_number",
            "vendor id": "vendor_external_id",
            "effective date": "effective_date",
            "expiry date": "expiry_date",
            "access level": "access_level",
        },
        required=["title", "document_type"],
        business_key_fields=["title"],
    ),
}

# Processing order matters: branches and device models must exist before
# branch-device-assignments and assets can resolve their references. Departments must
# exist before locations (Location.department_id is required); vendors must be
# imported before anything that links to them by "Vendor ID" (vendor contacts, AMC/CMC
# contracts, warranties, service history, document metadata); assets must exist before
# PM/calibration/contract/service-history sheets that reference them by asset number.
SHEET_IMPORT_ORDER = [
    "hospital master",
    "department master",
    "location master",
    "inventory locations",
    "device model master",
    "consumables",
    "spare parts",
    "device master",
    "branch device assignments",
    "vendor master",
    "vendor contacts",
    "pm schedule",
    "pm records",
    "calibration schedule",
    "calibration records",
    "amc contracts",
    "cmc contracts",
    "warranty",
    "service history",
    "incident history",
    "document metadata",
]


def normalize_sheet_name(name: str) -> str:
    return name.strip().lower().replace("_", " ")


def match_config(sheet_name: str) -> SheetConfig | None:
    return SHEET_CONFIGS.get(normalize_sheet_name(sheet_name))


# Minimum normalized-header overlap required before `best_fit_config` will trust a
# header-based guess — mirrors `parser.py`'s MIN_HEADER_MATCHES for the same reason:
# a couple of coincidentally-shared column names (e.g. "Status", "Notes") shouldn't be
# enough to misroute a whole sheet into the wrong entity type.
MIN_BEST_FIT_MATCHES = 2


def best_fit_config(headers: list[str]) -> SheetConfig | None:
    """Guess a sheet's `SheetConfig` purely from its header row, by normalized-column
    overlap — used for CSV uploads, which (unlike an .xlsx workbook's sheet tabs) have
    no sheet name to match against `match_config()`. Only ever a fallback: CSV import
    first tries the uploaded filename through the exact same `match_config()` path
    every .xlsx sheet uses (see `imports/parser.py::parse_csv`), and only calls this
    when that fails — so a well-named file (e.g. `vendor_master.csv`, matching the
    existing template-naming convention) never depends on header-guessing at all."""
    normalized_headers = {_norm(h) for h in headers if h}
    if not normalized_headers:
        return None

    best_config: SheetConfig | None = None
    best_score = 0
    for config in SHEET_CONFIGS.values():
        score = len(normalized_headers & set(config.column_map.keys()))
        if score > best_score:
            best_score = score
            best_config = config

    return best_config if best_score >= MIN_BEST_FIT_MATCHES else None
