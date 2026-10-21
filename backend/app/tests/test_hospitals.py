def test_create_and_list_branch(admin_client):
    create = admin_client.post("/api/v1/branches", json={"name": "Saket", "status": "ACTIVE"})
    assert create.status_code == 201
    branch_id = create.json()["id"]

    listed = admin_client.get("/api/v1/branches")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    fetched = admin_client.get(f"/api/v1/branches/{branch_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Saket"


def test_department_requires_existing_branch(admin_client):
    master = admin_client.post("/api/v1/department-masters", json={"name": "Physiotherapy"}).json()
    response = admin_client.post(
        "/api/v1/departments", json={"branch_id": "does-not-exist", "department_master_id": master["id"]}
    )
    assert response.status_code == 404


def test_branch_hierarchy_end_to_end(admin_client):
    branch = admin_client.post("/api/v1/branches", json={"name": "Dwarka", "status": "ACTIVE"}).json()
    building = admin_client.post("/api/v1/buildings", json={"branch_id": branch["id"], "name": "Main"}).json()
    floor = admin_client.post("/api/v1/floors", json={"building_id": building["id"], "name": "Floor 1"}).json()
    master = admin_client.post("/api/v1/department-masters", json={"name": "Rehab"}).json()
    department = admin_client.post(
        "/api/v1/departments",
        json={"branch_id": branch["id"], "floor_id": floor["id"], "department_master_id": master["id"]},
    ).json()
    location = admin_client.post(
        "/api/v1/locations", json={"department_id": department["id"], "name": "Room 1"}
    )
    assert location.status_code == 201


# ---- Stale/deleted master-data visibility audit — Location picker (Final task) ----
# Location.status exists on the model but no endpoint currently sets it away from its
# ACTIVE default, so this bug was latent — GET /locations never filtered by status at
# all. Fixed for consistency with Branch/Department/Vendor: status_filter is opt-in
# (unfiltered by default), a "new record" picker passes status_filter=ACTIVE.
def test_inactive_location_excluded_from_active_filtered_list(admin_client, db_session):
    from app.models.hospital import Location

    branch = admin_client.post("/api/v1/branches", json={"name": "Location QA Branch", "status": "ACTIVE"}).json()
    master = admin_client.post("/api/v1/department-masters", json={"name": "Location QA Dept"}).json()
    department = admin_client.post(
        "/api/v1/departments", json={"branch_id": branch["id"], "department_master_id": master["id"]}
    ).json()
    location = admin_client.post(
        "/api/v1/locations", json={"department_id": department["id"], "name": "Stale Location QA"}
    ).json()

    db_location = db_session.get(Location, location["id"])
    db_location.status = "INACTIVE"
    db_session.commit()

    active_only = admin_client.get(f"/api/v1/locations?department_id={department['id']}&status_filter=ACTIVE")
    assert all(loc["id"] != location["id"] for loc in active_only.json()["items"])

    unfiltered = admin_client.get(f"/api/v1/locations?department_id={department['id']}")
    assert any(loc["id"] == location["id"] for loc in unfiltered.json()["items"])
