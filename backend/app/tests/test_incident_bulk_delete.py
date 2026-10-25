"""Incident bulk-delete and Replace-all-from-Excel — explicit, user-initiated
destructive operations (see services/incident_service.py's delete_incident_safe
and routers/incidents.py's bulk-delete/replace-all endpoints). Never run
automatically by any import; every test here calls them directly, the way the
live app's confirm screens would.
"""
import io

import openpyxl

from app.models.device import Asset
from app.models.identity import Role
from app.models.incident import Incident, IncidentStatusHistory, ServiceVisit
from app.models.procurement import Quotation
from app.models.vendor import Vendor

from .conftest import auth_headers, create_user


def _branch(admin_client, name="Bulk Delete Branch"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _asset(admin_client, branch_id, asset_number):
    model = admin_client.post(
        "/api/v1/device-models", json={"name": f"Model-{asset_number}", "category": "Ventilator"}
    ).json()
    return admin_client.post(
        "/api/v1/assets", json={"asset_number": asset_number, "device_model_id": model["id"], "branch_id": branch_id}
    ).json()


def _vendor(admin_client, name="Bulk Delete Vendor"):
    return admin_client.post("/api/v1/vendors", json={"name": name, "vendor_type": "SERVICE", "status": "ACTIVE"}).json()


def _incident(admin_client, branch_id, description="Bulk delete test incident"):
    return admin_client.post(
        "/api/v1/incidents", json={"branch_id": branch_id, "description": description}
    ).json()


def _workbook_bytes(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Sheet"
    sheet.append([
        "Item Name", "Date", "Unit*", "Make", "Vendor", "Issue Description*",
        "Cer No", "Indent No", "Po No", "Po Closure Date", "Status",
        "Po Cost - Excl. Gst", "Po Gst %", "Po Amount",
        "Vendor Estimate Cost - Excl. Gst", "Vendor Estimate Gst %", "Vendor Estimate Cost - Incl. Gst",
        "Remark",
    ])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ---- Safe single-incident delete (delete_incident_safe) ----

def test_single_delete_without_force_now_actually_deletes_when_unblocked(admin_client, db_session):
    branch = _branch(admin_client, "Single Delete Safe")
    incident = _incident(admin_client, branch["id"])

    response = admin_client.delete(f"/api/v1/incidents/{incident['id']}")
    assert response.status_code == 204, response.text
    assert db_session.get(Incident, incident["id"]) is None


def test_single_delete_cascades_owned_history_and_detaches_nullable_service_visit(admin_client, db_session):
    branch = _branch(admin_client, "Single Delete Cascade")
    asset = _asset(admin_client, branch["id"], "AST-BULK-001")
    incident = _incident(admin_client, branch["id"])

    # Status change creates an owned IncidentStatusHistory row.
    admin_client.post(f"/api/v1/incidents/{incident['id']}/status", json={"to_status": "WORK_IN_PROGRESS"})
    assert db_session.query(IncidentStatusHistory).filter(
        IncidentStatusHistory.incident_id == incident["id"]
    ).count() > 0

    visit = ServiceVisit(asset_id=asset["id"], incident_id=incident["id"], summary="Linked visit")
    db_session.add(visit)
    db_session.commit()
    visit_id = visit.id

    response = admin_client.delete(f"/api/v1/incidents/{incident['id']}")
    assert response.status_code == 204, response.text

    assert db_session.get(Incident, incident["id"]) is None
    assert db_session.query(IncidentStatusHistory).filter(
        IncidentStatusHistory.incident_id == incident["id"]
    ).count() == 0
    # The service visit itself survives — only detached, never deleted.
    survived_visit = db_session.get(ServiceVisit, visit_id)
    assert survived_visit is not None
    assert survived_visit.incident_id is None


def test_single_delete_blocked_by_real_quotation_does_not_delete_anything(admin_client, db_session):
    branch = _branch(admin_client, "Single Delete Blocked")
    asset = _asset(admin_client, branch["id"], "AST-BULK-002")
    vendor = _vendor(admin_client, "Blocking Vendor")
    incident = _incident(admin_client, branch["id"])

    quotation = Quotation(
        quotation_number="Q-BULK-BLOCK-1", incident_id=incident["id"], asset_id=asset["id"], vendor_id=vendor["id"],
    )
    db_session.add(quotation)
    db_session.commit()

    response = admin_client.delete(f"/api/v1/incidents/{incident['id']}")
    assert response.status_code == 409
    assert "quotation" in response.json()["detail"].lower()

    assert db_session.get(Incident, incident["id"]) is not None
    assert db_session.get(Quotation, quotation.id) is not None


# ---- Bulk delete ----

def test_bulk_delete_preview_reports_blocked_and_deletable_separately(admin_client, db_session):
    branch = _branch(admin_client, "Bulk Preview Branch")
    asset = _asset(admin_client, branch["id"], "AST-BULK-003")
    vendor = _vendor(admin_client, "Bulk Preview Vendor")
    deletable = _incident(admin_client, branch["id"], "Deletable one")
    blocked = _incident(admin_client, branch["id"], "Blocked one")
    db_session.add(Quotation(quotation_number="Q-BULK-PREVIEW-1", incident_id=blocked["id"],
                              asset_id=asset["id"], vendor_id=vendor["id"]))
    db_session.commit()

    response = admin_client.post("/api/v1/incidents/bulk-delete/preview", json={
        "incident_ids": [deletable["id"], blocked["id"]],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_selected"] == 2
    assert body["deletable_count"] == 1
    assert len(body["blocked"]) == 1
    assert body["blocked"][0]["incident_id"] == blocked["id"]

    # Preview must never write anything.
    assert db_session.get(Incident, deletable["id"]) is not None
    assert db_session.get(Incident, blocked["id"]) is not None


def test_bulk_delete_is_best_effort_deletes_safe_skips_blocked(admin_client, db_session):
    branch = _branch(admin_client, "Bulk Delete Best Effort")
    asset = _asset(admin_client, branch["id"], "AST-BULK-004")
    vendor = _vendor(admin_client, "Bulk Delete Vendor")
    deletable = _incident(admin_client, branch["id"], "Deletable")
    blocked = _incident(admin_client, branch["id"], "Blocked")
    db_session.add(Quotation(quotation_number="Q-BULK-DELETE-1", incident_id=blocked["id"],
                              asset_id=asset["id"], vendor_id=vendor["id"]))
    db_session.commit()

    response = admin_client.post("/api/v1/incidents/bulk-delete", json={
        "incident_ids": [deletable["id"], blocked["id"]],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 1
    assert body["skipped"] == 1
    assert body["skipped_incidents"][0]["incident_id"] == blocked["id"]

    assert db_session.get(Incident, deletable["id"]) is None
    assert db_session.get(Incident, blocked["id"]) is not None


def test_bulk_delete_select_all_matching_filter(admin_client, db_session):
    branch = _branch(admin_client, "Bulk Delete Select All")
    other_branch = _branch(admin_client, "Bulk Delete Select All Other")
    _incident(admin_client, branch["id"], "In scope 1")
    _incident(admin_client, branch["id"], "In scope 2")
    _incident(admin_client, other_branch["id"], "Out of scope")

    response = admin_client.post("/api/v1/incidents/bulk-delete", json={
        "select_all_matching_filter": True, "branch_id": branch["id"],
    })
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 2

    remaining = db_session.query(Incident).filter(Incident.branch_id == branch["id"]).count()
    assert remaining == 0
    assert db_session.query(Incident).filter(Incident.branch_id == other_branch["id"]).count() == 1


def test_bulk_delete_writes_one_audit_entry_for_the_whole_batch(admin_client, db_session):
    from app.models.audit import AuditLog

    branch = _branch(admin_client, "Bulk Delete Audit")
    one = _incident(admin_client, branch["id"], "Audit test 1")
    two = _incident(admin_client, branch["id"], "Audit test 2")

    admin_client.post("/api/v1/incidents/bulk-delete", json={"incident_ids": [one["id"], two["id"]]})

    logs = db_session.query(AuditLog).filter(AuditLog.action == "BULK_DELETE", AuditLog.entity == "incident").all()
    assert len(logs) == 1
    assert logs[0].new_value["deleted_count"] == 2
    assert set(logs[0].new_value["deleted_ids"]) == {one["id"], two["id"]}


def test_bulk_delete_requires_incident_manage_permission(client, db_session):
    create_user(db_session, "no.manage@example.com", "STAFF_OPERATOR", "Password123!")
    headers = auth_headers(client, "no.manage@example.com", "Password123!")
    response = client.post("/api/v1/incidents/bulk-delete", json={"incident_ids": ["whatever"]}, headers=headers)
    assert response.status_code == 403


def test_bulk_delete_select_all_is_branch_scoped_for_a_branch_scoped_manager(admin_client, client, db_session):
    """Defense-in-depth: a UNIT_HEAD granted incident.manage (role permissions are
    DB-driven — a real admin action, not a hypothetical) must still only be able to
    bulk-delete within their own branch via select_all_matching_filter."""
    mine = _branch(admin_client, "Bulk Delete Scoped Mine")
    theirs = _branch(admin_client, "Bulk Delete Scoped Theirs")
    _incident(admin_client, mine["id"], "Mine")
    _incident(admin_client, theirs["id"], "Theirs")

    role = db_session.query(Role).filter(Role.code == "UNIT_HEAD").first()
    codes = sorted({rp.permission.code for rp in role.permissions} | {"incident.manage"})
    admin_client.patch(f"/api/v1/roles/{role.id}", json={"permission_codes": codes})

    user = create_user(db_session, "scoped.manager@example.com", "UNIT_HEAD", "Password123!")
    user.hospital_scope_id = mine["id"]
    db_session.add(user)
    db_session.commit()
    headers = auth_headers(client, "scoped.manager@example.com", "Password123!")

    response = client.post("/api/v1/incidents/bulk-delete", json={
        "select_all_matching_filter": True, "branch_id": theirs["id"],  # tries to target the OTHER branch
    }, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 1  # only "mine" was actually deletable, "theirs" filter was ignored

    assert db_session.query(Incident).filter(Incident.branch_id == theirs["id"]).count() == 1
    assert db_session.query(Incident).filter(Incident.branch_id == mine["id"]).count() == 0


def test_bulk_delete_cannot_touch_unrelated_tables(admin_client, db_session):
    """Explicit proof the bulk-delete endpoint only ever touches Incident and its
    own owned/nullable dependents — never Assets, Vendors, Branches, or anything
    else, even when those rows are directly reachable from the deleted incidents."""
    branch = _branch(admin_client, "Unrelated Tables Branch")
    asset = _asset(admin_client, branch["id"], "AST-BULK-005")
    vendor = _vendor(admin_client, "Unrelated Tables Vendor")
    incident = _incident(admin_client, branch["id"])

    branch_count_before = db_session.query(Asset).filter(Asset.branch_id == branch["id"]).count()
    vendor_count_before = db_session.query(Vendor).filter(Vendor.id == vendor["id"]).count()
    assert branch_count_before == 1
    assert vendor_count_before == 1

    response = admin_client.post("/api/v1/incidents/bulk-delete", json={"incident_ids": [incident["id"]]})
    assert response.status_code == 200
    assert response.json()["deleted"] == 1

    assert db_session.query(Asset).filter(Asset.branch_id == branch["id"]).count() == branch_count_before
    assert db_session.query(Vendor).filter(Vendor.id == vendor["id"]).count() == vendor_count_before
    assert db_session.get(Asset, asset["id"]) is not None


# ---- Replace-all ----

def test_replace_all_preview_shows_counts_with_no_writes(admin_client, db_session):
    branch = _branch(admin_client, "Replace All Preview")
    _incident(admin_client, branch["id"], "Existing before replace")
    before_count = db_session.query(Incident).count()

    file_bytes = _workbook_bytes([[
        "Item", "2025-04-01", branch["name"], "Local", "Vendor", "New row from workbook",
        "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None,
    ]])
    response = admin_client.post(
        "/api/v1/incidents/replace-all/preview",
        files={"file": ("replace.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["current_incident_count"] == before_count
    assert body["incoming_valid_rows"] == 1
    assert body["incoming_invalid_rows"] == 0
    assert body["blocking_existing_incidents"] == []

    assert db_session.query(Incident).count() == before_count


def test_replace_all_deletes_existing_and_inserts_new_atomically(admin_client, db_session):
    branch = _branch(admin_client, "Replace All Success")
    old1 = _incident(admin_client, branch["id"], "Old incident 1")
    old2 = _incident(admin_client, branch["id"], "Old incident 2")

    file_bytes = _workbook_bytes([
        ["Item A", "2025-04-01", branch["name"], "Local", "Vendor", "Fresh row A",
         "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None],
        ["Item B", "2025-04-02", branch["name"], "Local", "Vendor", "Fresh row B",
         "NA", "NA", "NA", None, "Closed", None, None, None, None, None, None, None],
    ])
    preview = admin_client.post(
        "/api/v1/incidents/replace-all/preview",
        files={"file": ("replace.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()

    response = admin_client.post(
        "/api/v1/incidents/replace-all",
        params={"confirm_replace": "true", "expected_current_count": preview["current_incident_count"]},
        files={"file": ("replace.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == preview["current_incident_count"]
    assert body["inserted"] == 2

    assert db_session.get(Incident, old1["id"]) is None
    assert db_session.get(Incident, old2["id"]) is None
    remaining = db_session.query(Incident).all()
    assert {i.description for i in remaining} == {"Fresh row A", "Fresh row B"}


def test_replace_all_refuses_entirely_when_an_existing_incident_is_blocked_nothing_changes(admin_client, db_session):
    """Atomicity guarantee: if even one existing incident can't be safely deleted
    (a real procurement record references it), the whole replace-all aborts before
    touching anything — not a partial delete."""
    branch = _branch(admin_client, "Replace All Blocked")
    asset = _asset(admin_client, branch["id"], "AST-BULK-006")
    vendor = _vendor(admin_client, "Replace All Blocked Vendor")
    safe_incident = _incident(admin_client, branch["id"], "Safe to delete")
    blocked_incident = _incident(admin_client, branch["id"], "Blocked by quotation")
    db_session.add(Quotation(quotation_number="Q-REPLACE-BLOCK-1", incident_id=blocked_incident["id"],
                              asset_id=asset["id"], vendor_id=vendor["id"]))
    db_session.commit()
    before_count = db_session.query(Incident).count()

    file_bytes = _workbook_bytes([[
        "Item", "2025-04-01", branch["name"], "Local", "Vendor", "Would-be new row",
        "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None,
    ]])
    preview = admin_client.post(
        "/api/v1/incidents/replace-all/preview",
        files={"file": ("replace.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    assert len(preview["blocking_existing_incidents"]) == 1
    assert preview["blocking_existing_incidents"][0]["incident_id"] == blocked_incident["id"]

    response = admin_client.post(
        "/api/v1/incidents/replace-all",
        params={"confirm_replace": "true", "expected_current_count": preview["current_incident_count"]},
        files={"file": ("replace.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 409, response.text

    # Nothing changed at all — not even the safely-deletable incident.
    assert db_session.query(Incident).count() == before_count
    assert db_session.get(Incident, safe_incident["id"]) is not None
    assert db_session.get(Incident, blocked_incident["id"]) is not None
    assert db_session.query(Incident).filter(Incident.description == "Would-be new row").count() == 0


def test_replace_all_rejects_stale_confirmation(admin_client, db_session):
    branch = _branch(admin_client, "Replace All Stale")
    _incident(admin_client, branch["id"], "Will be counted")

    file_bytes = _workbook_bytes([[
        "Item", "2025-04-01", branch["name"], "Local", "Vendor", "New row",
        "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None,
    ]])
    response = admin_client.post(
        "/api/v1/incidents/replace-all",
        params={"confirm_replace": "true", "expected_current_count": 999999},  # deliberately wrong
        files={"file": ("replace.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 409
    assert "stale" in response.json()["detail"].lower()


def test_replace_all_requires_explicit_confirm_flag(admin_client, db_session):
    branch = _branch(admin_client, "Replace All No Confirm")
    file_bytes = _workbook_bytes([[
        "Item", "2025-04-01", branch["name"], "Local", "Vendor", "New row",
        "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None,
    ]])
    response = admin_client.post(
        "/api/v1/incidents/replace-all",
        params={"confirm_replace": "false", "expected_current_count": 0},
        files={"file": ("replace.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 400


def test_replace_all_cannot_touch_unrelated_tables(admin_client, db_session):
    branch = _branch(admin_client, "Replace All Unrelated")
    asset = _asset(admin_client, branch["id"], "AST-BULK-007")
    vendor = _vendor(admin_client, "Replace All Unrelated Vendor")
    _incident(admin_client, branch["id"], "Old one")

    file_bytes = _workbook_bytes([[
        "Item", "2025-04-01", branch["name"], "Local", "Vendor", "New one",
        "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None,
    ]])
    preview = admin_client.post(
        "/api/v1/incidents/replace-all/preview",
        files={"file": ("replace.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    admin_client.post(
        "/api/v1/incidents/replace-all",
        params={"confirm_replace": "true", "expected_current_count": preview["current_incident_count"]},
        files={"file": ("replace.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert db_session.get(Asset, asset["id"]) is not None
    assert db_session.get(Vendor, vendor["id"]) is not None
