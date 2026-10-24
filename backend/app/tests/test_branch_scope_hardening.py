"""F11 — branch isolation must not depend on a role lacking a permission.

Role permissions are DB-driven: PATCH /api/v1/roles/{id} accepts any valid permission
code for any role, so a Super Admin can grant pm.read (or calibration.read,
contract.read, procurement.read, inventory.read) to TL/UNIT_HEAD at any time from the
Role & Permissions screen. Before this pass, seven routers had no branch scoping at
all and were safe only because branch roles happened to hold none of those
permissions — a grant, not a boundary.

Every test here therefore GRANTS the permission first, then proves the branch-scoped
role still cannot read another branch's records, through both the list and the
direct-ID route.

Deliberately NOT tested here, because these resources are genuinely global:
  * InventoryItem — a spare-part catalog row with no branch column at all.
  * Audit logs — a polymorphic compliance trail (entity/entity_id strings, no branch
    relationship); organisation-wide visibility is the point of an audit log.
"""
from app.models.identity import Role

from .conftest import auth_headers, create_user

PASSWORD = "Password123!"


# ---- helpers ----

def _grant(admin_client, db_session, role_code: str, *permission_codes: str) -> None:
    """Grant extra permissions to a role exactly the way the Role & Permissions screen
    does — a full replacement of the role's permission set."""
    role = db_session.query(Role).filter(Role.code == role_code).first()
    assert role is not None, f"role {role_code} must exist"
    codes = sorted({rp.permission.code for rp in role.permissions} | set(permission_codes))
    response = admin_client.patch(f"/api/v1/roles/{role.id}", json={"permission_codes": codes})
    assert response.status_code == 200, response.text


def _scoped_headers(client, db_session, *, email, role_code, branch_id):
    user = create_user(db_session, email, role_code, PASSWORD)
    user.hospital_scope_id = branch_id
    db_session.add(user)
    db_session.commit()
    return auth_headers(client, email, PASSWORD)


def _branch(admin_client, name):
    response = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"})
    assert response.status_code == 201, response.text
    return response.json()


def _device_model(admin_client, name="Scope Monitor"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


def _asset(admin_client, device_model_id, branch_id, asset_number):
    response = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": device_model_id, "branch_id": branch_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _requirement_payload(branch_id, device_model_id, reason):
    return {
        "branch_id": branch_id,
        "reason": reason,
        "priority": "MEDIUM",
        "items": [{
            "item_type": "DEVICE_MODEL",
            "device_model_id": device_model_id,
            "requested_quantity": 1,
            "unit": "UNIT",
        }],
    }


def _two_branch_assets(admin_client):
    """Returns (mine, theirs, my_asset, their_asset)."""
    mine = _branch(admin_client, "Scope Mine")
    theirs = _branch(admin_client, "Scope Theirs")
    model = _device_model(admin_client)
    my_asset = _asset(admin_client, model["id"], mine["id"], "SCOPE-MINE-1")
    their_asset = _asset(admin_client, model["id"], theirs["id"], "SCOPE-THEIRS-1")
    return mine, theirs, my_asset, their_asset


# ---- PM ----

def test_tl_with_granted_pm_read_cannot_see_foreign_branch_schedules(admin_client, client, db_session):
    mine, theirs, my_asset, their_asset = _two_branch_assets(admin_client)

    my_schedule = admin_client.post(
        "/api/v1/pm/schedules", json={"asset_id": my_asset["id"], "frequency": "QUARTERLY"}
    ).json()
    their_schedule = admin_client.post(
        "/api/v1/pm/schedules", json={"asset_id": their_asset["id"], "frequency": "QUARTERLY"}
    ).json()

    _grant(admin_client, db_session, "TL", "pm.read")
    headers = _scoped_headers(client, db_session, email="scope.tl.pm@example.com",
                               role_code="TL", branch_id=mine["id"])

    listing = client.get("/api/v1/pm/schedules", headers=headers)
    assert listing.status_code == 200, listing.text
    ids = {row["id"] for row in listing.json()["items"]}
    assert my_schedule["id"] in ids
    assert their_schedule["id"] not in ids, "foreign branch PM schedule leaked into the list"

    # A forged branch_id must not widen the scope. The enforced scope overrides the
    # requested one (same convention as service_visits.py), so the caller keeps seeing
    # their own branch and never the foreign one.
    forged = client.get(f"/api/v1/pm/schedules?branch_id={theirs['id']}", headers=headers)
    assert forged.status_code == 200
    forged_ids = {row["id"] for row in forged.json()["items"]}
    assert their_schedule["id"] not in forged_ids, "forged branch_id leaked a foreign schedule"
    assert forged_ids == {my_schedule["id"]}

    # Direct-ID access to the foreign schedule must 404.
    direct = client.get(f"/api/v1/pm/schedules/{their_schedule['id']}", headers=headers)
    assert direct.status_code == 404, direct.text
    assert client.get(f"/api/v1/pm/schedules/{my_schedule['id']}", headers=headers).status_code == 200


def test_unit_head_with_granted_pm_read_cannot_see_foreign_branch_records(admin_client, client, db_session):
    mine, theirs, my_asset, their_asset = _two_branch_assets(admin_client)

    their_record = admin_client.post(
        "/api/v1/pm/records",
        json={"asset_id": their_asset["id"], "performed_date": "2026-01-05", "work_done": "theirs"},
    ).json()
    my_record = admin_client.post(
        "/api/v1/pm/records",
        json={"asset_id": my_asset["id"], "performed_date": "2026-01-06", "work_done": "mine"},
    ).json()

    _grant(admin_client, db_session, "UNIT_HEAD", "pm.read")
    headers = _scoped_headers(client, db_session, email="scope.uh.pm@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    ids = {row["id"] for row in client.get("/api/v1/pm/records", headers=headers).json()["items"]}
    assert my_record["id"] in ids
    assert their_record["id"] not in ids

    assert client.get(f"/api/v1/pm/records/{their_record['id']}", headers=headers).status_code == 404


# ---- Calibration ----

def test_tl_with_granted_calibration_read_cannot_see_foreign_branch(admin_client, client, db_session):
    mine, theirs, my_asset, their_asset = _two_branch_assets(admin_client)

    mine_sched = admin_client.post(
        "/api/v1/calibration/schedules", json={"asset_id": my_asset["id"], "frequency": "YEARLY"}
    ).json()
    theirs_sched = admin_client.post(
        "/api/v1/calibration/schedules", json={"asset_id": their_asset["id"], "frequency": "YEARLY"}
    ).json()

    _grant(admin_client, db_session, "TL", "calibration.read")
    headers = _scoped_headers(client, db_session, email="scope.tl.cal@example.com",
                               role_code="TL", branch_id=mine["id"])

    ids = {row["id"] for row in client.get("/api/v1/calibration/schedules", headers=headers).json()["items"]}
    assert mine_sched["id"] in ids
    assert theirs_sched["id"] not in ids

    assert client.get(f"/api/v1/calibration/schedules/{theirs_sched['id']}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/calibration/schedules/{mine_sched['id']}", headers=headers).status_code == 200


# ---- Contracts (warranty is per-asset; AMC/CMC cover many assets) ----

def _vendor(admin_client, name="Scope Vendor"):
    return admin_client.post(
        "/api/v1/vendors", json={"name": name, "vendor_type": "SERVICE", "status": "ACTIVE"}
    ).json()


def test_unit_head_with_granted_contract_read_sees_only_contracts_covering_own_branch(
    admin_client, client, db_session
):
    mine, theirs, my_asset, their_asset = _two_branch_assets(admin_client)
    vendor = _vendor(admin_client)

    mine_amc = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "vendor_id": vendor["id"], "asset_ids": [my_asset["id"]],
              "start_date": "2026-01-01", "end_date": "2026-12-31", "status": "ACTIVE"},
    ).json()
    theirs_amc = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "vendor_id": vendor["id"], "asset_ids": [their_asset["id"]],
              "start_date": "2026-01-01", "end_date": "2026-12-31", "status": "ACTIVE"},
    ).json()

    _grant(admin_client, db_session, "UNIT_HEAD", "contract.read")
    headers = _scoped_headers(client, db_session, email="scope.uh.contract@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    ids = {row["id"] for row in client.get("/api/v1/contracts", headers=headers).json()["items"]}
    assert mine_amc["id"] in ids
    assert theirs_amc["id"] not in ids, "AMC covering only another branch's asset leaked"

    assert client.get(f"/api/v1/contracts/{theirs_amc['id']}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/contracts/{mine_amc['id']}", headers=headers).status_code == 200


def test_contract_spanning_both_branches_stays_visible_to_each(admin_client, client, db_session):
    """An AMC is an organisation-level commercial agreement and may legitimately cover
    assets in several branches. Scoping must be a coverage filter, not ownership — each
    branch still sees the shared contract."""
    mine, theirs, my_asset, their_asset = _two_branch_assets(admin_client)
    vendor = _vendor(admin_client, "Shared Vendor")

    shared = admin_client.post(
        "/api/v1/contracts",
        json={"contract_type": "AMC", "vendor_id": vendor["id"],
              "asset_ids": [my_asset["id"], their_asset["id"]],
              "start_date": "2026-01-01", "end_date": "2026-12-31", "status": "ACTIVE"},
    ).json()

    _grant(admin_client, db_session, "UNIT_HEAD", "contract.read")
    headers = _scoped_headers(client, db_session, email="scope.uh.shared@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    ids = {row["id"] for row in client.get("/api/v1/contracts", headers=headers).json()["items"]}
    assert shared["id"] in ids, "a contract covering my branch must stay visible"
    assert client.get(f"/api/v1/contracts/{shared['id']}", headers=headers).status_code == 200


# ---- Inventory: locations scope, the item catalog stays global ----

def test_unit_head_with_granted_inventory_read_cannot_see_foreign_locations(admin_client, client, db_session):
    mine, theirs, _, _ = _two_branch_assets(admin_client)

    mine_loc = admin_client.post(
        "/api/v1/inventory/locations", json={"name": "Mine Store", "branch_id": mine["id"], "kind": "STORE"}
    ).json()
    theirs_loc = admin_client.post(
        "/api/v1/inventory/locations", json={"name": "Theirs Store", "branch_id": theirs["id"], "kind": "STORE"}
    ).json()

    _grant(admin_client, db_session, "UNIT_HEAD", "inventory.read")
    headers = _scoped_headers(client, db_session, email="scope.uh.inv@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    ids = {row["id"] for row in client.get("/api/v1/inventory/locations", headers=headers).json()["items"]}
    assert mine_loc["id"] in ids
    assert theirs_loc["id"] not in ids

    assert client.get(f"/api/v1/inventory/locations/{theirs_loc['id']}", headers=headers).status_code == 404


def test_inventory_item_catalog_stays_global_for_branch_roles(admin_client, client, db_session):
    """InventoryItem has no branch column — it is a global spare-part catalog. Scoping
    it would be wrong, so a branch role granted inventory.read still sees the catalog."""
    mine, _, _, _ = _two_branch_assets(admin_client)

    item = admin_client.post(
        "/api/v1/inventory/items",
        json={"item_code": "SCOPE-GLOBAL-1", "name": "Global Spare", "unit": "EA", "is_consumable": False},
    ).json()

    _grant(admin_client, db_session, "UNIT_HEAD", "inventory.read")
    headers = _scoped_headers(client, db_session, email="scope.uh.catalog@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    ids = {row["id"] for row in client.get("/api/v1/inventory/items", headers=headers).json()["items"]}
    assert item["id"] in ids, "the global spare-part catalog must not become branch-scoped"


def test_central_consumable_pool_stays_visible_to_branch_roles(admin_client, client, db_session):
    """Consumable.branch_id IS NULL is the central pool, not another branch's stock, and
    the New Requirement consumable picker depends on reading it. Scoping must not break
    that — while quantities stay masked for a picker-only role."""
    mine, _, _, _ = _two_branch_assets(admin_client)

    central = admin_client.post(
        "/api/v1/consumables",
        json={"item_code": "SCOPE-CENTRAL-1", "name": "Central Gauze", "unit": "BOX",
              "opening_quantity": 500},
    ).json()

    headers = _scoped_headers(client, db_session, email="scope.uh.central@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    response = client.get("/api/v1/consumables?central_only=true", headers=headers)
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    ids = {row["id"] for row in rows}
    assert central["id"] in ids, "the central consumable pool must remain pickable"

    row = next(r for r in rows if r["id"] == central["id"])
    assert row.get("opening_quantity") is None, "picker-only role must not see central quantities"


def test_unit_head_with_granted_inventory_read_cannot_reach_foreign_consumable_by_id(admin_client, client, db_session):
    """Security hardening: list_consumables already scoped correctly, but the by-ID
    routes (get/update/delete/transactions/last-transaction) used a bare _get_or_404
    with no branch check — a branch role granted inventory.read (a real possibility,
    see this file's own docstring) could read/edit/delete another branch's consumable
    directly by ID. All five by-ID routes must now 404, matching the list route's
    confirm-nothing posture."""
    mine, theirs, _, _ = _two_branch_assets(admin_client)

    theirs_consumable = admin_client.post(
        "/api/v1/consumables",
        json={"item_code": "SCOPE-THEIRS-CONS-1", "name": "Theirs Gauze", "branch_id": theirs["id"],
              "unit": "BOX", "opening_quantity": 10},
    ).json()

    _grant(admin_client, db_session, "UNIT_HEAD", "inventory.read", "inventory.write")
    headers = _scoped_headers(client, db_session, email="scope.uh.cons.byid@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    assert client.get(f"/api/v1/consumables/{theirs_consumable['id']}", headers=headers).status_code == 404
    assert client.patch(
        f"/api/v1/consumables/{theirs_consumable['id']}", json={"name": "Hijacked"}, headers=headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/consumables/{theirs_consumable['id']}/transactions", headers=headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/consumables/{theirs_consumable['id']}/last-transaction", headers=headers
    ).status_code == 404
    assert client.delete(f"/api/v1/consumables/{theirs_consumable['id']}", headers=headers).status_code == 404

    # The record must still exist — every one of the above was blocked, not applied.
    still_there = admin_client.get(f"/api/v1/consumables/{theirs_consumable['id']}")
    assert still_there.status_code == 200
    assert still_there.json()["name"] == "Theirs Gauze"


def test_unit_head_with_granted_inventory_read_can_reach_own_branch_consumable_by_id(admin_client, client, db_session):
    mine, _, _, _ = _two_branch_assets(admin_client)

    mine_consumable = admin_client.post(
        "/api/v1/consumables",
        json={"item_code": "SCOPE-MINE-CONS-1", "name": "Mine Gauze", "branch_id": mine["id"],
              "unit": "BOX", "opening_quantity": 10},
    ).json()

    _grant(admin_client, db_session, "UNIT_HEAD", "inventory.read")
    headers = _scoped_headers(client, db_session, email="scope.uh.cons.own@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    response = client.get(f"/api/v1/consumables/{mine_consumable['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Mine Gauze"


def test_unit_head_with_granted_inventory_read_can_reach_central_consumable_by_id(admin_client, client, db_session):
    """The shared central pool (branch_id IS NULL) must stay reachable by ID too, same
    as it already is through the list endpoint's central_only filter."""
    mine, _, _, _ = _two_branch_assets(admin_client)

    central = admin_client.post(
        "/api/v1/consumables",
        json={"item_code": "SCOPE-CENTRAL-BYID-1", "name": "Central Bandage", "unit": "BOX",
              "opening_quantity": 500},
    ).json()

    _grant(admin_client, db_session, "UNIT_HEAD", "inventory.read")
    headers = _scoped_headers(client, db_session, email="scope.uh.cons.central@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    response = client.get(f"/api/v1/consumables/{central['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Central Bandage"


# ---- Fulfillments (F8 + F11) ----

def test_unit_head_sees_whole_branch_fulfillments_not_only_own(admin_client, client, db_session):
    """F8: a Unit Head is a branch operator. Requirements raised by other users in
    their branch must appear on their dashboard, not just their own submissions."""
    mine, theirs, my_asset, _ = _two_branch_assets(admin_client)
    model_id = my_asset["device_model_id"]

    colleague = create_user(db_session, "scope.colleague@example.com", "TL", PASSWORD)
    colleague.hospital_scope_id = mine["id"]
    db_session.add(colleague)
    db_session.commit()
    colleague_headers = auth_headers(client, "scope.colleague@example.com", PASSWORD)

    raised_by_colleague = client.post(
        "/api/v1/requirements",
        json=_requirement_payload(mine["id"], model_id, "colleague raised"),
        headers=colleague_headers,
    )
    assert raised_by_colleague.status_code == 201, raised_by_colleague.text

    headers = _scoped_headers(client, db_session, email="scope.uh.fulfil@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    listing = client.get("/api/v1/requirements", headers=headers)
    assert listing.status_code == 200, listing.text
    ids = {row["id"] for row in listing.json()["items"]}
    assert raised_by_colleague.json()["id"] in ids, (
        "F8: unit head must see branch-wide requirements, not only self-created ones"
    )


def test_branch_role_cannot_see_foreign_branch_fulfillments(admin_client, client, db_session):
    """F11 counterpart: even granted requirement.read (which previously meant "see
    everything"), a branch-scoped role stays inside its branch."""
    mine, theirs, my_asset, _ = _two_branch_assets(admin_client)

    foreign = admin_client.post(
        "/api/v1/requirements",
        json=_requirement_payload(theirs["id"], my_asset["device_model_id"], "foreign requirement"),
    )
    assert foreign.status_code == 201, foreign.text

    _grant(admin_client, db_session, "TL", "requirement.read")
    headers = _scoped_headers(client, db_session, email="scope.tl.fulfil@example.com",
                               role_code="TL", branch_id=mine["id"])

    ids = {row["id"] for row in client.get("/api/v1/requirements", headers=headers).json()["items"]}
    assert foreign.json()["id"] not in ids, "requirement.read must not defeat branch scope"

    direct = client.get(f"/api/v1/requirements/{foreign.json()['id']}", headers=headers)
    assert direct.status_code == 404


# ---- Audit stays organisation-wide ----

def test_audit_trail_is_not_branch_scoped(admin_client, client, db_session):
    """Audit is a polymorphic compliance trail with no branch relationship. It must
    stay organisation-wide — the correct control is not granting audit.read to a branch
    role, not pretending audit rows have a branch."""
    mine, theirs, _, _ = _two_branch_assets(admin_client)

    _grant(admin_client, db_session, "UNIT_HEAD", "audit.read")
    headers = _scoped_headers(client, db_session, email="scope.uh.audit@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    response = client.get("/api/v1/audit-logs", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["total"] > 0, "audit trail must remain readable organisation-wide"
