from .mapping import SheetConfig, _norm

BLANK_TOKENS = {"", "n/a", "na", "not provided", "not applicable", "unknown", "pending", "null"}


def is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in BLANK_TOKENS
    return False


def map_row(config: SheetConfig, raw: dict[str, object]) -> dict[str, object]:
    """Translate raw column headers to model field names. Unknown headers are dropped
    from mapped_data but remain available in raw_data for audit purposes."""
    mapped: dict[str, object] = {}
    for header, value in raw.items():
        field_name = config.column_map.get(_norm(header))
        if field_name is None:
            continue
        mapped[field_name] = None if is_blank(value) else value
    return mapped


def business_key(config: SheetConfig, mapped: dict[str, object]) -> str | None:
    parts = [str(mapped[f]) for f in config.business_key_fields if mapped.get(f) not in (None, "")]
    return " | ".join(parts) if parts else None


def validate_row(config: SheetConfig, mapped: dict[str, object]) -> list[tuple[str | None, str, str]]:
    """Return a list of (field, message, severity) issues. severity is ERROR or WARNING."""
    issues: list[tuple[str | None, str, str]] = []
    for required_field in config.required:
        if mapped.get(required_field) in (None, ""):
            issues.append((required_field, f"Required field '{required_field}' is missing", "ERROR"))

    if not business_key(config, mapped):
        issues.append((None, "No stable business key could be determined for this row", "ERROR"))

    known_quantity = mapped.get("known_quantity")
    if known_quantity is not None and not isinstance(known_quantity, (int, float)):
        issues.append(("known_quantity", "Known quantity must be numeric", "WARNING"))

    opening_quantity = mapped.get("opening_quantity")
    if opening_quantity is not None and not isinstance(opening_quantity, (int, float)):
        issues.append(("opening_quantity", "Opening quantity must be numeric", "WARNING"))

    return issues
