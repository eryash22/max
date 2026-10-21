"""Business-workflow audit — Incident AMC/CMC/Warranty coverage visibility.

GET /incidents/{id} surfaces read-only coverage derived live from the existing
Module 05A contract tables (same functions the asset lifecycle summary uses) —
nothing is duplicated onto Incident, no new permission required.
"""
from datetime import date, timedelta

from .conftest import auth_headers, create_user

TODAY = date.today()


def _iso(days_offset: int) -> str:
    return (TODAY + timedelta(days=days_offset)).isoformat()


def _make_branch(admin_client, name="Coverage Test Branch"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_asset(admin_client, asset_number, branch_id=None, **extra):
    branch_id = branch_id or _make_branch(admin_client, f"Branch-{asset_number}")["id"]
    model = admin_client.post(
        "/api/v1/device-models", json={"name": f"Model-{asset_number}", "category": "Ventilator"}
    ).json()
    payload = {"asset_number": asset_number, "device_model_id": model["id"], "branch_id": branch_id, **extra}
    asset = admin_client.post("/api/v1/assets", json=payload).json()
    return branch_id, asset


def _make_vendor(admin_client, name="Coverage Vendor"):
    return admin_client.post("/api/v1/vendors", json={"name": name, "vendor_type": "SERVICE", "status": "ACTIVE"}).json()


def _make_contract(admin_client, contract_type, asset_id, vendor_id, start_date, end_date, **extra):
    response = admin_client.post(
        "/api/v1/contracts",
        json={
            "contract_type": contract_type, "vendor_id": vendor_id, "asset_ids": [asset_id],
            "start_date": start_date, "end_date": end_date, "status": "ACTIVE", **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_incident(admin_client, branch_id, asset_id=None, description="Device not powering on"):
    payload = {"branch_id": branch_id, "description": description}
    if asset_id:
        payload["asset_id"] = asset_id
    return admin_client.post("/api/v1/incidents", json=payload).json()


def _bme_headers(client, db_session, email="bme-cov@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


# ---- 1. Incident with AMC-covered asset ----

def test_incident_coverage_shows_active_amc(admin_client):
    branch_id, asset = _make_asset(admin_client, "AST-COV-AMC")
    vendor = _make_vendor(admin_client, "AMC Vendor")
    contract = _make_contract(admin_client, "AMC", asset["id"], vendor["id"], _iso(-30), _iso(300))
    incident = _make_incident(admin_client, branch_id, asset["id"])

    response = admin_client.get(f"/api/v1/incidents/{incident['id']}")
    assert response.status_code == 200
    coverage = response.json()["coverage"]
    assert coverage is not None
    assert coverage["asset_id"] == asset["id"]
    assert coverage["coverage_status"] == "AMC"
    assert coverage["vendor_id"] == vendor["id"]
    assert coverage["amc"]["contract_id"] == contract["id"]
    assert coverage["amc"]["expiry_status"] == "ACTIVE"


# ---- 2. Incident with CMC-covered asset ----

def test_incident_coverage_shows_active_cmc(admin_client):
    branch_id, asset = _make_asset(admin_client, "AST-COV-CMC")
    vendor = _make_vendor(admin_client, "CMC Vendor")
    contract = _make_contract(admin_client, "CMC", asset["id"], vendor["id"], _iso(-10), _iso(200))
    incident = _make_incident(admin_client, branch_id, asset["id"])

    response = admin_client.get(f"/api/v1/incidents/{incident['id']}")
    coverage = response.json()["coverage"]
    assert coverage["coverage_status"] == "CMC"
    assert coverage["cmc"]["contract_id"] == contract["id"]


# ---- 3. Incident with Warranty-covered asset ----

def test_incident_coverage_shows_active_warranty(admin_client):
    branch_id, asset = _make_asset(admin_client, "AST-COV-WARR")
    vendor = _make_vendor(admin_client, "Warranty Vendor")
    contract = _make_contract(admin_client, "WARRANTY", asset["id"], vendor["id"], _iso(-5), _iso(90))
    incident = _make_incident(admin_client, branch_id, asset["id"])

    response = admin_client.get(f"/api/v1/incidents/{incident['id']}")
    coverage = response.json()["coverage"]
    assert coverage["coverage_status"] == "WARRANTY"
    assert coverage["warranty"]["contract_id"] == contract["id"]


# ---- 4. Incident with no coverage ----

def test_incident_coverage_not_covered(admin_client):
    branch_id, asset = _make_asset(admin_client, "AST-COV-NONE")
    incident = _make_incident(admin_client, branch_id, asset["id"])

    response = admin_client.get(f"/api/v1/incidents/{incident['id']}")
    coverage = response.json()["coverage"]
    assert coverage is not None
    assert coverage["coverage_status"] == "NOT_COVERED"
    assert coverage["vendor_id"] is None
    assert coverage["amc"] is None
    assert coverage["cmc"] is None
    assert coverage["warranty"] is None


# ---- 5. Incident without asset_id ----

def test_incident_without_asset_has_no_coverage_block(admin_client):
    branch = _make_branch(admin_client, "No Asset Branch")
    incident = _make_incident(admin_client, branch["id"], asset_id=None)

    response = admin_client.get(f"/api/v1/incidents/{incident['id']}")
    assert response.status_code == 200
    assert response.json()["coverage"] is None


# ---- 6. Correct vendor/contract data (AMC preferred over expired CMC) ----

def test_incident_coverage_prefers_active_amc_over_expired_cmc(admin_client):
    branch_id, asset = _make_asset(admin_client, "AST-COV-MULTI")
    amc_vendor = _make_vendor(admin_client, "Active AMC Vendor")
    cmc_vendor = _make_vendor(admin_client, "Expired CMC Vendor")
    _make_contract(admin_client, "CMC", asset["id"], cmc_vendor["id"], _iso(-400), _iso(-30))
    _make_contract(admin_client, "AMC", asset["id"], amc_vendor["id"], _iso(-10), _iso(300))
    incident = _make_incident(admin_client, branch_id, asset["id"])

    response = admin_client.get(f"/api/v1/incidents/{incident['id']}")
    coverage = response.json()["coverage"]
    assert coverage["coverage_status"] == "AMC"
    assert coverage["vendor_id"] == amc_vendor["id"]
    assert coverage["cmc"] is not None  # still shown, just not the winning layer
    assert coverage["cmc"]["expiry_status"] == "EXPIRED"


# ---- 7. No contract data duplicated into Incident ----

def test_incident_model_has_no_contract_fields(admin_client):
    branch_id, asset = _make_asset(admin_client, "AST-COV-NODUP")
    vendor = _make_vendor(admin_client, "No Dup Vendor")
    _make_contract(admin_client, "AMC", asset["id"], vendor["id"], _iso(-1), _iso(300))
    incident = _make_incident(admin_client, branch_id, asset["id"])

    response = admin_client.get(f"/api/v1/incidents/{incident['id']}").json()
    top_level_keys = set(response.keys()) - {"coverage"}
    contract_like_fields = {"amc", "cmc", "warranty", "contract_id", "contract_number", "expiry_status"}
    assert not (top_level_keys & contract_like_fields), "contract data leaked onto Incident's own fields"

    # List endpoint doesn't compute coverage (avoids a query per row).
    listed = admin_client.get(f"/api/v1/incidents?branch_id={branch_id}").json()
    for item in listed["items"]:
        assert item.get("coverage") is None
