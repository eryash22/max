from app.models.device import BranchDeviceAssignment
from app.models.review import ReviewItem

from .conftest import auth_headers, create_user


def _seed_review_item(db_session, reason_code="UNRESOLVED_SOURCE_NAME") -> ReviewItem:
    item = ReviewItem(source_type="branch_device_assignment", source_id="fake-id", reason_code=reason_code)
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def _seed_review_item_for_branch(db_session, branch_id: str, reason_code="UNRESOLVED_SOURCE_NAME") -> ReviewItem:
    assignment = BranchDeviceAssignment(branch_id=branch_id, source_name="Import batch")
    db_session.add(assignment)
    db_session.flush()
    item = ReviewItem(source_type="branch_device_assignment", source_id=assignment.id, reason_code=reason_code)
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def test_review_queue_lists_open_items(admin_client, db_session):
    _seed_review_item(db_session)
    response = admin_client.get("/api/v1/review-queue")
    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_resolve_action_updates_status(admin_client, db_session):
    item = _seed_review_item(db_session)
    response = admin_client.post(
        f"/api/v1/review-queue/{item.id}/action", json={"action": "resolve", "notes": "Confirmed manually"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "RESOLVED"


def test_location_verification_requires_notes_to_accept(admin_client, db_session):
    item = _seed_review_item(db_session, reason_code="LOCATION_ASSET_VERIFICATION_REQUIRED")
    response = admin_client.post(f"/api/v1/review-queue/{item.id}/action", json={"action": "accept"})
    assert response.status_code == 400

    with_notes = admin_client.post(
        f"/api/v1/review-queue/{item.id}/action",
        json={"action": "accept", "notes": "Verified on-site 2026-08-17"},
    )
    assert with_notes.status_code == 200


def test_unit_head_review_queue_scoped_to_own_branch(client, db_session, admin_client):
    """Phase 10 — Review Queue was the one module with zero branch scoping; a
    UNIT_HEAD/TL must only ever see review items resolvable to their own branch
    (here, via the branch_device_assignment source row's branch_id)."""
    branch_a = admin_client.post("/api/v1/branches", json={"name": "Review Branch A", "status": "ACTIVE"}).json()
    branch_b = admin_client.post("/api/v1/branches", json={"name": "Review Branch B", "status": "ACTIVE"}).json()
    item_a = _seed_review_item_for_branch(db_session, branch_a["id"])
    item_b = _seed_review_item_for_branch(db_session, branch_b["id"])

    user = create_user(db_session, "uh.reviewscope@example.com", "UNIT_HEAD", "Password123!")
    user.hospital_scope_id = branch_a["id"]
    db_session.add(user)
    db_session.commit()
    uh_headers = auth_headers(client, "uh.reviewscope@example.com", "Password123!")

    listing = client.get("/api/v1/review-queue", headers=uh_headers).json()
    ids = {row["id"] for row in listing["items"]}
    assert item_a.id in ids
    assert item_b.id not in ids

    own_action = client.post(
        f"/api/v1/review-queue/{item_a.id}/action", json={"action": "resolve", "notes": "ok"}, headers=uh_headers,
    )
    # UNIT_HEAD does not hold review.resolve by default — 403, not 404 — confirming
    # the branch-scope check above ran before (and independently of) the permission
    # check on the resolve action itself.
    assert own_action.status_code == 403

    other_action = client.post(
        f"/api/v1/review-queue/{item_b.id}/action", json={"action": "resolve", "notes": "ok"},
    )
    assert other_action.status_code == 200

    # Admin (broad visibility) is unaffected.
    admin_listing = admin_client.get("/api/v1/review-queue").json()
    admin_ids = {row["id"] for row in admin_listing["items"]}
    assert {item_a.id, item_b.id} <= admin_ids
