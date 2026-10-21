"""Final admin/inventory/vendor/dashboard polish pass — Part 4: consumable last
transaction."""

from .conftest import auth_headers, create_user


def _make_branch(admin_client, name="Consumable-Branch"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_consumable(admin_client, branch_id=None, code="LT-CON-1"):
    payload = {"item_code": code, "name": "Syringe", "unit": "PCS", "opening_quantity": 100}
    if branch_id:
        payload["branch_id"] = branch_id
    return admin_client.post("/api/v1/consumables", json=payload).json()


def test_last_transaction_null_when_no_history(admin_client):
    consumable = _make_consumable(admin_client)
    response = admin_client.get(f"/api/v1/consumables/{consumable['id']}/last-transaction")
    assert response.status_code == 200
    assert response.json() is None


def test_last_transaction_not_found_for_missing_consumable(admin_client):
    response = admin_client.get("/api/v1/consumables/does-not-exist/last-transaction")
    assert response.status_code == 404


def test_last_transaction_returns_most_recent(admin_client, db_session):
    from datetime import datetime, timedelta

    from app.models.inventory import Consumable, ConsumableTransaction
    from app.services import inventory_service

    branch = _make_branch(admin_client)
    dest_branch = admin_client.post(
        "/api/v1/branches", json={"name": "Consumable-Dest-Branch", "status": "ACTIVE"}
    ).json()
    consumable = _make_consumable(admin_client, branch_id=branch["id"], code="LT-CON-2")

    consumable_row = db_session.get(Consumable, consumable["id"])
    inventory_service.transfer_consumable_stock(
        db_session, consumable=consumable_row, destination_branch_id=branch["id"],
        quantity=5, requirement_id=None, reference="first-out", user_id="test-user",
    )
    db_session.commit()
    inventory_service.transfer_consumable_stock(
        db_session, consumable=consumable_row, destination_branch_id=dest_branch["id"],
        quantity=3, requirement_id=None, reference="second-transfer", user_id="test-user",
    )
    db_session.commit()

    # Force a deterministic ordering regardless of same-second SQLite timestamp
    # granularity — the "first-out" row is backdated so "second-transfer" is
    # unambiguously the most recent.
    first_row = db_session.query(ConsumableTransaction).filter(
        ConsumableTransaction.reference == "first-out"
    ).first()
    first_row.created_at = datetime.utcnow() - timedelta(hours=1)
    db_session.commit()

    response = admin_client.get(f"/api/v1/consumables/{consumable['id']}/last-transaction")
    assert response.status_code == 200
    body = response.json()
    assert body is not None
    assert body["reference"] == "second-transfer"
    assert body["transaction_type"] == "TRANSFER"


def test_last_transaction_requires_inventory_read(client, db_session):
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.get("/api/v1/consumables/does-not-exist/last-transaction", headers=headers)
    assert response.status_code == 403
