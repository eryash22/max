"""Phase 2 — translates a physical asset's real lifecycle changes into communication events.

This is a thin mapper, not a second notification system. Every event it raises goes
through the one existing pipeline (communication_service.dispatch_asset_event_safe ->
Communication Rule -> template -> recipient -> Notification -> optional auto-send ->
the existing WhatsApp Web / SMTP senders).

The mapping is driven by AssetHistory, which stays the authoritative physical asset
history and is neither replaced nor duplicated here: the workflow writes its history
rows exactly as before, and this module reads those rows back to decide what (if
anything) is worth telling someone about. That gives three properties the brief asks
for at once —

  * no duplicate/conflicting audit: the notification is keyed to the AssetHistory row
    id that recorded the change, so both trails describe one transition;
  * no trivial-edit noise: a serial number, purchase date or criticality edit writes
    no history row (see asset_history_service._TRACKED_FIELD_EVENTS) and so raises
    nothing here. Only status, branch, department and location — the four fields this
    codebase already considers lifecycle-significant — can produce an event;
  * one event per business action: several internal saves inside one operation still
    resolve to the same AssetHistory row(s), and the dedup key makes the second
    dispatch a no-op.

CONDEMNED and RETIRED are emitted as their own events rather than as a generic status
change, because they are genuinely separate workflows here (an approved CONDEMNATION
requirement vs. archive_asset) with different stakeholders. A transition landing on
either value emits that specific event INSTEAD of ASSET_STATUS_CHANGED — never both.
"""
from sqlalchemy.orm import Session

from ..models.asset_history import AssetHistory
from ..models.device import Asset
from ..schemas.notification import (
    EVENT_ASSET_ALLOCATED,
    EVENT_ASSET_CONDEMNED,
    EVENT_ASSET_LOCATION_CHANGED,
    EVENT_ASSET_RETIRED,
    EVENT_ASSET_STATUS_CHANGED,
    EVENT_ASSET_TRANSFERRED,
)
from .communication_service import dispatch_asset_event_safe

# A status transition landing on one of these is that lifecycle event, not a generic
# status change. Compared case-insensitively for the same reason
# asset_lifecycle_service does: Asset.status is a free-text column with legacy values.
_TERMINAL_STATUS_EVENTS = {
    "CONDEMNED": EVENT_ASSET_CONDEMNED,
    "RETIRED": EVENT_ASSET_RETIRED,
}

_HISTORY_EVENT_MAP = {
    "BRANCH_TRANSFER": EVENT_ASSET_TRANSFERRED,
    "DEPARTMENT_CHANGE": EVENT_ASSET_LOCATION_CHANGED,
    "LOCATION_CHANGE": EVENT_ASSET_LOCATION_CHANGED,
}


def event_for_history(entry: AssetHistory) -> str | None:
    """The Phase 2 event one AssetHistory row represents, or None if it represents
    nothing worth notifying about."""
    if entry.event_type == "STATUS_CHANGE":
        return _TERMINAL_STATUS_EVENTS.get((entry.to_value or "").upper(), EVENT_ASSET_STATUS_CHANGED)
    return _HISTORY_EVENT_MAP.get(entry.event_type)


def dispatch_for_history(
    db: Session,
    *,
    asset: Asset,
    entries: list[AssetHistory],
    previous: dict | None = None,
    user_id: str | None,
    reference: str | None = None,
) -> None:
    """Raise one communication event per meaningful AssetHistory row just written.

    Must be called AFTER the business transaction has committed — dispatch commits its
    own Notification rows, and a communication problem must never roll back the asset
    change that caused it (dispatch_asset_event_safe enforces the second half).

    A department AND location move made in the same PATCH is one relocation, so it
    raises ASSET_LOCATION_CHANGED once rather than twice.
    """
    seen: set[str] = set()
    for entry in entries:
        event = event_for_history(entry)
        if event is None or event in seen:
            continue
        seen.add(event)
        dispatch_asset_event_safe(
            db,
            event,
            asset=asset,
            user_id=user_id,
            # The authoritative history row for this transition is the dedup identity.
            dedup_key=f"asset:{asset.id}:{event}:{entry.id}",
            previous=previous,
            reference=reference,
            notes=entry.notes,
        )


def dispatch_allocation(
    db: Session, *, asset: Asset, fulfillment_id: str, reference: str | None,
    previous: dict | None, user_id: str | None,
) -> None:
    """A device issued to a branch through the requirement/fulfillment workflow.

    Keyed on the fulfillment rather than on an AssetHistory row: allocation is a real
    business event even when the asset was already sitting at the destination branch
    (in which case complete_fulfillment writes no BRANCH_TRANSFER row at all). One
    allocation of one asset produces exactly one event, however many times the
    completion step is retried.
    """
    dispatch_asset_event_safe(
        db,
        EVENT_ASSET_ALLOCATED,
        asset=asset,
        user_id=user_id,
        dedup_key=f"asset:{asset.id}:{EVENT_ASSET_ALLOCATED}:{fulfillment_id}",
        previous=previous,
        reference=reference,
    )


def dispatch_created(db: Session, *, asset: Asset, user_id: str | None, reference: str | None = None) -> None:
    """A new physical device/asset was added to the register. Keyed on the asset alone
    — an asset is only ever created once."""
    from ..schemas.notification import EVENT_ASSET_CREATED

    dispatch_asset_event_safe(
        db,
        EVENT_ASSET_CREATED,
        asset=asset,
        user_id=user_id,
        dedup_key=f"asset:{asset.id}:{EVENT_ASSET_CREATED}",
        reference=reference,
    )
