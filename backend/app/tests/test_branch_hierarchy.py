"""Final non-AI hardening pass — real branch user hierarchy (UNIT_HEAD/TL).

Covers: the two new roles carry the same permission ceiling as
HOSPITAL_UNIT_MANAGER (no inventory quantities, no approval/fulfillment
authority); Branch.unit_head_user_id and the branch_tls join table are real,
validated User relationships; and branch-scoped visibility on
requirements/incidents/assets — a UNIT_HEAD/TL sees their whole assigned
branch, not just their own submissions, and never another branch's data.
HOSPITAL_UNIT_MANAGER's pre-existing own-submissions-only scope is confirmed
unchanged (back-compat).
"""
from app.models.identity import Role

from .conftest import auth_headers, create_user


def _make_branch(admin_client, name):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _scoped_user(client, db_session, admin_client, *, email, role_code, branch_id):
    user = create_user(db_session, email, role_code, "Password123!")
    user.hospital_scope_id = branch_id
    db_session.add(user)
    db_session.commit()
    return auth_headers(client, email, "Password123!")


def test_unit_head_and_tl_roles_seeded_with_hospital_unit_manager_ceiling(db_session, client):
    unit_head = db_session.query(Role).filter(Role.code == "UNIT_HEAD").first()
    tl = db_session.query(Role).filter(Role.code == "TL").first()
    hum = db_session.query(Role).filter(Role.code == "HOSPITAL_UNIT_MANAGER").first()
    assert unit_head is not None and tl is not None and hum is not None

    hum_codes = {rp.permission.code for rp in hum.permissions}
    unit_head_codes = {rp.permission.code for rp in unit_head.permissions}
    tl_codes = {rp.permission.code for rp in tl.permissions}
    # Branch-scoping hardening pass: UNIT_HEAD/TL no longer carry the same ceiling as
    # HOSPITAL_UNIT_MANAGER exactly — device.read (full Device Catalog admin
    # visibility) is swapped for device.read_picker (list-only, category/model
    # picker for raising a requirement; see security/permissions.py and migration
    # 0022). Every other permission code stays identical.
    assert unit_head_codes == (hum_codes - {"device.read"}) | {"device.read_picker"}
    assert tl_codes == (hum_codes - {"device.read"}) | {"device.read_picker"}
    assert "device.read" not in unit_head_codes
    assert "device.read" not in tl_codes
    assert "inventory.read" not in hum_codes
    assert "requirement.approve" not in hum_codes
    assert "requirement.fulfill" not in hum_codes


def test_branch_unit_head_assignment_validates_real_user(admin_client, db_session):
    branch = _make_branch(admin_client, "Hierarchy Branch A")
    unit_head_user = create_user(db_session, "uh.a@example.com", "UNIT_HEAD", "Password123!")

    bad = admin_client.patch(f"/api/v1/branches/{branch['id']}", json={"name": branch["name"], "unit_head_user_id": "not-a-real-user"})
    assert bad.status_code == 404

    ok = admin_client.patch(f"/api/v1/branches/{branch['id']}", json={"name": branch["name"], "unit_head_user_id": unit_head_user.id})
    assert ok.status_code == 200
    assert ok.json()["unit_head_user_id"] == unit_head_user.id


def test_branch_tl_assignment_supports_multiple_real_users(admin_client, db_session):
    branch = _make_branch(admin_client, "Hierarchy Branch B")
    tl1 = create_user(db_session, "tl1.b@example.com", "TL", "Password123!")
    tl2 = create_user(db_session, "tl2.b@example.com", "TL", "Password123!")

    missing_user = admin_client.post(f"/api/v1/branches/{branch['id']}/tls", json={"user_id": "nope"})
    assert missing_user.status_code == 404

    r1 = admin_client.post(f"/api/v1/branches/{branch['id']}/tls", json={"user_id": tl1.id})
    r2 = admin_client.post(f"/api/v1/branches/{branch['id']}/tls", json={"user_id": tl2.id})
    assert r1.status_code == 201 and r2.status_code == 201

    listing = admin_client.get(f"/api/v1/branches/{branch['id']}/tls").json()
    assert {row["user_id"] for row in listing} == {tl1.id, tl2.id}

    delete = admin_client.delete(f"/api/v1/branches/{branch['id']}/tls/{r1.json()['id']}")
    assert delete.status_code == 204
    listing_after = admin_client.get(f"/api/v1/branches/{branch['id']}/tls").json()
    assert {row["user_id"] for row in listing_after} == {tl2.id}


def test_tl_branch_scoped_requirement_visibility(client, db_session, admin_client):
    branch_a = _make_branch(admin_client, "Req Branch A")
    branch_b = _make_branch(admin_client, "Req Branch B")

    other_user = create_user(db_session, "other.reqbranch@example.com", "TL", "Password123!")
    other_user.hospital_scope_id = branch_a["id"]
    db_session.add(other_user)
    db_session.commit()
    other_headers = auth_headers(client, "other.reqbranch@example.com", "Password123!")
    req_a = client.post(
        "/api/v1/requirements",
        json={"branch_id": branch_a["id"], "reason": "Branch A supplies", "priority": "MEDIUM",
              "items": [{"item_type": "OTHER", "description": "Gloves", "requested_quantity": 1}]},
        headers=other_headers,
    )
    assert req_a.status_code == 201, req_a.text

    req_b = admin_client.post(
        "/api/v1/requirements",
        json={"branch_id": branch_b["id"], "reason": "Branch B supplies", "priority": "MEDIUM",
              "items": [{"item_type": "OTHER", "description": "Gloves", "requested_quantity": 1}]},
    )
    assert req_b.status_code == 201, req_b.text

    tl_headers = _scoped_user(client, db_session, admin_client, email="tl.reqscope@example.com",
                               role_code="TL", branch_id=branch_a["id"])

    listing = client.get("/api/v1/requirements", headers=tl_headers).json()
    ids = {item["id"] for item in listing["items"]}
    assert req_a.json()["id"] in ids
    assert req_b.json()["id"] not in ids

    get_a = client.get(f"/api/v1/requirements/{req_a.json()['id']}", headers=tl_headers)
    assert get_a.status_code == 200
    get_b = client.get(f"/api/v1/requirements/{req_b.json()['id']}", headers=tl_headers)
    assert get_b.status_code == 404


def test_unit_head_branch_scoped_incident_visibility(client, db_session, admin_client):
    branch_a = _make_branch(admin_client, "Inc Branch A")
    branch_b = _make_branch(admin_client, "Inc Branch B")
    model = admin_client.post("/api/v1/device-models", json={"name": "Inc-Device", "category": "Ventilator"}).json()
    asset_a = admin_client.post("/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch_a["id"]}).json()
    asset_b = admin_client.post("/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch_b["id"]}).json()

    inc_a = admin_client.post(
        "/api/v1/incidents",
        json={"branch_id": branch_a["id"], "asset_id": asset_a["id"], "description": "A issue", "severity": "LOW"},
    )
    inc_b = admin_client.post(
        "/api/v1/incidents",
        json={"branch_id": branch_b["id"], "asset_id": asset_b["id"], "description": "B issue", "severity": "LOW"},
    )
    assert inc_a.status_code == 201 and inc_b.status_code == 201

    uh_headers = _scoped_user(client, db_session, admin_client, email="uh.incscope@example.com",
                               role_code="UNIT_HEAD", branch_id=branch_a["id"])

    listing = client.get("/api/v1/incidents", headers=uh_headers).json()
    ids = {item["id"] for item in listing["items"]}
    assert inc_a.json()["id"] in ids
    assert inc_b.json()["id"] not in ids

    assert client.get(f"/api/v1/incidents/{inc_a.json()['id']}", headers=uh_headers).status_code == 200
    assert client.get(f"/api/v1/incidents/{inc_b.json()['id']}", headers=uh_headers).status_code == 404


def test_tl_branch_scoped_asset_visibility(client, db_session, admin_client):
    branch_a = _make_branch(admin_client, "Asset Branch A")
    branch_b = _make_branch(admin_client, "Asset Branch B")
    model = admin_client.post("/api/v1/device-models", json={"name": "Asset-Device", "category": "Monitor"}).json()
    asset_a = admin_client.post("/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch_a["id"]}).json()
    asset_b = admin_client.post("/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch_b["id"]}).json()

    tl_headers = _scoped_user(client, db_session, admin_client, email="tl.assetscope@example.com",
                               role_code="TL", branch_id=branch_a["id"])

    listing = client.get("/api/v1/assets", headers=tl_headers).json()
    ids = {item["id"] for item in listing["items"]}
    assert asset_a["id"] in ids
    assert asset_b["id"] not in ids

    assert client.get(f"/api/v1/assets/{asset_a['id']}", headers=tl_headers).status_code == 200
    assert client.get(f"/api/v1/assets/{asset_b['id']}", headers=tl_headers).status_code == 404


def test_tl_branch_scoped_asset_provenance_endpoints(client, db_session, admin_client):
    """Security scope fix: GET /assets/{id}/lifecycle, /provenance, and
    /procurement-provenance must enforce the same branch_scope_id() check as
    GET /assets/{id} — a TL blocked from another branch's asset record must not be
    able to reach that asset's lifecycle/provenance data directly by ID either."""
    branch_a = _make_branch(admin_client, "Provenance Branch A")
    branch_b = _make_branch(admin_client, "Provenance Branch B")
    model = admin_client.post("/api/v1/device-models", json={"name": "Provenance-Device", "category": "Monitor"}).json()
    asset_a = admin_client.post("/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch_a["id"]}).json()
    asset_b = admin_client.post("/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch_b["id"]}).json()

    tl_headers = _scoped_user(client, db_session, admin_client, email="tl.provenancescope@example.com",
                               role_code="TL", branch_id=branch_a["id"])

    # Security hardening pass: "history" and "maintenance-history" were the two
    # sibling by-ID endpoints that had NOT been fixed alongside lifecycle/provenance/
    # procurement-provenance above (missing _enforce_asset_branch_scope call).
    for suffix in ("lifecycle", "provenance", "procurement-provenance", "history", "maintenance-history"):
        own_branch = client.get(f"/api/v1/assets/{asset_a['id']}/{suffix}", headers=tl_headers)
        assert own_branch.status_code == 200, f"{suffix}: {own_branch.text}"
        other_branch = client.get(f"/api/v1/assets/{asset_b['id']}/{suffix}", headers=tl_headers)
        assert other_branch.status_code == 404, f"{suffix}: {other_branch.text}"
        # Admin (broad visibility) is unaffected.
        admin_access = admin_client.get(f"/api/v1/assets/{asset_b['id']}/{suffix}")
        assert admin_access.status_code == 200, f"{suffix}: {admin_access.text}"


def test_hospital_unit_manager_own_scope_unchanged(client, db_session, admin_client):
    """Back-compat: HOSPITAL_UNIT_MANAGER keeps its pre-existing own-submissions-only
    scope, unaffected by the new branch_scope_id mechanism (it never has a
    UNIT_HEAD/TL role, so branch_scope_id returns None for it regardless of
    hospital_scope_id)."""
    branch = _make_branch(admin_client, "HUM Branch")
    hum = create_user(db_session, "hum.unchanged@example.com", "HOSPITAL_UNIT_MANAGER", "Password123!")
    hum.hospital_scope_id = branch["id"]
    db_session.add(hum)
    db_session.commit()
    hum_headers = auth_headers(client, "hum.unchanged@example.com", "Password123!")

    other_req = admin_client.post(
        "/api/v1/requirements",
        json={"branch_id": branch["id"], "reason": "Same branch, different requester", "priority": "MEDIUM",
              "items": [{"item_type": "OTHER", "description": "Gloves", "requested_quantity": 1}]},
    )
    assert other_req.status_code == 201

    listing = client.get("/api/v1/requirements", headers=hum_headers).json()
    assert other_req.json()["id"] not in {item["id"] for item in listing["items"]}


def test_unit_head_cannot_browse_other_branches(client, db_session, admin_client):
    """Phase 2/11 — a UNIT_HEAD/TL must not be able to list or fetch any branch other
    than their own, even though they keep hospital.read (needed to resolve their own
    branch's name/details elsewhere in the app)."""
    branch_a = _make_branch(admin_client, "Scope Branch A")
    branch_b = _make_branch(admin_client, "Scope Branch B")

    uh_headers = _scoped_user(client, db_session, admin_client, email="uh.branchscope@example.com",
                               role_code="UNIT_HEAD", branch_id=branch_a["id"])

    listing = client.get("/api/v1/branches", headers=uh_headers).json()
    ids = {item["id"] for item in listing["items"]}
    assert ids == {branch_a["id"]}

    own = client.get(f"/api/v1/branches/{branch_a['id']}", headers=uh_headers)
    assert own.status_code == 200
    other = client.get(f"/api/v1/branches/{branch_b['id']}", headers=uh_headers)
    assert other.status_code == 404

    # Admin (broad visibility) is unaffected.
    admin_listing = admin_client.get("/api/v1/branches").json()
    admin_ids = {item["id"] for item in admin_listing["items"]}
    assert {branch_a["id"], branch_b["id"]} <= admin_ids


def test_unit_head_cannot_raise_requirement_for_other_branch(client, db_session, admin_client):
    """Phase 5 root-cause fix — a client-supplied branch_id that doesn't match the
    UNIT_HEAD/TL's own hospital_scope_id must be rejected at create time (403), not
    silently accepted and then 404 on the very next GET."""
    branch_a = _make_branch(admin_client, "Req Reject Branch A")
    branch_b = _make_branch(admin_client, "Req Reject Branch B")

    uh_headers = _scoped_user(client, db_session, admin_client, email="uh.reqreject@example.com",
                               role_code="UNIT_HEAD", branch_id=branch_a["id"])

    rejected = client.post(
        "/api/v1/requirements",
        json={"branch_id": branch_b["id"], "reason": "Should be rejected", "priority": "MEDIUM",
              "items": [{"item_type": "OTHER", "description": "Gloves", "requested_quantity": 1}]},
        headers=uh_headers,
    )
    assert rejected.status_code == 403

    accepted = client.post(
        "/api/v1/requirements",
        json={"branch_id": branch_a["id"], "reason": "Own branch, should work", "priority": "MEDIUM",
              "items": [{"item_type": "OTHER", "description": "Gloves", "requested_quantity": 1}]},
        headers=uh_headers,
    )
    assert accepted.status_code == 201, accepted.text
    # Create-then-immediately-retrieve must work — this is the exact "Requirement not
    # found" bug this fix closes.
    fetch = client.get(f"/api/v1/requirements/{accepted.json()['id']}", headers=uh_headers)
    assert fetch.status_code == 200


def test_tl_cannot_report_incident_for_other_branch_or_foreign_asset(client, db_session, admin_client):
    """Phase 8 root-cause fix — same branch_id rejection as requirements, plus a new
    cross-check: an incident's asset_id must belong to the incident's own branch."""
    branch_a = _make_branch(admin_client, "Inc Reject Branch A")
    branch_b = _make_branch(admin_client, "Inc Reject Branch B")
    model = admin_client.post("/api/v1/device-models", json={"name": "Inc-Reject-Device", "category": "Ventilator"}).json()
    asset_a = admin_client.post("/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch_a["id"]}).json()
    asset_b = admin_client.post("/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch_b["id"]}).json()

    tl_headers = _scoped_user(client, db_session, admin_client, email="tl.increject@example.com",
                               role_code="TL", branch_id=branch_a["id"])

    wrong_branch = client.post(
        "/api/v1/incidents",
        json={"branch_id": branch_b["id"], "description": "Should be rejected", "severity": "LOW"},
        headers=tl_headers,
    )
    assert wrong_branch.status_code == 403

    mismatched_asset = admin_client.post(
        "/api/v1/incidents",
        json={"branch_id": branch_a["id"], "asset_id": asset_b["id"], "description": "Mismatch", "severity": "LOW"},
    )
    assert mismatched_asset.status_code == 422

    accepted = client.post(
        "/api/v1/incidents",
        json={"branch_id": branch_a["id"], "asset_id": asset_a["id"], "description": "Own branch asset", "severity": "LOW"},
        headers=tl_headers,
    )
    assert accepted.status_code == 201, accepted.text
    fetch = client.get(f"/api/v1/incidents/{accepted.json()['id']}", headers=tl_headers)
    assert fetch.status_code == 200


def test_condemnation_item_must_target_asset_in_requirement_branch(admin_client, db_session):
    """Phase 7 — a CONDEMNATION item's asset must belong to the requirement's own
    branch; applies to every role, not just UNIT_HEAD/TL, so branch_id/asset_id can
    never silently diverge on the same requirement."""
    branch_a = _make_branch(admin_client, "Condemn Branch A")
    branch_b = _make_branch(admin_client, "Condemn Branch B")
    model = admin_client.post("/api/v1/device-models", json={"name": "Condemn-Device", "category": "Monitor"}).json()
    asset_a = admin_client.post("/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch_a["id"]}).json()
    asset_b = admin_client.post("/api/v1/assets", json={"device_model_id": model["id"], "branch_id": branch_b["id"]}).json()

    mismatched = admin_client.post(
        "/api/v1/requirements",
        json={"branch_id": branch_a["id"], "reason": "Condemn wrong-branch asset", "priority": "MEDIUM",
              "items": [{"item_type": "CONDEMNATION", "asset_id": asset_b["id"], "requested_quantity": 1}]},
    )
    assert mismatched.status_code == 404

    matched = admin_client.post(
        "/api/v1/requirements",
        json={"branch_id": branch_a["id"], "reason": "Condemn same-branch asset", "priority": "MEDIUM",
              "items": [{"item_type": "CONDEMNATION", "asset_id": asset_a["id"], "requested_quantity": 1}]},
    )
    assert matched.status_code == 201, matched.text


def test_unit_head_device_catalog_access_blocked(client, db_session, admin_client):
    """Phase 4 — UNIT_HEAD/TL must not reach the Device Catalog: catalog admin
    endpoints (detail/create/update/delete) reject them, while the stripped-down
    list/category picker (device.read_picker) stays available for requirement
    creation."""
    model = admin_client.post("/api/v1/device-models", json={
        "name": "Catalog-Blocked-Device", "category": "Monitor", "notes": "internal note",
        "data_source": "import batch 7",
    }).json()

    uh_headers = _scoped_user(client, db_session, admin_client, email="uh.catalogblocked@example.com",
                               role_code="UNIT_HEAD", branch_id=None)

    detail = client.get(f"/api/v1/device-models/{model['id']}", headers=uh_headers)
    assert detail.status_code == 403

    create = client.post("/api/v1/device-models", json={"name": "Nope", "category": "Monitor"}, headers=uh_headers)
    assert create.status_code == 403

    listing = client.get("/api/v1/device-models", headers=uh_headers)
    assert listing.status_code == 200
    row = next(r for r in listing.json()["items"] if r["id"] == model["id"])
    # Catalog-admin-only fields stripped for a device.read_picker-only caller.
    assert row["notes"] is None
    assert row["data_source"] is None

    admin_listing = admin_client.get("/api/v1/device-models").json()
    admin_row = next(r for r in admin_listing["items"] if r["id"] == model["id"])
    assert admin_row["notes"] == "internal note"


def test_manager_and_admin_retain_full_visibility_across_branches(client, db_session, admin_client):
    branch_a = _make_branch(admin_client, "Full Vis Branch A")
    branch_b = _make_branch(admin_client, "Full Vis Branch B")
    req_a = admin_client.post(
        "/api/v1/requirements",
        json={"branch_id": branch_a["id"], "reason": "A", "priority": "LOW",
              "items": [{"item_type": "OTHER", "description": "Gloves", "requested_quantity": 1}]},
    ).json()
    req_b = admin_client.post(
        "/api/v1/requirements",
        json={"branch_id": branch_b["id"], "reason": "B", "priority": "LOW",
              "items": [{"item_type": "OTHER", "description": "Gloves", "requested_quantity": 1}]},
    ).json()

    manager = create_user(db_session, "manager.fullvis@example.com", "MANAGER", "Password123!")
    manager_headers = auth_headers(client, "manager.fullvis@example.com", "Password123!")

    listing = client.get("/api/v1/requirements", headers=manager_headers).json()
    ids = {item["id"] for item in listing["items"]}
    assert req_a["id"] in ids and req_b["id"] in ids


def test_unit_head_floors_scoped_to_own_branch(client, db_session, admin_client):
    branch_a = _make_branch(admin_client, "Floor Scope Branch A")
    branch_b = _make_branch(admin_client, "Floor Scope Branch B")
    building_a = admin_client.post("/api/v1/buildings", json={"branch_id": branch_a["id"], "name": "Building A"}).json()
    building_b = admin_client.post("/api/v1/buildings", json={"branch_id": branch_b["id"], "name": "Building B"}).json()
    floor_a = admin_client.post("/api/v1/floors", json={"building_id": building_a["id"], "name": "Floor A1"}).json()
    admin_client.post("/api/v1/floors", json={"building_id": building_b["id"], "name": "Floor B1"})

    headers = _scoped_user(client, db_session, admin_client, email="uh.floor@example.com", role_code="UNIT_HEAD", branch_id=branch_a["id"])
    listing = client.get("/api/v1/floors", headers=headers).json()
    ids = {item["id"] for item in listing["items"]}
    assert ids == {floor_a["id"]}


def test_tl_locations_scoped_to_own_branch(client, db_session, admin_client):
    branch_a = _make_branch(admin_client, "Location Scope Branch A")
    branch_b = _make_branch(admin_client, "Location Scope Branch B")
    master_a = admin_client.post("/api/v1/department-masters", json={"name": "Dept A"}).json()
    master_b = admin_client.post("/api/v1/department-masters", json={"name": "Dept B"}).json()
    dept_a = admin_client.post(
        "/api/v1/departments", json={"branch_id": branch_a["id"], "department_master_id": master_a["id"]}
    ).json()
    dept_b = admin_client.post(
        "/api/v1/departments", json={"branch_id": branch_b["id"], "department_master_id": master_b["id"]}
    ).json()
    location_a = admin_client.post("/api/v1/locations", json={"department_id": dept_a["id"], "name": "Loc A1"}).json()
    admin_client.post("/api/v1/locations", json={"department_id": dept_b["id"], "name": "Loc B1"})

    headers = _scoped_user(client, db_session, admin_client, email="tl.location@example.com", role_code="TL", branch_id=branch_a["id"])
    listing = client.get("/api/v1/locations", headers=headers).json()
    ids = {item["id"] for item in listing["items"]}
    assert ids == {location_a["id"]}
