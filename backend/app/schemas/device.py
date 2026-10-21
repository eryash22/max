from pydantic import BaseModel, field_validator

from .asset_lifecycle import LIFECYCLE_STATUSES

# Final hardening pass — Asset.status stays free text at the DB level (breaking
# existing legacy data by switching to a real enum column is explicitly out of
# scope, see models/device.py's docstring), but new writes are now validated
# against the canonical vocabulary plus these three legacy values already present
# in real data before this pass, so they keep working unchanged.
LEGACY_ASSET_STATUSES = ["Pending", "In Service", "Decommissioned"]
ALLOWED_ASSET_STATUSES = set(LIFECYCLE_STATUSES) | set(LEGACY_ASSET_STATUSES)


def _blank_to_none(value: str | None) -> str | None:
    """Asset Number must never be stored as an empty string standing in for
    'not provided' — Final Operational Gap Pass, Part 1. NULL/absence is the only
    representation of 'no asset number yet'. Inventory/Asset unification pass —
    also trims incidental leading/trailing whitespace (e.g. a pasted value), so
    " MAX-DEV-001 " and "MAX-DEV-001" are treated as the one asset number they
    actually are, both in storage and in the uniqueness check callers run
    afterward (routers/devices.py's create_asset/update_asset/create_device)."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class DeviceCategoryIn(BaseModel):
    code: str | None = None
    name: str


class DeviceCategoryOut(DeviceCategoryIn):
    id: str

    model_config = {"from_attributes": True}


class DeviceModelIn(BaseModel):
    category_id: str | None = None
    name: str
    manufacturer: str | None = None
    model_number: str | None = None
    category: str
    record_type: str = "DEVICE"
    model_status: str | None = None
    firmware_version: str | None = None
    criticality_default: str | None = None
    known_quantity: int | None = None
    notes: str | None = None
    data_source: str | None = None
    model_information_status: str = "MODEL_NOT_PROVIDED"
    catalog_match_status: str = "UNMATCHED"


class DeviceModelOut(DeviceModelIn):
    id: str
    environment: str
    is_active: bool = True

    model_config = {"from_attributes": True}


class DeviceModelStockOut(DeviceModelOut):
    """Device Catalog / Allocate-to-Branch unification pass — the catalog list now
    also reports live physical-unit counts per model so a 'Register Physical Unit &
    Allocate' picker can show zero-stock models alongside ones that already have
    central/branch stock, without a second round-trip per row. Counts are computed
    from real Asset rows at request time (never stored), same "derive, don't
    duplicate" discipline as coverage/vendor status elsewhere in this codebase."""

    total_units: int = 0
    available_units: int = 0
    allocated_units: int = 0


class AssetIn(BaseModel):
    asset_number: str | None = None
    serial_number: str | None = None
    device_model_id: str
    branch_id: str
    department_id: str | None = None
    location_id: str | None = None
    status: str = "Pending"
    criticality: str | None = None
    purchase_date: str | None = None
    installation_date: str | None = None
    operational_since: str | None = None

    _normalize_asset_number = field_validator("asset_number")(_blank_to_none)


class AssetOut(AssetIn):
    id: str
    environment: str

    model_config = {"from_attributes": True}


class AssetAllocateIn(BaseModel):
    """Inventory/Asset unification pass — 'Allocate Device to Branch' is a narrow,
    purpose-built action distinct from the full-replace AssetIn PATCH: it only ever
    touches where the physical Asset currently sits (branch/department/location),
    never its device model, status, serial/asset number, or any other field, so an
    allocate-to-branch screen can never accidentally clobber unrelated Asset data
    it doesn't even show. See routers/devices.py's allocate_asset_to_branch."""

    branch_id: str
    department_id: str | None = None
    location_id: str | None = None


class DeviceCreateIn(BaseModel):
    """Composite 'Add Device' payload — Part D of the restructure consolidation pass.
    Either reuses an existing `DeviceModel` (`device_model_id` set) or creates one
    inline from `model_*` fields, then always creates one `Asset` in the same
    request/transaction. No new Device/Asset table — this composes the two existing
    `DeviceModelIn`/`AssetIn` payloads instead of duplicating their fields as a third
    model."""

    # Device model: reuse an existing one, or create inline (name/category required if new).
    device_model_id: str | None = None
    model_name: str | None = None
    manufacturer: str | None = None
    model_number: str | None = None
    category_id: str | None = None
    category: str | None = None
    criticality_default: str | None = None

    # Asset (physical unit) fields. asset_number is genuinely optional — Final
    # Operational Gap Pass, Part 1 — left NULL (never a generated placeholder) when
    # not provided; can be attached later via PATCH /api/v1/assets/{id}.
    serial_number: str | None = None
    asset_number: str | None = None
    branch_id: str
    department_id: str | None = None
    location_id: str | None = None
    status: str = "Pending"
    criticality: str | None = None
    purchase_date: str | None = None
    installation_date: str | None = None
    operational_since: str | None = None

    _normalize_asset_number = field_validator("asset_number")(_blank_to_none)


class DeviceCreateOut(BaseModel):
    device_model: DeviceModelOut
    asset: AssetOut


class BranchDeviceAssignmentIn(BaseModel):
    branch_id: str
    device_model_id: str | None = None
    source_name: str
    known_quantity: int | None = None
    assignment_status: str = "PENDING_ASSET_IDENTIFICATION"
    inventory_mapping_status: str | None = None
    asset_identification_status: str | None = None
    data_quality_flag: str | None = None
    notes: str | None = None


class BranchDeviceAssignmentOut(BranchDeviceAssignmentIn):
    id: str
    environment: str

    model_config = {"from_attributes": True}
