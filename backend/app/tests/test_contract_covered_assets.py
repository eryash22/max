"""Contract covered-asset eligibility and scoping.

Covered assets are physical `Asset` records (never DeviceModel catalog rows). Before
this pass `contract_service._validate_assets` only checked that an asset id existed, so
a brand-new AMC could be written against a condemned or retired device, and a
branch-scoped user could attach another branch's asset by posting its id.

The rule is deliberately asymmetric: it gates what may be ADDED, never what is already
saved. A warranty signed years ago on a device that has since been retired is a
legitimate historical record and must stay readable.
"""
from app.services.contract_service import is_asset_contract_eligible

from .conftest import auth_headers, create_user

PASSWORD = "Password123!"


def _branch(admin_client, name):
    response = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"})
    assert response.status_code == 201, response.text
    return response.json()


def _model(admin_client, name="Contract Monitor"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


def _asset(admin_client, model_id, branch_id, asset_number=None, status=None):
    payload = {"device_model_id": model_id, "branch_id": branch_id}
    if asset_number is not None:
        payload["asset_number"] = asset_number
    if status is not None:
        payload["status"] = status
    response = admin_client.post("/api/v1/assets", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _vendor(admin_client, name="Contract Vendor"):
    return admin_client.post(
        "/api/v1/vendors", json={"name": name, "vendor_type": "SERVICE", "status": "ACTIVE"}
    ).json()


def _contract_payload(vendor_id, asset_ids, contract_type="AMC"):
    return {
        "contract_type": contract_type,
        "vendor_id": vendor_id,
        "asset_ids": asset_ids,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "status": "ACTIVE",
    }


# ---- The eligibility predicate itself ----

class _FakeAsset:
    def __init__(self, status):
        self.status = status


def test_condemned_and_retired_are_not_contract_eligible():
    assert not is_asset_contract_eligible(_FakeAsset("CONDEMNED"))
    assert not is_asset_contract_eligible(_FakeAsset("RETIRED"))
    # Case-insensitive, matching the canonical lifecycle vocabulary.
    assert not is_asset_contract_eligible(_FakeAsset("condemned"))


def test_in_service_statuses_are_contract_eligible():
    for status in ("OPERATIONAL", "UNDER_MAINTENANCE", "UNDER_REPAIR", "Pending", None):
        assert is_asset_contract_eligible(_FakeAsset(status)), status


# ---- Creation is blocked ----

def test_new_contract_cannot_cover_a_condemned_asset(admin_client):
    branch = _branch(admin_client, "Contract Branch A")
    model = _model(admin_client)
    condemned = _asset(admin_client, model["id"], branch["id"], "CON-001", status="CONDEMNED")
    vendor = _vendor(admin_client)

    response = admin_client.post("/api/v1/contracts", json=_contract_payload(vendor["id"], [condemned["id"]]))
    assert response.status_code == 422, response.text
    assert "CONDEMNED" in response.text


def test_new_contract_cannot_cover_a_retired_asset(admin_client):
    branch = _branch(admin_client, "Contract Branch B")
    model = _model(admin_client)
    retired = _asset(admin_client, model["id"], branch["id"], "RET-001", status="RETIRED")
    vendor = _vendor(admin_client)

    response = admin_client.post("/api/v1/contracts", json=_contract_payload(vendor["id"], [retired["id"]]))
    assert response.status_code == 422, response.text
    assert "RETIRED" in response.text


def test_new_contract_rejects_the_whole_request_if_any_asset_is_ineligible(admin_client):
    """A partially-valid list must not be silently trimmed — the caller asked for
    coverage they would not have got."""
    branch = _branch(admin_client, "Contract Branch C")
    model = _model(admin_client)
    good = _asset(admin_client, model["id"], branch["id"], "OK-001", status="OPERATIONAL")
    bad = _asset(admin_client, model["id"], branch["id"], "BAD-001", status="CONDEMNED")
    vendor = _vendor(admin_client)

    response = admin_client.post(
        "/api/v1/contracts", json=_contract_payload(vendor["id"], [good["id"], bad["id"]])
    )
    assert response.status_code == 422, response.text
    assert admin_client.get("/api/v1/contracts").json()["total"] == 0


def test_amc_can_cover_multiple_eligible_assets(admin_client):
    branch = _branch(admin_client, "Contract Branch D")
    model = _model(admin_client)
    a1 = _asset(admin_client, model["id"], branch["id"], "MULTI-001", status="OPERATIONAL")
    a2 = _asset(admin_client, model["id"], branch["id"], "MULTI-002", status="OPERATIONAL")
    a3 = _asset(admin_client, model["id"], branch["id"], "MULTI-003", status="UNDER_MAINTENANCE")
    vendor = _vendor(admin_client)

    response = admin_client.post(
        "/api/v1/contracts", json=_contract_payload(vendor["id"], [a1["id"], a2["id"], a3["id"]])
    )
    assert response.status_code == 201, response.text
    assert set(response.json()["asset_ids"]) == {a1["id"], a2["id"], a3["id"]}


def test_asset_with_no_asset_number_can_still_be_covered(admin_client):
    """asset_number is genuinely optional, so it must not be an eligibility criterion —
    the UI renders "Asset number not assigned" rather than excluding the device."""
    branch = _branch(admin_client, "Contract Branch E")
    model = _model(admin_client)
    unnumbered = _asset(admin_client, model["id"], branch["id"], status="OPERATIONAL")
    assert unnumbered["asset_number"] is None
    vendor = _vendor(admin_client)

    response = admin_client.post("/api/v1/contracts", json=_contract_payload(vendor["id"], [unnumbered["id"]]))
    assert response.status_code == 201, response.text


# ---- History is preserved ----

def test_existing_contract_still_lists_an_asset_condemned_after_signing(admin_client, db_session):
    """The whole point of gating only NEW links: coverage signed while the device was
    healthy must remain readable after it is condemned."""
    branch = _branch(admin_client, "Contract Branch F")
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], "HIST-001", status="OPERATIONAL")
    vendor = _vendor(admin_client)

    created = admin_client.post("/api/v1/contracts", json=_contract_payload(vendor["id"], [asset["id"]]))
    assert created.status_code == 201, created.text
    contract_id = created.json()["id"]

    # CONDEMNED is only reachable through an approved condemnation requirement (the API
    # refuses a direct PATCH), so the status is set at the model level here — this test
    # is about what the contract does once the asset is already condemned, not about how
    # it got there.
    from app.models.device import Asset

    row = db_session.get(Asset, asset["id"])
    row.status = "CONDEMNED"
    db_session.add(row)
    db_session.commit()

    fetched = admin_client.get(f"/api/v1/contracts/{contract_id}")
    assert fetched.status_code == 200, fetched.text
    assert asset["id"] in fetched.json()["asset_ids"], "historical coverage must not disappear"


# ---- Branch scope ----

def test_branch_role_cannot_cover_a_foreign_branch_asset(admin_client, client, db_session):
    """Server-side: posting another branch's asset id must be rejected even though the
    id is real, and without confirming that it exists."""
    mine = _branch(admin_client, "Contract Mine")
    theirs = _branch(admin_client, "Contract Theirs")
    model = _model(admin_client)
    foreign = _asset(admin_client, model["id"], theirs["id"], "FOREIGN-001", status="OPERATIONAL")
    vendor = _vendor(admin_client)

    from app.models.identity import Role

    role = db_session.query(Role).filter(Role.code == "UNIT_HEAD").first()
    codes = sorted({rp.permission.code for rp in role.permissions} | {"contract.read", "contract.write"})
    assert admin_client.patch(f"/api/v1/roles/{role.id}", json={"permission_codes": codes}).status_code == 200

    user = create_user(db_session, "contract.uh@example.com", "UNIT_HEAD", PASSWORD)
    user.hospital_scope_id = mine["id"]
    db_session.add(user)
    db_session.commit()
    headers = auth_headers(client, "contract.uh@example.com", PASSWORD)

    response = client.post(
        "/api/v1/contracts", json=_contract_payload(vendor["id"], [foreign["id"]]), headers=headers
    )
    assert response.status_code == 422, response.text
    assert "not found" in response.text.lower()


def test_super_admin_can_cover_assets_across_branches(admin_client):
    """AMC/CMC are organisation-level agreements — an unscoped admin keeps the ability
    to write one contract across several branches."""
    b1 = _branch(admin_client, "Contract Cross A")
    b2 = _branch(admin_client, "Contract Cross B")
    model = _model(admin_client)
    a1 = _asset(admin_client, model["id"], b1["id"], "CROSS-001", status="OPERATIONAL")
    a2 = _asset(admin_client, model["id"], b2["id"], "CROSS-002", status="OPERATIONAL")
    vendor = _vendor(admin_client)

    response = admin_client.post("/api/v1/contracts", json=_contract_payload(vendor["id"], [a1["id"], a2["id"]]))
    assert response.status_code == 201, response.text
    assert set(response.json()["asset_ids"]) == {a1["id"], a2["id"]}


def test_warranty_still_requires_exactly_one_asset(admin_client):
    """Pre-existing rule must survive this change."""
    branch = _branch(admin_client, "Contract Warranty")
    model = _model(admin_client)
    a1 = _asset(admin_client, model["id"], branch["id"], "WAR-001", status="OPERATIONAL")
    a2 = _asset(admin_client, model["id"], branch["id"], "WAR-002", status="OPERATIONAL")
    vendor = _vendor(admin_client)

    two = admin_client.post(
        "/api/v1/contracts", json=_contract_payload(vendor["id"], [a1["id"], a2["id"]], contract_type="WARRANTY")
    )
    assert two.status_code == 422

    one = admin_client.post(
        "/api/v1/contracts", json=_contract_payload(vendor["id"], [a1["id"]], contract_type="WARRANTY")
    )
    assert one.status_code == 201, one.text
