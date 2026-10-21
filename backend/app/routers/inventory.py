from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..database import get_db
from ..dependencies.auth import require_any_permission, require_permission
from ..exports.writer import EXPORT_FORMATS, build_export_response
from ..models.hospital import Branch
from ..models.inventory import Consumable, InventoryItem, InventoryLocation, InventoryTransaction, SparePartCompatibility
from ..models.vendor import Vendor
from ..repositories.crud import paginate
from ..schemas.auth import CurrentUser
from ..schemas.common import Page
from ..schemas.inventory import (
    ConsumableIn, ConsumableOut, ConsumableTransactionOut, ConsumableUpdateIn, InventoryItemIn, InventoryItemOut,
    InventoryItemUpdateIn, InventoryLocationIn, InventoryLocationOut, InventoryLocationUpdateIn,
    InventoryTransactionIn, InventoryTransactionOut, SparePartCompatibilityIn, SparePartCompatibilityOut,
)
from ..security.permissions import (
    DEV_DATA_CLEANUP, INVENTORY_PICKER_READ, INVENTORY_READ, INVENTORY_WRITE, branch_scope_id,
)
from ..services import inventory_service
from ..services.force_delete import assert_dev_environment
from ..services.export_service import MAX_EXPORT_ROWS, record_export_audit
from ..services.inventory_service import (
    ConsumableInUseError, InsufficientStockError, InventoryServiceError, LocationInUseError,
)

router = APIRouter(prefix="/api/v1", tags=["inventory"])


def _get_or_404(db: Session, model, entity_id: str, label: str):
    obj = db.get(model, entity_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return obj


# ---- Items ----

@router.get("/inventory/items", response_model=Page[InventoryItemOut])
def list_items(
    page: int = 1, page_size: int = 25, search: str | None = None, category: str | None = None,
    is_consumable: bool | None = None, stock_status: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_any_permission(INVENTORY_READ, INVENTORY_PICKER_READ)),
):
    # A picker-only caller (TL/Unit Head) must never see stock_status in the filter
    # either — it's a stock-derived field, so silently ignore that query param rather
    # than let it leak whether the caller could otherwise narrow by real stock levels.
    has_full_read = INVENTORY_READ in current_user.permissions
    items, total = inventory_service.list_items(
        db, page=page, page_size=page_size, search=search, category=category,
        is_consumable=is_consumable, stock_status=stock_status if has_full_read else None,
    )
    if not has_full_read:
        items = [inventory_service.strip_stock_fields(i, is_consumable=False) for i in items]
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/inventory/items/export")
def export_inventory_items(
    format: str = "xlsx", search: str | None = None, category: str | None = None,
    is_consumable: bool | None = None, stock_status: str | None = None,
    db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_permission(INVENTORY_READ)),
):
    """Same filters as `GET /inventory/items`, exported instead of paginated — reuses
    `inventory_service.list_items()` as-is (including its computed `current_stock`/
    `stock_status`) rather than re-deriving that logic, gated on the identical
    `inventory.read` permission."""
    if format not in EXPORT_FORMATS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"format must be one of {', '.join(EXPORT_FORMATS)}")

    items, total = inventory_service.list_items(
        db, page=1, page_size=MAX_EXPORT_ROWS, search=search, category=category,
        is_consumable=is_consumable, stock_status=stock_status,
    )

    headers = [
        "Item Code", "Name", "Category", "Unit", "Min Level", "Max Level", "Reorder Level",
        "Unit Cost", "Supplier", "Consumable", "Current Stock", "Stock Status",
    ]
    rows = [
        [i["item_code"], i["name"], i["category"], i["unit"], i["min_level"], i["max_level"],
         i["reorder_level"], i["unit_cost"], i["supplier"], i["is_consumable"], i["current_stock"], i["stock_status"]]
        for i in items
    ]

    record_export_audit(
        db, user_id=current_user.id, module="inventory_items",
        filters={"search": search, "category": category, "is_consumable": is_consumable, "stock_status": stock_status},
        format=format, record_count=len(rows), truncated=total > MAX_EXPORT_ROWS,
    )
    return build_export_response(headers=headers, rows=rows, format=format, filename_stem="inventory_items_export")


@router.post("/inventory/items", response_model=InventoryItemOut, status_code=status.HTTP_201_CREATED)
def create_item(payload: InventoryItemIn, db: Session = Depends(get_db),
                 current_user: CurrentUser = Depends(require_permission(INVENTORY_WRITE))):
    if db.query(InventoryItem).filter(InventoryItem.item_code == payload.item_code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Item code already exists")
    item = inventory_service.create_item(db, payload, user_id=current_user.id)
    return inventory_service.to_item_out(db, item)


@router.get("/inventory/items/{item_id}", response_model=InventoryItemOut)
def get_item(item_id: str, db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(INVENTORY_READ))):
    item = _get_or_404(db, InventoryItem, item_id, "Inventory item")
    return inventory_service.to_item_out(db, item)


@router.patch("/inventory/items/{item_id}", response_model=InventoryItemOut)
def update_item(item_id: str, payload: InventoryItemUpdateIn, db: Session = Depends(get_db),
                 current_user: CurrentUser = Depends(require_permission(INVENTORY_WRITE))):
    item = _get_or_404(db, InventoryItem, item_id, "Inventory item")
    item = inventory_service.update_item(db, item, payload, user_id=current_user.id)
    return inventory_service.to_item_out(db, item)


@router.get("/inventory/items/{item_id}/stock")
def get_item_stock(item_id: str, db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(INVENTORY_READ))):
    item = _get_or_404(db, InventoryItem, item_id, "Inventory item")
    out = inventory_service.to_item_out(db, item)
    return {
        "item_id": item.id,
        "current_stock": out["current_stock"],
        "stock_status": out["stock_status"],
        "min_level": item.min_level,
        "max_level": item.max_level,
        "reorder_level": item.reorder_level,
    }


# ---- Locations (warehouses/bins/stores — Module 13) ----

@router.get("/inventory/locations", response_model=Page[InventoryLocationOut])
def list_locations(page: int = 1, page_size: int = 25, search: str | None = None, branch_id: str | None = None,
                    kind: str | None = None, db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(require_permission(INVENTORY_READ))):
    # Branch scope (F11): InventoryLocation.branch_id is non-nullable, so a stock
    # location genuinely belongs to one branch — unlike InventoryItem, which is a
    # global spare-part catalog row and is deliberately NOT scoped.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None:
        branch_id = scoped_branch_id
    items, total = inventory_service.list_locations(db, page=page, page_size=page_size, search=search,
                                                      branch_id=branch_id, kind=kind)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/inventory/locations", response_model=InventoryLocationOut, status_code=status.HTTP_201_CREATED)
def create_location(payload: InventoryLocationIn, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(INVENTORY_WRITE))):
    _get_or_404(db, Branch, payload.branch_id, "Branch")
    return inventory_service.create_location(db, payload, user_id=current_user.id)


@router.get("/inventory/locations/{location_id}", response_model=InventoryLocationOut)
def get_location(location_id: str, db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(require_permission(INVENTORY_READ))):
    location = _get_or_404(db, InventoryLocation, location_id, "Inventory location")
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and location.branch_id != scoped_branch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventory location not found")
    return location


@router.patch("/inventory/locations/{location_id}", response_model=InventoryLocationOut)
def update_location(location_id: str, payload: InventoryLocationUpdateIn, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(INVENTORY_WRITE))):
    location = _get_or_404(db, InventoryLocation, location_id, "Inventory location")
    if payload.branch_id is not None:
        _get_or_404(db, Branch, payload.branch_id, "Branch")
    return inventory_service.update_location(db, location, payload, user_id=current_user.id)


@router.delete("/inventory/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location(location_id: str, db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(require_permission(INVENTORY_WRITE))):
    location = _get_or_404(db, InventoryLocation, location_id, "Inventory location")
    try:
        inventory_service.delete_location(db, location, user_id=current_user.id)
    except LocationInUseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return None


# ---- Spare-part compatibility ----

@router.get("/inventory/items/{item_id}/compatibility", response_model=list[SparePartCompatibilityOut])
def list_compatibility(item_id: str, db: Session = Depends(get_db),
                        _: CurrentUser = Depends(require_permission(INVENTORY_READ))):
    _get_or_404(db, InventoryItem, item_id, "Inventory item")
    return db.query(SparePartCompatibility).filter(SparePartCompatibility.inventory_item_id == item_id).all()


@router.post("/inventory/items/{item_id}/compatibility", response_model=SparePartCompatibilityOut,
             status_code=status.HTTP_201_CREATED)
def add_compatibility(item_id: str, payload: SparePartCompatibilityIn, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(INVENTORY_WRITE))):
    try:
        link = inventory_service.add_compatibility(
            db, item_id, payload.device_model_id, payload.notes, user_id=current_user.id
        )
    except InventoryServiceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Compatibility link already exists")
    return link


@router.delete("/inventory/items/{item_id}/compatibility/{compatibility_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_compatibility(item_id: str, compatibility_id: str, db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(require_permission(INVENTORY_WRITE))):
    link = db.get(SparePartCompatibility, compatibility_id)
    if link is None or link.inventory_item_id != item_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compatibility link not found")
    before = {"inventory_item_id": link.inventory_item_id, "device_model_id": link.device_model_id, "notes": link.notes}
    db.delete(link)
    audit_record(db, user_id=current_user.id, action="DELETE", entity="spare_part_compatibility",
                 entity_id=compatibility_id, old_value=before)
    db.commit()


# ---- Consumables ----

@router.get("/consumables", response_model=Page[ConsumableOut])
def list_consumables(page: int = 1, page_size: int = 25, branch_id: str | None = None, category: str | None = None,
                      central_only: bool = False,
                      db: Session = Depends(get_db),
                      current_user: CurrentUser = Depends(require_any_permission(INVENTORY_READ, INVENTORY_PICKER_READ))):
    # Batch D, Part 4 — server-side category filter, added the same way branch_id
    # already works: `Consumable.category` is a real column and `paginate()` already
    # supports arbitrary equality filters via **filters, so this is additive only —
    # no new query-building path, no change to any existing caller that omits it.
    has_full_read = INVENTORY_READ in current_user.permissions
    # Branch scope (F11): a UNIT_HEAD/TL must never read another branch's consumable
    # rows. `central_only` is deliberately left alone — those rows are the central/hub
    # pool (branch_id IS NULL), not another branch's stock, and the New Requirement
    # consumable picker depends on this exact call (see requirement_service.py's
    # matching "always fulfilled from central stock" rule); their quantities are still
    # stripped below for anyone without full inventory.read.
    scoped_branch_id = branch_scope_id(current_user.roles, current_user.hospital_scope_id)
    if scoped_branch_id is not None and not central_only:
        branch_id = scoped_branch_id
    if central_only:
        # Business-rule correction: a consumable Requirement is always fulfilled
        # FROM CENTRAL STOCK to the requesting branch, never branch-to-branch (see
        # services/requirement_service.py's matching server-side check). The
        # Requirement consumable picker uses this so a requester chooses WHAT they
        # need, never WHICH branch's row -- branch_id IS NULL is the one central/
        # hub row per item_code, which paginate()'s plain equality-filter helper
        # can't express (a query param can't carry a literal "IS NULL"), so this
        # one case builds its own query, same pattern GET /assets already uses for
        # its `category` filter.
        query = db.query(Consumable).filter(Consumable.branch_id.is_(None))
        if category:
            query = query.filter(Consumable.category == category)
        total = query.count()
        rows = query.order_by(Consumable.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    else:
        rows, total = paginate(db, Consumable, page=page, page_size=page_size, branch_id=branch_id, category=category)
    outs = [inventory_service.to_consumable_out(c) for c in rows]
    if not has_full_read:
        outs = [inventory_service.strip_stock_fields(o, is_consumable=True) for o in outs]
    return Page(items=outs, total=total, page=page, page_size=page_size)


@router.post("/consumables", response_model=ConsumableOut, status_code=status.HTTP_201_CREATED)
def create_consumable(payload: ConsumableIn, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(INVENTORY_WRITE))):
    if payload.branch_id is not None:
        _get_or_404(db, Branch, payload.branch_id, "Branch")
    if payload.vendor_id is not None:
        _get_or_404(db, Vendor, payload.vendor_id, "Vendor")
    if db.query(Consumable).filter(Consumable.item_code == payload.item_code, Consumable.branch_id == payload.branch_id).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Item code already exists for this branch")
    obj = Consumable(**payload.model_dump())
    db.add(obj)
    db.flush()
    audit_record(db, user_id=current_user.id, action="CREATE", entity="consumable", entity_id=obj.id, new_value=payload.model_dump())
    db.commit()
    db.refresh(obj)
    return inventory_service.to_consumable_out(obj)


@router.get("/consumables/{consumable_id}", response_model=ConsumableOut)
def get_consumable(consumable_id: str, db: Session = Depends(get_db),
                    _: CurrentUser = Depends(require_permission(INVENTORY_READ))):
    consumable = _get_or_404(db, Consumable, consumable_id, "Consumable")
    return inventory_service.to_consumable_out(consumable)


@router.patch("/consumables/{consumable_id}", response_model=ConsumableOut)
def update_consumable(consumable_id: str, payload: ConsumableUpdateIn, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(INVENTORY_WRITE))):
    consumable = _get_or_404(db, Consumable, consumable_id, "Consumable")
    if payload.vendor_id is not None:
        _get_or_404(db, Vendor, payload.vendor_id, "Vendor")
    consumable = inventory_service.update_consumable(db, consumable, payload, user_id=current_user.id)
    return inventory_service.to_consumable_out(consumable)


@router.delete("/consumables/{consumable_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_consumable(consumable_id: str, force: bool = False, db: Session = Depends(get_db),
                       current_user: CurrentUser = Depends(require_permission(INVENTORY_WRITE))):
    """Batch F: hard delete only when zero rows reference this consumable (its own
    ConsumableTransaction ledger history, or a RequirementItem that requested it);
    otherwise 409, directing the caller to deactivate (PATCH status=INACTIVE)
    instead — same reference-check-then-hard-delete-or-reject pattern as
    delete_location above.

    ``force=true`` (Super Admin only, gated on DEV_DATA_CLEANUP) bypasses that guard —
    see inventory_service.delete_consumable's force branch."""
    consumable = _get_or_404(db, Consumable, consumable_id, "Consumable")
    if force:
        if DEV_DATA_CLEANUP not in current_user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Force-delete requires Super Admin (dev.data_cleanup permission)",
            )
        assert_dev_environment()
    try:
        inventory_service.delete_consumable(db, consumable, user_id=current_user.id, force=force)
    except ConsumableInUseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return None


@router.get("/consumables/{consumable_id}/transactions", response_model=Page[ConsumableTransactionOut])
def list_consumable_transactions(consumable_id: str, page: int = 1, page_size: int = 25,
                                  db: Session = Depends(get_db),
                                  _: CurrentUser = Depends(require_permission(INVENTORY_READ))):
    _get_or_404(db, Consumable, consumable_id, "Consumable")
    items, total = inventory_service.list_consumable_transactions(
        db, page=page, page_size=page_size, consumable_id=consumable_id,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/consumables/{consumable_id}/last-transaction", response_model=ConsumableTransactionOut | None)
def get_consumable_last_transaction(consumable_id: str, db: Session = Depends(get_db),
                                     _: CurrentUser = Depends(require_permission(INVENTORY_READ))):
    """Final admin/inventory/vendor/dashboard polish pass — Part 4: the single most
    recent stock movement affecting this consumable's own location (STOCK_OUT at its
    own branch, or a TRANSFER naming it as source/destination — see
    `inventory_service.list_consumable_transactions()`'s docstring for the exact
    matching rule). Reuses that existing query (already ordered newest-first) with
    `page_size=1` rather than duplicating its item_code/branch matching logic. `null`
    when the consumable has no transaction history yet — not a 404, since the
    consumable itself exists."""
    _get_or_404(db, Consumable, consumable_id, "Consumable")
    items, _total = inventory_service.list_consumable_transactions(
        db, page=1, page_size=1, consumable_id=consumable_id,
    )
    return items[0] if items else None


# ---- Transactions ----

@router.get("/inventory/transactions", response_model=Page[InventoryTransactionOut])
def list_transactions(
    page: int = 1, page_size: int = 25, item_id: str | None = None, transaction_type: str | None = None,
    batch_number: str | None = None, expiry_status: str | None = None,
    db: Session = Depends(get_db), _: CurrentUser = Depends(require_permission(INVENTORY_READ)),
):
    items, total = inventory_service.list_transactions(
        db, page=page, page_size=page_size, item_id=item_id, transaction_type=transaction_type,
        batch_number=batch_number, expiry_status=expiry_status,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/inventory/transactions", response_model=InventoryTransactionOut, status_code=status.HTTP_201_CREATED)
def create_transaction(payload: InventoryTransactionIn, db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(require_permission(INVENTORY_WRITE))):
    try:
        txn = inventory_service.create_transaction(db, payload, user_id=current_user.id)
    except InventoryServiceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except InsufficientStockError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return inventory_service.to_transaction_out(txn)


@router.get("/inventory/transactions/{transaction_id}", response_model=InventoryTransactionOut)
def get_transaction(transaction_id: str, db: Session = Depends(get_db),
                     _: CurrentUser = Depends(require_permission(INVENTORY_READ))):
    txn = _get_or_404(db, InventoryTransaction, transaction_id, "Inventory transaction")
    return inventory_service.to_transaction_out(txn)
