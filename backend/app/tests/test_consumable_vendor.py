"""Final non-AI cleanup pass — Consumable vendor relationship (migration 0019).

Consumable previously only had a free-text `supplier` string with no Vendor FK,
unlike Device (which links to Vendor via DeviceModelVendor). This adds a nullable
`vendor_id` FK and exercises: create/update validation, and that it round-trips.
"""

from .conftest import auth_headers, create_user


def _make_vendor(admin_client, name="Consumable Vendor Co"):
    return admin_client.post("/api/v1/vendors", json={"name": name}).json()


def test_create_consumable_with_vendor(admin_client):
    vendor = _make_vendor(admin_client)
    response = admin_client.post(
        "/api/v1/consumables",
        json={"item_code": "CON-VEND-1", "name": "Suction Catheters", "unit": "PCS", "vendor_id": vendor["id"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["vendor_id"] == vendor["id"]


def test_create_consumable_with_unknown_vendor_rejected(admin_client):
    response = admin_client.post(
        "/api/v1/consumables",
        json={"item_code": "CON-VEND-2", "name": "Gauze", "unit": "PCS", "vendor_id": "does-not-exist"},
    )
    assert response.status_code == 404


def test_update_consumable_vendor(admin_client):
    vendor_a = _make_vendor(admin_client, "Vendor A")
    vendor_b = _make_vendor(admin_client, "Vendor B")
    created = admin_client.post(
        "/api/v1/consumables",
        json={"item_code": "CON-VEND-3", "name": "IV Sets", "unit": "PCS", "vendor_id": vendor_a["id"]},
    ).json()
    assert created["vendor_id"] == vendor_a["id"]

    updated = admin_client.patch(f"/api/v1/consumables/{created['id']}", json={"vendor_id": vendor_b["id"]})
    assert updated.status_code == 200
    assert updated.json()["vendor_id"] == vendor_b["id"]


def test_consumable_vendor_contact_masked_for_limited_role(admin_client, client, db_session):
    """Consumable's vendor is resolved by the frontend from the same masked /vendors
    list every other module uses — this confirms that masking still applies when a
    vendor is reached via a consumable, not just via a device/asset."""
    vendor = admin_client.post(
        "/api/v1/vendors", json={"name": "Masked Consumable Vendor", "email": "sales@vendor.example", "phone": "555-0100"}
    ).json()
    admin_client.post(
        "/api/v1/consumables",
        json={"item_code": "CON-VEND-4", "name": "Syringes", "unit": "PCS", "vendor_id": vendor["id"]},
    )

    create_user(db_session, "tl-consumable@example.com", "TL", "Password123!")
    headers = auth_headers(client, "tl-consumable@example.com", "Password123!")
    fetched = client.get(f"/api/v1/vendors/{vendor['id']}", headers=headers)
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["email"] is None
    assert body["phone"] is None
