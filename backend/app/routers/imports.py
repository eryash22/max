from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies.auth import require_permission
from ..exports.writer import build_export_response
from ..imports.mapping import SHEET_CONFIGS, normalize_sheet_name
from ..models.imports import ImportError as ImportErrorModel, ImportJob, ImportRow
from ..repositories.crud import paginate
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.imports import ImportApprovalIn, ImportJobOut, ImportPreview
from ..security.permissions import IMPORT_APPROVE, IMPORT_CREATE
from ..services.import_service import ImportServiceError, approve_job, create_job, rollback_job

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])

ALLOWED_EXTENSIONS = {".xlsx", ".xlsm", ".csv"}
MAX_IMPORT_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB — same cap as document uploads


@router.get("/templates/{sheet_key}")
def download_import_template(
    sheet_key: str, format: str = "xlsx",
    _: CurrentUser = Depends(require_permission(IMPORT_CREATE)),
):
    """Blank upload template for one of the Import Center's known sheets — headers only
    (marked `*` when required), generated straight from `SHEET_CONFIGS` so it can never
    drift from what the import engine actually accepts, and reusing the same
    XLSX/CSV writer every export endpoint already uses (no second template engine, no
    fabricated example values)."""
    config = SHEET_CONFIGS.get(normalize_sheet_name(sheet_key))
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown template: {sheet_key}")
    if format not in ("xlsx", "csv"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="format must be 'xlsx' or 'csv'")

    headers = [
        f"{column.title()}{'*' if field in config.required else ''}"
        for column, field in config.column_map.items()
    ]
    filename_stem = normalize_sheet_name(sheet_key).replace(" ", "_") + "_template"
    return build_export_response(headers=headers, rows=[], format=format, filename_stem=filename_stem)


@router.get("", response_model=Page[ImportJobOut])
def list_imports(page: int = 1, page_size: int = 25, status_filter: str | None = None,
                  db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(IMPORT_CREATE))):
    filters = {"status": status_filter} if status_filter else {}
    items, total = paginate(db, ImportJob, page=page, page_size=page_size, **filters)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=ImportJobOut, status_code=status.HTTP_201_CREATED)
async def upload_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(IMPORT_CREATE)),
):
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A file is required")
    suffix = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .xlsx/.xlsm/.csv files are accepted")

    file_bytes = await file.read()
    if len(file_bytes) > MAX_IMPORT_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {MAX_IMPORT_UPLOAD_BYTES // (1024 * 1024)} MB upload limit",
        )
    job = create_job(db, file_bytes=file_bytes, file_name=file.filename, uploaded_by=current_user.id)
    return job


@router.get("/{job_id}", response_model=ImportJobOut)
def get_import(job_id: str, db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(IMPORT_CREATE))):
    job = db.get(ImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import job not found")
    return job


@router.get("/{job_id}/preview", response_model=ImportPreview)
def preview_import(job_id: str, only_issues: bool = False, db: Session = Depends(get_db),
                    _: CurrentUser = Depends(require_permission(IMPORT_CREATE))):
    job = db.get(ImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import job not found")

    row_query = db.query(ImportRow).filter(ImportRow.job_id == job_id)
    if only_issues:
        row_query = row_query.filter(ImportRow.outcome.in_(["ERROR", "DUPLICATE_CANDIDATE"]))
    rows = row_query.order_by(ImportRow.sheet_name, ImportRow.row_number).all()
    errors = db.query(ImportErrorModel).filter(ImportErrorModel.job_id == job_id).order_by(ImportErrorModel.row_number).all()

    return ImportPreview(job=job, rows=rows, errors=errors)


@router.post("/{job_id}/approve", response_model=ImportJobOut)
def approve_import(job_id: str, payload: ImportApprovalIn, db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(require_permission(IMPORT_APPROVE))):
    try:
        return approve_job(db, job_id=job_id, approved_by=current_user.id, notes=payload.notes)
    except ImportServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/{job_id}/rollback", response_model=ImportJobOut)
def rollback_import(job_id: str, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(IMPORT_APPROVE))):
    try:
        return rollback_job(db, job_id=job_id, requested_by=current_user.id)
    except ImportServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
