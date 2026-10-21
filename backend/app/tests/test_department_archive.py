"""Final non-AI cleanup pass — Department CRUD gap closure.

Departments previously had no DELETE/activate/deactivate at all (create+list+update
only). This mirrors the exact same reference-check-then-hard-delete-or-reject
pattern already covered for Branch in test_batch_c_archive.py.
"""

from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _make_branch(admin_client, name="Dept-Test-Branch"):
    response = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"})
    assert response.status_code == 201, response.text
    return response.json()


def _make_department(admin_client, branch_id, name="Physiotherapy"):
    # Departments are now branch assignments of a central DepartmentMaster — a
    # fresh master per call keeps these tests independent (a duplicate master name
    # is rejected, and `name` here is only ever used to derive a unique one).
    master = admin_client.post("/api/v1/department-masters", json={"name": name}).json()
    response = admin_client.post(
        "/api/v1/departments", json={"branch_id": branch_id, "department_master_id": master["id"]}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_device_model(admin_client, name="Dept-Test-Model"):
    return admin_client.post("/api/v1/device-models", json={"name": name, "category": "Imaging"}).json()


def test_delete_unreferenced_department_succeeds(admin_client):
    branch = _make_branch(admin_client)
    department = _make_department(admin_client, branch["id"])

    response = admin_client.delete(f"/api/v1/departments/{department['id']}")
    assert response.status_code == 204

    listed = admin_client.get("/api/v1/departments")
    assert all(d["id"] != department["id"] for d in listed.json()["items"])


def test_delete_referenced_department_blocked(admin_client):
    branch = _make_branch(admin_client, "Referenced Dept Branch")
    department = _make_department(admin_client, branch["id"], "Referenced Dept")
    model = _make_device_model(admin_client)
    asset = admin_client.post(
        "/api/v1/assets",
        json={
            "asset_number": "DEPT-AST-1",
            "device_model_id": model["id"],
            "branch_id": branch["id"],
            "department_id": department["id"],
        },
    )
    assert asset.status_code == 201, asset.text

    response = admin_client.delete(f"/api/v1/departments/{department['id']}")
    assert response.status_code == 409
    assert "cannot be deleted" in response.json()["detail"]

    still_there = admin_client.get("/api/v1/departments")
    assert any(d["id"] == department["id"] for d in still_there.json()["items"])


def test_activate_and_deactivate_department(admin_client, db_session):
    branch = _make_branch(admin_client, "Toggle Dept Branch")
    department = _make_department(admin_client, branch["id"], "Toggle Dept")
    assert department["status"] == "ACTIVE"

    deactivated = admin_client.post(f"/api/v1/departments/{department['id']}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "INACTIVE"

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "department", AuditLog.entity_id == department["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None

    activated = admin_client.post(f"/api/v1/departments/{department['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"


def test_deactivate_referenced_department_allowed(admin_client):
    """Deactivating is always allowed even when referenced — only the hard DELETE is
    blocked, same convention as Branch."""
    branch = _make_branch(admin_client, "Deactivatable Dept Branch")
    department = _make_department(admin_client, branch["id"], "Deactivatable Dept")
    model = _make_device_model(admin_client, "Deactivatable Dept Model")
    asset = admin_client.post(
        "/api/v1/assets",
        json={
            "asset_number": "DEPT-AST-2",
            "device_model_id": model["id"],
            "branch_id": branch["id"],
            "department_id": department["id"],
        },
    )
    assert asset.status_code == 201, asset.text

    response = admin_client.post(f"/api/v1/departments/{department['id']}/deactivate")
    assert response.status_code == 200
    assert response.json()["status"] == "INACTIVE"


def test_delete_department_not_found(admin_client):
    response = admin_client.delete("/api/v1/departments/does-not-exist")
    assert response.status_code == 404


def test_delete_department_requires_hospital_write(client, db_session):
    create_user(db_session, "dept-viewer@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "dept-viewer@example.com", "Password123!")
    response = client.delete("/api/v1/departments/does-not-exist", headers=headers)
    assert response.status_code == 403


# ---- Stale/deleted master-data visibility audit — Department picker (Final task) ----
# The "Department / category" leak the user observed traced to a hardcoded frontend
# category taxonomy (DEVICE_CATEGORIES/CONSUMABLE_CATEGORIES), not this endpoint — but
# a real, separate bug was found in the same audit: an INACTIVE Department assignment
# was still returned by every branch-scoped Department picker (devices/new,
# incidents/new, requirements/new) because GET /departments never filtered by status.

def test_deactivated_department_excluded_from_active_filtered_list(admin_client):
    branch = _make_branch(admin_client, "Stale Dept Picker QA Branch")
    department = _make_department(admin_client, branch["id"], "Stale Dept Picker QA")
    admin_client.post(f"/api/v1/departments/{department['id']}/deactivate")

    active_only = admin_client.get(f"/api/v1/departments?branch_id={branch['id']}&status_filter=ACTIVE")
    assert all(d["id"] != department["id"] for d in active_only.json()["items"])


def test_deactivated_department_still_visible_unfiltered(admin_client):
    """/departments/page.tsx's own branch-assignment toggle view relies on seeing both
    ACTIVE and INACTIVE assignments (to render the toggle state) — it deliberately
    omits status_filter, so that must keep returning everything."""
    branch = _make_branch(admin_client, "Stale Dept Admin QA Branch")
    department = _make_department(admin_client, branch["id"], "Stale Dept Admin QA")
    admin_client.post(f"/api/v1/departments/{department['id']}/deactivate")

    unfiltered = admin_client.get(f"/api/v1/departments?branch_id={branch['id']}")
    assert any(d["id"] == department["id"] for d in unfiltered.json()["items"])


def test_historical_asset_still_readable_after_department_deactivated(admin_client):
    """Deactivating a Department must not break a historical Asset row that already
    references it (Phase A2/A3's "historical record retains its relationship")."""
    branch = _make_branch(admin_client, "Stale Dept History QA Branch")
    department = _make_department(admin_client, branch["id"], "Stale Dept History QA")
    model = _make_device_model(admin_client, "Stale Dept History QA Model")
    asset = admin_client.post(
        "/api/v1/assets",
        json={
            "asset_number": "DEPT-HIST-1", "device_model_id": model["id"],
            "branch_id": branch["id"], "department_id": department["id"],
        },
    ).json()

    admin_client.post(f"/api/v1/departments/{department['id']}/deactivate")

    fetched = admin_client.get(f"/api/v1/assets/{asset['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["department_id"] == department["id"]
