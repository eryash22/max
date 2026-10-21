from datetime import datetime, timedelta, timezone

from app.models.audit import AuditLog

from .conftest import auth_headers, create_user

# contract_service._today() computes expiry status from UTC, not local time, so
# TODAY must be pinned to UTC to avoid a local/UTC day-boundary mismatch.
TODAY = datetime.now(timezone.utc).date()


def _iso(delta_days: int) -> str:
    return (TODAY + timedelta(days=delta_days)).isoformat()


def _engineer_headers(client, db_session, email="contracteng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _doctor_headers(client, db_session, email="contractdoc@example.com"):
    create_user(db_session, email, "DOCTOR", "Password123!")
    return auth_headers(client, email, "Password123!")


def _make_vendor(admin_client, name="Contract Vendor"):
    return admin_client.post("/api/v1/vendors", json={"name": name}).json()


def _make_device_model(admin_client, name="Ventilator-Model"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "Respiratory"}).json()


def _make_asset(admin_client, model_id=None, asset_number="AST-CT-1"):
    branch = admin_client.post("/api/v1/branches", json={"name": f"Contract Test Branch {asset_number}"}).json()
    if model_id is None:
        model_id = _make_device_model(admin_client, name=f"Model-{asset_number}")["id"]
    return admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": model_id, "branch_id": branch["id"]},
    ).json()


# ---- Contract creation ----

def test_create_amc_contract(admin_client):
    vendor = _make_vendor(admin_client)
    asset = _make_asset(admin_client)
    response = admin_client.post(
        "/api/v1/contracts",
        json={
            "contract_type": "AMC",
            "vendor_id": vendor["id"],
            "asset_ids": [asset["id"]],
            "contract_number": "AMC-001",
            "start_date": _iso(-10),
            "end_date": _iso(300),
            "pm_frequency": "QUARTERLY",
            "contract_value": 50000,
            "status": "ACTIVE",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["contract_type"] == "AMC"
    assert body["asset_ids"] == [asset["id"]]
    assert body["vendor_id"] == vendor["id"]
    assert body["contract_number"] == "AMC-001"
    assert body["status"] == "ACTIVE"


def test_create_cmc_contract(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-CMC-1")
    response = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "CMC", "asset_ids": [asset["id"]], "contract_number": "CMC-001"},
    )
    assert response.status_code == 201
    assert response.json()["contract_type"] == "CMC"


def test_create_warranty_contract(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-WAR-1")
    response = admin_client.post(
        "/api/v1/contracts",
        json={
            "contract_type": "WARRANTY",
            "asset_ids": [asset["id"]],
            "contract_number": "WAR-001",
            "end_date": _iso(400),
            "coverage_notes": "Parts and labor",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["contract_type"] == "WARRANTY"
    assert body["contract_number"] == "WAR-001"
    assert body["coverage_notes"] == "Parts and labor"


def test_warranty_requires_exactly_one_asset(admin_client):
    asset1 = _make_asset(admin_client, asset_number="AST-WAR-2A")
    asset2 = _make_asset(admin_client, model_id=asset1["device_model_id"], asset_number="AST-WAR-2B")
    response = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "WARRANTY", "asset_ids": [asset1["id"], asset2["id"]]},
    )
    assert response.status_code == 422


def test_create_contract_invalid_type_rejected(admin_client):
    response = admin_client.post("/api/v1/contracts", json={"contract_type": "SERVICE_PLAN"})
    assert response.status_code == 422


def test_create_contract_invalid_vendor_rejected(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-BADV")
    response = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "vendor_id": "does-not-exist", "asset_ids": [asset["id"]]},
    )
    assert response.status_code == 422


def test_create_contract_invalid_asset_rejected(admin_client):
    response = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "AMC", "asset_ids": ["does-not-exist"]}
    )
    assert response.status_code == 422


# ---- Retrieval ----

def test_get_contract_by_id(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-GET")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "AMC", "asset_ids": [asset["id"]]}
    ).json()
    fetched = admin_client.get(f"/api/v1/contracts/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]


def test_get_contract_not_found(admin_client):
    response = admin_client.get("/api/v1/contracts/does-not-exist")
    assert response.status_code == 404


# ---- Update ----

def test_update_amc_contract(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-UPD")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "AMC", "asset_ids": [asset["id"]], "contract_value": 1000}
    ).json()
    updated = admin_client.patch(f"/api/v1/contracts/{created['id']}", json={"contract_value": 2000, "status": "ACTIVE"})
    assert updated.status_code == 200
    body = updated.json()
    assert body["contract_value"] == 2000
    assert body["status"] == "ACTIVE"


def test_update_contract_not_found(admin_client):
    response = admin_client.patch("/api/v1/contracts/does-not-exist", json={"status": "ACTIVE"})
    assert response.status_code == 404


def test_activate_deactivate_contract(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-TOGGLE")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "CMC", "asset_ids": [asset["id"]]}
    ).json()

    deactivated = admin_client.post(f"/api/v1/contracts/{created['id']}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "CANCELLED"

    activated = admin_client.post(f"/api/v1/contracts/{created['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"


# ---- Delete ----

def test_delete_unreferenced_warranty_succeeds(admin_client, db_session):
    asset = _make_asset(admin_client, asset_number="AST-DEL-WAR")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "WARRANTY", "asset_ids": [asset["id"]]}
    ).json()

    response = admin_client.delete(f"/api/v1/contracts/{created['id']}")
    assert response.status_code == 204

    get_response = admin_client.get(f"/api/v1/contracts/{created['id']}")
    assert get_response.status_code == 404

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "warranty", AuditLog.entity_id == created["id"], AuditLog.action == "DELETE"
    ).first()
    assert log is not None


def test_delete_active_warranty_blocked(admin_client):
    """Final non-AI cleanup pass — a Warranty has no inbound join-table row (unlike
    AMC/CMC), so it previously could always be hard-deleted even while it was the
    asset's current active coverage. An ACTIVE warranty must now be deactivated
    first, same as any other in-use contract."""
    asset = _make_asset(admin_client, asset_number="AST-DEL-WAR-ACTIVE")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "WARRANTY", "asset_ids": [asset["id"]], "status": "ACTIVE"}
    ).json()
    assert created["status"] == "ACTIVE"

    response = admin_client.delete(f"/api/v1/contracts/{created['id']}")
    assert response.status_code == 409
    assert "cannot be deleted" in response.json()["detail"]

    still_there = admin_client.get(f"/api/v1/contracts/{created['id']}")
    assert still_there.status_code == 200

    # Deactivating (the archive path) is always allowed, then delete succeeds.
    deactivated = admin_client.post(f"/api/v1/contracts/{created['id']}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "CANCELLED"

    deleted = admin_client.delete(f"/api/v1/contracts/{created['id']}")
    assert deleted.status_code == 204


def test_delete_unreferenced_amc_contract_succeeds(admin_client, db_session):
    # No asset_ids — AMC is the only contract type that can be created with zero
    # linked assets (WARRANTY requires exactly one), so it has no AmcContractAsset
    # rows blocking the delete.
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "AMC", "contract_number": "AMC-NOLINK"}
    ).json()

    response = admin_client.delete(f"/api/v1/contracts/{created['id']}")
    assert response.status_code == 204

    get_response = admin_client.get(f"/api/v1/contracts/{created['id']}")
    assert get_response.status_code == 404

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "amc_contract", AuditLog.entity_id == created["id"], AuditLog.action == "DELETE"
    ).first()
    assert log is not None


def test_delete_amc_contract_with_asset_link_blocked(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-DEL-AMC2")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "AMC", "asset_ids": [asset["id"]]}
    ).json()

    response = admin_client.delete(f"/api/v1/contracts/{created['id']}")
    assert response.status_code == 409
    assert "1" in response.json()["detail"]

    still_there = admin_client.get(f"/api/v1/contracts/{created['id']}")
    assert still_there.status_code == 200


def test_delete_cmc_contract_with_asset_link_blocked(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-DEL-CMC")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "CMC", "asset_ids": [asset["id"]]}
    ).json()

    response = admin_client.delete(f"/api/v1/contracts/{created['id']}")
    assert response.status_code == 409


def test_delete_contract_not_found(admin_client):
    response = admin_client.delete("/api/v1/contracts/does-not-exist")
    assert response.status_code == 404


def test_delete_contract_requires_contract_write(admin_client, client, db_session):
    asset = _make_asset(admin_client, asset_number="AST-DEL-RBAC")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "WARRANTY", "asset_ids": [asset["id"]]}
    ).json()
    headers = _doctor_headers(client, db_session)
    response = client.delete(f"/api/v1/contracts/{created['id']}", headers=headers)
    assert response.status_code == 403


# ---- List / search / pagination ----

def test_list_contracts_pagination(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-PAGE")
    for i in range(3):
        admin_client.post(
            "/api/v1/contracts",
            json={"contract_type": "AMC", "asset_ids": [asset["id"]], "contract_number": f"PG-{i}"},
        )
    page1 = admin_client.get("/api/v1/contracts?page=1&page_size=2")
    assert page1.status_code == 200
    body = page1.json()
    assert body["total"] >= 3
    assert len(body["items"]) == 2


def test_list_contracts_search_by_number(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-SEARCH")
    admin_client.post(
        "/api/v1/contracts", json={"contract_type": "AMC", "asset_ids": [asset["id"]], "contract_number": "UNIQUE-777"}
    )
    response = admin_client.get("/api/v1/contracts?search=UNIQUE-777")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["contract_number"] == "UNIQUE-777"


# ---- AMC/CMC/Warranty type handling ----

def test_list_contracts_filter_by_type(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-TYPE")
    admin_client.post("/api/v1/contracts", json={"contract_type": "AMC", "asset_ids": [asset["id"]]})
    admin_client.post("/api/v1/contracts", json={"contract_type": "CMC", "asset_ids": [asset["id"]]})
    admin_client.post("/api/v1/contracts", json={"contract_type": "WARRANTY", "asset_ids": [asset["id"]]})

    response = admin_client.get("/api/v1/contracts?contract_type=CMC")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["contract_type"] == "CMC"


def test_warranty_has_no_pm_frequency_field(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-WARFIELD")
    created = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "WARRANTY", "asset_ids": [asset["id"]], "pm_frequency": "MONTHLY"},
    ).json()
    assert created["pm_frequency"] is None


# ---- Vendor linkage ----

def test_contract_vendor_linkage(admin_client):
    vendor = _make_vendor(admin_client, name="Linked Vendor")
    asset = _make_asset(admin_client, asset_number="AST-VENDLINK")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "AMC", "vendor_id": vendor["id"], "asset_ids": [asset["id"]]}
    ).json()
    response = admin_client.get(f"/api/v1/contracts?vendor_id={vendor['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == created["id"]


# ---- Asset linkage ----

def test_contract_asset_linkage(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-ASSETLINK")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "CMC", "asset_ids": [asset["id"]]}
    ).json()
    response = admin_client.get(f"/api/v1/contracts?asset_id={asset['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == created["id"]


def test_contract_service_visits_linkage(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-SVLINK")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "AMC", "asset_ids": [asset["id"]]}
    ).json()
    response = admin_client.get(f"/api/v1/contracts/{created['id']}/service-visits")
    assert response.status_code == 200
    assert response.json()["total"] == 0


# ---- Expiry / status logic ----

def test_expiry_status_expired(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-EXPIRED")
    created = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "asset_ids": [asset["id"]], "end_date": _iso(-5)},
    ).json()
    assert created["expiry_status"] == "EXPIRED"


def test_expiry_status_expiring_soon(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-SOON")
    created = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "asset_ids": [asset["id"]], "end_date": _iso(10)},
    ).json()
    assert created["expiry_status"] == "EXPIRING_SOON"


def test_expiry_status_upcoming(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-UPCOMING")
    created = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "asset_ids": [asset["id"]], "start_date": _iso(20), "end_date": _iso(400)},
    ).json()
    assert created["expiry_status"] == "UPCOMING"


def test_expiry_status_active(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-ACTIVE")
    created = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "asset_ids": [asset["id"]], "start_date": _iso(-10), "end_date": _iso(200)},
    ).json()
    assert created["expiry_status"] == "ACTIVE"


def test_expiry_status_not_set(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-NOTSET")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "AMC", "asset_ids": [asset["id"]]}
    ).json()
    assert created["expiry_status"] == "NOT_SET"


def test_list_contracts_filter_by_expiry_status(admin_client):
    expired_asset = _make_asset(admin_client, asset_number="AST-FILT-EXP")
    active_asset = _make_asset(admin_client, model_id=expired_asset["device_model_id"], asset_number="AST-FILT-ACT")
    admin_client.post("/api/v1/contracts", json={"contract_type": "AMC", "asset_ids": [expired_asset["id"]], "end_date": _iso(-1)})
    admin_client.post("/api/v1/contracts", json={"contract_type": "AMC", "asset_ids": [active_asset["id"]], "start_date": _iso(-1), "end_date": _iso(200)})

    response = admin_client.get("/api/v1/contracts?expiry_status=EXPIRED")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["expiry_status"] == "EXPIRED"


# ---- RBAC ----

def test_doctor_cannot_read_contracts(client, db_session):
    headers = _doctor_headers(client, db_session)
    response = client.get("/api/v1/contracts", headers=headers)
    assert response.status_code == 403


def test_doctor_cannot_create_contract(client, db_session):
    headers = _doctor_headers(client, db_session)
    response = client.post("/api/v1/contracts", json={"contract_type": "AMC"}, headers=headers)
    assert response.status_code == 403


def test_engineer_can_read_but_not_write_contracts(admin_client, client, db_session):
    asset = _make_asset(admin_client, asset_number="AST-ENGRBAC")
    admin_client.post("/api/v1/contracts", json={"contract_type": "AMC", "asset_ids": [asset["id"]]})
    headers = _engineer_headers(client, db_session)

    read_response = client.get("/api/v1/contracts", headers=headers)
    assert read_response.status_code == 200

    write_response = client.post("/api/v1/contracts", json={"contract_type": "AMC"}, headers=headers)
    assert write_response.status_code == 403


# ---- Audit logging ----

def test_contract_creation_is_audited(admin_client, db_session):
    asset = _make_asset(admin_client, asset_number="AST-AUDIT-C")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "AMC", "asset_ids": [asset["id"]]}
    ).json()
    log = db_session.query(AuditLog).filter(AuditLog.entity == "amc_contract", AuditLog.entity_id == created["id"]).first()
    assert log is not None
    assert log.action == "CREATE"


def test_contract_update_is_audited(admin_client, db_session):
    asset = _make_asset(admin_client, asset_number="AST-AUDIT-U")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "CMC", "asset_ids": [asset["id"]]}
    ).json()
    admin_client.patch(f"/api/v1/contracts/{created['id']}", json={"contract_value": 500})

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "cmc_contract", AuditLog.entity_id == created["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None
    assert log.new_value.get("contract_value") == 500


def test_contract_status_change_is_audited(admin_client, db_session):
    asset = _make_asset(admin_client, asset_number="AST-AUDIT-S")
    created = admin_client.post(
        "/api/v1/contracts", json={"contract_type": "WARRANTY", "asset_ids": [asset["id"]]}
    ).json()
    admin_client.post(f"/api/v1/contracts/{created['id']}/deactivate")

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "warranty", AuditLog.entity_id == created["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None
    assert log.new_value.get("status") == "CANCELLED"


# ---- Invalid data ----

def test_create_contract_invalid_status_rejected(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-BADSTATUS")
    response = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "asset_ids": [asset["id"]], "status": "NOT_A_STATUS"},
    )
    assert response.status_code == 422


def test_create_contract_negative_value_rejected(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-NEGVALUE")
    response = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "asset_ids": [asset["id"]], "contract_value": -100},
    )
    assert response.status_code == 422


def test_create_contract_invalid_email_rejected(admin_client):
    asset = _make_asset(admin_client, asset_number="AST-BADEMAIL")
    response = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "asset_ids": [asset["id"]], "contact_email": "not-an-email"},
    )
    assert response.status_code == 422
