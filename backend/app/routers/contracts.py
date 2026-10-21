from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies.auth import require_permission
from ..models.incident import ServiceVisit
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.contract import ContractIn, ContractOut, ContractUpdateIn
from ..security.permissions import CONTRACT_READ, CONTRACT_WRITE, DEV_DATA_CLEANUP, branch_scope_id
from ..services import contract_service
from ..services.contract_service import ContractNotFoundError, ContractServiceError
from ..audit.service import record as audit_record
from ..services.force_delete import assert_dev_environment, force_delete_row

router = APIRouter(prefix="/api/v1/contracts", tags=["contracts"])


def _get_contract_or_404(db: Session, contract_id: str):
    try:
        return contract_service.get_contract(db, contract_id)
    except ContractNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")


def _enforce_contract_branch_scope(db: Session, contract_type: str, contract, current_user: CurrentUser) -> None:
    """Direct-ID counterpart of the list's branch filter — a branch-scoped caller must
    not reach a contract by id that covers nothing in their branch. Shares
    contract_service.contract_covers_branch() with the list so the two can't diverge.

    Enforced in the request path rather than relying on UNIT_HEAD/TL not holding
    contract.read today: that grant is editable at runtime from the Role & Permissions
    screen, so it is not a branch-isolation boundary."""
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is None:
        return
    if not contract_service.contract_covers_branch(db, contract_type, contract, scoped_branch_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")


@router.get("", response_model=Page[ContractOut])
def list_contracts(
    page: int = 1, page_size: int = 25,
    contract_type: str | None = None,
    vendor_id: str | None = None,
    asset_id: str | None = None,
    status_filter: str | None = None,
    expiry_status: str | None = None,
    end_date_after: str | None = None,
    end_date_before: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission(CONTRACT_READ)),
):
    # Branch scope (F11): UNIT_HEAD/TL only see contracts covering an asset in their
    # own branch. AMC/CMC can span branches, so this is a coverage filter, not ownership.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    items, total = contract_service.list_contracts(
        db, page=page, page_size=page_size, contract_type=contract_type, vendor_id=vendor_id,
        asset_id=asset_id, status_filter=status_filter, expiry_status=expiry_status,
        end_date_after=end_date_after, end_date_before=end_date_before, search=search,
        branch_id=scoped_branch_id,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=ContractOut, status_code=status.HTTP_201_CREATED)
def create_contract(payload: ContractIn, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(CONTRACT_WRITE))):
    # A branch-scoped caller (UNIT_HEAD/TL) may only cover their own branch's assets;
    # the service rejects any other asset id rather than trusting the posted list.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    try:
        contract_type, contract = contract_service.create_contract(
            db, payload, user_id=current_user.id, branch_id=scoped_branch_id
        )
    except ContractServiceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return contract_service.to_contract_out(db, contract_type, contract)


@router.get("/{contract_id}", response_model=ContractOut)
def get_contract(contract_id: str, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_permission(CONTRACT_READ))):
    contract_type, contract = _get_contract_or_404(db, contract_id)
    _enforce_contract_branch_scope(db, contract_type, contract, current_user)
    return contract_service.to_contract_out(db, contract_type, contract)


@router.patch("/{contract_id}", response_model=ContractOut)
def update_contract(contract_id: str, payload: ContractUpdateIn, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(CONTRACT_WRITE))):
    contract_type, contract = _get_contract_or_404(db, contract_id)
    try:
        contract = contract_service.update_contract(db, contract_type, contract, payload, user_id=current_user.id)
    except ContractServiceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return contract_service.to_contract_out(db, contract_type, contract)


@router.delete("/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contract(contract_id: str, force: bool = False, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(CONTRACT_WRITE))):
    """Final non-AI cleanup pass — Batch B: hard delete only when zero rows reference
    this contract (AMC/CMC asset links); otherwise 409, directing the caller to
    deactivate (archive) instead — same reference-check-then-hard-delete-or-reject
    pattern as routers/devices.py:delete_device_model / routers/vendors.py:delete_vendor.

    ``force=true`` (Super Admin only, gated on DEV_DATA_CLEANUP) bypasses that guard
    via the generic FK-graph cascade (force_delete_row)."""
    contract_type, contract = _get_contract_or_404(db, contract_id)
    if force:
        if DEV_DATA_CLEANUP not in current_user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Force-delete requires Super Admin (dev.data_cleanup permission)",
            )
        assert_dev_environment()
        deleted_count = force_delete_row(db, type(contract).__tablename__, contract.id)
        audit_record(
            db, user_id=current_user.id, action="FORCE_DELETE", entity=contract_type, entity_id=contract_id,
            new_value={"cascaded_row_count": deleted_count},
        )
        db.commit()
        return
    in_use = contract_service.count_asset_links(db, contract_type, contract)
    if in_use > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Contract is linked to {in_use} asset(s) and cannot be deleted — deactivate it instead",
        )
    contract_service.delete_contract(db, contract_type, contract, user_id=current_user.id)


@router.post("/{contract_id}/activate", response_model=ContractOut)
def activate_contract(contract_id: str, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(CONTRACT_WRITE))):
    contract_type, contract = _get_contract_or_404(db, contract_id)
    contract = contract_service.set_status(db, contract_type, contract, "ACTIVE", user_id=current_user.id)
    return contract_service.to_contract_out(db, contract_type, contract)


@router.post("/{contract_id}/deactivate", response_model=ContractOut)
def deactivate_contract(contract_id: str, db: Session = Depends(get_db),
                         current_user: CurrentUser = Depends(require_permission(CONTRACT_WRITE))):
    contract_type, contract = _get_contract_or_404(db, contract_id)
    contract = contract_service.set_status(db, contract_type, contract, "CANCELLED", user_id=current_user.id)
    return contract_service.to_contract_out(db, contract_type, contract)


@router.get("/{contract_id}/service-visits")
def list_contract_service_visits(contract_id: str, page: int = 1, page_size: int = 25,
                                  db: Session = Depends(get_db),
                                  _: CurrentUser = Depends(require_permission(CONTRACT_READ))):
    contract_type, contract = _get_contract_or_404(db, contract_id)
    asset_ids = contract_service.asset_ids_for(db, contract_type, contract)
    if not asset_ids:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    query = db.query(ServiceVisit).filter(ServiceVisit.asset_id.in_(asset_ids))
    total = query.count()
    items = query.order_by(ServiceVisit.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [
            {
                "id": v.id,
                "incident_id": v.incident_id,
                "asset_id": v.asset_id,
                "vendor_id": v.vendor_id,
                "engineer_id": v.engineer_id,
                "visit_type": v.visit_type,
                "visit_date": v.visit_date,
                "summary": v.summary,
                "parts_used": v.parts_used,
                "cost": v.cost,
                "next_action": v.next_action,
            }
            for v in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


class ContractAssetsIn(BaseModel):
    """Physical Asset ids to attach to / detach from an AMC or CMC."""

    asset_ids: list[str]


@router.post("/{contract_id}/assets", response_model=ContractOut)
def add_contract_assets(contract_id: str, payload: ContractAssetsIn, db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(require_permission(CONTRACT_WRITE))):
    """Extend an existing AMC/CMC's covered assets.

    Same branch scope and same eligibility rule as creation — the service rejects a
    condemned/retired device or another branch's asset regardless of what is posted."""
    contract_type, contract = _get_contract_or_404(db, contract_id)
    _enforce_contract_branch_scope(db, contract_type, contract, current_user)
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    try:
        contract_service.add_covered_assets(
            db, contract_type, contract, payload.asset_ids,
            user_id=current_user.id, branch_id=scoped_branch_id,
        )
    except ContractServiceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    db.refresh(contract)
    return contract_service.to_contract_out(db, contract_type, contract)


@router.post("/{contract_id}/assets/remove", response_model=ContractOut)
def remove_contract_assets(contract_id: str, payload: ContractAssetsIn, db: Session = Depends(get_db),
                           current_user: CurrentUser = Depends(require_permission(CONTRACT_WRITE))):
    """Detach covered assets from an AMC/CMC. Deliberately a POST rather than a DELETE
    body — this codebase's DELETE endpoints take no payload."""
    contract_type, contract = _get_contract_or_404(db, contract_id)
    _enforce_contract_branch_scope(db, contract_type, contract, current_user)
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    try:
        contract_service.remove_covered_assets(
            db, contract_type, contract, payload.asset_ids,
            user_id=current_user.id, branch_id=scoped_branch_id,
        )
    except ContractServiceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    db.refresh(contract)
    return contract_service.to_contract_out(db, contract_type, contract)


@router.post("/run-expiry-scan")
def run_contract_expiry_scan(db: Session = Depends(get_db),
                             current_user: CurrentUser = Depends(require_permission(CONTRACT_WRITE))):
    """Daily-triggerable scan for contracts approaching expiry or newly expired.

    This codebase runs no background process by design, so the schedule lives outside
    it — point Windows Task Scheduler (or cron) at this endpoint once a day. It is
    idempotent: every notification it creates carries a dedup key, so running it twice
    in a day, or re-running after a failure, produces no duplicate messages.

    Gated on contract.write (the same permission that manages contracts) rather than a
    new machine credential, so the scheduled call uses an ordinary service account and
    every run is attributable in the audit trail."""
    summary = contract_service.scan_contract_expiry(db, user_id=current_user.id)
    audit_record(db, user_id=current_user.id, action="UPDATE", entity="contract_expiry_scan",
                 entity_id="scan", new_value=summary)
    db.commit()
    return summary
