from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..models.device import Asset
from ..models.fulfillment import FulfillmentItem, FulfillmentItemAsset, FulfillmentRequest, FulfillmentStatusHistory
from ..models.inventory import Consumable, InventoryLocation, InventoryTransaction
from ..models.requirement import Requirement, RequirementItem
from ..schemas.fulfillment import (
    SERIALIZED_ITEM_TYPES,
    FulfillmentAllocateIn,
    FulfillmentCancelIn,
    FulfillmentCreateIn,
    FulfillmentDeliverIn,
)
from ..schemas.notification import (
    EVENT_FULFILLMENT_CANCELLED,
    EVENT_FULFILLMENT_CREATED,
    EVENT_FULFILLMENT_DELIVERED,
    EVENT_FULFILLMENT_DELIVERY_DATE_CHANGED,
    EVENT_FULFILLMENT_COMPLETED,
    EVENT_FULFILLMENT_OUT_FOR_DELIVERY,
    EVENT_FULFILLMENT_PACKED,
    EVENT_FULFILLMENT_UNDER_PROCESS,
)
from ..services import asset_event_service, inventory_service
from ..services.asset_history_service import record_auto_history_for_update
from ..services.asset_lifecycle_service import is_asset_available_for_allocation
from ..services.communication_service import dispatch_event_safe

# Valid forward transitions. COMPLETED/CANCELLED are terminal. Cancellation is only
# reachable up through PACKED — once a fulfillment is OUT_FOR_DELIVERY the goods have
# physically left, so "cancel" no longer makes sense (out of scope per spec: "cancel
# where valid").
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "PENDING_ALLOCATION": {"ALLOCATED", "CANCELLED"},
    "ALLOCATED": {"UNDER_PROCESS", "CANCELLED"},
    "UNDER_PROCESS": {"PACKED", "CANCELLED"},
    "PACKED": {"OUT_FOR_DELIVERY", "CANCELLED"},
    "OUT_FOR_DELIVERY": {"DELIVERED"},
    "DELIVERED": {"COMPLETED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}


class FulfillmentServiceError(Exception):
    """Generic 422-mappable validation failure."""


class FulfillmentReferenceError(FulfillmentServiceError):
    """A referenced entity (requirement/requirement item/asset/location) does not
    exist — 404-mappable."""


class FulfillmentStateError(FulfillmentServiceError):
    """An action was attempted from a status that does not allow it — 409-mappable."""


class FulfillmentAvailabilityError(FulfillmentServiceError):
    """Requested allocation exceeds real availability, or an asset is already
    allocated elsewhere — 409-mappable."""


def _next_fulfillment_number(db: Session) -> str:
    count = db.query(FulfillmentRequest).count()
    for attempt in range(count + 1, count + 1000):
        candidate = f"FUL-{attempt:06d}"
        if db.query(FulfillmentRequest).filter(FulfillmentRequest.fulfillment_number == candidate).first() is None:
            return candidate
    raise FulfillmentServiceError("unable to allocate a fulfillment number")


def _reserved_quantity(db: Session, ref_field: str, ref_id: str, *, exclude_fulfillment_id: str | None) -> int:
    """Sum of allocated_quantity across every FulfillmentItem referencing the same
    inventory item / consumable, on a fulfillment that is still active (not
    CANCELLED), excluding `exclude_fulfillment_id` (the caller's own fulfillment — an
    allocate call replaces that fulfillment's own reservation rather than adding to
    it, so its current holding must not count against itself)."""
    query = (
        db.query(FulfillmentItem)
        .join(RequirementItem, FulfillmentItem.requirement_item_id == RequirementItem.id)
        .join(FulfillmentRequest, FulfillmentItem.fulfillment_id == FulfillmentRequest.id)
        .filter(getattr(RequirementItem, ref_field) == ref_id)
        .filter(FulfillmentRequest.status != "CANCELLED")
    )
    if exclude_fulfillment_id:
        query = query.filter(FulfillmentRequest.id != exclude_fulfillment_id)
    return sum(item.allocated_quantity for item in query.all())


def compute_item_availability(
    db: Session, requirement_item: RequirementItem, *, exclude_fulfillment_id: str | None = None,
) -> int | None:
    """Available quantity for a quantity-tracked (non-serialized) item, or None if
    this item_type has no real stock ledger in this schema (OTHER — free text,
    never blocked). Serialized DEVICE/DEVICE_MODEL availability is asset-based —
    see compute_available_assets."""
    if requirement_item.item_type == "SPARE_PART" and requirement_item.inventory_item_id:
        total = inventory_service.compute_current_stock(db, requirement_item.inventory_item_id)
        reserved = _reserved_quantity(
            db, "inventory_item_id", requirement_item.inventory_item_id, exclude_fulfillment_id=exclude_fulfillment_id,
        )
        return max(total - reserved, 0)
    if requirement_item.item_type == "CONSUMABLE" and requirement_item.consumable_id:
        consumable = db.get(Consumable, requirement_item.consumable_id)
        total = (consumable.opening_quantity or 0) if consumable else 0
        reserved = _reserved_quantity(
            db, "consumable_id", requirement_item.consumable_id, exclude_fulfillment_id=exclude_fulfillment_id,
        )
        return max(total - reserved, 0)
    return None


def compute_available_assets(
    db: Session, device_model_id: str, *, exclude_fulfillment_id: str | None = None,
) -> list[Asset]:
    """Assets matching device_model_id that are not already linked (via a
    FulfillmentItemAsset row) to another active (non-CANCELLED) fulfillment, and are
    not CONDEMNED/RETIRED (same canonical eligibility check as the central-warehouse
    device stock summary — see asset_lifecycle_service.is_asset_available_for_allocation).
    A condemned/retired asset must never be offered for allocation to a requirement,
    same as it's excluded from the central stock count."""
    reserved_query = (
        db.query(FulfillmentItemAsset.asset_id)
        .join(FulfillmentItem, FulfillmentItemAsset.fulfillment_item_id == FulfillmentItem.id)
        .join(FulfillmentRequest, FulfillmentItem.fulfillment_id == FulfillmentRequest.id)
        .filter(FulfillmentRequest.status != "CANCELLED")
    )
    if exclude_fulfillment_id:
        reserved_query = reserved_query.filter(FulfillmentRequest.id != exclude_fulfillment_id)
    reserved_ids = {row[0] for row in reserved_query.all()}
    assets = db.query(Asset).filter(Asset.device_model_id == device_model_id).all()
    return [a for a in assets if a.id not in reserved_ids and is_asset_available_for_allocation(a)]


def _transition(db: Session, fulfillment: FulfillmentRequest, *, to_status: str, user_id: str, notes: str | None = None) -> None:
    allowed = ALLOWED_TRANSITIONS.get(fulfillment.status, set())
    if to_status not in allowed:
        raise FulfillmentStateError(f"cannot move fulfillment from {fulfillment.status} to {to_status}")
    from_status = fulfillment.status
    fulfillment.status = to_status
    db.add(FulfillmentStatusHistory(
        fulfillment_id=fulfillment.id, from_status=from_status, to_status=to_status,
        changed_by=user_id, notes=notes,
    ))


def get_active_fulfillment_for_requirement(db: Session, requirement_id: str) -> FulfillmentRequest | None:
    return (
        db.query(FulfillmentRequest)
        .filter(FulfillmentRequest.requirement_id == requirement_id, FulfillmentRequest.status != "CANCELLED")
        .first()
    )


def create_fulfillment(db: Session, payload: FulfillmentCreateIn, *, user_id: str) -> FulfillmentRequest:
    requirement = db.get(Requirement, payload.requirement_id)
    if requirement is None:
        raise FulfillmentReferenceError("Requirement not found")
    if requirement.status != "APPROVED":
        raise FulfillmentStateError("only an APPROVED requirement can enter fulfillment")
    if get_active_fulfillment_for_requirement(db, requirement.id) is not None:
        raise FulfillmentStateError("this requirement already has an active fulfillment")
    if payload.source_location_id is not None and db.get(InventoryLocation, payload.source_location_id) is None:
        raise FulfillmentReferenceError("Inventory location not found")

    fulfillment = FulfillmentRequest(
        fulfillment_number=_next_fulfillment_number(db),
        requirement_id=requirement.id,
        status="PENDING_ALLOCATION",
        source_location_id=payload.source_location_id,
        inventory_manager_id=user_id,
        expected_delivery_date=payload.expected_delivery_date,
    )
    db.add(fulfillment)
    db.flush()

    requirement_items = db.query(RequirementItem).filter(RequirementItem.requirement_id == requirement.id).all()
    for req_item in requirement_items:
        db.add(FulfillmentItem(fulfillment_id=fulfillment.id, requirement_item_id=req_item.id))

    db.add(FulfillmentStatusHistory(
        fulfillment_id=fulfillment.id, from_status=None, to_status="PENDING_ALLOCATION", changed_by=user_id,
    ))
    audit_record(db, user_id=user_id, action="CREATE", entity="fulfillment", entity_id=fulfillment.id,
                 new_value=payload.model_dump())
    db.commit()
    db.refresh(fulfillment)
    dispatch_event_safe(db, EVENT_FULFILLMENT_CREATED, requirement=requirement, fulfillment=fulfillment, user_id=user_id)
    return fulfillment


def allocate_fulfillment(db: Session, fulfillment: FulfillmentRequest, payload: FulfillmentAllocateIn, *, user_id: str) -> FulfillmentRequest:
    if fulfillment.status not in ("PENDING_ALLOCATION", "ALLOCATED"):
        raise FulfillmentStateError(f"cannot allocate a fulfillment in status {fulfillment.status}")

    fulfillment_items_by_req_item = {
        fi.requirement_item_id: fi
        for fi in db.query(FulfillmentItem).filter(FulfillmentItem.fulfillment_id == fulfillment.id).all()
    }

    # Pass 1: validate every entry before mutating anything — if any entry fails,
    # nothing about this fulfillment has changed yet.
    planned: list[dict] = []
    seen_requirement_item_ids: set[str] = set()
    claimed_assets_in_call: set[str] = set()
    for entry in payload.items:
        if entry.requirement_item_id in seen_requirement_item_ids:
            raise FulfillmentServiceError(f"duplicate requirement_item_id in allocation: {entry.requirement_item_id}")
        seen_requirement_item_ids.add(entry.requirement_item_id)

        fulfillment_item = fulfillment_items_by_req_item.get(entry.requirement_item_id)
        if fulfillment_item is None:
            raise FulfillmentReferenceError(f"requirement_item_id not on this fulfillment: {entry.requirement_item_id}")
        requirement_item = db.get(RequirementItem, entry.requirement_item_id)

        if requirement_item.item_type in SERIALIZED_ITEM_TYPES:
            if not entry.asset_ids:
                raise FulfillmentServiceError(f"asset_ids is required for serialized item {entry.requirement_item_id}")
            if len(set(entry.asset_ids)) != len(entry.asset_ids):
                raise FulfillmentServiceError("duplicate asset_ids in allocation entry")
            if len(entry.asset_ids) > requirement_item.requested_quantity:
                raise FulfillmentAvailabilityError(
                    f"cannot allocate {len(entry.asset_ids)} assets, only {requirement_item.requested_quantity} requested"
                )
            if set(entry.asset_ids) & claimed_assets_in_call:
                raise FulfillmentAvailabilityError("the same asset was requested for more than one item in this call")
            claimed_assets_in_call |= set(entry.asset_ids)

            currently_linked = {link.asset_id for link in fulfillment_item.assets}
            newly_requested = set(entry.asset_ids) - currently_linked
            if newly_requested:
                available_ids = {
                    a.id for a in compute_available_assets(db, requirement_item.device_model_id, exclude_fulfillment_id=fulfillment.id)
                }
                for asset_id in newly_requested:
                    asset = db.get(Asset, asset_id)
                    if asset is None:
                        raise FulfillmentReferenceError(f"Asset not found: {asset_id}")
                    if asset.device_model_id != requirement_item.device_model_id:
                        raise FulfillmentServiceError(f"Asset {asset_id} does not match the requested device model")
                    if asset_id not in available_ids:
                        raise FulfillmentAvailabilityError(f"Asset {asset_id} is already allocated to another fulfillment")
            planned.append({
                "kind": "assets", "fulfillment_item": fulfillment_item,
                "asset_ids": list(entry.asset_ids), "currently_linked": currently_linked,
            })
        else:
            if entry.quantity is None:
                raise FulfillmentServiceError(f"quantity is required for item {entry.requirement_item_id}")
            if entry.quantity > requirement_item.requested_quantity:
                raise FulfillmentAvailabilityError(
                    f"cannot allocate {entry.quantity}, only {requirement_item.requested_quantity} requested"
                )
            available = compute_item_availability(db, requirement_item, exclude_fulfillment_id=fulfillment.id)
            if available is not None and entry.quantity > available:
                raise FulfillmentAvailabilityError(
                    f"cannot allocate {entry.quantity} for item {entry.requirement_item_id}, only {available} available"
                )
            planned.append({"kind": "quantity", "fulfillment_item": fulfillment_item, "quantity": entry.quantity})

    # Pass 2: apply.
    for plan in planned:
        fulfillment_item = plan["fulfillment_item"]
        if plan["kind"] == "assets":
            new_ids = set(plan["asset_ids"])
            for link in list(fulfillment_item.assets):
                if link.asset_id not in new_ids:
                    db.delete(link)
            for asset_id in new_ids - plan["currently_linked"]:
                db.add(FulfillmentItemAsset(fulfillment_item_id=fulfillment_item.id, asset_id=asset_id))
            fulfillment_item.allocated_quantity = len(new_ids)
        else:
            fulfillment_item.allocated_quantity = plan["quantity"]

    if payload.source_location_id is not None:
        fulfillment.source_location_id = payload.source_location_id
    delivery_date_before = fulfillment.expected_delivery_date
    if payload.expected_delivery_date is not None:
        fulfillment.expected_delivery_date = payload.expected_delivery_date
    delivery_date_changed = payload.expected_delivery_date is not None and payload.expected_delivery_date != delivery_date_before

    if fulfillment.status == "PENDING_ALLOCATION":
        _transition(db, fulfillment, to_status="ALLOCATED", user_id=user_id)

    audit_record(db, user_id=user_id, action="ALLOCATE", entity="fulfillment", entity_id=fulfillment.id,
                 new_value=payload.model_dump())
    db.commit()
    db.refresh(fulfillment)
    if delivery_date_changed:
        requirement = db.get(Requirement, fulfillment.requirement_id)
        dispatch_event_safe(db, EVENT_FULFILLMENT_DELIVERY_DATE_CHANGED, requirement=requirement, fulfillment=fulfillment, user_id=user_id)
    return fulfillment


def process_fulfillment(db: Session, fulfillment: FulfillmentRequest, *, user_id: str) -> FulfillmentRequest:
    if fulfillment.status == "ALLOCATED" and not any(item.allocated_quantity > 0 for item in fulfillment.items):
        raise FulfillmentStateError("at least one item must be allocated before processing")
    from_status = fulfillment.status
    _transition(db, fulfillment, to_status="UNDER_PROCESS", user_id=user_id)
    audit_record(db, user_id=user_id, action="PROCESS", entity="fulfillment", entity_id=fulfillment.id,
                 old_value={"status": from_status}, new_value={"status": "UNDER_PROCESS"})
    db.commit()
    db.refresh(fulfillment)
    requirement = db.get(Requirement, fulfillment.requirement_id)
    dispatch_event_safe(db, EVENT_FULFILLMENT_UNDER_PROCESS, requirement=requirement, fulfillment=fulfillment, user_id=user_id)
    return fulfillment


def pack_fulfillment(db: Session, fulfillment: FulfillmentRequest, *, user_id: str) -> FulfillmentRequest:
    from_status = fulfillment.status
    _transition(db, fulfillment, to_status="PACKED", user_id=user_id)
    audit_record(db, user_id=user_id, action="PACK", entity="fulfillment", entity_id=fulfillment.id,
                 old_value={"status": from_status}, new_value={"status": "PACKED"})
    db.commit()
    db.refresh(fulfillment)
    requirement = db.get(Requirement, fulfillment.requirement_id)
    dispatch_event_safe(db, EVENT_FULFILLMENT_PACKED, requirement=requirement, fulfillment=fulfillment, user_id=user_id)
    return fulfillment


def dispatch_fulfillment(db: Session, fulfillment: FulfillmentRequest, *, user_id: str) -> FulfillmentRequest:
    from_status = fulfillment.status
    _transition(db, fulfillment, to_status="OUT_FOR_DELIVERY", user_id=user_id)
    audit_record(db, user_id=user_id, action="OUT_FOR_DELIVERY", entity="fulfillment", entity_id=fulfillment.id,
                 old_value={"status": from_status}, new_value={"status": "OUT_FOR_DELIVERY"})
    db.commit()
    db.refresh(fulfillment)
    requirement = db.get(Requirement, fulfillment.requirement_id)
    dispatch_event_safe(db, EVENT_FULFILLMENT_OUT_FOR_DELIVERY, requirement=requirement, fulfillment=fulfillment, user_id=user_id)
    return fulfillment


def deliver_fulfillment(db: Session, fulfillment: FulfillmentRequest, payload: FulfillmentDeliverIn, *, user_id: str) -> FulfillmentRequest:
    from_status = fulfillment.status
    _transition(db, fulfillment, to_status="DELIVERED", user_id=user_id, notes=payload.delivery_remarks)
    fulfillment.actual_delivery_date = payload.actual_delivery_date or datetime.now(timezone.utc).date().isoformat()
    fulfillment.delivered_by_id = user_id
    fulfillment.delivery_remarks = payload.delivery_remarks
    audit_record(
        db, user_id=user_id, action="DELIVER", entity="fulfillment", entity_id=fulfillment.id,
        old_value={"status": from_status},
        new_value={"status": "DELIVERED", "actual_delivery_date": fulfillment.actual_delivery_date},
    )
    db.commit()
    db.refresh(fulfillment)
    requirement = db.get(Requirement, fulfillment.requirement_id)
    dispatch_event_safe(db, EVENT_FULFILLMENT_DELIVERED, requirement=requirement, fulfillment=fulfillment, user_id=user_id)
    return fulfillment


def complete_fulfillment(db: Session, fulfillment: FulfillmentRequest, *, user_id: str) -> FulfillmentRequest:
    """The 'inventory stock update' stage: commits every reservation made during
    allocation into real state — InventoryTransaction STOCK_OUT rows for spare parts,
    Consumable.opening_quantity deduction, and Asset relocation (+ AssetHistory) for
    serialized devices. Nothing here runs before DELIVERED is confirmed, matching
    'deduct consumable stock only at the correct delivery stage.'"""
    requirement = db.get(Requirement, fulfillment.requirement_id)
    items = db.query(FulfillmentItem).filter(FulfillmentItem.fulfillment_id == fulfillment.id).all()

    # Pass 1: defensive re-validation against concurrent depletion since allocation
    # only reserved (never committed) stock.
    for item in items:
        if item.allocated_quantity <= 0:
            continue
        requirement_item = db.get(RequirementItem, item.requirement_item_id)
        if requirement_item.item_type == "SPARE_PART":
            current_stock = inventory_service.compute_current_stock(db, requirement_item.inventory_item_id)
            if current_stock < item.allocated_quantity:
                raise FulfillmentAvailabilityError(
                    f"Insufficient stock to complete: item {requirement_item.inventory_item_id} has "
                    f"{current_stock}, need {item.allocated_quantity}"
                )
        elif requirement_item.item_type == "CONSUMABLE":
            consumable = db.get(Consumable, requirement_item.consumable_id)
            available = (consumable.opening_quantity or 0) if consumable else 0
            if available < item.allocated_quantity:
                raise FulfillmentAvailabilityError(
                    f"Insufficient consumable stock to complete: {requirement_item.consumable_id} has "
                    f"{available}, need {item.allocated_quantity}"
                )

    # Pass 2: apply — this is the only place stock/asset state actually changes.
    allocated: list[tuple[Asset, dict]] = []
    for item in items:
        if item.allocated_quantity <= 0:
            continue
        requirement_item = db.get(RequirementItem, item.requirement_item_id)
        if requirement_item.item_type == "SPARE_PART":
            db.add(InventoryTransaction(
                item_id=requirement_item.inventory_item_id, location_id=fulfillment.source_location_id,
                transaction_type="STOCK_OUT", quantity=item.allocated_quantity,
                reference=fulfillment.fulfillment_number, performed_by=user_id,
            ))
        elif requirement_item.item_type == "CONSUMABLE":
            consumable = db.get(Consumable, requirement_item.consumable_id)
            inventory_service.transfer_consumable_stock(
                db, consumable=consumable, destination_branch_id=requirement.branch_id,
                quantity=item.allocated_quantity, requirement_id=requirement.id,
                reference=fulfillment.fulfillment_number, user_id=user_id,
            )
        elif requirement_item.item_type in SERIALIZED_ITEM_TYPES:
            for link in item.assets:
                if link.delivered:
                    continue
                asset = db.get(Asset, link.asset_id)
                before = {c.name: getattr(asset, c.name) for c in asset.__table__.columns}
                asset.branch_id = requirement.branch_id
                asset.department_id = requirement.department_id
                # Module 19 fix: "Active" was not a member of LIFECYCLE_STATUSES
                # (schemas/asset_lifecycle.py), so every asset delivered through
                # fulfillment silently dropped out of the dashboard's operational
                # count (_OPERATIONAL_VALUES only recognizes "OPERATIONAL").
                asset.status = "OPERATIONAL"
                record_auto_history_for_update(db, before=before, after=asset, changed_by=user_id)
                link.delivered = True
                # Phase 2 — remember what this asset looked like before it was issued,
                # so the allocation message can name the branch it came from. Dispatch
                # itself happens after commit, below.
                allocated.append((asset, before))
        item.delivered_quantity = item.allocated_quantity

    from_status = fulfillment.status
    _transition(db, fulfillment, to_status="COMPLETED", user_id=user_id)
    audit_record(db, user_id=user_id, action="COMPLETE", entity="fulfillment", entity_id=fulfillment.id,
                 old_value={"status": from_status}, new_value={"status": "COMPLETED"})
    db.commit()
    db.refresh(fulfillment)
    dispatch_event_safe(db, EVENT_FULFILLMENT_COMPLETED, requirement=requirement, fulfillment=fulfillment, user_id=user_id)
    # Phase 2 — one ASSET_ALLOCATED per physical device actually issued by this
    # fulfillment. This is the point the device becomes the destination branch's, so
    # it is the only place the event can honestly fire. The asset's branch/status
    # changes above already wrote their own AssetHistory rows; those are the asset
    # trail, this is the communication event for the allocation as a whole, keyed on
    # the fulfillment so a retried completion never notifies twice.
    for asset, before in allocated:
        asset_event_service.dispatch_allocation(
            db, asset=asset, fulfillment_id=fulfillment.id,
            reference=fulfillment.fulfillment_number, previous=before, user_id=user_id,
        )
    return fulfillment


def cancel_fulfillment(db: Session, fulfillment: FulfillmentRequest, payload: FulfillmentCancelIn, *, user_id: str) -> FulfillmentRequest:
    from_status = fulfillment.status
    _transition(db, fulfillment, to_status="CANCELLED", user_id=user_id, notes=payload.remarks)
    fulfillment.cancel_remarks = payload.remarks
    audit_record(db, user_id=user_id, action="CANCEL", entity="fulfillment", entity_id=fulfillment.id,
                 old_value={"status": from_status}, new_value={"status": "CANCELLED", "remarks": payload.remarks})
    db.commit()
    db.refresh(fulfillment)
    requirement = db.get(Requirement, fulfillment.requirement_id)
    dispatch_event_safe(db, EVENT_FULFILLMENT_CANCELLED, requirement=requirement, fulfillment=fulfillment, user_id=user_id)
    return fulfillment


def to_fulfillment_item_out(
    db: Session, item: FulfillmentItem, requirement_item: RequirementItem, *, fulfillment_id: str,
    mask_stock: bool = False,
) -> dict:
    is_stock_backed = requirement_item.item_type not in SERIALIZED_ITEM_TYPES
    if requirement_item.item_type in SERIALIZED_ITEM_TYPES and requirement_item.device_model_id:
        available = len(compute_available_assets(db, requirement_item.device_model_id, exclude_fulfillment_id=fulfillment_id))
    else:
        available = compute_item_availability(db, requirement_item, exclude_fulfillment_id=fulfillment_id)
    # inventory.picker_read callers (TL/Unit Head/Hospital-Unit-Manager) may see that
    # a requirement item exists, but must never learn the central warehouse/branch
    # stock level behind it — available_quantity for a SPARE_PART/CONSUMABLE item is
    # exactly that (current stock minus reservations), same value strip_stock_fields()
    # hides on the /inventory endpoints. Device-model asset availability is a count of
    # physical assets, not warehouse stock, so it stays visible.
    if mask_stock and is_stock_backed:
        available = None
    return {
        "id": item.id,
        "fulfillment_id": item.fulfillment_id,
        "requirement_item_id": item.requirement_item_id,
        "item_type": requirement_item.item_type,
        "device_model_id": requirement_item.device_model_id,
        "inventory_item_id": requirement_item.inventory_item_id,
        "consumable_id": requirement_item.consumable_id,
        "description": requirement_item.description,
        "requested_quantity": requirement_item.requested_quantity,
        "allocated_quantity": item.allocated_quantity,
        "delivered_quantity": item.delivered_quantity,
        "pending_quantity": max(requirement_item.requested_quantity - item.allocated_quantity, 0),
        "available_quantity": available,
        "unit": requirement_item.unit,
        "assets": [
            {"id": a.id, "fulfillment_item_id": a.fulfillment_item_id, "asset_id": a.asset_id, "delivered": a.delivered}
            for a in item.assets
        ],
    }


def to_fulfillment_out(db: Session, fulfillment: FulfillmentRequest, *, mask_stock: bool = False) -> dict:
    items = db.query(FulfillmentItem).filter(FulfillmentItem.fulfillment_id == fulfillment.id).all()
    item_outs = []
    for item in items:
        requirement_item = db.get(RequirementItem, item.requirement_item_id)
        item_outs.append(
            to_fulfillment_item_out(db, item, requirement_item, fulfillment_id=fulfillment.id, mask_stock=mask_stock)
        )
    return {
        "id": fulfillment.id,
        "fulfillment_number": fulfillment.fulfillment_number,
        "requirement_id": fulfillment.requirement_id,
        "status": fulfillment.status,
        "source_location_id": fulfillment.source_location_id,
        "inventory_manager_id": fulfillment.inventory_manager_id,
        "expected_delivery_date": fulfillment.expected_delivery_date,
        "actual_delivery_date": fulfillment.actual_delivery_date,
        "delivered_by_id": fulfillment.delivered_by_id,
        "delivery_remarks": fulfillment.delivery_remarks,
        "cancel_remarks": fulfillment.cancel_remarks,
        "created_at": fulfillment.created_at,
        "updated_at": fulfillment.updated_at,
        "items": item_outs,
    }
