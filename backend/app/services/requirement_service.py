from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..models.device import Asset, DeviceModel
from ..models.fulfillment import FulfillmentRequest
from ..models.hospital import Branch, Department
from ..models.inventory import Consumable, InventoryItem
from ..models.requirement import Requirement, RequirementItem, RequirementStatusHistory
from ..schemas.notification import (
    EVENT_REQUIREMENT_APPROVED,
    EVENT_REQUIREMENT_CANCELLED,
    EVENT_REQUIREMENT_REJECTED,
    EVENT_REQUIREMENT_SUBMITTED,
)
from ..schemas.requirement import (
    ITEM_TYPE_REFERENCE_FIELD,
    RequirementCancelIn,
    RequirementDecisionIn,
    RequirementIn,
    RequirementItemIn,
    RequirementUpdateIn,
)
from . import asset_event_service
from .asset_history_service import record_asset_history
from .communication_service import dispatch_event_safe

# Valid forward transitions. REJECTED/CANCELLED/APPROVED are terminal — Module 16A does
# not implement a resubmission flow (out of scope per spec), so once rejected/cancelled a
# requirement cannot move again; a new requirement must be raised instead.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"SUBMITTED", "CANCELLED"},
    "SUBMITTED": {"PENDING_MANAGER_APPROVAL", "CANCELLED"},
    "PENDING_MANAGER_APPROVAL": {"APPROVED", "REJECTED", "CANCELLED"},
    "APPROVED": set(),
    "REJECTED": set(),
    "CANCELLED": set(),
}


# Canonical terminal-asset-status vocabulary, matching
# services/asset_lifecycle_service.py's _CONDEMNED_VALUES/_RETIRED_VALUES — an asset
# already in one of these states cannot be the target of a new condemnation item.
_CONDEMNATION_INELIGIBLE_STATUSES = {"CONDEMNED", "RETIRED"}


class RequirementServiceError(Exception):
    """Generic 422-mappable validation failure."""


class RequirementReferenceError(RequirementServiceError):
    """A referenced entity (branch/department/device model/inventory item/consumable)
    does not exist — 404-mappable."""


class RequirementStateError(RequirementServiceError):
    """An action was attempted from a status that does not allow it — 409-mappable."""


class RequirementPermissionError(RequirementServiceError):
    """A business rule (e.g. cannot approve own request) blocks the action — 403-mappable."""


class RequirementCondemnationError(RequirementServiceError):
    """A CONDEMNATION item's target asset cannot be safely auto-transitioned on
    approval (e.g. the referenced asset no longer exists) — 422-mappable. Blocks the
    whole approval (raised before commit) rather than approving the requirement while
    silently skipping the condemnation it exists to authorize."""


def _validate_branch_and_department(db: Session, branch_id: str, department_id: str | None) -> None:
    if db.get(Branch, branch_id) is None:
        raise RequirementReferenceError("Branch not found")
    if department_id is not None:
        department = db.get(Department, department_id)
        if department is None:
            raise RequirementReferenceError("Department not found")
        if department.branch_id != branch_id:
            raise RequirementServiceError("department does not belong to the given branch")


def _validate_item_reference(db: Session, item: RequirementItemIn, *, branch_id: str) -> None:
    required_field = ITEM_TYPE_REFERENCE_FIELD.get(item.item_type)
    if required_field is None:
        return
    ref_id = getattr(item, required_field)
    model = {
        "device_model_id": DeviceModel,
        "inventory_item_id": InventoryItem,
        "consumable_id": Consumable,
        "asset_id": Asset,
    }[required_field]
    ref = db.get(model, ref_id)
    if ref is None:
        raise RequirementReferenceError(f"{required_field.replace('_id', '')} not found: {ref_id}")
    if item.item_type == "CONSUMABLE":
        if ref.branch_id is not None and ref.branch_id != branch_id:
            # A consumable requirement must be fulfilled FROM CENTRAL STOCK
            # (Consumable.branch_id IS NULL) to the requesting branch, or from the
            # requesting branch's OWN already-on-hand stock -- never from ANOTHER
            # branch's stock. Referencing a foreign branch's Consumable row here
            # would make fulfillment (services/fulfillment_service.py's
            # complete_fulfillment -> inventory_service.transfer_consumable_stock)
            # drain THAT branch's stock instead, since transfer_consumable_stock
            # treats whichever row this points at as the source. The frontend
            # requester picker only ever offers central rows (GET /consumables?
            # central_only=true, see requirements/new & [id] pages) -- it never
            # lets a requester pick any branch's row, their own included -- but a
            # direct API call must be rejected the same way every other
            # cross-branch reference already is; "own branch's stock" stays
            # allowed since it predates and is unrelated to this rule (see
            # test_consumables_workflow.py's own-stock-consumption tests).
            raise RequirementReferenceError("a consumable requirement must reference the central stock item or this branch's own stock, not another branch's stock")

    if item.item_type == "CONDEMNATION":
        if ref.branch_id != branch_id:
            # Condemnation targets a real, already-installed physical Asset — it must
            # belong to the same branch as the requirement itself (a Unit Head/TL raising
            # the requirement is separately blocked from picking another branch's asset by
            # branch_scope_id-driven picker scoping on the frontend/GET /assets, but this
            # is the backend cross-check that makes it a real rule, not just a UI hint).
            raise RequirementReferenceError("asset does not belong to the requirement's branch")
        if (ref.status or "").upper() in _CONDEMNATION_INELIGIBLE_STATUSES:
            # Same defense-in-depth reasoning as the branch check above — the frontend
            # picker (RequirementItemsTable.tsx) already excludes CONDEMNED/RETIRED
            # assets from the dropdown, but a direct API call must be rejected too.
            raise RequirementReferenceError("asset is already condemned or retired and cannot be condemned again")


def _next_request_number(db: Session) -> str:
    count = db.query(Requirement).count()
    for attempt in range(count + 1, count + 1000):
        candidate = f"REQ-{attempt:06d}"
        if db.query(Requirement).filter(Requirement.request_number == candidate).first() is None:
            return candidate
    raise RequirementServiceError("unable to allocate a request number")


def create_requirement(
    db: Session, payload: RequirementIn, *, requester_id: str, requester_role: str | None,
) -> Requirement:
    _validate_branch_and_department(db, payload.branch_id, payload.department_id)
    for item in payload.items:
        _validate_item_reference(db, item, branch_id=payload.branch_id)

    requirement = Requirement(
        request_number=_next_request_number(db),
        branch_id=payload.branch_id,
        department_id=payload.department_id,
        requester_id=requester_id,
        requester_role=requester_role,
        priority=payload.priority,
        required_by_date=payload.required_by_date,
        reason=payload.reason,
        status="DRAFT",
    )
    db.add(requirement)
    db.flush()

    for item in payload.items:
        db.add(RequirementItem(requirement_id=requirement.id, **item.model_dump()))

    db.add(RequirementStatusHistory(
        requirement_id=requirement.id, from_status=None, to_status="DRAFT", changed_by=requester_id,
    ))
    audit_record(db, user_id=requester_id, action="CREATE", entity="requirement", entity_id=requirement.id,
                 new_value=payload.model_dump())
    db.commit()
    db.refresh(requirement)
    return requirement


def update_requirement(
    db: Session, requirement: Requirement, payload: RequirementUpdateIn, *, user_id: str,
) -> Requirement:
    if requirement.status != "DRAFT":
        raise RequirementStateError("only a DRAFT requirement can be edited")

    changes = payload.model_dump(exclude_unset=True, exclude={"items"})
    if "branch_id" in changes or "department_id" in changes:
        effective_branch_id = changes.get("branch_id", requirement.branch_id)
        effective_department_id = changes.get("department_id", requirement.department_id)
        _validate_branch_and_department(db, effective_branch_id, effective_department_id)

    before = {c.name: getattr(requirement, c.name) for c in requirement.__table__.columns}
    for field, value in changes.items():
        setattr(requirement, field, value)

    if payload.items is not None:
        effective_branch_id = changes.get("branch_id", requirement.branch_id)
        for item in payload.items:
            _validate_item_reference(db, item, branch_id=effective_branch_id)
        db.query(RequirementItem).filter(RequirementItem.requirement_id == requirement.id).delete()
        for item in payload.items:
            db.add(RequirementItem(requirement_id=requirement.id, **item.model_dump()))

    audit_record(db, user_id=user_id, action="UPDATE", entity="requirement", entity_id=requirement.id,
                 old_value=before, new_value=changes)
    db.commit()
    db.refresh(requirement)
    return requirement


def _transition(
    db: Session, requirement: Requirement, *, to_status: str, user_id: str, notes: str | None = None,
) -> None:
    allowed = ALLOWED_TRANSITIONS.get(requirement.status, set())
    if to_status not in allowed:
        raise RequirementStateError(
            f"cannot move requirement from {requirement.status} to {to_status}"
        )
    from_status = requirement.status
    requirement.status = to_status
    db.add(RequirementStatusHistory(
        requirement_id=requirement.id, from_status=from_status, to_status=to_status,
        changed_by=user_id, notes=notes,
    ))


def submit_requirement(db: Session, requirement: Requirement, *, user_id: str) -> Requirement:
    """DRAFT -> SUBMITTED -> PENDING_MANAGER_APPROVAL in one call (both transitions are
    logged to status history individually), matching the lifecycle named in the spec
    while leaving the manager with a single actionable status to filter on."""
    if not requirement.items:
        raise RequirementServiceError("a requirement must contain at least one item to be submitted")
    _transition(db, requirement, to_status="SUBMITTED", user_id=user_id)
    _transition(db, requirement, to_status="PENDING_MANAGER_APPROVAL", user_id=user_id)

    audit_record(db, user_id=user_id, action="SUBMIT", entity="requirement", entity_id=requirement.id,
                 old_value={"status": "DRAFT"}, new_value={"status": requirement.status})
    db.commit()
    db.refresh(requirement)
    dispatch_event_safe(db, EVENT_REQUIREMENT_SUBMITTED, requirement=requirement, user_id=user_id)
    return requirement


def _apply_condemnation_items(db: Session, requirement: Requirement, *, manager_id: str) -> list[tuple]:
    """Approved CONDEMNATION items transition their target Asset to CONDEMNED as part
    of the same approval transaction — AssetHistory + audit trail included, no
    fulfillment/delivery step (condemnation removes a device from service, it doesn't
    deliver one). Called before commit inside approve_requirement() so a problem here
    (missing asset) fails the whole approval rather than leaving it half-applied.

    Returns (asset, history_entry, previous) for each asset actually condemned, so
    approve_requirement can raise the Phase 2 ASSET_CONDEMNED event AFTER the approval
    commits — never inside this transaction, which must not depend on messaging.
    """
    condemned: list[tuple] = []
    for item in requirement.items:
        if item.item_type != "CONDEMNATION":
            continue
        if not item.asset_id:
            raise RequirementCondemnationError(f"condemnation item {item.id} has no asset_id — cannot condemn")
        asset = db.get(Asset, item.asset_id)
        if asset is None:
            raise RequirementCondemnationError(
                f"condemnation item {item.id} references asset {item.asset_id}, which no longer exists"
            )
        if asset.status == "CONDEMNED":
            continue  # already condemned (e.g. a duplicate condemnation requirement) — idempotent no-op
        before = {c.name: getattr(asset, c.name) for c in asset.__table__.columns}
        before_status = asset.status
        asset.status = "CONDEMNED"
        entry = record_asset_history(
            db, asset_id=asset.id, event_type="STATUS_CHANGE", changed_by=manager_id,
            from_value=before_status, to_value="CONDEMNED",
            notes=f"Approved condemnation requirement {requirement.request_number}",
        )
        audit_record(
            db, user_id=manager_id, action="CONDEMN", entity="asset", entity_id=asset.id,
            old_value={"status": before_status},
            new_value={"status": "CONDEMNED", "requirement_id": requirement.id},
        )
        condemned.append((asset, entry, before))
    return condemned


def approve_requirement(
    db: Session, requirement: Requirement, payload: RequirementDecisionIn, *, manager_id: str,
) -> Requirement:
    if requirement.requester_id == manager_id:
        raise RequirementPermissionError("a user cannot approve their own requirement")

    from_status = requirement.status
    _transition(db, requirement, to_status="APPROVED", user_id=manager_id, notes=payload.remarks)
    requirement.manager_id = manager_id
    requirement.manager_decision = "APPROVED"
    requirement.manager_decision_at = datetime.now(timezone.utc).isoformat()
    requirement.manager_remarks = payload.remarks

    audit_record(db, user_id=manager_id, action="APPROVE", entity="requirement", entity_id=requirement.id,
                 old_value={"status": from_status}, new_value={"status": "APPROVED", "remarks": payload.remarks})
    condemned = _apply_condemnation_items(db, requirement, manager_id=manager_id)
    db.commit()
    db.refresh(requirement)
    dispatch_event_safe(db, EVENT_REQUIREMENT_APPROVED, requirement=requirement, user_id=manager_id)
    # Phase 2 — the device really is out of service now. Keyed on the AssetHistory row
    # that recorded the transition, so the condemnation message and the authoritative
    # asset history describe the same event; the approval itself is already committed
    # and can never be undone by a delivery problem here.
    for asset, entry, before in condemned:
        asset_event_service.dispatch_for_history(
            db, asset=asset, entries=[entry], previous=before, user_id=manager_id,
            reference=requirement.request_number,
        )
    return requirement


def reject_requirement(
    db: Session, requirement: Requirement, payload: RequirementDecisionIn, *, manager_id: str,
) -> Requirement:
    if requirement.requester_id == manager_id:
        raise RequirementPermissionError("a user cannot reject their own requirement")

    from_status = requirement.status
    _transition(db, requirement, to_status="REJECTED", user_id=manager_id, notes=payload.remarks)
    requirement.manager_id = manager_id
    requirement.manager_decision = "REJECTED"
    requirement.manager_decision_at = datetime.now(timezone.utc).isoformat()
    requirement.manager_remarks = payload.remarks

    audit_record(db, user_id=manager_id, action="REJECT", entity="requirement", entity_id=requirement.id,
                 old_value={"status": from_status}, new_value={"status": "REJECTED", "remarks": payload.remarks})
    db.commit()
    db.refresh(requirement)
    dispatch_event_safe(db, EVENT_REQUIREMENT_REJECTED, requirement=requirement, user_id=manager_id)
    return requirement


def delete_requirement(db: Session, requirement: Requirement, *, user_id: str) -> None:
    """Final admin/inventory/vendor/dashboard polish pass — Part 7: Super Admin
    (requirement.delete) hard delete. Only ever allowed while the requirement is still
    DRAFT and has no linked FulfillmentRequest (1:1 via requirement_id, only ever
    created once a requirement is APPROVED — so a DRAFT requirement never actually has
    one, this check is defensive). Any other status must use the existing `/cancel`
    endpoint instead — CANCELLED is this codebase's one safe archive mechanism for a
    requirement that has left DRAFT; this function deliberately does not add a second
    one. Cascades RequirementItem/RequirementStatusHistory the same way the ORM
    relationship's cascade="all, delete-orphan" already does for `Requirement.items`;
    RequirementStatusHistory has no ORM relationship/cascade defined, so its rows are
    deleted explicitly."""
    if requirement.status != "DRAFT":
        raise RequirementStateError(
            f"cannot delete a requirement in status {requirement.status} — use /cancel instead"
        )
    linked_fulfillment = (
        db.query(FulfillmentRequest).filter(FulfillmentRequest.requirement_id == requirement.id).first()
    )
    if linked_fulfillment is not None:
        raise RequirementStateError("cannot delete a requirement with a linked fulfillment request")

    before = {c.name: getattr(requirement, c.name) for c in requirement.__table__.columns}
    db.query(RequirementStatusHistory).filter(RequirementStatusHistory.requirement_id == requirement.id).delete()
    audit_record(db, user_id=user_id, action="DELETE", entity="requirement", entity_id=requirement.id, old_value=before)
    db.delete(requirement)
    db.commit()


def cancel_requirement(
    db: Session, requirement: Requirement, payload: RequirementCancelIn, *, user_id: str,
) -> Requirement:
    from_status = requirement.status
    _transition(db, requirement, to_status="CANCELLED", user_id=user_id, notes=payload.remarks)

    audit_record(db, user_id=user_id, action="CANCEL", entity="requirement", entity_id=requirement.id,
                 old_value={"status": from_status}, new_value={"status": "CANCELLED", "remarks": payload.remarks})
    db.commit()
    db.refresh(requirement)
    dispatch_event_safe(db, EVENT_REQUIREMENT_CANCELLED, requirement=requirement, user_id=user_id)
    return requirement
