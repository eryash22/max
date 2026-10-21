from sqlalchemy.orm import Session

from ..models.asset_history import AssetHistory
from ..models.device import Asset

# Asset columns that automatically generate a history row on change, mapped to
# the event_type documented in models/asset_history.py. `branch_id` maps to
# BRANCH_TRANSFER (matches the model docstring wording) rather than a generic
# "location" bucket.
_TRACKED_FIELD_EVENTS = {
    "status": "STATUS_CHANGE",
    "branch_id": "BRANCH_TRANSFER",
    "department_id": "DEPARTMENT_CHANGE",
    "location_id": "LOCATION_CHANGE",
}


def record_asset_history(
    db: Session,
    *,
    asset_id: str,
    event_type: str,
    changed_by: str | None,
    from_value: str | None = None,
    to_value: str | None = None,
    branch_id: str | None = None,
    department_id: str | None = None,
    location_id: str | None = None,
    notes: str | None = None,
) -> AssetHistory:
    entry = AssetHistory(
        asset_id=asset_id,
        event_type=event_type,
        from_value=from_value,
        to_value=to_value,
        branch_id=branch_id,
        department_id=department_id,
        location_id=location_id,
        changed_by=changed_by,
        notes=notes,
    )
    db.add(entry)
    db.flush()
    return entry


def record_auto_history_for_update(db: Session, *, before: dict, after: Asset, changed_by: str | None) -> list[AssetHistory]:
    """Diffs the tracked fields on an Asset PATCH and writes one history row per change.

    Called from routers/devices.py's update_asset alongside the existing audit-log
    write; this is a separate, append-only lifecycle log (asset_history table),
    not a replacement for the generic audit_logs entry.
    """
    entries: list[AssetHistory] = []
    for field, event_type in _TRACKED_FIELD_EVENTS.items():
        old_value = before.get(field)
        new_value = getattr(after, field)
        if old_value != new_value:
            entries.append(
                record_asset_history(
                    db,
                    asset_id=after.id,
                    event_type=event_type,
                    changed_by=changed_by,
                    from_value=str(old_value) if old_value is not None else None,
                    to_value=str(new_value) if new_value is not None else None,
                    branch_id=after.branch_id if field == "branch_id" else None,
                    department_id=after.department_id if field == "department_id" else None,
                    location_id=after.location_id if field == "location_id" else None,
                )
            )
    return entries
