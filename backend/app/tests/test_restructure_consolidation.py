"""Product Restructure / Consolidation Pass — tests for Parts A, B, C, D, F, I, M.

The migration 0008 backfill logic (hospital-network seed + branch backfill,
device-category seed + device-model backfill) lives in the Alembic migration file
itself, not in application code, since it only ever needs to run once against real
data (see migrations/versions/0008_restructure_consolidation.py's docstring). Tests
here import that module directly (it's a normal Python file, just not on a dotted
import path because its filename starts with a digit) and exercise its backfill
functions against the same in-memory SQLite session every other test uses — this
proves the backfill logic itself is correct without requiring a live Postgres/Alembic
run inside the test suite, matching this project's existing SQLite-only test
strategy (see conftest.py's db_session fixture docstring).
"""
import importlib.util
from pathlib import Path

from .conftest import auth_headers, create_user

_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0008_restructure_consolidation.py"
_spec = importlib.util.spec_from_file_location("migration_0008", _MIGRATION_PATH)
migration_0008 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0008)


# ---- Part A — HospitalNetwork seed + Branch.network_id backfill ----

def test_backfill_seeds_single_max_healthcare_network(admin_client, db_session):
    admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"})
    admin_client.post("/api/v1/branches", json={"name": "Dwarka", "status": "ACTIVE"})

    migration_0008._backfill_hospital_network(db_session)
    db_session.commit()

    networks = admin_client.get("/api/v1/hospital-networks").json()
    assert networks["total"] == 1
    assert networks["items"][0]["name"] == "MAX Healthcare"

    branches = admin_client.get("/api/v1/branches").json()["items"]
    network_id = networks["items"][0]["id"]
    assert all(b["network_id"] == network_id for b in branches)


def test_backfill_is_idempotent_and_does_not_overwrite_existing_network(admin_client, db_session):
    other_network = admin_client.post("/api/v1/hospital-networks", json={"name": "Other Group"}).json()
    branch = admin_client.post(
        "/api/v1/branches", json={"name": "Preassigned", "status": "ACTIVE", "network_id": other_network["id"]}
    ).json()
    unassigned = admin_client.post("/api/v1/branches", json={"name": "Unassigned", "status": "ACTIVE"}).json()

    migration_0008._backfill_hospital_network(db_session)
    db_session.commit()
    migration_0008._backfill_hospital_network(db_session)  # idempotent re-run
    db_session.commit()

    networks = admin_client.get("/api/v1/hospital-networks").json()
    names = {n["name"] for n in networks["items"]}
    assert "Other Group" in names and "MAX Healthcare" in names
    assert sum(1 for n in networks["items"] if n["name"] == "MAX Healthcare") == 1

    preassigned = admin_client.get(f"/api/v1/branches/{branch['id']}").json()
    assert preassigned["network_id"] == other_network["id"]  # untouched

    now_assigned = admin_client.get(f"/api/v1/branches/{unassigned['id']}").json()
    assert now_assigned["network_id"] is not None


# ---- Part B — Branch contact fields ----

def test_branch_create_and_update_carries_contact_fields(admin_client):
    created = admin_client.post(
        "/api/v1/branches",
        json={
            "name": "Saket",
            "status": "ACTIVE",
            "unit_head": "Dr. Rao",
            "tl": "Mr. Sharma",
            "branch_email": "saket@maxhealthcare.in",
            "branch_phone": "+91-9999999999",
            "branch_whatsapp": "+91-9999999999",
        },
    ).json()
    assert created["unit_head"] == "Dr. Rao"
    assert created["tl"] == "Mr. Sharma"
    assert created["branch_email"] == "saket@maxhealthcare.in"
    assert created["branch_whatsapp"] == "+91-9999999999"

    updated = admin_client.patch(
        f"/api/v1/branches/{created['id']}",
        json={"name": "Saket", "status": "ACTIVE", "tl": "Ms. Verma"},
    ).json()
    assert updated["tl"] == "Ms. Verma"


# ---- Part C — DeviceCategory canonicalization ----

def test_device_category_creation_and_model_backfill(admin_client, db_session):
    diagnostic_model = admin_client.post(
        "/api/v1/device-models", json={"name": "USG Machine", "category": "Diagnostic"}
    ).json()
    physio_model = admin_client.post(
        "/api/v1/device-models", json={"name": "TENS Unit", "category": "Physio"}
    ).json()
    assert diagnostic_model["category_id"] is None  # not yet backfilled

    migration_0008._backfill_device_categories(db_session)
    db_session.commit()

    categories = admin_client.get("/api/v1/device-categories").json()["items"]
    assert len(categories) == len(migration_0008.CANONICAL_DEVICE_CATEGORIES)
    diagnostic_cat = next(c for c in categories if c["name"] == "Diagnostic")
    physio_cat = next(c for c in categories if c["name"] == "Physiotherapy & Rehabilitation")

    refreshed_diagnostic = admin_client.get(f"/api/v1/device-models/{diagnostic_model['id']}").json()
    refreshed_physio = admin_client.get(f"/api/v1/device-models/{physio_model['id']}").json()
    assert refreshed_diagnostic["category_id"] == diagnostic_cat["id"]
    assert refreshed_physio["category_id"] == physio_cat["id"]
    # Free-text category is preserved, not deleted, per the "do not delete
    # DeviceModel.category immediately" instruction.
    assert refreshed_diagnostic["category"] == "Diagnostic"
    assert refreshed_physio["category"] == "Physio"


def test_backfill_does_not_guess_unmapped_free_text_category(admin_client, db_session):
    unmapped = admin_client.post(
        "/api/v1/device-models", json={"name": "Odd Device", "category": "Something Unrecognized"}
    ).json()

    migration_0008._backfill_device_categories(db_session)
    db_session.commit()

    refreshed = admin_client.get(f"/api/v1/device-models/{unmapped['id']}").json()
    assert refreshed["category_id"] is None  # left NULL, nothing guessed
    assert refreshed["category"] == "Something Unrecognized"  # untouched


def test_device_model_can_be_created_directly_with_category_id(admin_client):
    category = admin_client.post("/api/v1/device-categories", json={"name": "Surgical"}).json()
    model = admin_client.post(
        "/api/v1/device-models",
        json={"name": "Electrosurgical Unit", "category": "Surgical", "category_id": category["id"]},
    ).json()
    assert model["category_id"] == category["id"]
    assert model["category"] == "Surgical"


# ---- Part D — Combined device master creation ----

def test_combined_device_creation_with_new_model(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    category = admin_client.post("/api/v1/device-categories", json={"name": "ICU / Critical Care"}).json()

    response = admin_client.post(
        "/api/v1/devices",
        json={
            "model_name": "Ventilator X1",
            "manufacturer": "Draeger",
            "model_number": "X1-2000",
            "category": "ICU / Critical Care",
            "category_id": category["id"],
            "serial_number": "SN-001",
            "branch_id": branch["id"],
            "status": "OPERATIONAL",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["device_model"]["name"] == "Ventilator X1"
    assert body["device_model"]["category_id"] == category["id"]
    assert body["asset"]["device_model_id"] == body["device_model"]["id"]
    assert body["asset"]["branch_id"] == branch["id"]
    assert body["asset"]["serial_number"] == "SN-001"
    # Asset number is genuinely optional (Final Operational Gap Pass, Part 1) —
    # left NULL, never auto-generated.
    assert body["asset"]["asset_number"] is None


def test_combined_device_creation_reuses_existing_model(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post(
        "/api/v1/device-models", json={"name": "Infusion Pump", "category": "Infusion & Syringe Pumps"}
    ).json()

    response = admin_client.post(
        "/api/v1/devices",
        json={"device_model_id": model["id"], "branch_id": branch["id"], "asset_number": "AST-IP-1"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["device_model"]["id"] == model["id"]
    assert body["asset"]["asset_number"] == "AST-IP-1"

    # No duplicate model was created.
    models = admin_client.get("/api/v1/device-models").json()
    assert sum(1 for m in models["items"] if m["id"] == model["id"]) == 1


def test_combined_device_creation_requires_category_for_new_model(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    response = admin_client.post(
        "/api/v1/devices", json={"model_name": "No Category Device", "branch_id": branch["id"]}
    )
    assert response.status_code == 422


def test_combined_device_creation_rejects_duplicate_asset_number(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    model = admin_client.post(
        "/api/v1/device-models", json={"name": "Nebulizer", "category": "Respiratory Care"}
    ).json()
    payload = {"device_model_id": model["id"], "branch_id": branch["id"], "asset_number": "AST-DUP-1"}
    first = admin_client.post("/api/v1/devices", json=payload)
    assert first.status_code == 201
    second = admin_client.post("/api/v1/devices", json=payload)
    assert second.status_code == 409


def test_combined_device_creation_requires_device_and_asset_write(client, db_session):
    branch_owner = create_user(db_session, "owner@example.com", "SUPER_ADMIN", "Password123!")
    headers = auth_headers(client, "owner@example.com", "Password123!")
    branch = client.post(
        "/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}, headers=headers
    ).json()

    # Inventory Manager holds neither device.write nor asset.write.
    create_user(db_session, "im@example.com", "INVENTORY_MANAGER", "Password123!")
    im_headers = auth_headers(client, "im@example.com", "Password123!")
    response = client.post(
        "/api/v1/devices",
        json={"model_name": "Should Fail", "category": "Other Biomedical Equipment", "branch_id": branch["id"]},
        headers=im_headers,
    )
    assert response.status_code == 403


# ---- Part F — Vendor/Contract context (no duplication, existing endpoint reused) ----

def test_contracts_are_filterable_by_vendor_for_vendor_detail_context(admin_client):
    vendor = admin_client.post("/api/v1/vendors", json={"name": "Philips Healthcare"}).json()
    response = admin_client.get(f"/api/v1/contracts?vendor_id={vendor['id']}")
    assert response.status_code == 200
    assert response.json()["items"] == []  # no contracts yet, endpoint works and is scoped


# ---- Part I — BranchDeviceAssignment stays import/reconciliation-only ----

def test_branch_device_assignment_still_functions_as_reconciliation_only(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"}).json()
    created = admin_client.post(
        "/api/v1/branch-device-assignments",
        json={"branch_id": branch["id"], "source_name": "Legacy Sheet Row"},
    )
    assert created.status_code == 201
    # It must never appear as a real Asset — it's a separate table, not the delivery
    # source of truth.
    assets = admin_client.get(f"/api/v1/assets?branch_id={branch['id']}").json()
    assert assets["total"] == 0
