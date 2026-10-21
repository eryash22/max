from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..models.device import DeviceModel
from ..models.inventory import (
    Consumable, ConsumableTransaction, InventoryItem, InventoryLocation, InventoryTransaction,
    SparePartCompatibility,
)
from ..models.requirement import RequirementItem
from ..schemas.inventory import (
    NEAR_EXPIRY_WINDOW_DAYS, ConsumableUpdateIn, InventoryItemIn, InventoryItemUpdateIn, InventoryLocationIn,
    InventoryLocationUpdateIn, InventoryTransactionIn,
)


class InventoryServiceError(Exception):
    """FK-validation or business-rule failure — mapped to 422 by the router."""


class InsufficientStockError(Exception):
    """STOCK_OUT (or a negative ADJUSTMENT) would drive current_stock below zero — 409."""


class LocationInUseError(Exception):
    """A location cannot be deleted while any transaction still references it — 409."""


class ConsumableInUseError(Exception):
    """Batch F — a consumable cannot be hard-deleted while any `ConsumableTransaction`
    ledger row or `RequirementItem` still references it — 409, directing the caller
    to deactivate (status=INACTIVE via the existing PATCH /consumables/{id} path)
    instead, same reference-check-then-hard-delete-or-reject pattern as
    LocationInUseError/delete_location above and
    routers/vendors.py:_vendor_reference_count / delete_vendor."""


def _today() -> date:
    return datetime.now(timezone.utc).date()


def compute_expiry_status(expiry_date: str | None, *, today: date | None = None) -> str:
    if not expiry_date:
        return "NOT_SET"
    today = today or _today()
    expiry = date.fromisoformat(expiry_date)
    if expiry < today:
        return "EXPIRED"
    if expiry <= today + timedelta(days=NEAR_EXPIRY_WINDOW_DAYS):
        return "NEAR_EXPIRY"
    return "OK"


def compute_current_stock(db: Session, item_id: str) -> int:
    """Derive current stock from transaction history — no stored quantity column exists
    on `InventoryItem`, per the existing Phase B schema. STOCK_IN adds, STOCK_OUT
    subtracts, ADJUSTMENT applies its (possibly negative) signed quantity directly.
    TRANSFER is excluded from the net total: `InventoryTransaction` has a single
    `location_id` column (no from/to pair), so a transfer relocates stock between
    locations rather than changing the item's total on-hand quantity — documented as a
    schema limitation in PROJECT_STATUS.md rather than invented as a from/to redesign."""
    transactions = db.query(InventoryTransaction).filter(InventoryTransaction.item_id == item_id).all()
    stock = 0
    for txn in transactions:
        if txn.transaction_type == "STOCK_IN":
            stock += txn.quantity
        elif txn.transaction_type == "STOCK_OUT":
            stock -= txn.quantity
        elif txn.transaction_type == "ADJUSTMENT":
            stock += txn.quantity
        # TRANSFER: no net change to total stock.
    return stock


def compute_stock_status(current_stock: int, item: InventoryItem) -> str:
    if current_stock <= 0:
        return "OUT_OF_STOCK"
    threshold = item.reorder_level if item.reorder_level is not None else item.min_level
    if threshold is not None and current_stock <= threshold:
        return "LOW_STOCK"
    if item.max_level is not None and current_stock > item.max_level:
        return "OVERSTOCK"
    return "NORMAL"


ITEM_STOCK_FIELDS = ("min_level", "max_level", "reorder_level", "unit_cost", "current_stock", "stock_status")
CONSUMABLE_STOCK_FIELDS = ("opening_quantity", "min_level", "reorder_level", "stock_status")


def strip_stock_fields(out: dict, *, is_consumable: bool) -> dict:
    """Null out quantity/stock fields for inventory.picker_read callers (TL/Unit
    Head) — they may list items/consumables by name to fill a requirement form, but
    must never receive central warehouse or branch stock quantities. Called from the
    router, not baked into to_item_out()/to_consumable_out(), so INVENTORY_READ
    callers (Inventory Manager, BME, Manager, Admin) keep seeing real values."""
    masked = dict(out)
    for field in (CONSUMABLE_STOCK_FIELDS if is_consumable else ITEM_STOCK_FIELDS):
        masked[field] = None
    return masked


def to_item_out(db: Session, item: InventoryItem) -> dict:
    current_stock = compute_current_stock(db, item.id)
    return {
        "id": item.id,
        "item_code": item.item_code,
        "name": item.name,
        "category": item.category,
        "unit": item.unit,
        "min_level": item.min_level,
        "max_level": item.max_level,
        "reorder_level": item.reorder_level,
        "unit_cost": item.unit_cost,
        "supplier": item.supplier,
        "is_consumable": item.is_consumable,
        "current_stock": current_stock,
        "stock_status": compute_stock_status(current_stock, item),
    }


def to_transaction_out(txn: InventoryTransaction) -> dict:
    return {
        "id": txn.id,
        "item_id": txn.item_id,
        "location_id": txn.location_id,
        "transaction_type": txn.transaction_type,
        "quantity": txn.quantity,
        "batch_number": txn.batch_number,
        "expiry_date": txn.expiry_date,
        "expiry_status": compute_expiry_status(txn.expiry_date),
        "reference": txn.reference,
        "performed_by": txn.performed_by,
        "created_at": txn.created_at.isoformat(),
    }


def create_item(db: Session, payload: InventoryItemIn, *, user_id: str) -> InventoryItem:
    data = payload.model_dump()
    item = InventoryItem(**data)
    db.add(item)
    db.flush()
    audit_record(db, user_id=user_id, action="CREATE", entity="inventory_item", entity_id=item.id, new_value=data)
    db.commit()
    db.refresh(item)
    return item


def update_item(db: Session, item: InventoryItem, payload: InventoryItemUpdateIn, *, user_id: str) -> InventoryItem:
    changes = payload.model_dump(exclude_unset=True)
    before = {c.name: getattr(item, c.name) for c in item.__table__.columns}
    for field, value in changes.items():
        setattr(item, field, value)
    audit_record(db, user_id=user_id, action="UPDATE", entity="inventory_item", entity_id=item.id,
                 old_value=before, new_value=changes)
    db.commit()
    db.refresh(item)
    return item


def create_transaction(db: Session, payload: InventoryTransactionIn, *, user_id: str) -> InventoryTransaction:
    item = db.get(InventoryItem, payload.item_id)
    if item is None:
        raise InventoryServiceError(f"Inventory item not found: {payload.item_id}")
    if payload.location_id is not None and db.get(InventoryLocation, payload.location_id) is None:
        raise InventoryServiceError(f"Inventory location not found: {payload.location_id}")

    data = payload.model_dump()

    if payload.transaction_type == "STOCK_OUT" or (payload.transaction_type == "ADJUSTMENT" and payload.quantity < 0):
        current_stock = compute_current_stock(db, item.id)
        delta = payload.quantity if payload.transaction_type == "STOCK_OUT" else -payload.quantity
        if current_stock - delta < 0:
            raise InsufficientStockError(
                f"Insufficient stock for item {item.item_code}: have {current_stock}, requested {delta}"
            )

    txn = InventoryTransaction(**data, performed_by=user_id)
    db.add(txn)
    db.flush()
    audit_record(db, user_id=user_id, action="CREATE", entity="inventory_transaction", entity_id=txn.id, new_value=data)
    db.commit()
    db.refresh(txn)
    return txn


def list_items(
    db: Session, *, page: int, page_size: int, search: str | None = None, category: str | None = None,
    is_consumable: bool | None = None, stock_status: str | None = None,
) -> tuple[list[dict], int]:
    query = db.query(InventoryItem)
    if category:
        query = query.filter(InventoryItem.category == category)
    if is_consumable is not None:
        query = query.filter(InventoryItem.is_consumable == is_consumable)
    if search:
        like = f"%{search}%"
        query = query.filter((InventoryItem.name.ilike(like)) | (InventoryItem.item_code.ilike(like)))

    all_items = query.order_by(InventoryItem.created_at.desc()).all()
    outs = [to_item_out(db, item) for item in all_items]
    if stock_status:
        outs = [o for o in outs if o["stock_status"] == stock_status]

    total = len(outs)
    start = (page - 1) * page_size
    return outs[start:start + page_size], total


def list_transactions(
    db: Session, *, page: int, page_size: int, item_id: str | None = None,
    transaction_type: str | None = None, batch_number: str | None = None,
    expiry_status: str | None = None,
) -> tuple[list[dict], int]:
    query = db.query(InventoryTransaction)
    if item_id:
        query = query.filter(InventoryTransaction.item_id == item_id)
    if transaction_type:
        query = query.filter(InventoryTransaction.transaction_type == transaction_type)
    if batch_number:
        query = query.filter(InventoryTransaction.batch_number == batch_number)

    all_txns = query.order_by(InventoryTransaction.created_at.desc()).all()
    outs = [to_transaction_out(t) for t in all_txns]
    if expiry_status:
        outs = [o for o in outs if o["expiry_status"] == expiry_status]

    total = len(outs)
    start = (page - 1) * page_size
    return outs[start:start + page_size], total


def create_location(db: Session, payload: InventoryLocationIn, *, user_id: str) -> InventoryLocation:
    data = payload.model_dump()
    location = InventoryLocation(**data)
    db.add(location)
    db.flush()
    audit_record(db, user_id=user_id, action="CREATE", entity="inventory_location", entity_id=location.id, new_value=data)
    db.commit()
    db.refresh(location)
    return location


def update_location(db: Session, location: InventoryLocation, payload: InventoryLocationUpdateIn, *, user_id: str) -> InventoryLocation:
    changes = payload.model_dump(exclude_unset=True)
    before = {c.name: getattr(location, c.name) for c in location.__table__.columns}
    for field, value in changes.items():
        setattr(location, field, value)
    audit_record(db, user_id=user_id, action="UPDATE", entity="inventory_location", entity_id=location.id,
                 old_value=before, new_value=changes)
    db.commit()
    db.refresh(location)
    return location


def delete_location(db: Session, location: InventoryLocation, *, user_id: str) -> None:
    in_use = db.query(InventoryTransaction).filter(InventoryTransaction.location_id == location.id).first()
    if in_use is not None:
        raise LocationInUseError(f"Location {location.name} is referenced by existing inventory transactions")
    before = {c.name: getattr(location, c.name) for c in location.__table__.columns}
    audit_record(db, user_id=user_id, action="DELETE", entity="inventory_location", entity_id=location.id, old_value=before)
    db.delete(location)
    db.commit()


def list_locations(
    db: Session, *, page: int, page_size: int, search: str | None = None, branch_id: str | None = None,
    kind: str | None = None,
) -> tuple[list[InventoryLocation], int]:
    query = db.query(InventoryLocation)
    if branch_id:
        query = query.filter(InventoryLocation.branch_id == branch_id)
    if kind:
        query = query.filter(InventoryLocation.kind == kind)
    if search:
        like = f"%{search}%"
        query = query.filter((InventoryLocation.name.ilike(like)) | (InventoryLocation.code.ilike(like)))

    total = query.count()
    items = query.order_by(InventoryLocation.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return items, total


def add_compatibility(db: Session, item_id: str, device_model_id: str, notes: str | None, *, user_id: str) -> SparePartCompatibility:
    if db.get(InventoryItem, item_id) is None:
        raise InventoryServiceError(f"Inventory item not found: {item_id}")
    if db.get(DeviceModel, device_model_id) is None:
        raise InventoryServiceError(f"Device model not found: {device_model_id}")
    existing = (
        db.query(SparePartCompatibility)
        .filter(
            SparePartCompatibility.inventory_item_id == item_id,
            SparePartCompatibility.device_model_id == device_model_id,
        )
        .first()
    )
    if existing is not None:
        raise ValueError("duplicate")

    link = SparePartCompatibility(inventory_item_id=item_id, device_model_id=device_model_id, notes=notes)
    db.add(link)
    db.flush()
    audit_record(
        db, user_id=user_id, action="CREATE", entity="spare_part_compatibility", entity_id=link.id,
        new_value={"inventory_item_id": item_id, "device_model_id": device_model_id, "notes": notes},
    )
    db.commit()
    db.refresh(link)
    return link


# ---- Consumables — stock status, updates, and the central<->branch transfer ledger ----

def compute_consumable_stock_status(consumable: Consumable) -> str:
    quantity = consumable.opening_quantity or 0
    if quantity <= 0:
        return "OUT_OF_STOCK"
    threshold = consumable.reorder_level if consumable.reorder_level is not None else consumable.min_level
    if threshold is not None and quantity <= threshold:
        return "LOW_STOCK"
    return "NORMAL"


def to_consumable_out(consumable: Consumable) -> dict:
    return {
        "id": consumable.id,
        "item_code": consumable.item_code,
        "name": consumable.name,
        "branch_id": consumable.branch_id,
        "category": consumable.category,
        "manufacturer": consumable.manufacturer,
        "compatible_model_number": consumable.compatible_model_number,
        "unit": consumable.unit,
        "opening_quantity": consumable.opening_quantity,
        "min_level": consumable.min_level,
        "reorder_level": consumable.reorder_level,
        "expiry_date": consumable.expiry_date,
        "supplier": consumable.supplier,
        "vendor_id": consumable.vendor_id,
        "status": consumable.status,
        "data_source": consumable.data_source,
        "classification_status": consumable.classification_status,
        "environment": consumable.environment,
        "stock_status": compute_consumable_stock_status(consumable),
    }


def update_consumable(db: Session, consumable: Consumable, payload: ConsumableUpdateIn, *, user_id: str) -> Consumable:
    changes = payload.model_dump(exclude_unset=True)
    before = {c.name: getattr(consumable, c.name) for c in consumable.__table__.columns}
    for field, value in changes.items():
        setattr(consumable, field, value)
    audit_record(db, user_id=user_id, action="UPDATE", entity="consumable", entity_id=consumable.id,
                 old_value=before, new_value=changes)
    db.commit()
    db.refresh(consumable)
    return consumable


def _consumable_reference_count(db: Session, consumable_id: str) -> int:
    """Batch F: how many rows across the schema currently reference this Consumable —
    its own ledger history (ConsumableTransaction) and any requirement line item that
    requested it (RequirementItem.consumable_id). Zero means it can be hard-deleted;
    any other count means it must be deactivated instead."""
    return (
        db.query(ConsumableTransaction).filter(ConsumableTransaction.consumable_id == consumable_id).count()
        + db.query(RequirementItem).filter(RequirementItem.consumable_id == consumable_id).count()
    )


def delete_consumable(db: Session, consumable: Consumable, *, user_id: str, force: bool = False) -> None:
    """Batch F: hard delete only when zero rows reference this consumable; otherwise
    raise ConsumableInUseError so the router can 409 and direct the caller to
    deactivate (status=INACTIVE) via the existing PATCH /consumables/{id} endpoint
    instead — mirrors delete_location's shape above.

    ``force=True`` (Super-Admin-only — the router gates this on DEV_DATA_CLEANUP)
    bypasses that guard: its own ConsumableTransaction ledger is deleted outright,
    and any RequirementItem that requested it is detached (consumable_id set to NULL)
    rather than destroyed, so the underlying Requirement survives."""
    if force:
        db.query(ConsumableTransaction).filter(ConsumableTransaction.consumable_id == consumable.id).delete(
            synchronize_session=False
        )
        db.query(RequirementItem).filter(RequirementItem.consumable_id == consumable.id).update(
            {"consumable_id": None}, synchronize_session=False
        )
        before = {c.name: getattr(consumable, c.name) for c in consumable.__table__.columns}
        audit_record(db, user_id=user_id, action="FORCE_DELETE", entity="consumable", entity_id=consumable.id, old_value=before)
        db.delete(consumable)
        db.commit()
        return

    in_use = _consumable_reference_count(db, consumable.id)
    if in_use > 0:
        raise ConsumableInUseError(
            f"Consumable {consumable.item_code} is referenced by {in_use} record(s) "
            "and cannot be deleted — deactivate it instead"
        )
    before = {c.name: getattr(consumable, c.name) for c in consumable.__table__.columns}
    audit_record(db, user_id=user_id, action="DELETE", entity="consumable", entity_id=consumable.id, old_value=before)
    db.delete(consumable)
    db.commit()


def get_or_create_branch_consumable(db: Session, source: Consumable, branch_id: str) -> Consumable:
    """The given branch's own stock row for the same catalog item as `source`
    (matched by `item_code`), created on first transfer with `source`'s descriptive
    fields copied over and `opening_quantity` starting at 0. If `source` already
    belongs to `branch_id`, it *is* that row — nothing to create."""
    if source.branch_id == branch_id:
        return source
    existing = (
        db.query(Consumable)
        .filter(Consumable.item_code == source.item_code, Consumable.branch_id == branch_id)
        .first()
    )
    if existing is not None:
        return existing
    branch_consumable = Consumable(
        item_code=source.item_code,
        name=source.name,
        branch_id=branch_id,
        category=source.category,
        manufacturer=source.manufacturer,
        compatible_model_number=source.compatible_model_number,
        unit=source.unit,
        opening_quantity=0,
        min_level=source.min_level,
        reorder_level=source.reorder_level,
        expiry_date=source.expiry_date,
        supplier=source.supplier,
        status=source.status,
        data_source=source.data_source,
        classification_status=source.classification_status,
    )
    db.add(branch_consumable)
    db.flush()
    return branch_consumable


def transfer_consumable_stock(
    db: Session, *, consumable: Consumable, destination_branch_id: str, quantity: int,
    requirement_id: str | None, reference: str | None, user_id: str,
) -> None:
    """The one place a fulfillment mutates `Consumable.opening_quantity`: decrements
    `consumable` (the row the requirement item referenced — typically the central/
    hub pool) and, unless it already belongs to `destination_branch_id`, credits
    that branch's own row (created on demand). Always paired with a
    `ConsumableTransaction` ledger row, so the movement is auditable rather than a
    silent overwrite (Phase 7 of the consumables workflow)."""
    consumable.opening_quantity = (consumable.opening_quantity or 0) - quantity

    if consumable.branch_id == destination_branch_id:
        db.add(ConsumableTransaction(
            consumable_id=consumable.id, transaction_type="STOCK_OUT", quantity=quantity,
            source_branch_id=consumable.branch_id, destination_branch_id=None,
            requirement_id=requirement_id, reference=reference, performed_by=user_id,
        ))
        return

    destination = get_or_create_branch_consumable(db, consumable, destination_branch_id)
    destination.opening_quantity = (destination.opening_quantity or 0) + quantity
    db.add(ConsumableTransaction(
        consumable_id=consumable.id, transaction_type="TRANSFER", quantity=quantity,
        source_branch_id=consumable.branch_id, destination_branch_id=destination_branch_id,
        requirement_id=requirement_id, reference=reference, performed_by=user_id,
    ))


def to_consumable_transaction_out(txn: ConsumableTransaction) -> dict:
    return {
        "id": txn.id,
        "consumable_id": txn.consumable_id,
        "transaction_type": txn.transaction_type,
        "quantity": txn.quantity,
        "source_branch_id": txn.source_branch_id,
        "destination_branch_id": txn.destination_branch_id,
        "requirement_id": txn.requirement_id,
        "reference": txn.reference,
        "performed_by": txn.performed_by,
        "created_at": txn.created_at.isoformat(),
    }


def list_consumable_transactions(
    db: Session, *, page: int, page_size: int, consumable_id: str | None = None,
) -> tuple[list[dict], int]:
    """Movements affecting `consumable_id`'s own location: matched by `item_code`
    (a transfer's ledger row is keyed to the *source* consumable, so a branch row's
    history must also see transfers that named the central row) and only those
    where this row's own `branch_id` is the source or the destination."""
    query = db.query(ConsumableTransaction)
    if consumable_id:
        consumable = db.get(Consumable, consumable_id)
        if consumable is None:
            return [], 0
        matching_ids = [
            row.id for row in db.query(Consumable.id).filter(Consumable.item_code == consumable.item_code).all()
        ]
        query = query.filter(ConsumableTransaction.consumable_id.in_(matching_ids)).filter(
            or_(
                ConsumableTransaction.source_branch_id == consumable.branch_id,
                ConsumableTransaction.destination_branch_id == consumable.branch_id,
            )
        )

    total = query.count()
    items = (
        query.order_by(ConsumableTransaction.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size).all()
    )
    return [to_consumable_transaction_out(t) for t in items], total
