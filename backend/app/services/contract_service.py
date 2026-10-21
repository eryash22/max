from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..models.contracts import AmcContract, AmcContractAsset, CmcContract, CmcContractAsset, Warranty
from ..models.device import Asset
from ..models.notifications import Notification
from ..models.vendor import Vendor
from ..schemas.contract import ContractIn, ContractUpdateIn

# Window used to classify a contract as "EXPIRING_SOON" rather than "ACTIVE".
EXPIRING_SOON_WINDOW_DAYS = 30

# Reminder ladder for the daily expiry scan, in days before end_date. A contract fires
# CONTRACT_EXPIRING once per window it crosses — at 30, then 7, then 1 day out — so a
# 30-day-out AMC produces three reminders over a month, not thirty. Defined here as one
# explicit list rather than magic numbers spread through the scan; the widest entry is
# deliberately EXPIRING_SOON_WINDOW_DAYS so the reminder and the UI's "expiring soon"
# badge agree about when a contract starts being urgent.
EXPIRY_REMINDER_WINDOWS_DAYS = [EXPIRING_SOON_WINDOW_DAYS, 7, 1]

_TABLES = {"AMC": AmcContract, "CMC": CmcContract, "WARRANTY": Warranty}
_ASSET_LINK_TABLES = {"AMC": (AmcContractAsset, "amc_contract_id"), "CMC": (CmcContractAsset, "cmc_contract_id")}

# Fields that exist on AmcContract/CmcContract but not on Warranty (and vice versa).
_AMC_CMC_ONLY_FIELDS = {
    "contract_number", "pm_frequency", "calibration_frequency", "response_time_hours",
    "resolution_time_hours", "contract_value", "renewal_date", "contact_person",
    "contact_phone", "contact_email",
}
_WARRANTY_ONLY_FIELDS = {"coverage_notes"}


class ContractServiceError(Exception):
    pass


class ContractNotFoundError(Exception):
    pass


def _today() -> date:
    return datetime.now(timezone.utc).date()


def compute_expiry_status(start_date: str | None, end_date: str | None, *, today: date | None = None) -> str:
    if not end_date:
        return "NOT_SET"
    today = today or _today()
    end = date.fromisoformat(end_date)
    if end < today:
        return "EXPIRED"
    if end <= today + timedelta(days=EXPIRING_SOON_WINDOW_DAYS):
        return "EXPIRING_SOON"
    if start_date:
        start = date.fromisoformat(start_date)
        if start > today:
            return "UPCOMING"
    return "ACTIVE"


def _model_fields(contract_type: str, data: dict) -> dict:
    """Strip fields the underlying table (Warranty vs AMC/CMC) does not support."""
    exclude = _WARRANTY_ONLY_FIELDS if contract_type in ("AMC", "CMC") else _AMC_CMC_ONLY_FIELDS
    fields = {k: v for k, v in data.items() if k not in exclude and k not in ("contract_type", "asset_ids")}
    if contract_type == "WARRANTY" and "contract_number" in data:
        fields.pop("contract_number", None)
        fields["warranty_number"] = data["contract_number"]
    return fields


def asset_ids_for(db: Session, contract_type: str, contract) -> list[str]:
    if contract_type == "WARRANTY":
        return [contract.asset_id] if contract.asset_id else []
    link_model, fk_field = _ASSET_LINK_TABLES[contract_type]
    links = db.query(link_model).filter(getattr(link_model, fk_field) == contract.id).all()
    return [link.asset_id for link in links]


def to_contract_out(db: Session, contract_type: str, contract) -> dict:
    asset_ids = asset_ids_for(db, contract_type, contract)
    out = {
        "id": contract.id,
        "contract_type": contract_type,
        "vendor_id": contract.vendor_id,
        "asset_ids": asset_ids,
        "contract_number": contract.warranty_number if contract_type == "WARRANTY" else contract.contract_number,
        "start_date": contract.start_date,
        "end_date": contract.end_date,
        "pm_frequency": getattr(contract, "pm_frequency", None),
        "calibration_frequency": getattr(contract, "calibration_frequency", None),
        "response_time_hours": getattr(contract, "response_time_hours", None),
        "resolution_time_hours": getattr(contract, "resolution_time_hours", None),
        "contract_value": getattr(contract, "contract_value", None),
        "renewal_date": getattr(contract, "renewal_date", None),
        "contact_person": getattr(contract, "contact_person", None),
        "contact_phone": getattr(contract, "contact_phone", None),
        "contact_email": getattr(contract, "contact_email", None),
        "coverage_notes": getattr(contract, "coverage_notes", None),
        "status": contract.status,
        "expiry_status": compute_expiry_status(contract.start_date, contract.end_date),
        "environment": contract.environment,
    }
    return out


def _validate_vendor(db: Session, vendor_id: str | None) -> None:
    if vendor_id is not None and db.get(Vendor, vendor_id) is None:
        raise ContractServiceError("Vendor not found")


# Lifecycle statuses a contract may not NEWLY cover. Deliberately defined here rather
# than reusing asset_lifecycle_service.is_asset_available_for_allocation(): that helper's
# docstring scopes it to "eligible for fulfillment allocation / central-warehouse stock
# count", which is a different business question. The values themselves are the same
# canonical vocabulary, but the rule is contract-specific and must be free to diverge.
CONTRACT_INELIGIBLE_STATUSES = frozenset({"CONDEMNED", "RETIRED"})


def is_asset_contract_eligible(asset: Asset) -> bool:
    """True when `asset` may be NEWLY linked to a contract.

    A condemned or retired device is out of service, so putting fresh AMC/CMC/warranty
    coverage on it is a data-entry mistake. This is only checked for assets being added
    now — links already saved on an existing contract are never revoked (see
    _validate_assets), because a historical warranty on a since-retired device is a
    legitimate record."""
    return (asset.status or "").upper() not in CONTRACT_INELIGIBLE_STATUSES


def _validate_assets(
    db: Session,
    asset_ids: list[str],
    *,
    branch_id: str | None = None,
    already_linked_ids: set[str] | None = None,
) -> None:
    """Validate assets a contract is being pointed at.

    `already_linked_ids` are the asset ids the contract already covers — they are only
    existence-checked, never re-tested for eligibility or branch scope, so editing an
    old contract can never fail (or silently drop links) because one of its devices was
    condemned or moved branch since it was signed.

    `branch_id` is the caller's enforced branch scope (None = organisation-wide). It is
    applied server-side so a branch-scoped user cannot attach another branch's asset by
    posting its id directly."""
    untouched = already_linked_ids or set()
    for asset_id in asset_ids:
        asset = db.get(Asset, asset_id)
        if asset is None:
            raise ContractServiceError(f"Asset not found: {asset_id}")
        if asset_id in untouched:
            continue
        if not is_asset_contract_eligible(asset):
            label = asset.asset_number or asset_id
            raise ContractServiceError(
                f"Asset {label} is {asset.status} and cannot be added to a contract"
            )
        if branch_id is not None and asset.branch_id != branch_id:
            # Same 404-style opacity as every other branch guard: don't confirm that an
            # asset id in another branch exists.
            raise ContractServiceError(f"Asset not found: {asset_id}")


def create_contract(
    db: Session, payload: ContractIn, *, user_id: str, branch_id: str | None = None
) -> tuple[str, object]:
    """`branch_id` is the caller's enforced branch scope (None = organisation-wide),
    passed in by routers/contracts.py from branch_scope_id() — validated here rather
    than in the router so the rule sits with the rest of the coverage rules."""
    contract_type = payload.contract_type
    data = payload.model_dump()
    asset_ids = data.pop("asset_ids", []) or []

    _validate_vendor(db, data.get("vendor_id"))
    _validate_assets(db, asset_ids, branch_id=branch_id)

    if contract_type == "WARRANTY":
        if len(asset_ids) != 1:
            raise ContractServiceError("WARRANTY contracts must cover exactly one asset")
    fields = _model_fields(contract_type, data)

    model = _TABLES[contract_type]
    if contract_type == "WARRANTY":
        fields["asset_id"] = asset_ids[0]
        contract = model(**fields)
        db.add(contract)
        db.flush()
    else:
        contract = model(**fields)
        db.add(contract)
        db.flush()
        link_model, fk_field = _ASSET_LINK_TABLES[contract_type]
        for asset_id in asset_ids:
            db.add(link_model(**{fk_field: contract.id, "asset_id": asset_id}))
        db.flush()

    audit_record(
        db, user_id=user_id, action="CREATE", entity=_entity_name(contract_type),
        entity_id=contract.id, new_value={**fields, "asset_ids": asset_ids},
    )
    db.commit()
    db.refresh(contract)

    from ..schemas.notification import EVENT_CONTRACT_CREATED
    from .communication_service import dispatch_contract_event_safe

    dispatch_contract_event_safe(
        db, EVENT_CONTRACT_CREATED, contract_type=contract_type, contract=contract, user_id=user_id
    )
    return contract_type, contract


def add_covered_assets(db: Session, contract_type: str, contract, asset_ids: list[str], *,
                       user_id: str, branch_id: str | None = None) -> list[str]:
    """Attach more physical assets to an existing AMC/CMC.

    Warranty is excluded: it covers exactly one asset by construction (Warranty.asset_id,
    and create_contract enforces the count), so "add another" is not a thing it can do.

    New assets go through the same eligibility and branch checks as creation — a
    condemned/retired device or another branch's asset can no more be bolted onto an
    existing contract than written into a new one."""
    if contract_type not in _ASSET_LINK_TABLES:
        raise ContractServiceError(f"{contract_type} contracts cover exactly one asset and cannot be extended")

    existing = set(asset_ids_for(db, contract_type, contract))
    wanted = [a for a in asset_ids if a not in existing]
    if not wanted:
        return []

    _validate_assets(db, wanted, branch_id=branch_id)

    link_model, fk_field = _ASSET_LINK_TABLES[contract_type]
    for asset_id in wanted:
        db.add(link_model(**{fk_field: contract.id, "asset_id": asset_id}))
    audit_record(db, user_id=user_id, action="UPDATE", entity=_entity_name(contract_type),
                 entity_id=contract.id, new_value={"asset_ids_added": wanted})
    db.commit()

    from ..schemas.notification import EVENT_CONTRACT_ASSET_ADDED
    from .communication_service import dispatch_contract_event_safe

    added = db.query(Asset).filter(Asset.id.in_(wanted)).all()
    dispatch_contract_event_safe(
        db, EVENT_CONTRACT_ASSET_ADDED, contract_type=contract_type, contract=contract,
        user_id=user_id, assets=added,
    )
    return wanted


def remove_covered_assets(db: Session, contract_type: str, contract, asset_ids: list[str], *,
                          user_id: str, branch_id: str | None = None) -> list[str]:
    """Detach physical assets from an existing AMC/CMC.

    Eligibility is deliberately NOT re-checked here: removing coverage from a device
    that has since been condemned is exactly the housekeeping this exists for. Branch
    scope still applies, so a branch-scoped user cannot strip another branch's asset
    off a shared contract."""
    if contract_type not in _ASSET_LINK_TABLES:
        raise ContractServiceError(f"{contract_type} contracts cover exactly one asset and cannot be reduced")

    existing = set(asset_ids_for(db, contract_type, contract))
    wanted = [a for a in asset_ids if a in existing]
    if not wanted:
        return []

    if branch_id is not None:
        for asset_id in wanted:
            asset = db.get(Asset, asset_id)
            if asset is None or asset.branch_id != branch_id:
                raise ContractServiceError(f"Asset not found: {asset_id}")

    removed = db.query(Asset).filter(Asset.id.in_(wanted)).all()

    link_model, fk_field = _ASSET_LINK_TABLES[contract_type]
    db.query(link_model).filter(
        getattr(link_model, fk_field) == contract.id, link_model.asset_id.in_(wanted)
    ).delete(synchronize_session=False)
    audit_record(db, user_id=user_id, action="UPDATE", entity=_entity_name(contract_type),
                 entity_id=contract.id, new_value={"asset_ids_removed": wanted})
    db.commit()

    from ..schemas.notification import EVENT_CONTRACT_ASSET_REMOVED
    from .communication_service import dispatch_contract_event_safe

    dispatch_contract_event_safe(
        db, EVENT_CONTRACT_ASSET_REMOVED, contract_type=contract_type, contract=contract,
        user_id=user_id, assets=removed,
    )
    return wanted


def get_contract(db: Session, contract_id: str) -> tuple[str, object]:
    for contract_type, model in _TABLES.items():
        obj = db.get(model, contract_id)
        if obj is not None:
            return contract_type, obj
    raise ContractNotFoundError(contract_id)


def _entity_name(contract_type: str) -> str:
    return "warranty" if contract_type == "WARRANTY" else contract_type.lower() + "_contract"


def update_contract(db: Session, contract_type: str, contract, payload: ContractUpdateIn, *, user_id: str):
    changes = payload.model_dump(exclude_unset=True)
    if "vendor_id" in changes:
        _validate_vendor(db, changes["vendor_id"])
    fields = _model_fields(contract_type, changes)

    before = {c.name: getattr(contract, c.name) for c in contract.__table__.columns}
    previous_end_date = getattr(contract, "end_date", None)
    previous_status = getattr(contract, "status", None)
    for field, value in fields.items():
        setattr(contract, field, value)

    audit_record(db, user_id=user_id, action="UPDATE", entity=_entity_name(contract_type), entity_id=contract.id,
                 old_value=before, new_value=fields)
    db.commit()
    db.refresh(contract)

    # A renewal is "the coverage end date moved later" — the one edit on this form with
    # real commercial meaning. Every other field change (contact person, notes, SLA
    # hours) is a trivial edit and deliberately notifies no one.
    if _is_renewal(previous_end_date, getattr(contract, "end_date", None)):
        _dispatch_renewal(db, contract_type, contract, user_id=user_id)
    elif previous_status != getattr(contract, "status", None):
        _dispatch_status_change(db, contract_type, contract, user_id=user_id)
    return contract


def set_status(db: Session, contract_type: str, contract, new_status: str, *, user_id: str):
    before = {"status": contract.status}
    changed = contract.status != new_status
    contract.status = new_status
    audit_record(db, user_id=user_id, action="UPDATE", entity=_entity_name(contract_type), entity_id=contract.id,
                 old_value=before, new_value={"status": new_status})
    db.commit()
    db.refresh(contract)
    if changed:
        _dispatch_status_change(db, contract_type, contract, user_id=user_id)
    return contract


def _is_renewal(previous_end_date: str | None, new_end_date: str | None) -> bool:
    """True when coverage was extended. Dates are ISO strings on these models, so a
    plain string comparison is already chronological; a shortened or unchanged end date
    is not a renewal."""
    if not new_end_date:
        return False
    if not previous_end_date:
        # Coverage that had no end date now has one — that is setting the term, not
        # extending it, so it is not announced as a renewal.
        return False
    return new_end_date > previous_end_date


def _dispatch_renewal(db: Session, contract_type: str, contract, *, user_id: str) -> None:
    from ..schemas.notification import CONTRACT_RENEWAL_EVENT_BY_TYPE
    from .communication_service import dispatch_contract_event_safe

    event = CONTRACT_RENEWAL_EVENT_BY_TYPE.get(contract_type)
    if event:
        dispatch_contract_event_safe(
            db, event, contract_type=contract_type, contract=contract, user_id=user_id
        )


def _dispatch_status_change(db: Session, contract_type: str, contract, *, user_id: str) -> None:
    from ..schemas.notification import EVENT_CONTRACT_STATUS_CHANGED
    from .communication_service import dispatch_contract_event_safe

    dispatch_contract_event_safe(
        db, EVENT_CONTRACT_STATUS_CHANGED, contract_type=contract_type, contract=contract, user_id=user_id
    )


def count_asset_links(db: Session, contract_type: str, contract) -> int:
    """How many rows reference (point an inbound FK at) this contract, or — for a
    Warranty, which has no inbound join table (its link runs the other way,
    Warranty.asset_id -> Asset) — whether it is still its asset's active coverage.
    AMC/CMC have a join table (amc_contract_assets / cmc_contract_assets) whose rows
    point at the contract's id, so those are counted directly. An ACTIVE warranty is
    treated as "in use" (returns 1) so it cannot be silently hard-deleted while it is
    the asset's current coverage — deactivate it first, same as any other in-use
    contract. A CANCELLED/EXPIRED/RENEWAL_PENDING/UNKNOWN warranty has no live
    dependents and returns 0, so it can be hard-deleted. Zero means the contract can
    be hard deleted; any other count means it must be deactivated instead — see
    delete_contract() below and routers/contracts.py delete_contract endpoint."""
    if contract_type == "WARRANTY":
        return 1 if contract.status == "ACTIVE" else 0
    link_model, fk_field = _ASSET_LINK_TABLES[contract_type]
    return db.query(link_model).filter(getattr(link_model, fk_field) == contract.id).count()


def delete_contract(db: Session, contract_type: str, contract, *, user_id: str) -> None:
    before = {c.name: getattr(contract, c.name) for c in contract.__table__.columns}
    audit_record(db, user_id=user_id, action="DELETE", entity=_entity_name(contract_type), entity_id=contract.id,
                 old_value=before)
    db.delete(contract)
    db.commit()


def contract_covers_branch(db: Session, contract_type: str, contract, branch_id: str) -> bool:
    """True when `contract` covers at least one Asset in `branch_id`.

    The two contract shapes have genuinely different branch relationships:
      * WARRANTY carries a non-nullable `asset_id`, so its branch is that asset's.
      * AMC/CMC carry no asset column at all — they cover many assets through
        `amc_contract_assets` / `cmc_contract_assets` and one contract can legitimately
        span several branches (a single AMC bought for the whole organisation).
    So an AMC/CMC is *not* owned by a branch; the correct branch-scoped question is
    "does this contract cover anything of mine?", which is what this answers. Used for
    both the list filter and the direct-ID guard so they can't diverge."""
    if contract_type == "WARRANTY":
        asset = db.get(Asset, contract.asset_id)
        return asset is not None and asset.branch_id == branch_id
    link_model, fk_field = _ASSET_LINK_TABLES[contract_type]
    return (
        db.query(link_model.id)
        .join(Asset, Asset.id == link_model.asset_id)
        .filter(getattr(link_model, fk_field) == contract.id, Asset.branch_id == branch_id)
        .first()
        is not None
    )


def list_contracts(
    db: Session, *, page: int, page_size: int, contract_type: str | None = None, vendor_id: str | None = None,
    asset_id: str | None = None, status_filter: str | None = None, expiry_status: str | None = None,
    end_date_after: str | None = None, end_date_before: str | None = None, search: str | None = None,
    branch_id: str | None = None,
) -> tuple[list[dict], int]:
    types_to_query = [contract_type] if contract_type else list(_TABLES.keys())
    merged: list[tuple] = []

    for t in types_to_query:
        model = _TABLES[t]
        query = db.query(model)
        if vendor_id:
            query = query.filter(model.vendor_id == vendor_id)
        if status_filter:
            query = query.filter(model.status == status_filter)
        if end_date_after:
            query = query.filter(model.end_date >= end_date_after)
        if end_date_before:
            query = query.filter(model.end_date <= end_date_before)
        if asset_id:
            if t == "WARRANTY":
                query = query.filter(model.asset_id == asset_id)
            else:
                link_model, fk_field = _ASSET_LINK_TABLES[t]
                linked_ids = [row[0] for row in db.query(getattr(link_model, fk_field)).filter(link_model.asset_id == asset_id).all()]
                if not linked_ids:
                    continue
                query = query.filter(model.id.in_(linked_ids))
        if search:
            number_field = model.warranty_number if t == "WARRANTY" else model.contract_number
            query = query.filter(number_field.ilike(f"%{search}%"))

        for contract in query.all():
            # Branch scope (F11): a UNIT_HEAD/TL only sees contracts covering an asset
            # in their own branch. Applied here rather than as a SQL filter because
            # AMC/CMC coverage is a many-to-many the loop already materialises.
            if branch_id is not None and not contract_covers_branch(db, t, contract, branch_id):
                continue
            out = to_contract_out(db, t, contract)
            if expiry_status and out["expiry_status"] != expiry_status:
                continue
            merged.append((contract.created_at, out))

    merged.sort(key=lambda pair: pair[0], reverse=True)
    total = len(merged)
    start = (page - 1) * page_size
    items = [out for _, out in merged[start:start + page_size]]
    return items, total


def expiry_dedup_key(contract_type: str, contract_id: str, event: str, window: str) -> str:
    """The idempotency key the scan writes onto each Notification.

    Stable across runs and unique per (contract, event, reminder window), which is what
    makes a daily re-run a no-op: the same AMC crossing the 30-day mark produces one
    notification the first day and none on the twenty-nine after it, until it crosses
    the 7-day mark and earns a genuinely new one."""
    return f"contract:{contract_type}:{contract_id}:{event}:{window}"


def _due_window(days_until_expiry: int) -> int | None:
    """The tightest reminder window this contract has crossed, or None if expiry is
    still further out than the widest one. Returning the *tightest* crossed window is
    what advances the ladder: at 8 days out it is 30 (already notified, so deduped), at
    7 it becomes 7 and a fresh reminder goes out."""
    crossed = [w for w in EXPIRY_REMINDER_WINDOWS_DAYS if days_until_expiry <= w]
    return min(crossed) if crossed else None


def scan_contract_expiry(db: Session, *, user_id: str | None = None, today: date | None = None) -> dict:
    """Find contracts approaching expiry or newly expired and fire the matching events.

    Designed to be triggered once a day by an external scheduler (Windows Task
    Scheduler / cron) — this codebase deliberately runs no background process, so the
    trigger lives outside it. Safe to run any number of times a day: every notification
    it creates carries a `dedup_key`, and dispatch skips one that already exists.

    Only contracts that can still meaningfully expire are considered — a CANCELLED or
    already-EXPIRED contract is skipped, as is one with no end_date."""
    from ..schemas.notification import EVENT_CONTRACT_EXPIRED, EVENT_CONTRACT_EXPIRING
    from .communication_service import dispatch_contract_event_safe

    reference = today or _today()
    summary = {
        "scanned": 0,
        "expiring_events": 0,
        "expired_events": 0,
        "notifications_created": 0,
        "skipped_already_notified": 0,
    }

    for contract_type, model in _TABLES.items():
        contracts = (
            db.query(model)
            .filter(model.end_date.isnot(None))
            .filter(model.status.notin_(["CANCELLED", "EXPIRED"]))
            .all()
        )
        for contract in contracts:
            summary["scanned"] += 1
            try:
                end = date.fromisoformat(contract.end_date)
            except (TypeError, ValueError):
                # A malformed date is a data problem, not a reason to abort the scan.
                continue

            days = (end - reference).days
            if days < 0:
                event = EVENT_CONTRACT_EXPIRED
                window = "expired"
            else:
                crossed = _due_window(days)
                if crossed is None:
                    continue
                event = EVENT_CONTRACT_EXPIRING
                window = str(crossed)

            key = expiry_dedup_key(contract_type, contract.id, event, window)
            before = db.query(Notification).filter(Notification.dedup_key == key).count()
            results = dispatch_contract_event_safe(
                db, event, contract_type=contract_type, contract=contract, user_id=user_id,
                expiry_days=str(days), dedup_key=key,
            )
            created = db.query(Notification).filter(Notification.dedup_key == key).count() - before

            if created > 0:
                summary["notifications_created"] += created
                summary["expired_events" if event == EVENT_CONTRACT_EXPIRED else "expiring_events"] += 1
            elif results:
                summary["skipped_already_notified"] += 1

    return summary
