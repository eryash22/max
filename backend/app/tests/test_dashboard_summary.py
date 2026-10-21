"""F2 — the composed dashboard endpoint.

GET /api/v1/dashboard/summary replaces the Dashboard's ~37 + 8-per-branch count
requests with one aggregated call. These tests pin the two properties that make that
safe to rely on:

  * **Sections are omitted, not zeroed, when the caller lacks the permission** — an
    unauthorized metric is never serialised, so the frontend cannot accidentally
    render one and a network inspector never shows it.
  * **Branch-scoped callers are pinned to their branch**, including against the
    `branch_id` cross-filter that drives the Power BI-style branch click.
"""
from app.models.identity import Role

from .conftest import auth_headers, create_user

PASSWORD = "Password123!"


def _grant(admin_client, db_session, role_code, *permission_codes):
    role = db_session.query(Role).filter(Role.code == role_code).first()
    codes = sorted({rp.permission.code for rp in role.permissions} | set(permission_codes))
    assert admin_client.patch(f"/api/v1/roles/{role.id}", json={"permission_codes": codes}).status_code == 200


def _scoped_headers(client, db_session, *, email, role_code, branch_id=None):
    user = create_user(db_session, email, role_code, PASSWORD)
    if branch_id:
        user.hospital_scope_id = branch_id
    db_session.add(user)
    db_session.commit()
    return auth_headers(client, email, PASSWORD)


def _branch(admin_client, name, **extra):
    payload = {"name": name, "status": "ACTIVE", **extra}
    response = admin_client.post("/api/v1/branches", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _asset(admin_client, device_model_id, branch_id, asset_number, **extra):
    response = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": device_model_id,
              "branch_id": branch_id, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _model(admin_client, name="Dash Monitor"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "MONITOR"}).json()


# ---- Super Admin: organisation-wide ----

def test_super_admin_gets_organization_scope_and_every_section(admin_client):
    body = admin_client.get("/api/v1/dashboard/summary").json()

    assert body["scope"] == "ORGANIZATION"
    assert body["branch_id"] is None
    assert body["can_choose_branch"] is True
    for section in ("assets", "incidents", "requirements", "fulfillments", "branches"):
        assert section in body["sections"], f"super admin should see {section}"


def test_super_admin_can_cross_filter_by_branch(admin_client):
    """Clicking a branch in a chart re-requests the same endpoint with branch_id —
    a real server-side filter, not client-side slicing."""
    mine = _branch(admin_client, "Dash Branch A")
    other = _branch(admin_client, "Dash Branch B")
    model = _model(admin_client)
    _asset(admin_client, model["id"], mine["id"], "DASH-A-1")
    _asset(admin_client, model["id"], other["id"], "DASH-B-1")
    _asset(admin_client, model["id"], other["id"], "DASH-B-2")

    filtered = admin_client.get(f"/api/v1/dashboard/summary?branch_id={other['id']}").json()
    assert filtered["branch_id"] == other["id"]
    assert filtered["assets"]["total"] == 2

    unfiltered = admin_client.get("/api/v1/dashboard/summary").json()
    assert unfiltered["assets"]["total"] >= 3


# ---- Branch roles: pinned scope ----

def test_unit_head_scope_is_pinned_and_cannot_be_widened(admin_client, client, db_session):
    mine = _branch(admin_client, "Dash Mine")
    theirs = _branch(admin_client, "Dash Theirs")
    model = _model(admin_client)
    _asset(admin_client, model["id"], mine["id"], "DASH-MINE-1")
    _asset(admin_client, model["id"], theirs["id"], "DASH-THEIRS-1")
    _asset(admin_client, model["id"], theirs["id"], "DASH-THEIRS-2")

    headers = _scoped_headers(client, db_session, email="dash.uh@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    body = client.get("/api/v1/dashboard/summary", headers=headers).json()
    assert body["scope"] == "BRANCH"
    assert body["branch_id"] == mine["id"]
    assert body["can_choose_branch"] is False, "a branch role must not get a branch selector"
    assert body["assets"]["total"] == 1

    forged = client.get(f"/api/v1/dashboard/summary?branch_id={theirs['id']}", headers=headers).json()
    assert forged["branch_id"] == mine["id"]
    assert forged["assets"]["total"] == 1, "cross-filter must not become a cross-branch read"


def test_tl_branch_table_contains_only_own_branch(admin_client, client, db_session):
    mine = _branch(admin_client, "Dash TL Mine")
    _branch(admin_client, "Dash TL Theirs")

    headers = _scoped_headers(client, db_session, email="dash.tl@example.com",
                               role_code="TL", branch_id=mine["id"])

    body = client.get("/api/v1/dashboard/summary", headers=headers).json()
    branch_ids = {row["id"] for row in body["branches"]}
    assert branch_ids == {mine["id"]}, f"TL branch table must hold only their branch, got {branch_ids}"


# ---- Permission gating: omitted, not zeroed ----

def test_sections_are_absent_when_permission_is_missing(admin_client, client, db_session):
    """A Unit Head holds no inventory.read / pm.read / calibration.read by default, so
    those keys must not appear at all."""
    mine = _branch(admin_client, "Dash Gate Branch")
    headers = _scoped_headers(client, db_session, email="dash.gate@example.com",
                               role_code="UNIT_HEAD", branch_id=mine["id"])

    body = client.get("/api/v1/dashboard/summary", headers=headers).json()

    assert "central_device_stock" not in body, "central stock must not reach a picker-only role"
    assert "maintenance" not in body, "no pm.read/calibration.read means no maintenance section"
    assert body["capabilities"]["inventory"] is False
    assert body["capabilities"]["pm"] is False

    # The sections they DO hold are present.
    assert "incidents" in body
    assert "review" in body, "unit head holds review.read"


def test_granting_a_permission_adds_its_section_but_keeps_branch_scope(admin_client, client, db_session):
    """The F11 property expressed through the dashboard: granting pm.read reveals the
    maintenance section, but the numbers in it stay branch-scoped."""
    mine = _branch(admin_client, "Dash Grant Mine")
    theirs = _branch(admin_client, "Dash Grant Theirs")
    model = _model(admin_client)
    my_asset = _asset(admin_client, model["id"], mine["id"], "DASH-GRANT-1")
    their_asset = _asset(admin_client, model["id"], theirs["id"], "DASH-GRANT-2")

    admin_client.post("/api/v1/pm/schedules",
                      json={"asset_id": my_asset["id"], "frequency": "QUARTERLY", "next_due_date": "2020-01-01"})
    admin_client.post("/api/v1/pm/schedules",
                      json={"asset_id": their_asset["id"], "frequency": "QUARTERLY", "next_due_date": "2020-01-01"})

    _grant(admin_client, db_session, "TL", "pm.read")
    headers = _scoped_headers(client, db_session, email="dash.grant.tl@example.com",
                               role_code="TL", branch_id=mine["id"])

    body = client.get("/api/v1/dashboard/summary", headers=headers).json()
    assert "maintenance" in body, "granted pm.read should surface the section"
    assert body["maintenance"]["pm"]["overdue"] == 1, (
        f"only the caller's own branch schedule may be counted, got {body['maintenance']}"
    )


# ---- Central warehouse device stock ----

def test_central_device_stock_counts_physical_assets_and_excludes_unavailable(admin_client):
    """Phase 6: the count is physical Asset rows per DeviceModel, never a DeviceModel
    count, with CONDEMNED/RETIRED excluded by the shared eligibility predicate."""
    central = _branch(admin_client, "Dash Central", is_central_warehouse=True)
    model = _model(admin_client, "Central TENS")

    _asset(admin_client, model["id"], central["id"], "DASH-C-1")
    _asset(admin_client, model["id"], central["id"], "DASH-C-2")
    _asset(admin_client, model["id"], central["id"], "DASH-C-3", status="CONDEMNED")
    _asset(admin_client, model["id"], central["id"], "DASH-C-4", status="RETIRED")

    body = admin_client.get("/api/v1/dashboard/summary").json()
    rows = [r for r in body["central_device_stock"] if r["device_model_id"] == model["id"]]
    assert len(rows) == 1
    assert rows[0]["available_count"] == 2, (
        f"CONDEMNED/RETIRED must not count as available, got {rows[0]}"
    )
    assert rows[0]["name"] == "Central TENS"


def test_central_device_stock_withheld_without_inventory_read(admin_client, client, db_session):
    central = _branch(admin_client, "Dash Central Guard", is_central_warehouse=True)
    model = _model(admin_client, "Guarded Device")
    _asset(admin_client, model["id"], central["id"], "DASH-CG-1")

    headers = _scoped_headers(client, db_session, email="dash.nostock@example.com",
                               role_code="TL", branch_id=central["id"])

    body = client.get("/api/v1/dashboard/summary", headers=headers).json()
    assert "central_device_stock" not in body, "inventory quantities must stay masked"


# ---- Degenerate cases the page must survive ----

def test_role_without_dashboard_permissions_gets_empty_sections_not_an_error(client, db_session):
    """A minimal role gets an empty dashboard, not a 403 — the page renders its own
    empty state rather than an error screen."""
    headers = _scoped_headers(client, db_session, email="dash.viewer@example.com", role_code="VIEWER")
    response = client.get("/api/v1/dashboard/summary", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["sections"], list)
    assert body["scope"] in ("ORGANIZATION", "BRANCH")


def test_summary_requires_authentication(client):
    assert client.get("/api/v1/dashboard/summary").status_code == 401


def test_empty_branch_returns_zeroed_vocabulary_not_missing_keys(admin_client):
    """A brand-new branch has no incidents, but a chart still needs its axis — every
    known status is present with a zero rather than absent."""
    empty = _branch(admin_client, "Dash Empty Branch")
    body = admin_client.get(f"/api/v1/dashboard/summary?branch_id={empty['id']}").json()

    assert body["incidents"]["total"] == 0
    assert body["incidents"]["open"] == 0
    assert body["incidents"]["by_status"]["OPEN"] == 0
    assert body["requirements"]["by_status"]["DRAFT"] == 0
    assert body["fulfillments"]["by_status"]["PENDING_ALLOCATION"] == 0
