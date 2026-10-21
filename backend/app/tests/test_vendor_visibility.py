"""Final admin/inventory/vendor/dashboard polish pass — Part 5: vendor visibility
masking (vendor.read vs vendor.read_limited)."""

from .conftest import auth_headers, create_user


def _make_vendor(admin_client, name="Masking Test Vendor", **overrides):
    payload = {
        "name": name, "vendor_type": "OEM", "city": "Pune",
        "email": "contact@vendor.example", "phone": "+91-9876500000",
    }
    payload.update(overrides)
    response = admin_client.post("/api/v1/vendors", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _limited_headers(client, db_session, role_code="TL", email="limited@example.com"):
    create_user(db_session, email, role_code, "Password123!")
    return auth_headers(client, email, "Password123!")


def _full_headers(client, db_session, role_code="BIOMEDICAL_ENGINEER", email="full@example.com"):
    create_user(db_session, email, role_code, "Password123!")
    return auth_headers(client, email, "Password123!")


# ---- Full VENDOR_READ role gets full contact fields ----

def test_full_read_role_gets_full_contact_fields(admin_client, client, db_session):
    vendor = _make_vendor(admin_client, "Full Read Vendor")
    headers = _full_headers(client, db_session)

    response = client.get(f"/api/v1/vendors/{vendor['id']}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "contact@vendor.example"
    assert body["phone"] == "+91-9876500000"


def test_full_read_role_gets_full_list(admin_client, client, db_session):
    _make_vendor(admin_client, "Full Read List Vendor")
    headers = _full_headers(client, db_session)

    response = client.get("/api/v1/vendors", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    match = next(i for i in items if i["name"] == "Full Read List Vendor")
    assert match["email"] == "contact@vendor.example"


# ---- VENDOR_READ_LIMITED role gets nulled contact fields, direct API call ----

def test_limited_role_gets_nulled_contact_fields_on_get(admin_client, client, db_session):
    vendor = _make_vendor(admin_client, "Limited Read Vendor")
    headers = _limited_headers(client, db_session)

    response = client.get(f"/api/v1/vendors/{vendor['id']}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["email"] is None
    assert body["phone"] is None
    # Non-contact fields are still visible.
    assert body["name"] == "Limited Read Vendor"
    assert body["city"] == "Pune"


def test_limited_role_gets_nulled_contact_fields_on_list(admin_client, client, db_session):
    _make_vendor(admin_client, "Limited Read List Vendor")
    headers = _limited_headers(client, db_session)

    response = client.get("/api/v1/vendors", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    match = next(i for i in items if i["name"] == "Limited Read List Vendor")
    assert match["email"] is None
    assert match["phone"] is None


def test_limited_role_gets_nulled_contact_fields_on_contacts(admin_client, client, db_session):
    vendor = _make_vendor(admin_client, "Contact Masking Vendor")
    admin_client.post(
        f"/api/v1/vendors/{vendor['id']}/contacts",
        json={"name": "Jane Contact", "email": "jane@vendor.example", "phone": "555-1234"},
    )
    headers = _limited_headers(client, db_session)

    response = client.get(f"/api/v1/vendors/{vendor['id']}/contacts", headers=headers)
    assert response.status_code == 200
    contacts = response.json()
    assert len(contacts) == 1
    assert contacts[0]["email"] is None
    assert contacts[0]["phone"] is None
    assert contacts[0]["name"] == "Jane Contact"


def test_limited_role_export_masks_contact_columns(admin_client, client, db_session):
    import csv
    import io

    _make_vendor(admin_client, "Export Masking Vendor")
    headers = _limited_headers(client, db_session)

    response = client.get("/api/v1/vendors/export?format=csv", headers=headers)
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    header_row = rows[0]
    email_idx = header_row.index("Email")
    phone_idx = header_row.index("Phone")
    row = next(r for r in rows[1:] if r[0] == "Export Masking Vendor")
    assert row[email_idx] == ""
    assert row[phone_idx] == ""


def test_role_with_no_vendor_permission_still_gets_403(client, db_session):
    # MANAGER's bundle holds neither vendor.read nor vendor.read_limited — confirms
    # granting the limited code to the six named roles didn't accidentally leak to
    # every role.
    create_user(db_session, "novendor@example.com", "MANAGER", "Password123!")
    headers = auth_headers(client, "novendor@example.com", "Password123!")
    response = client.get("/api/v1/vendors", headers=headers)
    assert response.status_code == 403
