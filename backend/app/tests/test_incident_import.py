"""Incident history import/export — extends the existing generic Import Center
(see app/services/import_service.py's "Incident history reconciliation" section)
rather than a parallel pipeline. These tests cover the matching tiers
(_match_incident) end-to-end through the real HTTP upload -> preview -> approve
flow, exactly the way an admin would use it.
"""
import io
from datetime import datetime

import openpyxl

from app.models.incident import Incident


def _build_workbook(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Incident History"
    sheet.append(["Incident history"])
    sheet.append([
        "Item name", "Date", "Unit", "Make", "Vendor", "Issue Description",
        "CER No", "Indent No", "PO No", "PO Closure date", "Status",
        "PO Cost - excl. GST", "PO GST %", "PO Amount",
        "Vendor Estimate Cost - excl. GST", "Vendor Estimate GST %", "Vendor Estimate Cost - incl. GST",
        "Remark",
    ])
    for row in rows:
        sheet.append(row)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _upload(admin_client, rows):
    file_bytes = _build_workbook(rows)
    response = admin_client.post(
        "/api/v1/imports",
        files={"file": ("incidents.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _branch(admin_client, name="Incident Import Branch"):
    response = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"})
    assert response.status_code == 201, response.text
    return response.json()


def test_new_incident_is_created_with_no_existing_match(admin_client, db_session):
    branch = _branch(admin_client, "Incident Import New")
    job = _upload(admin_client, [[
        "CPM", "2025-04-01", branch["name"], "OrthoFlex", "Aslam Engineers", "Motor not working",
        "NA", "NA", "NA", None, "Open",
        None, None, None, None, None, None, None,
    ]])
    assert job["summary"]["errors"] == 0

    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview").json()
    assert any(e["severity"] == "INFO" and "new incident" in e["message"] for e in preview["errors"])

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200
    assert approval.json()["summary"]["inserted"] == 1

    created = db_session.query(Incident).filter(Incident.branch_id == branch["id"]).first()
    assert created is not None
    assert created.description == "Motor not working"
    assert created.status == "OPEN"
    assert created.created_at.isoformat().startswith("2025-04-01")


def test_po_number_match_updates_existing_incident_and_corrects_date(admin_client, db_session):
    branch = _branch(admin_client, "Incident Import PO Match")
    existing = Incident(
        branch_id=branch["id"], description="Stim board not working", status="OPEN",
        po_number="PO25MAH00302",
    )
    db_session.add(existing)
    db_session.commit()
    wrong_original_date = existing.created_at.isoformat()

    job = _upload(admin_client, [[
        "Intelect mobile combo", "2025-04-01", branch["name"], "Chattanooga", "Orbimed",
        "A totally different description — must not block the PO match",
        "NA", "NA", "PO25MAH00302", None, "Closed",
        15840, 0.18, 18691.2, None, None, None, "Matched by PO",
    ]])
    assert job["summary"]["errors"] == 0

    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview").json()
    assert any(e["severity"] == "INFO" and "Matched existing incident by PO" in e["message"] for e in preview["errors"])

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200
    assert approval.json()["summary"]["updated"] == 1
    assert approval.json()["summary"]["inserted"] == 0

    db_session.refresh(existing)
    assert existing.status == "OPERATIONAL"
    assert existing.po_cost_excl_gst == 15840
    # The PO match must win even though the description text is unrelated —
    # description is never used to override/reject an exact-key match.
    assert existing.description == "Stim board not working"
    assert existing.created_at.isoformat() != wrong_original_date
    assert existing.created_at.isoformat().startswith("2025-04-01")


def test_branch_and_description_match_when_no_external_keys_present(admin_client, db_session):
    branch = _branch(admin_client, "Incident Import Desc Match")
    existing = Incident(branch_id=branch["id"], description="Cooling pump not working", status="OPEN")
    db_session.add(existing)
    db_session.commit()

    job = _upload(admin_client, [[
        "Game ready machine", "2025-04-01", branch["name"], "Game Ready", "Aslam Engineers",
        "Cooling pump not working",
        "NA", "NA", "NA", None, "Closed",
        None, None, None, None, None, None, None,
    ]])
    assert job["summary"]["errors"] == 0

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200
    assert approval.json()["summary"]["updated"] == 1

    db_session.refresh(existing)
    assert existing.status == "OPERATIONAL"


def test_ambiguous_description_match_is_not_imported(admin_client, db_session):
    branch = _branch(admin_client, "Incident Import Ambiguous")
    first = Incident(branch_id=branch["id"], description="Identical wording on purpose", status="OPEN")
    second = Incident(branch_id=branch["id"], description="Identical wording on purpose", status="OPEN")
    db_session.add_all([first, second])
    db_session.commit()

    job = _upload(admin_client, [[
        "Static cycle", "2025-04-01", branch["name"], "Local", "Aslam Engineers",
        "Identical wording on purpose",
        "NA", "NA", "NA", None, "Closed",
        None, None, None, None, None, None, None,
    ]])
    # Ambiguous rows are an ERROR at validation time — never auto-applied.
    assert job["summary"]["errors"] == 1

    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview").json()
    assert any(e["severity"] == "ERROR" and "ambiguously" in e["message"] for e in preview["errors"])

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200
    assert approval.json()["summary"]["inserted"] == 0
    assert approval.json()["summary"]["updated"] == 0
    assert approval.json()["summary"]["skipped"] == 1

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.status == "OPEN"
    assert second.status == "OPEN"


def test_unresolved_branch_is_a_validation_error(admin_client):
    job = _upload(admin_client, [[
        "Static cycle", "2025-04-01", "No Such Branch At All", "Local", "Aslam Engineers",
        "Some issue",
        "NA", "NA", "NA", None, "Closed",
        None, None, None, None, None, None, None,
    ]])
    assert job["summary"]["errors"] == 1
    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview").json()
    assert any(e["field"] == "branch_code" and e["severity"] == "ERROR" for e in preview["errors"])


def test_unrecognized_status_is_a_validation_error(admin_client):
    branch = _branch(admin_client, "Incident Import Bad Status")
    job = _upload(admin_client, [[
        "Static cycle", "2025-04-01", branch["name"], "Local", "Aslam Engineers",
        "Some issue",
        "NA", "NA", "NA", None, "Somehow Fixed Itself",
        None, None, None, None, None, None, None,
    ]])
    assert job["summary"]["errors"] == 1
    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview").json()
    assert any(e["field"] == "status" and e["severity"] == "ERROR" for e in preview["errors"])


def test_na_tokens_are_treated_as_blank_not_as_literal_values(admin_client, db_session):
    """Workbook convention: 'NA' means "no CER/Indent/PO number", not the literal
    string "NA" — already handled generically by imports/validators.py's is_blank(),
    confirmed here specifically for the incident sheet's three reference-number
    columns so they never collide with each other across unrelated rows."""
    branch = _branch(admin_client, "Incident Import NA Tokens")
    job = _upload(admin_client, [
        [
            "Item A", "2025-04-01", branch["name"], "Local", "Vendor A", "First issue",
            "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None,
        ],
        [
            "Item B", "2025-04-02", branch["name"], "Local", "Vendor B", "Second issue",
            "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None,
        ],
    ])
    assert job["summary"]["errors"] == 0

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200
    assert approval.json()["summary"]["inserted"] == 2

    rows = db_session.query(Incident).filter(Incident.branch_id == branch["id"]).all()
    assert len(rows) == 2
    assert all(r.po_number is None and r.cer_number is None and r.indent_number is None for r in rows)


def test_real_xlsx_date_typed_cell_does_not_crash_the_json_mapped_data_column(admin_client, db_session):
    """Regression guard: a real workbook's Date column comes through openpyxl as an
    actual `datetime` object (not text) — confirmed by inspecting the user's real
    source file. ImportRow.mapped_data is a JSON column, and a raw Python `datetime`
    isn't JSON-serializable; this must be converted (see imports/validators.py's
    map_row) before it's ever stored, not just when the test happens to use a string
    literal for the date cell like every other test in this file does."""
    branch = _branch(admin_client, "Incident Import Real Date Cell")
    file_bytes = _build_workbook([[
        "Static cycle", datetime(2025, 4, 1), branch["name"], "Local", "Aslam Engineers",
        "Sound issue in motor",
        "NA", "NA", "NA", None, "Closed",
        None, None, None, None, None, None, None,
    ]])
    response = admin_client.post(
        "/api/v1/imports",
        files={"file": ("real_dates.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["summary"]["errors"] == 0

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200, approval.text
    assert approval.json()["summary"]["inserted"] == 1

    created = db_session.query(Incident).filter(Incident.branch_id == branch["id"]).first()
    assert created.created_at.isoformat().startswith("2025-04-01")


def test_matching_zero_as_int_and_float_is_not_reported_as_a_change(admin_client, db_session):
    """Regression guard: an existing incident's po_cost_excl_gst stored as 0.0 (DB
    float) vs. the workbook's 0 (openpyxl int) must not be reported as a diff or
    treated as a change — a naive str() comparison sees '0.0' != '0' and would flag
    every untouched numeric field as "changed" on every description-tier match."""
    branch = _branch(admin_client, "Incident Import Numeric Noise")
    existing = Incident(
        branch_id=branch["id"], description="Numeric noise check", status="OPEN",
        po_cost_excl_gst=0.0, po_gst_percent=0.0, po_amount=0.0,
        created_at=datetime(2025, 4, 1),
    )
    db_session.add(existing)
    db_session.commit()

    job = _upload(admin_client, [[
        "Item", "2025-04-01", branch["name"], "Local", "Vendor",
        "Numeric noise check",
        "NA", "NA", "NA", None, "Open",
        0, 0, 0, None, None, None, None,
    ]])
    assert job["summary"]["errors"] == 0

    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview").json()
    matched_msg = next(e for e in preview["errors"] if "Matched existing incident by DESCRIPTION" in e["message"])
    assert "0 field(s) will change" in matched_msg["message"]


# ---- Export ----

def test_export_incidents_returns_xlsx_with_matching_columns(admin_client, db_session):
    branch = _branch(admin_client, "Incident Export Branch")
    incident = Incident(
        branch_id=branch["id"], description="Export round-trip check", status="OPERATIONAL",
        po_number="PO-EXPORT-1",
    )
    db_session.add(incident)
    db_session.commit()

    response = admin_client.get("/api/v1/incidents/export", params={"branch_id": branch["id"]})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    wb = openpyxl.load_workbook(io.BytesIO(response.content))
    ws = wb.active
    header_row = [c.value for c in ws[1]]
    assert header_row[:6] == ["Item Name", "Date", "Unit", "Make", "Vendor", "Issue Description"]

    data_row = [c.value for c in ws[2]]
    row_dict = dict(zip(header_row, data_row))
    assert row_dict["Unit"] == branch["name"]
    assert row_dict["Issue Description"] == "Export round-trip check"
    assert row_dict["Status"] == "Closed"
    assert row_dict["PO No"] == "PO-EXPORT-1"


def test_export_incidents_is_branch_scoped(admin_client, client, db_session):
    from .conftest import auth_headers, create_user

    mine = _branch(admin_client, "Incident Export Mine")
    theirs = _branch(admin_client, "Incident Export Theirs")
    db_session.add_all([
        Incident(branch_id=mine["id"], description="Mine", status="OPEN"),
        Incident(branch_id=theirs["id"], description="Theirs", status="OPEN"),
    ])
    db_session.commit()

    from app.models.identity import Role
    role = db_session.query(Role).filter(Role.code == "UNIT_HEAD").first()
    codes = sorted({rp.permission.code for rp in role.permissions} | {"incident.read"})
    admin_client.patch(f"/api/v1/roles/{role.id}", json={"permission_codes": codes})

    user = create_user(db_session, "export.scope.uh@example.com", "UNIT_HEAD", "Password123!")
    user.hospital_scope_id = mine["id"]
    db_session.add(user)
    db_session.commit()
    headers = auth_headers(client, "export.scope.uh@example.com", "Password123!")

    response = client.get("/api/v1/incidents/export", headers=headers)
    assert response.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(response.content))
    ws = wb.active
    descriptions = [row[5].value for row in ws.iter_rows(min_row=2)]
    assert "Mine" in descriptions
    assert "Theirs" not in descriptions
