CONTACT_FIELDS = ("email", "phone")


def strip_contact_fields(out: dict) -> dict:
    """Null out contact fields for `vendor.read_limited` callers (TL/Unit Head/Doctor/
    Staff-Operator/Viewer/Hospital-Unit-Manager) — they may see a vendor's identity,
    type, coverage, and status, but must never receive its direct contact details
    (email/phone), matching the client's role-gated vendor visibility requirement.
    Mirrors `inventory_service.strip_stock_fields()`'s shape exactly: called from the
    router, not baked into serialization, so `vendor.read` callers (Super Admin,
    Biomedical Admin, Biomedical Engineer, Inventory Manager, Procurement Officer)
    keep seeing real values.

    Works uniformly on both a serialized `Vendor` dict (email/phone are the vendor's
    own contact fields) and a serialized `VendorContact` dict (email/phone are that
    contact person's fields) — whichever of the two keys are present get nulled,
    nothing else is touched."""
    masked = dict(out)
    for field in CONTACT_FIELDS:
        if field in masked:
            masked[field] = None
    return masked
