"""Business-workflow audit (2026-08-22) — regression tests for the specific gaps the
audit closed. See PROJECT_STATUS.md for the full audit matrix.

1. HOSPITAL_UNIT_MANAGER (Hospital TL / Unit Head) must be able to list inventory
   items/consumables (requirement-form pickers) but must never receive real stock
   quantities from those endpoints, and must be denied every other inventory
   endpoint (single-item detail, stock, locations, transactions) outright.
2. Every other inventory-visibility role (INVENTORY_MANAGER, SUPER_ADMIN, MANAGER,
   BIOMEDICAL_ADMIN, BIOMEDICAL_ENGINEER) keeps full stock visibility — the fix must
   be scoped to HOSPITAL_UNIT_MANAGER only.
3. Incident assignment rejects an engineer who doesn't hold a BME-eligible role.
"""
from .conftest import auth_headers, create_user

STOCK_ITEM_FIELDS = ("min_level", "max_level", "reorder_level", "unit_cost", "current_stock", "stock_status")
STOCK_CONSUMABLE_FIELDS = ("opening_quantity", "min_level", "reorder_level", "stock_status")


def _make_branch(admin_client, name="TL Audit Branch"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _tl_headers(client, db_session, email="tl-audit@example.com"):
    create_user(db_session, email, "HOSPITAL_UNIT_MANAGER", "Password123!")
    return auth_headers(client, email, "Password123!")


def test_tl_can_list_inventory_items_but_stock_fields_are_masked(admin_client, client, db_session):
    admin_client.post("/api/v1/inventory/items", json={"item_code": "AUD-ITM-1", "name": "Ultrasound Gel"})
    headers = _tl_headers(client, db_session)

    response = client.get("/api/v1/inventory/items", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert any(i["item_code"] == "AUD-ITM-1" for i in items)
    for item in items:
        for field in STOCK_ITEM_FIELDS:
            assert item[field] is None, f"{field} leaked central stock to TL/Unit Head"


def test_tl_can_list_consumables_but_stock_fields_are_masked(admin_client, client, db_session):
    admin_client.post(
        "/api/v1/consumables",
        json={"item_code": "AUD-CON-1", "name": "Dysphagia Electrode Pack of 2", "opening_quantity": 40},
    )
    headers = _tl_headers(client, db_session)

    response = client.get("/api/v1/consumables", headers=headers)
    assert response.status_code == 200
    consumables = response.json()["items"]
    assert any(c["item_code"] == "AUD-CON-1" for c in consumables)
    for consumable in consumables:
        for field in STOCK_CONSUMABLE_FIELDS:
            assert consumable[field] is None, f"{field} leaked central/branch stock to TL/Unit Head"


def test_tl_cannot_read_single_item_stock_or_detail(admin_client, client, db_session):
    item = admin_client.post("/api/v1/inventory/items", json={"item_code": "AUD-ITM-2", "name": "Cold Pack"}).json()
    headers = _tl_headers(client, db_session, "tl-audit2@example.com")

    assert client.get(f"/api/v1/inventory/items/{item['id']}", headers=headers).status_code == 403
    assert client.get(f"/api/v1/inventory/items/{item['id']}/stock", headers=headers).status_code == 403


def test_tl_cannot_read_inventory_locations_or_transactions(client, db_session):
    headers = _tl_headers(client, db_session, "tl-audit3@example.com")
    assert client.get("/api/v1/inventory/locations", headers=headers).status_code == 403
    assert client.get("/api/v1/inventory/transactions", headers=headers).status_code == 403


def test_tl_stock_status_filter_is_ignored_not_honored(admin_client, client, db_session):
    """A picker-only caller must not be able to use the stock_status filter to infer
    real stock levels (e.g. probing which items are OUT_OF_STOCK) — the filter param
    is silently dropped for HOSPITAL_UNIT_MANAGER rather than applied."""
    admin_client.post("/api/v1/inventory/items", json={"item_code": "AUD-ITM-3", "name": "Hot Pack"})
    headers = _tl_headers(client, db_session, "tl-audit4@example.com")

    response = client.get("/api/v1/inventory/items?stock_status=OUT_OF_STOCK", headers=headers)
    assert response.status_code == 200
    assert any(i["item_code"] == "AUD-ITM-3" for i in response.json()["items"])


def test_inventory_manager_still_sees_full_stock(admin_client, client, db_session):
    admin_client.post("/api/v1/inventory/items", json={"item_code": "AUD-ITM-4", "name": "Thrive Massager"})
    create_user(db_session, "invmgr-audit@example.com", "INVENTORY_MANAGER", "Password123!")
    headers = auth_headers(client, "invmgr-audit@example.com", "Password123!")

    response = client.get("/api/v1/inventory/items", headers=headers)
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["item_code"] == "AUD-ITM-4")
    assert item["current_stock"] is not None
    assert item["stock_status"] is not None


def test_manager_role_still_sees_full_stock(admin_client, client, db_session):
    admin_client.post("/api/v1/inventory/items", json={"item_code": "AUD-ITM-5", "name": "Walker With Wheels"})
    create_user(db_session, "mgr-audit@example.com", "MANAGER", "Password123!")
    headers = auth_headers(client, "mgr-audit@example.com", "Password123!")

    response = client.get("/api/v1/inventory/items", headers=headers)
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["item_code"] == "AUD-ITM-5")
    assert item["current_stock"] is not None


def test_incident_assignment_rejects_non_bme_role(admin_client, client, db_session):
    branch = admin_client.post("/api/v1/branches", json={"name": "BME Audit Branch", "status": "ACTIVE"}).json()
    create_user(db_session, "doc-audit2@example.com", "DOCTOR", "Password123!")
    doc_headers = auth_headers(client, "doc-audit2@example.com", "Password123!")
    non_bme_user = create_user(db_session, "viewer-audit@example.com", "VIEWER", "Password123!")
    engineer_user = create_user(db_session, "eng-audit@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    eng_headers = auth_headers(client, "eng-audit@example.com", "Password123!")

    created = client.post(
        "/api/v1/incidents", json={"branch_id": branch["id"], "description": "Broken CPM"}, headers=doc_headers
    ).json()

    rejected = client.post(
        f"/api/v1/incidents/{created['id']}/assign",
        json={"assigned_engineer_id": non_bme_user.id},
        headers=eng_headers,
    )
    assert rejected.status_code == 422

    accepted = client.post(
        f"/api/v1/incidents/{created['id']}/assign",
        json={"assigned_engineer_id": engineer_user.id},
        headers=eng_headers,
    )
    assert accepted.status_code == 200
    assert accepted.json()["assigned_engineer_id"] == engineer_user.id
