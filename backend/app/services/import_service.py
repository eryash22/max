from dataclasses import dataclass, field as dc_field
from datetime import datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..config import settings
from ..imports.mapping import SHEET_CONFIGS, SHEET_IMPORT_ORDER, normalize_sheet_name
from ..imports.parser import checksum_bytes, parse_csv, parse_workbook
from ..imports.validators import business_key as _bk, map_row, validate_row
from ..models.contracts import AmcContract, AmcContractAsset, CmcContract, CmcContractAsset, Warranty
from ..models.device import Asset, BranchDeviceAssignment, DeviceCategory, DeviceModel
from ..models.documents import Document
from ..models.hospital import Branch, Department, DepartmentMaster, Location
from ..models.identity import User
from ..models.imports import ImportApproval, ImportError as ImportErrorModel, ImportJob, ImportRow
from ..models.incident import Incident, ServiceVisit
from ..models.inventory import Consumable, InventoryItem, InventoryLocation
from ..models.maintenance import CalibrationRecord, CalibrationSchedule, PMRecord, PMSchedule
from ..models.review import ReviewItem
from . import qr_service
from ..models.vendor import Vendor, VendorContact
from .document_service import _safe_filename

DEFAULT_ASSIGNMENT_STATUS = "PENDING_ASSET_IDENTIFICATION"
NULL_DATA_QUALITY_FLAGS = (None, "NONE")

# Frequency synonyms seen in the Phase 0 templates' Reference Values sheet
# (SEMI_ANNUAL/ANNUAL) that don't match the vocabulary Modules 02A/03A actually
# implemented (HALF_YEARLY/YEARLY) — normalized, never fabricated or blocked;
# anything else passes through unchanged, same leniency as every other sheet's
# status/category free-text fields in this import pipeline.
_FREQUENCY_SYNONYMS = {"SEMI_ANNUAL": "HALF_YEARLY", "SEMI-ANNUAL": "HALF_YEARLY", "ANNUAL": "YEARLY"}


class ImportServiceError(Exception):
    pass


def _storage_dir() -> Path:
    path = settings.file_storage_path / "imports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_job(db: Session, *, file_bytes: bytes, file_name: str, uploaded_by: str | None) -> ImportJob:
    job = ImportJob(file_name=file_name, status="VALIDATING", uploaded_by=uploaded_by)
    db.add(job)
    db.flush()

    checksum = checksum_bytes(file_bytes)
    job.file_checksum = checksum
    # Sanitize the on-disk filename the same way document_service.create_version does
    # (job.id prefix alone was an accidental, fragile mitigation — see _safe_filename's
    # own docstring for the path-traversal class of bug this closes). `file_name`
    # itself (unsanitized) is still what's stored on the job row for display.
    saved_path = _storage_dir() / f"{job.id}_{_safe_filename(file_name)}"
    saved_path.write_bytes(file_bytes)

    suffix = saved_path.suffix.lower()
    if suffix == ".csv":
        display_name = Path(file_name).stem
        parsed_sheets = parse_csv(saved_path, display_name=display_name)
    else:
        parsed_sheets = parse_workbook(saved_path)
    job.sheet_names = [s.name for s in parsed_sheets]
    job.column_mapping = {s.name: (s.config.entity_type if s.config else None) for s in parsed_sheets}

    seen_keys: dict[str, set[str]] = {}
    total_rows = 0
    ready_count = 0
    error_row_count = 0
    duplicate_count = 0
    unsupported_sheets: list[str] = []
    # Loaded once per job (not per row) — the existing device-category taxonomy
    # (models.device.DeviceCategory, seeded server-side per migration 0008) is what
    # "the existing category convention" means for a device_model row's free-text
    # category field; an unmatched value is a WARNING (still importable, matches this
    # pipeline's existing leniency on free-text status/category fields elsewhere), not
    # a hard block, and is never auto-created as a new category.
    known_category_names = {c.name.strip().lower() for c in db.query(DeviceCategory).all()}

    for sheet in parsed_sheets:
        if sheet.config is None:
            if sheet.rows:
                unsupported_sheets.append(sheet.name)
                db.add(ImportErrorModel(
                    job_id=job.id, sheet_name=sheet.name, row_number=None, field=None, severity="INFO",
                    message="Sheet is not mapped to a Phase 1 entity; rows are staged for audit only and will not be imported.",
                ))
            continue

        config = sheet.config
        seen_keys.setdefault(config.entity_type, set())

        for parsed_row in sheet.rows:
            total_rows += 1
            mapped = map_row(config, parsed_row.raw)
            key = _bk(config, mapped)
            issues = validate_row(config, mapped)

            if config.entity_type == "device_model":
                category = mapped.get("category")
                if category and str(category).strip().lower() not in known_category_names:
                    issues.append((
                        "category",
                        f"Category '{category}' does not match an existing device category; "
                        "it will be stored as free text and won't link to the catalog taxonomy",
                        "WARNING",
                    ))

            if config.entity_type == "incident" and not any(sev == "ERROR" for _, _, sev in issues):
                issues.extend(_validate_incident_row(db, mapped))

            is_duplicate = bool(key) and key in seen_keys[config.entity_type]
            if key:
                seen_keys[config.entity_type].add(key)

            has_error = any(sev == "ERROR" for _, _, sev in issues)
            if has_error:
                outcome = "ERROR"
                error_row_count += 1
            elif is_duplicate:
                outcome = "DUPLICATE_CANDIDATE"
                duplicate_count += 1
            else:
                outcome = "READY"
                ready_count += 1

            db.add(ImportRow(
                job_id=job.id, sheet_name=sheet.name, row_number=parsed_row.row_number,
                raw_data=parsed_row.raw, mapped_data=mapped, business_key=key, outcome=outcome,
                entity_type=config.entity_type, is_duplicate_candidate=is_duplicate,
            ))
            for field_name, message, severity in issues:
                db.add(ImportErrorModel(
                    job_id=job.id, sheet_name=sheet.name, row_number=parsed_row.row_number,
                    field=field_name, severity=severity, message=message,
                ))

    job.status = "READY_FOR_REVIEW"
    job.summary = {
        "total_rows": total_rows,
        "ready": ready_count,
        "duplicate_candidates": duplicate_count,
        "errors": error_row_count,
        "unsupported_sheets": unsupported_sheets,
    }
    audit_record(db, user_id=uploaded_by, action="UPLOAD", entity="import_job", entity_id=job.id, new_value=job.summary, import_job_id=job.id)
    db.commit()
    db.refresh(job)
    return job


# ---- Entity resolution/upsert handlers used at approval time ----

def _upsert_branch(db: Session, mapped: dict) -> tuple[str, str]:
    name = mapped.get("name")
    code = mapped.get("code")
    existing = None
    if code:
        existing = db.query(Branch).filter(Branch.code == code).first()
    if existing is None and name:
        existing = db.query(Branch).filter(Branch.name == name).first()

    fields = ["code", "status", "address", "city", "state", "region", "unit_head", "inventory_mapping_status", "environment"]
    if existing:
        for f in fields:
            value = mapped.get(f)
            if value is not None:
                setattr(existing, f, value)
        return existing.id, "UPDATE"

    obj = Branch(
        name=name, code=code, status=mapped.get("status") or "ACTIVE", address=mapped.get("address"),
        city=mapped.get("city"), state=mapped.get("state"), region=mapped.get("region"),
        unit_head=mapped.get("unit_head"), inventory_mapping_status=mapped.get("inventory_mapping_status") or "Inventory Pending",
        environment=mapped.get("environment") or "DEVELOPMENT / DEMO DATA",
    )
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _resolve_category_id(db: Session, category_name: str | None) -> str | None:
    """Case-insensitive match against the existing DeviceCategory taxonomy — never
    creates a new category row (see create_job's device_model category WARNING for
    the companion validation-time check)."""
    if not category_name:
        return None
    existing = (
        db.query(DeviceCategory)
        .filter(DeviceCategory.name.ilike(category_name.strip()))
        .first()
    )
    return existing.id if existing else None


def _upsert_device_model(db: Session, mapped: dict) -> tuple[str, str]:
    name = mapped.get("name")
    manufacturer = mapped.get("manufacturer")
    model_number = mapped.get("model_number")
    category_id = _resolve_category_id(db, mapped.get("category"))

    query = db.query(DeviceModel).filter(DeviceModel.name == name)
    query = query.filter(DeviceModel.manufacturer == manufacturer) if manufacturer else query.filter(DeviceModel.manufacturer.is_(None))
    if model_number:
        query = query.filter(DeviceModel.model_number == model_number)
    existing = query.first()

    fields = ["manufacturer", "model_number", "category", "model_status", "firmware_version",
              "criticality_default", "known_quantity", "notes", "data_source",
              "model_information_status", "catalog_match_status", "environment"]
    if existing:
        for f in fields:
            value = mapped.get(f)
            if value is not None:
                setattr(existing, f, value)
        if category_id is not None:
            existing.category_id = category_id
        return existing.id, "UPDATE"

    obj = DeviceModel(
        name=name, manufacturer=manufacturer, model_number=model_number,
        category=mapped.get("category") or "Uncategorized", category_id=category_id,
        model_status=mapped.get("model_status"), firmware_version=mapped.get("firmware_version"),
        criticality_default=mapped.get("criticality_default"), known_quantity=mapped.get("known_quantity"),
        notes=mapped.get("notes"), data_source=mapped.get("data_source"),
        model_information_status=mapped.get("model_information_status") or "MODEL_NOT_PROVIDED",
        catalog_match_status=mapped.get("catalog_match_status") or "UNMATCHED",
        environment=mapped.get("environment") or "DEVELOPMENT / DEMO DATA",
    )
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _upsert_consumable(db: Session, mapped: dict) -> tuple[str, str]:
    item_code = mapped.get("item_code")
    name = mapped.get("name")
    existing = None
    if item_code:
        existing = db.query(Consumable).filter(Consumable.item_code == item_code).first()
    if existing is None and name:
        existing = db.query(Consumable).filter(Consumable.name == name).first()

    fields = ["category", "manufacturer", "compatible_model_number", "unit", "opening_quantity", "min_level",
              "reorder_level", "status", "data_source", "classification_status", "environment"]
    if existing:
        for f in fields:
            value = mapped.get(f)
            if value is not None:
                setattr(existing, f, value)
        return existing.id, "UPDATE"

    obj = Consumable(
        item_code=item_code or f"PENDING-{name}", name=name, category=mapped.get("category"),
        manufacturer=mapped.get("manufacturer"), compatible_model_number=mapped.get("compatible_model_number"),
        unit=mapped.get("unit"), opening_quantity=mapped.get("opening_quantity"),
        min_level=mapped.get("min_level"), reorder_level=mapped.get("reorder_level"),
        status=mapped.get("status") or "ACTIVE", data_source=mapped.get("data_source"),
        classification_status=mapped.get("classification_status"),
        environment=mapped.get("environment") or "DEVELOPMENT / DEMO DATA",
    )
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _resolve_device_model_id(db: Session, *, manufacturer: str | None, model_number: str | None, name: str | None) -> str | None:
    if not name and not model_number:
        return None
    query = db.query(DeviceModel)
    if manufacturer:
        query = query.filter(DeviceModel.manufacturer == manufacturer)
    if model_number:
        query = query.filter(DeviceModel.model_number == model_number)
    elif name:
        query = query.filter(DeviceModel.name == name)
    match = query.first()
    return match.id if match else None


def _import_asset(db: Session, row: ImportRow, mapped: dict, context: dict | None = None) -> tuple[str, str] | None:
    branch = None
    hospital_code = mapped.get("hospital_code")
    if hospital_code:
        branch = db.query(Branch).filter(Branch.code == hospital_code).first()
    if branch is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="hospital_code", severity="ERROR",
                                 message="Unresolved branch: import/confirm the hospital master record first."))
        return None

    device_model_id = _resolve_device_model_id(
        db, manufacturer=mapped.get("manufacturer"), model_number=mapped.get("model_number"), name=mapped.get("device_name")
    )
    if device_model_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="model_number", severity="ERROR",
                                 message="Unresolved device model: import/confirm the device model master record first."))
        return None

    existing = db.query(Asset).filter(Asset.asset_number == mapped["asset_number"]).first()
    if existing:
        for f in ["serial_number", "status", "criticality", "purchase_date", "installation_date"]:
            value = mapped.get(f)
            if value is not None:
                setattr(existing, f, value)
        return existing.id, "UPDATE"

    obj = Asset(
        asset_number=mapped["asset_number"], serial_number=mapped.get("serial_number"),
        device_model_id=device_model_id, branch_id=branch.id, status=mapped.get("status") or "Pending",
        criticality=mapped.get("criticality"), purchase_date=mapped.get("purchase_date"),
        installation_date=mapped.get("installation_date"), environment="DEVELOPMENT / DEMO DATA",
    )
    db.add(obj)
    # Device QR Code Management System — imported physical Assets get a stable QR
    # identity too, same as every other Asset creation workflow.
    qr_service.ensure_qr_token(db, obj)
    db.flush()
    return obj.id, "INSERT"


def _import_branch_device_assignment(db: Session, row: ImportRow, mapped: dict, context: dict | None = None) -> tuple[str, str] | None:
    branch_name = mapped.get("branch_name")
    branch = db.query(Branch).filter(Branch.name == branch_name).first()
    if branch is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="branch_name", severity="ERROR",
                                 message=f"Unresolved branch '{branch_name}': import the hospital master first."))
        return None

    device_model_id = _resolve_device_model_id(
        db, manufacturer=mapped.get("manufacturer"), model_number=mapped.get("model_number"),
        name=mapped.get("catalog_record_name"),
    )

    source_name = mapped.get("source_name")
    existing = db.query(BranchDeviceAssignment).filter(
        BranchDeviceAssignment.branch_id == branch.id, BranchDeviceAssignment.source_name == source_name
    ).first()

    assignment_status = mapped.get("assignment_status") or DEFAULT_ASSIGNMENT_STATUS
    data_quality_flag = mapped.get("data_quality_flag")

    if existing:
        existing.device_model_id = device_model_id or existing.device_model_id
        existing.known_quantity = mapped.get("known_quantity") if mapped.get("known_quantity") is not None else existing.known_quantity
        existing.assignment_status = assignment_status
        existing.inventory_mapping_status = mapped.get("inventory_mapping_status") or existing.inventory_mapping_status
        existing.asset_identification_status = mapped.get("asset_identification_status") or existing.asset_identification_status
        existing.data_quality_flag = data_quality_flag
        existing.notes = mapped.get("notes") or existing.notes
        obj, action = existing, "UPDATE"
    else:
        obj = BranchDeviceAssignment(
            branch_id=branch.id, device_model_id=device_model_id, source_name=source_name,
            known_quantity=mapped.get("known_quantity"), assignment_status=assignment_status,
            inventory_mapping_status=mapped.get("inventory_mapping_status"),
            asset_identification_status=mapped.get("asset_identification_status"),
            data_quality_flag=data_quality_flag, notes=mapped.get("notes"),
            environment=mapped.get("environment") or "DEVELOPMENT / DEMO DATA",
        )
        db.add(obj)
        db.flush()
        action = "INSERT"

    needs_review = assignment_status != DEFAULT_ASSIGNMENT_STATUS or data_quality_flag not in NULL_DATA_QUALITY_FLAGS
    if needs_review:
        already_open = db.query(ReviewItem).filter(
            ReviewItem.source_type == "branch_device_assignment", ReviewItem.source_id == obj.id,
            ReviewItem.status == "OPEN",
        ).first()
        if already_open is None:
            reason_code = data_quality_flag if data_quality_flag not in NULL_DATA_QUALITY_FLAGS else assignment_status
            db.add(ReviewItem(
                source_type="branch_device_assignment", source_id=obj.id, reason_code=reason_code,
                detail=mapped.get("notes"), import_job_id=row.job_id,
            ))

    return obj.id, action


# ---- Module 14: Vendor / Contract / PM / Calibration handlers ----

def _normalize_frequency(value: object) -> str | None:
    if not value:
        return None
    upper = str(value).strip().upper().replace(" ", "_")
    return _FREQUENCY_SYNONYMS.get(upper, upper)


def _status_to_is_active(value: object) -> bool:
    if value is None:
        return True
    return str(value).strip().upper() == "ACTIVE"


def _resolve_asset_id_by_number(db: Session, asset_number: object) -> str | None:
    if not asset_number:
        return None
    asset = db.query(Asset).filter(Asset.asset_number == asset_number).first()
    return asset.id if asset else None


def _resolve_vendor_id(db: Session, external_id: object, context: dict) -> str | None:
    """Resolve a sheet's "Vendor ID" value to a real `Vendor.id`.

    `Vendor` has no external-code column (see Module 04A's documented schema
    limitation), so this first checks the same-job vendor-master external-id
    index built in `approve_job()` (populated after the "vendor master" sheet
    is processed, since `SHEET_IMPORT_ORDER` guarantees it runs first), then
    falls back to an exact match against `Vendor.name` for the case where a
    prior job already imported the vendor and the value happens to equal its
    name. Returns `None` (never raises) if neither resolves — callers decide
    whether that's a hard block or a best-effort warning based on whether
    their target column is nullable.
    """
    if not external_id:
        return None
    index = context.get("vendor_external_index", {})
    if external_id in index:
        return index[external_id]
    match = db.query(Vendor).filter(Vendor.name == external_id).first()
    return match.id if match else None


def _resolve_user_id_by_email(db: Session, email: object) -> str | None:
    if not email:
        return None
    user = db.query(User).filter(User.email == email).first()
    return user.id if user else None


def _upsert_vendor(db: Session, mapped: dict) -> tuple[str, str]:
    name = mapped.get("name")
    existing = db.query(Vendor).filter(Vendor.name == name).first()
    fields = ["vendor_type", "status", "email", "phone", "city", "response_sla_hours"]
    if existing:
        for f in fields:
            value = mapped.get(f)
            if value is not None:
                setattr(existing, f, value)
        return existing.id, "UPDATE"

    obj = Vendor(
        name=name, vendor_type=mapped.get("vendor_type"), status=mapped.get("status") or "ACTIVE",
        email=mapped.get("email"), phone=mapped.get("phone"), city=mapped.get("city"),
        response_sla_hours=mapped.get("response_sla_hours"), environment="DEVELOPMENT / DEMO DATA",
    )
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _import_vendor_contact(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    vendor_id = _resolve_vendor_id(db, mapped.get("vendor_external_id"), context)
    if vendor_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="vendor_external_id", severity="ERROR",
                                 message="Unresolved vendor: import the vendor master record first."))
        return None

    name = mapped.get("name")
    existing = db.query(VendorContact).filter(
        VendorContact.vendor_id == vendor_id, VendorContact.name == name
    ).first()
    if existing:
        for f in ["role", "email", "phone"]:
            value = mapped.get(f)
            if value is not None:
                setattr(existing, f, value)
        return existing.id, "UPDATE"

    obj = VendorContact(vendor_id=vendor_id, name=name, role=mapped.get("role"),
                         email=mapped.get("email"), phone=mapped.get("phone"))
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _import_pm_schedule(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    asset_id = _resolve_asset_id_by_number(db, mapped.get("asset_number"))
    if asset_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="asset_number", severity="ERROR",
                                 message="Unresolved asset: import the device master record first."))
        return None

    frequency = _normalize_frequency(mapped.get("frequency"))
    next_due_date = mapped.get("next_due_date")
    is_active = _status_to_is_active(mapped.get("status"))

    existing = db.query(PMSchedule).filter(PMSchedule.asset_id == asset_id).first()
    if existing:
        if frequency is not None:
            existing.frequency = frequency
        if next_due_date is not None:
            existing.next_due_date = next_due_date
        existing.is_active = is_active
        return existing.id, "UPDATE"

    obj = PMSchedule(asset_id=asset_id, frequency=frequency, next_due_date=next_due_date, is_active=is_active)
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _import_pm_record(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    asset_id = _resolve_asset_id_by_number(db, mapped.get("asset_number"))
    if asset_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="asset_number", severity="ERROR",
                                 message="Unresolved asset: import the device master record first."))
        return None

    performed_date = mapped.get("performed_date")
    existing = db.query(PMRecord).filter(
        PMRecord.asset_id == asset_id, PMRecord.performed_date == performed_date
    ).first()

    schedule = db.query(PMSchedule).filter(PMSchedule.asset_id == asset_id).first()
    engineer_id = _resolve_user_id_by_email(db, mapped.get("performed_by"))
    # "Result*" has no single matching PMRecord column — the closest existing free-text
    # outcome field is functional_check_result (documented judgment call, see Module 14
    # notes in PROJECT_STATUS.md). "Performed By*" resolves to engineer_id when it matches
    # a real user's email; otherwise it's preserved in notes rather than silently dropped.
    notes = mapped.get("notes")
    performed_by_raw = mapped.get("performed_by")
    if engineer_id is None and performed_by_raw:
        notes = f"{notes}\nPerformed by: {performed_by_raw}" if notes else f"Performed by: {performed_by_raw}"

    if existing:
        if mapped.get("result") is not None:
            existing.functional_check_result = mapped.get("result")
        if mapped.get("next_due_date") is not None:
            existing.next_due_date = mapped.get("next_due_date")
        if notes is not None:
            existing.notes = notes
        if engineer_id is not None:
            existing.engineer_id = engineer_id
        return existing.id, "UPDATE"

    obj = PMRecord(
        schedule_id=schedule.id if schedule else None, asset_id=asset_id, engineer_id=engineer_id,
        performed_date=performed_date, functional_check_result=mapped.get("result"),
        next_due_date=mapped.get("next_due_date"), notes=notes,
    )
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _import_calibration_schedule(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    asset_id = _resolve_asset_id_by_number(db, mapped.get("asset_number"))
    if asset_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="asset_number", severity="ERROR",
                                 message="Unresolved asset: import the device master record first."))
        return None

    frequency = _normalize_frequency(mapped.get("frequency"))
    next_due_date = mapped.get("next_due_date")
    is_active = _status_to_is_active(mapped.get("status"))

    existing = db.query(CalibrationSchedule).filter(CalibrationSchedule.asset_id == asset_id).first()
    if existing:
        if frequency is not None:
            existing.frequency = frequency
        if next_due_date is not None:
            existing.next_due_date = next_due_date
        existing.is_active = is_active
        return existing.id, "UPDATE"

    obj = CalibrationSchedule(asset_id=asset_id, frequency=frequency, next_due_date=next_due_date, is_active=is_active)
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _import_calibration_record(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    asset_id = _resolve_asset_id_by_number(db, mapped.get("asset_number"))
    if asset_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="asset_number", severity="ERROR",
                                 message="Unresolved asset: import the device master record first."))
        return None

    performed_date = mapped.get("performed_date")
    result = mapped.get("result")
    # "Provider/Vendor ID" maps to calibration_agency as free text, not a resolved Vendor
    # FK — CalibrationRecord has no vendor_id column (documented in Module 03A).
    pass_fail = str(result).strip().upper() if result and str(result).strip().upper() in ("PASS", "FAIL") else None

    existing = db.query(CalibrationRecord).filter(
        CalibrationRecord.asset_id == asset_id, CalibrationRecord.performed_date == performed_date
    ).first()
    schedule = db.query(CalibrationSchedule).filter(CalibrationSchedule.asset_id == asset_id).first()

    if existing:
        for f, value in [("result", result), ("certificate_number", mapped.get("certificate_number")),
                          ("calibration_agency", mapped.get("calibration_agency")),
                          ("next_due_date", mapped.get("next_due_date")), ("notes", mapped.get("notes"))]:
            if value is not None:
                setattr(existing, f, value)
        if pass_fail is not None:
            existing.pass_fail = pass_fail
        return existing.id, "UPDATE"

    obj = CalibrationRecord(
        schedule_id=schedule.id if schedule else None, asset_id=asset_id, performed_date=performed_date,
        certificate_number=mapped.get("certificate_number"), calibration_agency=mapped.get("calibration_agency"),
        result=result, pass_fail=pass_fail, next_due_date=mapped.get("next_due_date"), notes=mapped.get("notes"),
    )
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _import_amc_or_cmc_contract(db: Session, row: ImportRow, mapped: dict, context: dict,
                                 *, contract_model, asset_link_model, asset_link_field: str) -> tuple[str, str] | None:
    asset_id = _resolve_asset_id_by_number(db, mapped.get("asset_number"))
    if asset_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="asset_number", severity="ERROR",
                                 message="Unresolved asset: import the device master record first."))
        return None

    vendor_id = _resolve_vendor_id(db, mapped.get("vendor_external_id"), context)
    if mapped.get("vendor_external_id") and vendor_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="vendor_external_id", severity="WARNING",
                                 message="Unresolved vendor; contract imported without a vendor link."))

    contract_number = mapped.get("contract_number")
    existing = db.query(contract_model).filter(contract_model.contract_number == contract_number).first()
    fields = {
        "vendor_id": vendor_id, "start_date": mapped.get("start_date"), "end_date": mapped.get("end_date"),
        "status": mapped.get("status"), "response_time_hours": mapped.get("response_time_hours"),
        "contract_value": mapped.get("contract_value"),
    }
    if existing:
        for f, value in fields.items():
            if value is not None:
                setattr(existing, f, value)
        contract = existing
        action = "UPDATE"
    else:
        contract = contract_model(
            contract_number=contract_number, vendor_id=vendor_id, start_date=mapped.get("start_date"),
            end_date=mapped.get("end_date"), status=mapped.get("status") or "UNKNOWN",
            response_time_hours=mapped.get("response_time_hours"), contract_value=mapped.get("contract_value"),
            environment="DEVELOPMENT / DEMO DATA",
        )
        db.add(contract)
        db.flush()
        action = "INSERT"

    already_linked = db.query(asset_link_model).filter(
        getattr(asset_link_model, asset_link_field) == contract.id, asset_link_model.asset_id == asset_id
    ).first()
    if already_linked is None:
        db.add(asset_link_model(**{asset_link_field: contract.id, "asset_id": asset_id}))

    return contract.id, action


def _import_amc_contract(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    return _import_amc_or_cmc_contract(db, row, mapped, context, contract_model=AmcContract,
                                        asset_link_model=AmcContractAsset, asset_link_field="amc_contract_id")


def _import_cmc_contract(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    return _import_amc_or_cmc_contract(db, row, mapped, context, contract_model=CmcContract,
                                        asset_link_model=CmcContractAsset, asset_link_field="cmc_contract_id")


def _import_warranty(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    asset_id = _resolve_asset_id_by_number(db, mapped.get("asset_number"))
    if asset_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="asset_number", severity="ERROR",
                                 message="Unresolved asset: import the device master record first."))
        return None

    vendor_id = _resolve_vendor_id(db, mapped.get("vendor_external_id"), context)
    if mapped.get("vendor_external_id") and vendor_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="vendor_external_id", severity="WARNING",
                                 message="Unresolved vendor; warranty imported without a vendor link."))

    # Warranty is one-per-asset by design (Module 05A) — dedup on asset_id, not
    # warranty_number, so a re-import of the same asset's warranty updates it in place.
    existing = db.query(Warranty).filter(Warranty.asset_id == asset_id).first()
    fields = {
        "vendor_id": vendor_id, "warranty_number": mapped.get("warranty_number"),
        "start_date": mapped.get("start_date"), "end_date": mapped.get("end_date"),
        "coverage_notes": mapped.get("coverage_notes"), "status": mapped.get("status"),
    }
    if existing:
        for f, value in fields.items():
            if value is not None:
                setattr(existing, f, value)
        return existing.id, "UPDATE"

    obj = Warranty(
        asset_id=asset_id, vendor_id=vendor_id, warranty_number=mapped.get("warranty_number"),
        start_date=mapped.get("start_date"), end_date=mapped.get("end_date"),
        coverage_notes=mapped.get("coverage_notes"), status=mapped.get("status") or "UNKNOWN",
        environment="DEVELOPMENT / DEMO DATA",
    )
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


# ---- Import & Export Enhancement: Departments/Locations/Inventory Locations/
# Spare Parts/Service History/Document Metadata ----

def _resolve_branch_id_by_code(db: Session, hospital_code: object) -> str | None:
    if not hospital_code:
        return None
    branch = db.query(Branch).filter(Branch.code == hospital_code).first()
    return branch.id if branch else None


def _resolve_department_master(db: Session, row: ImportRow, name: str | None, code: str | None) -> str | None:
    """Central-Department-Master pass: matches an imported department's name/code
    against the DepartmentMaster catalog exactly the way the Department Master
    admin screen does (routers/hospitals.py's create_department_master), so an
    import can never fork a second "Physiotherapy" catalog entry.

    Matching key, in the same code-before-name preference already established two
    lines below (the pre-existing Department-row lookup): a code identifies one
    master unambiguously, so it is checked first; name is the fallback when no
    code is given. Two masters are never silently merged just because a name
    happens to match -- a genuine code-vs-name conflict, or a match landing on an
    INACTIVE master, is reported as an ImportError (the same mechanism already
    used for an unresolved branch below) and the row is skipped rather than
    guessed at. Returns the resolved master id, or None if an error was recorded.
    """
    norm_name = (name or "").strip()
    norm_code = code.strip() if code else None
    if not norm_name:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="name", severity="ERROR",
                                 message="Department name is required to resolve a department master."))
        return None

    by_code = db.query(DepartmentMaster).filter(DepartmentMaster.code.ilike(norm_code)).first() if norm_code else None
    by_name = db.query(DepartmentMaster).filter(DepartmentMaster.name.ilike(norm_name)).first()

    def _inactive_error(master: DepartmentMaster, field: str) -> None:
        db.add(ImportErrorModel(
            job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number, field=field, severity="ERROR",
            message=(f"Department master '{master.name}' matches this row but is INACTIVE. Reactivate it on the "
                     "Department Master page, or correct this row, before importing -- an import never reactivates "
                     "a master on its own."),
        ))

    if by_code:
        if by_code.name.strip().lower() != norm_name.lower():
            db.add(ImportErrorModel(
                job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number, field="code", severity="ERROR",
                message=(f"Department code '{norm_code}' already identifies department master '{by_code.name}', "
                         f"which does not match this row's name '{norm_name}'. Resolve manually on the Department "
                         "Master page before importing this row."),
            ))
            return None
        if by_code.status != "ACTIVE":
            _inactive_error(by_code, "code")
            return None
        return by_code.id

    if by_name:
        existing_code = (by_name.code or "").strip()
        if norm_code and existing_code and existing_code.lower() != norm_code.lower():
            db.add(ImportErrorModel(
                job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number, field="code", severity="ERROR",
                message=(f"Department name '{norm_name}' already identifies department master with code "
                         f"'{by_name.code}', which conflicts with this row's code '{norm_code}'. Resolve manually "
                         "on the Department Master page before importing this row."),
            ))
            return None
        if by_name.status != "ACTIVE":
            _inactive_error(by_name, "name")
            return None
        # The existing master has no code recorded, or the same code -- attach to
        # it as-is. A code this row supplies but the master lacks is NOT written
        # back onto the master here: a per-row import silently editing shared
        # central data (rather than being reported as a decision for a human,
        # like every other conflict above) is exactly the drift this feature
        # exists to prevent.
        return by_name.id

    master = DepartmentMaster(name=norm_name, code=norm_code, status="ACTIVE")
    db.add(master)
    db.flush()
    return master.id


def _import_department(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    branch_id = _resolve_branch_id_by_code(db, mapped.get("hospital_code"))
    if branch_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="hospital_code", severity="ERROR",
                                 message="Unresolved branch: import the hospital master record first."))
        return None

    name = mapped.get("name")
    code = mapped.get("code")

    master_id = _resolve_department_master(db, row, name, code)
    if master_id is None:
        return None
    master = db.get(DepartmentMaster, master_id)

    # Which BRANCH ASSIGNMENT row this import row updates-vs-inserts is unchanged
    # from before this pass -- matched on the raw imported code/name within this
    # branch, same as ever. Only what gets WRITTEN onto that row (name/code always
    # synced from the resolved master, plus the new department_master_id link)
    # reflects the central-master architecture.
    query = db.query(Department).filter(Department.branch_id == branch_id)
    existing = query.filter(Department.code == code).first() if code else query.filter(Department.name == name).first()

    if existing:
        if mapped.get("status") is not None:
            existing.status = mapped.get("status")
        existing.department_master_id = master.id
        existing.name = master.name
        existing.code = master.code
        return existing.id, "UPDATE"

    obj = Department(branch_id=branch_id, department_master_id=master.id, code=master.code, name=master.name,
                      status=mapped.get("status") or "ACTIVE")
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _import_location(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    branch_id = _resolve_branch_id_by_code(db, mapped.get("hospital_code"))
    if branch_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="hospital_code", severity="ERROR",
                                 message="Unresolved branch: import the hospital master record first."))
        return None

    department = db.query(Department).filter(
        Department.branch_id == branch_id, Department.code == mapped.get("department_code")
    ).first()
    if department is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="department_code", severity="ERROR",
                                 message="Unresolved department: import the department master record first."))
        return None

    name = mapped.get("name")
    code = mapped.get("code")
    query = db.query(Location).filter(Location.department_id == department.id)
    existing = query.filter(Location.code == code).first() if code else query.filter(Location.name == name).first()

    if existing:
        for f in ["room", "status"]:
            value = mapped.get(f)
            if value is not None:
                setattr(existing, f, value)
        return existing.id, "UPDATE"

    obj = Location(department_id=department.id, code=code, name=name, room=mapped.get("room"),
                    status=mapped.get("status") or "ACTIVE")
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _import_inventory_location(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    branch_id = _resolve_branch_id_by_code(db, mapped.get("hospital_code"))
    if branch_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="hospital_code", severity="ERROR",
                                 message="Unresolved branch: import the hospital master record first."))
        return None

    name = mapped.get("name")
    code = mapped.get("code")
    kind = mapped.get("kind") or "WAREHOUSE"
    query = db.query(InventoryLocation).filter(InventoryLocation.branch_id == branch_id)
    existing = query.filter(InventoryLocation.code == code).first() if code else query.filter(InventoryLocation.name == name).first()

    if existing:
        if mapped.get("kind") is not None:
            existing.kind = kind
        return existing.id, "UPDATE"

    obj = InventoryLocation(branch_id=branch_id, code=code, name=name, kind=kind)
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _upsert_inventory_item(db: Session, mapped: dict) -> tuple[str, str]:
    item_code = mapped.get("item_code")
    name = mapped.get("name")
    existing = None
    if item_code:
        existing = db.query(InventoryItem).filter(InventoryItem.item_code == item_code).first()
    if existing is None and name:
        existing = db.query(InventoryItem).filter(InventoryItem.name == name).first()

    fields = ["category", "unit", "min_level", "reorder_level", "supplier"]
    if existing:
        for f in fields:
            value = mapped.get(f)
            if value is not None:
                setattr(existing, f, value)
        return existing.id, "UPDATE"

    obj = InventoryItem(
        item_code=item_code or f"PENDING-{name}", name=name, category=mapped.get("category"),
        unit=mapped.get("unit"), min_level=mapped.get("min_level"), reorder_level=mapped.get("reorder_level"),
        supplier=mapped.get("supplier"), is_consumable=False,
    )
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


def _import_service_visit(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    asset_id = _resolve_asset_id_by_number(db, mapped.get("asset_number"))
    if asset_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="asset_number", severity="ERROR",
                                 message="Unresolved asset: import the device master record first."))
        return None

    vendor_id = _resolve_vendor_id(db, mapped.get("vendor_external_id"), context)
    if mapped.get("vendor_external_id") and vendor_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="vendor_external_id", severity="WARNING",
                                 message="Unresolved vendor; service history row imported without a vendor link."))

    # "Complaint"/"Root Cause"/"Action Taken"/"Resolution Status"/"Downtime Hours" have
    # no individual ServiceVisit column (only `summary`/`next_action`/`cost` exist) —
    # composed into `summary` as labeled sections rather than silently dropped, same
    # judgment-call discipline as PM Record's "Performed By" fallback into `notes`.
    parts = []
    for label, key in [("Complaint", "complaint"), ("Root cause", "root_cause"), ("Action taken", "action_taken"),
                        ("Resolution status", "resolution_status"), ("Downtime hours", "downtime_hours")]:
        if mapped.get(key) not in (None, ""):
            parts.append(f"{label}: {mapped[key]}")
    summary = " | ".join(parts) or None

    visit_date = mapped.get("visit_date")
    existing = db.query(ServiceVisit).filter(
        ServiceVisit.asset_id == asset_id, ServiceVisit.visit_date == visit_date
    ).first()

    if existing:
        if mapped.get("visit_type") is not None:
            existing.visit_type = mapped.get("visit_type")
        if vendor_id is not None:
            existing.vendor_id = vendor_id
        if summary is not None:
            existing.summary = summary
        if mapped.get("parts_used") is not None:
            existing.parts_used = mapped.get("parts_used")
        if mapped.get("cost") is not None:
            existing.cost = mapped.get("cost")
        return existing.id, "UPDATE"

    obj = ServiceVisit(
        asset_id=asset_id, vendor_id=vendor_id, visit_type=mapped.get("visit_type"), visit_date=visit_date,
        summary=summary, parts_used=mapped.get("parts_used"), cost=mapped.get("cost"),
    )
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


# ---- Incident history reconciliation ----
#
# The one entity type in this pipeline where "will this match an existing record"
# has to be visible to the admin BEFORE they approve, not just decided at apply time
# (every other handler above only resolves its match when approve_job() actually
# calls it) — reconciling historical data against a workbook is exactly the
# known-wrong-data-already-in-production case the rest of this pipeline was never
# asked to handle. `_match_incident` is the single source of truth for that decision,
# called twice: once read-only during create_job() validation (to annotate the
# preview with INFO rows showing the proposed before/after), and once for real during
# approve_job() (to actually write it) — never duplicated, just invoked at two points.

_INCIDENT_STATUS_MAP = {
    "closed": "OPERATIONAL",
    "wip": "WORK_IN_PROGRESS",
    "work in progress": "WORK_IN_PROGRESS",
    "to be condemned": "TO_BE_CONDEMNED",
    "open": "OPEN",
}

# Every Incident field this sheet can correct, beyond branch/description/status/date
# (handled separately since each needs its own resolution/mapping logic).
_INCIDENT_UPDATE_FIELDS = [
    "cer_number", "indent_number", "po_number", "po_closure_date",
    "po_cost_excl_gst", "po_gst_percent", "po_amount",
    "vendor_estimate_cost_excl_gst", "vendor_estimate_gst_percent", "vendor_estimate_cost_incl_gst",
]


@dataclass
class IncidentMatch:
    tier: str  # "PO" | "CER" | "INDENT" | "DESCRIPTION" | "NEW" | "AMBIGUOUS"
    incident: Incident | None = None
    conflict_ids: list[str] = dc_field(default_factory=list)


def _resolve_branch_id_by_name_ci(db: Session, name: object) -> str | None:
    if not name:
        return None
    branch = db.query(Branch).filter(func.lower(Branch.name) == str(name).strip().lower()).first()
    return branch.id if branch else None


def _coerce_incident_date(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _match_incident(db: Session, branch_id: str | None, mapped: dict) -> IncidentMatch:
    """Find the existing Incident (if any) a workbook row corresponds to.

    Tiers, in priority order — each trusted only when it resolves to exactly one row:
    1) PO number (exact)  2) CER number (exact)  3) Indent number (exact)
    4) branch + description (exact text, case/whitespace as stored)
    Two keys pointing at two DIFFERENT existing rows, or any key matching more than
    one row, is AMBIGUOUS — never guessed, per the explicit instruction not to match
    on approximate text or resolve conflicts automatically. No match at any tier
    means this row is a new incident, not an update.
    """
    candidates: dict[str, Incident] = {}
    for field_name, tier in (("po_number", "PO"), ("cer_number", "CER"), ("indent_number", "INDENT")):
        value = mapped.get(field_name)
        if not value:
            continue
        rows = db.query(Incident).filter(getattr(Incident, field_name) == value).all()
        if len(rows) == 1:
            candidates[tier] = rows[0]
        elif len(rows) > 1:
            return IncidentMatch(tier="AMBIGUOUS", conflict_ids=[r.id for r in rows])

    distinct_ids = {inc.id for inc in candidates.values()}
    if len(distinct_ids) > 1:
        return IncidentMatch(tier="AMBIGUOUS", conflict_ids=sorted(distinct_ids))
    if len(distinct_ids) == 1:
        tier, incident = next(iter(candidates.items()))
        return IncidentMatch(tier=tier, incident=incident)

    description = mapped.get("description")
    if branch_id and description:
        rows = db.query(Incident).filter(
            Incident.branch_id == branch_id, Incident.description == description,
        ).all()
        if len(rows) == 1:
            return IncidentMatch(tier="DESCRIPTION", incident=rows[0])
        if len(rows) > 1:
            return IncidentMatch(tier="AMBIGUOUS", conflict_ids=[r.id for r in rows])

    return IncidentMatch(tier="NEW")


_INCIDENT_NUMERIC_FIELDS = {
    "po_cost_excl_gst", "po_gst_percent", "po_amount",
    "vendor_estimate_cost_excl_gst", "vendor_estimate_gst_percent", "vendor_estimate_cost_incl_gst",
}


def _incident_value_differs(field_name: str, old_value: object, new_value: object) -> bool:
    """A plain str() comparison treats `0.0` (float, from the DB) and `0` (int, from
    openpyxl) as different values when they're numerically identical — a false
    "change" on every untouched cost/GST field. Numeric fields compare as floats;
    everything else compares as text."""
    if field_name in _INCIDENT_NUMERIC_FIELDS:
        try:
            return float(new_value) != float(old_value or 0)
        except (TypeError, ValueError):
            pass
    return str(new_value) != (str(old_value) if old_value is not None else None)


def _incident_field_diff(incident: Incident, mapped: dict) -> list[tuple[str, object, object]]:
    """Every (label, old, new) where the workbook's value differs from the existing
    row — drives both what gets written at approval and what the admin sees in the
    preview beforehand."""
    diffs: list[tuple[str, object, object]] = []
    new_date = _coerce_incident_date(mapped.get("incident_date"))
    if new_date is not None:
        old_iso = incident.created_at.isoformat() if incident.created_at else None
        if new_date.isoformat() != old_iso:
            diffs.append(("created_at (the DATE column shown in the Incidents UI)", old_iso, new_date.isoformat()))
    status_raw = mapped.get("status")
    new_status = _INCIDENT_STATUS_MAP.get(str(status_raw).strip().lower()) if status_raw else None
    if new_status and new_status != incident.status:
        diffs.append(("status", incident.status, new_status))
    for field_name in _INCIDENT_UPDATE_FIELDS:
        new_value = mapped.get(field_name)
        if new_value is None:
            continue
        old_value = getattr(incident, field_name)
        if _incident_value_differs(field_name, old_value, new_value):
            diffs.append((field_name, old_value, new_value))
    return diffs


def _validate_incident_row(db: Session, mapped: dict) -> list[tuple[str | None, str, str]]:
    """Called from create_job()'s validation loop — read-only, writes nothing.
    Returns ERROR issues for anything that must block the row (unresolved branch,
    unrecognized status, ambiguous match, unparseable date), and INFO issues that
    surface the proposed match/diff in the preview without blocking it."""
    issues: list[tuple[str | None, str, str]] = []

    branch_id = _resolve_branch_id_by_name_ci(db, mapped.get("branch_code"))
    if branch_id is None:
        issues.append(("branch_code", f"'{mapped.get('branch_code')}' does not match any existing branch", "ERROR"))
        return issues

    status_raw = mapped.get("status")
    if status_raw and str(status_raw).strip().lower() not in _INCIDENT_STATUS_MAP:
        issues.append(("status", f"Status '{status_raw}' not recognized (expected Open/WIP/Closed/To Be Condemned)", "ERROR"))
        return issues

    if mapped.get("incident_date") is not None and _coerce_incident_date(mapped.get("incident_date")) is None:
        issues.append(("incident_date", f"Date value '{mapped.get('incident_date')}' could not be parsed", "ERROR"))
        return issues

    match = _match_incident(db, branch_id, mapped)
    if match.tier == "AMBIGUOUS":
        issues.append((
            None,
            f"Matches {len(match.conflict_ids)} existing incidents ambiguously "
            f"({', '.join(match.conflict_ids)}) — needs manual resolution, will not be imported automatically",
            "ERROR",
        ))
        return issues

    if match.tier == "NEW":
        issues.append((None, "No matching existing incident found — will be created as a new incident", "INFO"))
    else:
        diffs = _incident_field_diff(match.incident, mapped)
        issues.append((
            None,
            f"Matched existing incident by {match.tier} ({match.incident.id}) — {len(diffs)} field(s) will change",
            "INFO",
        ))
        for field_name, old, new in diffs:
            issues.append((field_name, f"{old!r} -> {new!r}", "INFO"))
    return issues


def _import_incident(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    branch_id = _resolve_branch_id_by_name_ci(db, mapped.get("branch_code"))
    if branch_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="branch_code", severity="ERROR",
                                 message=f"'{mapped.get('branch_code')}' does not match any existing branch"))
        return None

    status_raw = mapped.get("status")
    new_status = _INCIDENT_STATUS_MAP.get(str(status_raw).strip().lower()) if status_raw else None
    new_date = _coerce_incident_date(mapped.get("incident_date"))

    match = _match_incident(db, branch_id, mapped)
    if match.tier == "AMBIGUOUS":
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field=None, severity="ERROR",
                                 message="Ambiguous match against existing incidents — not imported"))
        return None

    if match.tier == "NEW":
        incident = Incident(
            branch_id=branch_id, description=mapped.get("description"), status=new_status or "OPEN",
            reported_by=None,
        )
        if new_date is not None:
            incident.created_at = new_date
        for field_name in _INCIDENT_UPDATE_FIELDS:
            value = mapped.get(field_name)
            if value is not None:
                setattr(incident, field_name, value)
        db.add(incident)
        db.flush()
        return incident.id, "INSERT"

    incident = match.incident
    if new_date is not None:
        incident.created_at = new_date
    if new_status:
        incident.status = new_status
    for field_name in _INCIDENT_UPDATE_FIELDS:
        value = mapped.get(field_name)
        if value is not None:
            setattr(incident, field_name, value)
    return incident.id, "UPDATE"


def _import_document(db: Session, row: ImportRow, mapped: dict, context: dict) -> tuple[str, str] | None:
    device_model_id = _resolve_device_model_id(
        db, manufacturer=mapped.get("manufacturer"), model_number=mapped.get("model_number"), name=None,
    )
    asset_number = mapped.get("asset_number")
    asset_id = _resolve_asset_id_by_number(db, asset_number) if asset_number else None
    if asset_number and asset_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="asset_number", severity="WARNING",
                                 message="Unresolved asset; document imported without an asset link."))

    vendor_external_id = mapped.get("vendor_external_id")
    vendor_id = _resolve_vendor_id(db, vendor_external_id, context) if vendor_external_id else None
    if vendor_external_id and vendor_id is None:
        db.add(ImportErrorModel(job_id=row.job_id, sheet_name=row.sheet_name, row_number=row.row_number,
                                 field="vendor_external_id", severity="WARNING",
                                 message="Unresolved vendor; document imported without a vendor link."))

    title = mapped.get("title")
    fields = {
        "document_type": mapped.get("document_type"), "manufacturer": mapped.get("manufacturer"),
        "model_number": mapped.get("model_number"), "device_model_id": device_model_id,
        "asset_id": asset_id, "vendor_id": vendor_id, "effective_date": mapped.get("effective_date"),
        "expiry_date": mapped.get("expiry_date"), "access_level": mapped.get("access_level"),
    }
    existing = db.query(Document).filter(Document.title == title).first()
    if existing:
        for f, value in fields.items():
            if value is not None:
                setattr(existing, f, value)
        return existing.id, "UPDATE"

    obj = Document(title=title, access_level=fields.pop("access_level") or "INTERNAL", **fields)
    db.add(obj)
    db.flush()
    return obj.id, "INSERT"


_HANDLERS = {
    "branch": _upsert_branch,
    "device_model": _upsert_device_model,
    "consumable": _upsert_consumable,
    "vendor": _upsert_vendor,
    "inventory_item": _upsert_inventory_item,
}
_ROW_HANDLERS = {
    "asset": _import_asset,
    "branch_device_assignment": _import_branch_device_assignment,
    "vendor_contact": _import_vendor_contact,
    "pm_schedule": _import_pm_schedule,
    "pm_record": _import_pm_record,
    "calibration_schedule": _import_calibration_schedule,
    "calibration_record": _import_calibration_record,
    "amc_contract": _import_amc_contract,
    "cmc_contract": _import_cmc_contract,
    "warranty": _import_warranty,
    "department": _import_department,
    "location": _import_location,
    "inventory_location": _import_inventory_location,
    "service_visit": _import_service_visit,
    "incident": _import_incident,
    "document": _import_document,
}
# Row handlers added by Module 14 take a `context` dict (currently just
# `vendor_external_index`); the two Phase 1 row handlers don't need it but share the
# same dispatch call in `approve_job()`, so they accept and ignore it for a uniform signature.


def approve_job(db: Session, *, job_id: str, approved_by: str, notes: str | None) -> ImportJob:
    job = db.get(ImportJob, job_id)
    if job is None:
        raise ImportServiceError("Import job not found")
    if job.status != "READY_FOR_REVIEW":
        raise ImportServiceError(f"Import job is in status '{job.status}' and cannot be approved")

    db.add(ImportApproval(job_id=job.id, approved_by=approved_by, decision="APPROVED", notes=notes))

    inserted = updated = skipped = failed = 0
    # Group by each row's *classified* entity type's canonical sheet name, not its raw
    # uploaded sheet/file name. A CSV upload matches its entity type at validation time
    # via `best_fit_config()` (header overlap) whenever the filename itself isn't one
    # of the canonical names (e.g. "march_devices.csv" instead of
    # "device_model_master.csv") — grouping by the raw name here meant such a row was
    # already correctly validated and marked READY, but then silently never visited by
    # the `for sheet_name in SHEET_IMPORT_ORDER` loop below (its raw name matches none
    # of those literal keys), so it was dropped at approval without even being counted
    # as skipped/failed. Reusing the row's own entity_type — set once during
    # validation and stable — instead keeps grouping correct regardless of what the
    # sheet/file was actually named.
    entity_type_to_sheet_name = {config.entity_type: name for name, config in SHEET_CONFIGS.items()}
    rows_by_sheet: dict[str, list[ImportRow]] = {}
    for import_row in db.query(ImportRow).filter(ImportRow.job_id == job.id).all():
        canonical_name = entity_type_to_sheet_name.get(
            import_row.entity_type, normalize_sheet_name(import_row.sheet_name)
        )
        rows_by_sheet.setdefault(canonical_name, []).append(import_row)

    # Populated once the "vendor master" sheet (which SHEET_IMPORT_ORDER runs first
    # among vendor-referencing sheets) has been processed, so vendor_contacts/
    # amc_contracts/cmc_contracts/warranty in the same job can resolve their "Vendor ID"
    # column against vendors this same job just created — see _resolve_vendor_id().
    context: dict = {"vendor_external_index": {}}

    for sheet_name in SHEET_IMPORT_ORDER:
        for import_row in sorted(rows_by_sheet.get(sheet_name, []), key=lambda r: r.row_number):
            if import_row.outcome not in ("READY", "DUPLICATE_CANDIDATE"):
                skipped += 1
                continue

            mapped = import_row.mapped_data or {}
            result = None
            if import_row.entity_type in _HANDLERS:
                result = _HANDLERS[import_row.entity_type](db, mapped)
            elif import_row.entity_type in _ROW_HANDLERS:
                result = _ROW_HANDLERS[import_row.entity_type](db, import_row, mapped, context)

            if result is None:
                import_row.outcome = "IMPORT_FAILED"
                failed += 1
                continue

            entity_id, action = result
            import_row.entity_id = entity_id
            import_row.outcome = f"IMPORTED_{action}"
            if action == "INSERT":
                inserted += 1
            else:
                updated += 1

            audit_record(
                db, user_id=approved_by, action=action, entity=import_row.entity_type, entity_id=entity_id,
                new_value=mapped, import_job_id=job.id,
            )

        if sheet_name == "vendor master":
            for import_row in rows_by_sheet.get(sheet_name, []):
                if import_row.entity_id and import_row.mapped_data:
                    external_id = import_row.mapped_data.get("external_id")
                    if external_id:
                        context["vendor_external_index"][external_id] = import_row.entity_id

    job.status = "COMPLETED"
    job.summary = {**(job.summary or {}), "inserted": inserted, "updated": updated, "skipped": skipped, "failed": failed}
    audit_record(db, user_id=approved_by, action="APPROVE", entity="import_job", entity_id=job.id, new_value=job.summary, import_job_id=job.id)
    db.commit()
    db.refresh(job)
    return job


_ENTITY_MODELS = {
    "branch": Branch,
    "device_model": DeviceModel,
    "consumable": Consumable,
    "asset": Asset,
    "branch_device_assignment": BranchDeviceAssignment,
    "vendor": Vendor,
    "vendor_contact": VendorContact,
    "pm_schedule": PMSchedule,
    "pm_record": PMRecord,
    "calibration_schedule": CalibrationSchedule,
    "calibration_record": CalibrationRecord,
    "amc_contract": AmcContract,
    "cmc_contract": CmcContract,
    "warranty": Warranty,
    "department": Department,
    "location": Location,
    "inventory_location": InventoryLocation,
    "inventory_item": InventoryItem,
    "service_visit": ServiceVisit,
    "incident": Incident,
    "document": Document,
}

# Reverse of SHEET_IMPORT_ORDER dependency direction: delete dependents before their references.
_ROLLBACK_ORDER = [
    "document metadata", "incident history", "service history", "warranty", "cmc contracts", "amc contracts",
    "calibration records", "calibration schedule", "pm records", "pm schedule", "vendor contacts", "vendor master",
    "branch device assignments", "device master", "spare parts", "consumables", "device model master",
    "inventory locations", "location master", "department master", "hospital master",
]


def rollback_job(db: Session, *, job_id: str, requested_by: str) -> ImportJob:
    """Best-effort rollback limited to rows this job itself INSERTed.

    Rows that were an UPDATE to a pre-existing record are never reverted (we do not
    know the prior values were solely this job's concern), and any row whose entity
    now has a foreign-key dependent created by later activity is left in place and
    flagged ROLLBACK_BLOCKED rather than deleted unsafely.
    """
    job = db.get(ImportJob, job_id)
    if job is None:
        raise ImportServiceError("Import job not found")
    if job.status != "COMPLETED":
        raise ImportServiceError(f"Import job is in status '{job.status}' and cannot be rolled back")

    # Same canonical-name grouping fix as approve_job above — group by the row's own
    # classified entity_type, not its raw uploaded sheet/file name.
    entity_type_to_sheet_name = {config.entity_type: name for name, config in SHEET_CONFIGS.items()}
    rows_by_sheet: dict[str, list[ImportRow]] = {}
    for import_row in db.query(ImportRow).filter(ImportRow.job_id == job.id, ImportRow.outcome == "IMPORTED_INSERT").all():
        canonical_name = entity_type_to_sheet_name.get(
            import_row.entity_type, normalize_sheet_name(import_row.sheet_name)
        )
        rows_by_sheet.setdefault(canonical_name, []).append(import_row)

    reverted = blocked = 0
    for sheet_name in _ROLLBACK_ORDER:
        for import_row in rows_by_sheet.get(sheet_name, []):
            model = _ENTITY_MODELS.get(import_row.entity_type)
            if model is None or import_row.entity_id is None:
                continue
            entity = db.get(model, import_row.entity_id)
            if entity is None:
                continue
            try:
                with db.begin_nested():
                    db.delete(entity)
                    db.flush()
                import_row.outcome = "ROLLED_BACK"
                reverted += 1
            except IntegrityError:
                import_row.outcome = "ROLLBACK_BLOCKED"
                blocked += 1

    job.status = "ROLLED_BACK" if blocked == 0 else "PARTIALLY_ROLLED_BACK"
    job.summary = {**(job.summary or {}), "rolled_back": reverted, "rollback_blocked": blocked}
    audit_record(db, user_id=requested_by, action="ROLLBACK", entity="import_job", entity_id=job.id, new_value=job.summary, import_job_id=job.id)
    db.commit()
    db.refresh(job)
    return job
