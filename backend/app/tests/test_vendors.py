from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _engineer_headers(client, db_session, email="vendeng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _inventory_manager_headers(client, db_session, email="invmgr@example.com"):
    create_user(db_session, email, "INVENTORY_MANAGER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _make_device_model(admin_client, name="XRay-Model-1"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "Imaging"}).json()


# ---- Vendor CRUD ----

def test_admin_can_create_vendor(admin_client):
    response = admin_client.post(
        "/api/v1/vendors",
        json={
            "name": "Acme Biomedical Services",
            "vendor_type": "SERVICE",
            "is_service_vendor": True,
            "city": "Pune",
            "state": "Maharashtra",
            "email": "contact@acme-biomed.com",
            "phone": "+91-9876543210",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Acme Biomedical Services"
    assert body["status"] == "ACTIVE"
    assert body["is_service_vendor"] is True


def test_doctor_cannot_create_vendor(client, db_session):
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.post("/api/v1/vendors", json={"name": "Blocked Vendor"}, headers=headers)
    assert response.status_code == 403


def test_doctor_gets_masked_vendor_read(client, db_session):
    """Final admin/inventory/vendor/dashboard polish pass — Part 5: DOCTOR now holds
    `vendor.read_limited` (net-new grant, see `security/permissions.py`), so it no
    longer 403s on vendor read — it gets 200 with contact fields (email/phone)
    nulled server-side. Supersedes the old `test_doctor_cannot_read_vendors`, which
    predated this pass. See `test_vendor_visibility.py` for full masking coverage."""
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.get("/api/v1/vendors", headers=headers)
    assert response.status_code == 200


def test_engineer_can_read_but_not_write_vendors(admin_client, client, db_session):
    admin_client.post("/api/v1/vendors", json={"name": "Read Only Vendor"})
    headers = _engineer_headers(client, db_session)

    read_response = client.get("/api/v1/vendors", headers=headers)
    assert read_response.status_code == 200

    write_response = client.post("/api/v1/vendors", json={"name": "Engineer Created"}, headers=headers)
    assert write_response.status_code == 403


def test_inventory_manager_can_read_but_not_write_vendors(admin_client, client, db_session):
    admin_client.post("/api/v1/vendors", json={"name": "Inventory Read Vendor"})
    headers = _inventory_manager_headers(client, db_session)

    read_response = client.get("/api/v1/vendors", headers=headers)
    assert read_response.status_code == 200

    write_response = client.post("/api/v1/vendors", json={"name": "Inventory Manager Created"}, headers=headers)
    assert write_response.status_code == 403


def test_get_vendor_by_id(admin_client):
    created = admin_client.post("/api/v1/vendors", json={"name": "Get Vendor"}).json()
    fetched = admin_client.get(f"/api/v1/vendors/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]


def test_get_vendor_not_found(admin_client):
    response = admin_client.get("/api/v1/vendors/does-not-exist")
    assert response.status_code == 404


def test_update_vendor(admin_client):
    created = admin_client.post("/api/v1/vendors", json={"name": "Update Vendor", "city": "Mumbai"}).json()
    updated = admin_client.patch(f"/api/v1/vendors/{created['id']}", json={"city": "Delhi", "rating": 4})
    assert updated.status_code == 200
    body = updated.json()
    assert body["city"] == "Delhi"
    assert body["rating"] == 4
    assert body["name"] == "Update Vendor"


def test_update_vendor_not_found(admin_client):
    response = admin_client.patch("/api/v1/vendors/does-not-exist", json={"city": "Delhi"})
    assert response.status_code == 404


# ---- Activate / deactivate ----

def test_deactivate_and_activate_vendor(admin_client):
    created = admin_client.post("/api/v1/vendors", json={"name": "Toggle Vendor"}).json()
    assert created["status"] == "ACTIVE"

    deactivated = admin_client.post(f"/api/v1/vendors/{created['id']}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "INACTIVE"

    activated = admin_client.post(f"/api/v1/vendors/{created['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"


def test_engineer_cannot_deactivate_vendor(admin_client, client, db_session):
    created = admin_client.post("/api/v1/vendors", json={"name": "Protected Vendor"}).json()
    headers = _engineer_headers(client, db_session)
    response = client.post(f"/api/v1/vendors/{created['id']}/deactivate", headers=headers)
    assert response.status_code == 403


# ---- Stale/deleted master-data visibility audit (Final task) ----
# GET /vendors already supported status_filter, but no "new record" picker
# (inventory/new, contracts/new, documents/new) ever passed it, so a deactivated
# vendor stayed selectable everywhere. Fixed at those frontend call sites — this
# confirms the backend filter itself behaves correctly for status_filter=ACTIVE.

def test_deactivated_vendor_excluded_from_active_filtered_list(admin_client):
    created = admin_client.post("/api/v1/vendors", json={"name": "Stale Vendor Picker QA"}).json()
    admin_client.post(f"/api/v1/vendors/{created['id']}/deactivate")

    active_only = admin_client.get("/api/v1/vendors?status_filter=ACTIVE&page_size=200")
    assert all(v["id"] != created["id"] for v in active_only.json()["items"])


def test_deactivated_vendor_still_visible_unfiltered(admin_client):
    """The Vendors admin list (no status_filter passed) must keep showing inactive
    vendors so they stay manageable/reactivatable."""
    created = admin_client.post("/api/v1/vendors", json={"name": "Stale Vendor Admin QA"}).json()
    admin_client.post(f"/api/v1/vendors/{created['id']}/deactivate")

    unfiltered = admin_client.get("/api/v1/vendors?page_size=200")
    assert any(v["id"] == created["id"] for v in unfiltered.json()["items"])


# ---- Delete ----

def test_delete_unreferenced_vendor_succeeds(admin_client, db_session):
    vendor = admin_client.post("/api/v1/vendors", json={"name": "Unreferenced Vendor"}).json()
    response = admin_client.delete(f"/api/v1/vendors/{vendor['id']}")
    assert response.status_code == 204

    get_response = admin_client.get(f"/api/v1/vendors/{vendor['id']}")
    assert get_response.status_code == 404

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "vendor", AuditLog.entity_id == vendor["id"], AuditLog.action == "DELETE"
    ).first()
    assert log is not None


def test_delete_referenced_vendor_blocked(admin_client):
    vendor = admin_client.post("/api/v1/vendors", json={"name": "Referenced Vendor"}).json()
    model = _make_device_model(admin_client, name="Referenced Vendor Model")
    admin_client.post(
        f"/api/v1/vendors/{vendor['id']}/device-models",
        json={"device_model_id": model["id"], "rank": 0},
    )

    response = admin_client.delete(f"/api/v1/vendors/{vendor['id']}")
    assert response.status_code == 409
    assert "1" in response.json()["detail"]

    still_there = admin_client.get(f"/api/v1/vendors/{vendor['id']}")
    assert still_there.status_code == 200


def test_delete_vendor_with_contact_blocked(admin_client):
    vendor = admin_client.post("/api/v1/vendors", json={"name": "Contacted Vendor"}).json()
    admin_client.post(f"/api/v1/vendors/{vendor['id']}/contacts", json={"name": "Jane Doe"})

    response = admin_client.delete(f"/api/v1/vendors/{vendor['id']}")
    assert response.status_code == 409


def test_delete_vendor_not_found(admin_client):
    response = admin_client.delete("/api/v1/vendors/does-not-exist")
    assert response.status_code == 404


def test_delete_vendor_requires_vendor_write(admin_client, client, db_session):
    vendor = admin_client.post("/api/v1/vendors", json={"name": "Protected Delete Vendor"}).json()
    headers = _engineer_headers(client, db_session)
    response = client.delete(f"/api/v1/vendors/{vendor['id']}", headers=headers)
    assert response.status_code == 403


# ---- List / search / filter / pagination ----

def test_list_vendors_search_by_name(admin_client):
    admin_client.post("/api/v1/vendors", json={"name": "Philips Healthcare"})
    admin_client.post("/api/v1/vendors", json={"name": "GE Medical Systems"})

    response = admin_client.get("/api/v1/vendors?search=Philips")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Philips Healthcare"


def test_list_vendors_filter_by_status(admin_client):
    active = admin_client.post("/api/v1/vendors", json={"name": "Active Vendor"}).json()
    inactive = admin_client.post("/api/v1/vendors", json={"name": "Inactive Vendor"}).json()
    admin_client.post(f"/api/v1/vendors/{inactive['id']}/deactivate")

    response = admin_client.get("/api/v1/vendors?status_filter=INACTIVE")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == inactive["id"]


def test_list_vendors_filter_by_vendor_type(admin_client):
    admin_client.post("/api/v1/vendors", json={"name": "OEM Vendor", "vendor_type": "OEM"})
    admin_client.post("/api/v1/vendors", json={"name": "Distributor Vendor", "vendor_type": "DISTRIBUTOR"})

    response = admin_client.get("/api/v1/vendors?vendor_type=OEM")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["vendor_type"] == "OEM"


def test_list_vendors_filter_by_location(admin_client):
    admin_client.post("/api/v1/vendors", json={"name": "Pune Vendor", "city": "Pune", "state": "Maharashtra"})
    admin_client.post("/api/v1/vendors", json={"name": "Delhi Vendor", "city": "Delhi", "state": "Delhi"})

    response = admin_client.get("/api/v1/vendors?city=Pune")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["city"] == "Pune"


def test_list_vendors_pagination(admin_client):
    for i in range(3):
        admin_client.post("/api/v1/vendors", json={"name": f"Paged Vendor {i}"})

    page1 = admin_client.get("/api/v1/vendors?page=1&page_size=2")
    assert page1.status_code == 200
    assert page1.json()["total"] >= 3
    assert len(page1.json()["items"]) == 2


# ---- Validation ----

def test_create_vendor_requires_name(admin_client):
    response = admin_client.post("/api/v1/vendors", json={"name": ""})
    assert response.status_code == 422


def test_create_vendor_invalid_email_rejected(admin_client):
    response = admin_client.post("/api/v1/vendors", json={"name": "Bad Email Vendor", "email": "not-an-email"})
    assert response.status_code == 422


def test_create_vendor_invalid_vendor_type_rejected(admin_client):
    response = admin_client.post("/api/v1/vendors", json={"name": "Bad Type Vendor", "vendor_type": "RANDOM"})
    assert response.status_code == 422


def test_create_vendor_invalid_rating_rejected(admin_client):
    response = admin_client.post("/api/v1/vendors", json={"name": "Bad Rating Vendor", "rating": 10})
    assert response.status_code == 422


def test_create_vendor_duplicate_name_rejected(admin_client):
    admin_client.post("/api/v1/vendors", json={"name": "Duplicate Vendor"})
    response = admin_client.post("/api/v1/vendors", json={"name": "Duplicate Vendor"})
    assert response.status_code == 409


def test_update_vendor_duplicate_name_rejected(admin_client):
    admin_client.post("/api/v1/vendors", json={"name": "Existing Name"})
    other = admin_client.post("/api/v1/vendors", json={"name": "Other Name"}).json()
    response = admin_client.patch(f"/api/v1/vendors/{other['id']}", json={"name": "Existing Name"})
    assert response.status_code == 409


# ---- Vendor contacts ----

def test_create_and_list_vendor_contact(admin_client):
    vendor = admin_client.post("/api/v1/vendors", json={"name": "Contact Vendor"}).json()
    contact = admin_client.post(
        f"/api/v1/vendors/{vendor['id']}/contacts",
        json={"name": "John Doe", "role": "Account Manager", "email": "john@vendor.com", "phone": "12345", "is_primary": True},
    )
    assert contact.status_code == 201
    body = contact.json()
    assert body["vendor_id"] == vendor["id"]
    assert body["is_primary"] is True

    listed = admin_client.get(f"/api/v1/vendors/{vendor['id']}/contacts")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_create_vendor_contact_requires_existing_vendor(admin_client):
    response = admin_client.post("/api/v1/vendors/does-not-exist/contacts", json={"name": "Jane Doe"})
    assert response.status_code == 404


# ---- Vendor <-> device model linkage ----

def test_link_vendor_to_device_model(admin_client):
    vendor = admin_client.post("/api/v1/vendors", json={"name": "Device Vendor"}).json()
    model = _make_device_model(admin_client)

    link = admin_client.post(
        f"/api/v1/vendors/{vendor['id']}/device-models",
        json={"device_model_id": model["id"], "rank": 0},
    )
    assert link.status_code == 201
    assert link.json()["device_model_id"] == model["id"]

    listed = admin_client.get(f"/api/v1/vendors/{vendor['id']}/device-models")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_link_vendor_to_device_model_requires_valid_model(admin_client):
    vendor = admin_client.post("/api/v1/vendors", json={"name": "Bad Link Vendor"}).json()
    response = admin_client.post(
        f"/api/v1/vendors/{vendor['id']}/device-models",
        json={"device_model_id": "does-not-exist"},
    )
    assert response.status_code == 404


# ---- Audit logging ----

def test_vendor_creation_is_audited(admin_client, db_session):
    created = admin_client.post("/api/v1/vendors", json={"name": "Audited Vendor"}).json()
    log = db_session.query(AuditLog).filter(AuditLog.entity == "vendor", AuditLog.entity_id == created["id"]).first()
    assert log is not None
    assert log.action == "CREATE"


def test_vendor_update_is_audited(admin_client, db_session):
    created = admin_client.post("/api/v1/vendors", json={"name": "Audit Update Vendor"}).json()
    admin_client.patch(f"/api/v1/vendors/{created['id']}", json={"city": "Chennai"})

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "vendor", AuditLog.entity_id == created["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None
    assert log.new_value.get("city") == "Chennai"


def test_vendor_status_change_is_audited(admin_client, db_session):
    created = admin_client.post("/api/v1/vendors", json={"name": "Audit Status Vendor"}).json()
    admin_client.post(f"/api/v1/vendors/{created['id']}/deactivate")

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "vendor", AuditLog.entity_id == created["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None
    assert log.new_value.get("status") == "INACTIVE"


# ---- Vendor service visit history ----

def test_list_vendor_service_visits_empty(admin_client):
    vendor = admin_client.post("/api/v1/vendors", json={"name": "No Visits Vendor"}).json()
    response = admin_client.get(f"/api/v1/vendors/{vendor['id']}/service-visits")
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_list_vendor_service_visits_requires_existing_vendor(admin_client):
    response = admin_client.get("/api/v1/vendors/does-not-exist/service-visits")
    assert response.status_code == 404
