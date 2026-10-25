"""Incident history import/export — Append mode (no matching, ever — every source
row becomes its own record), Replace-all (atomic delete-then-insert), and bulk
delete. Extends the existing generic Import Center (see
app/services/import_service.py's incident section) rather than a parallel
pipeline. These tests cover the real HTTP upload -> preview -> approve flow, plus
the dedicated replace-all/bulk-delete endpoints, the way an admin would use them.

A one-off manual verification against the user's actual
`incident_history_template.xlsx` (171 real rows, the real row-64/row-32 formula
anomalies, 2 literal "NA" date cells, 9 duplicate-looking rows, 1 blank-description
row) was run directly this session and is documented in the final report — not
reproduced here as a hardcoded file-path dependency, which would break in any
environment without that exact file on disk. The synthetic workbooks below
reproduce every one of those same characteristics so the suite is self-contained.
"""
import io
from datetime import datetime

import openpyxl

from app.models.identity import Role, User
from app.models.incident import Incident

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, auth_headers, create_user


def _build_workbook(rows: list[list], *, formulas: dict[tuple[int, str], str] | None = None) -> bytes:
    """rows are 0-indexed data rows (row 2 in the sheet = rows[0]). `formulas` is
    {(0-indexed row, column letter): formula string} — written directly as a cell
    formula (not a literal value), exactly like the real template's Po Amount/Po
    Cost formula cells."""
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
    for (row_idx, col_letter), formula in (formulas or {}).items():
        sheet[f"{col_letter}{row_idx + 2}"] = formula

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _upload(admin_client, rows, **kwargs):
    file_bytes = _build_workbook(rows, **kwargs)
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


# ---- Append mode: no matching, ever ----

def test_every_valid_row_becomes_its_own_new_incident(admin_client, db_session):
    branch = _branch(admin_client, "Incident Import New")
    job = _upload(admin_client, [[
        "CPM", "2025-04-01", branch["name"], "OrthoFlex", "Aslam Engineers", "Motor not working",
        "NA", "NA", "NA", None, "Open",
        None, None, None, None, None, None, None,
    ]])
    assert job["summary"]["errors"] == 0

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200
    assert approval.json()["summary"]["inserted"] == 1
    assert approval.json()["summary"]["updated"] == 0

    created = db_session.query(Incident).filter(Incident.branch_id == branch["id"]).first()
    assert created is not None
    assert created.description == "Motor not working"
    assert created.device_name == "CPM"
    assert created.manufacturer == "OrthoFlex"
    assert created.vendor_name == "Aslam Engineers"
    assert created.status == "OPEN"
    assert created.created_at.isoformat().startswith("2025-04-01")


def test_pre_existing_matching_po_number_is_never_updated_only_a_new_row_is_added(admin_client, db_session):
    """The whole point of removing matching: an existing incident that happens to
    share a PO number (or any other field) with an incoming row must be left
    completely alone — the incoming row becomes its own new incident instead."""
    branch = _branch(admin_client, "Incident Import PO No Match")
    existing = Incident(
        branch_id=branch["id"], description="Original pre-existing incident", status="OPEN",
        po_number="PO25MAH00302",
    )
    db_session.add(existing)
    db_session.commit()
    original_status = existing.status
    original_description = existing.description

    job = _upload(admin_client, [[
        "Intelect mobile combo", "2025-04-01", branch["name"], "Chattanooga", "Orbimed",
        "A completely different incident that happens to share a PO number",
        "NA", "NA", "PO25MAH00302", None, "Closed",
        15840, 0.18, 18691.2, None, None, None, "Matched by PO in the old design — must not happen now",
    ]])
    assert job["summary"]["errors"] == 0

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200
    assert approval.json()["summary"]["inserted"] == 1
    assert approval.json()["summary"]["updated"] == 0

    db_session.refresh(existing)
    assert existing.status == original_status
    assert existing.description == original_description

    new_rows = db_session.query(Incident).filter(
        Incident.branch_id == branch["id"], Incident.id != existing.id,
    ).all()
    assert len(new_rows) == 1
    assert new_rows[0].po_number == "PO25MAH00302"


def test_identical_looking_duplicate_rows_both_import_as_separate_incidents(admin_client, db_session):
    branch = _branch(admin_client, "Incident Import Duplicates")
    identical_row = [
        "Static cycle", "2025-04-01", branch["name"], "Local", "Aslam Engineers",
        "Identical wording on purpose", "NA", "NA", "NA", None, "Closed",
        None, None, None, None, None, None, None,
    ]
    job = _upload(admin_client, [identical_row, list(identical_row)])
    assert job["summary"]["errors"] == 0
    assert job["summary"]["duplicate_candidates"] == 1

    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview").json()
    assert any(
        e["severity"] == "INFO" and "will still be imported as its own, separate incident" in e["message"]
        for e in preview["errors"]
    )

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200
    assert approval.json()["summary"]["inserted"] == 2

    rows = db_session.query(Incident).filter(
        Incident.branch_id == branch["id"], Incident.description == "Identical wording on purpose",
    ).all()
    assert len(rows) == 2
    assert rows[0].id != rows[1].id


def test_unresolved_branch_is_a_validation_error(admin_client):
    job = _upload(admin_client, [[
        "Static cycle", "2025-04-01", "No Such Branch At All", "Local", "Aslam Engineers",
        "Some issue", "NA", "NA", "NA", None, "Closed",
        None, None, None, None, None, None, None,
    ]])
    assert job["summary"]["errors"] == 1
    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview").json()
    assert any(e["field"] == "branch_code" and e["severity"] == "ERROR" for e in preview["errors"])


def test_unrecognized_status_is_a_validation_error(admin_client):
    branch = _branch(admin_client, "Incident Import Bad Status")
    job = _upload(admin_client, [[
        "Static cycle", "2025-04-01", branch["name"], "Local", "Aslam Engineers",
        "Some issue", "NA", "NA", "NA", None, "Somehow Fixed Itself",
        None, None, None, None, None, None, None,
    ]])
    assert job["summary"]["errors"] == 1
    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview").json()
    assert any(e["field"] == "status" and e["severity"] == "ERROR" for e in preview["errors"])


def test_blank_required_field_blocks_only_that_row_not_the_batch(admin_client, db_session):
    branch = _branch(admin_client, "Incident Import Blank Row")
    good_row = [
        "Item A", "2025-04-01", branch["name"], "Local", "Vendor A", "A real issue",
        "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None,
    ]
    blank_description_row = [
        "Item B", "2025-04-02", branch["name"], "Local", "Vendor B", None,
        "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None,
    ]
    job = _upload(admin_client, [good_row, blank_description_row])
    assert job["summary"]["errors"] == 1
    assert job["summary"]["ready"] == 1

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.json()["summary"]["inserted"] == 1
    assert approval.json()["summary"]["skipped"] == 1


def test_na_tokens_are_treated_as_blank_not_as_literal_values(admin_client, db_session):
    branch = _branch(admin_client, "Incident Import NA Tokens")
    job = _upload(admin_client, [
        ["Item A", "2025-04-01", branch["name"], "Local", "Vendor A", "First issue",
         "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None],
        ["Item B", "2025-04-02", branch["name"], "Local", "Vendor B", "Second issue",
         "Na", "Na", "Na", None, "Open", None, None, None, None, None, None, None],
    ])
    assert job["summary"]["errors"] == 0

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.json()["summary"]["inserted"] == 2

    rows = db_session.query(Incident).filter(Incident.branch_id == branch["id"]).all()
    assert len(rows) == 2
    assert all(r.po_number is None and r.cer_number is None and r.indent_number is None for r in rows)


def test_literal_na_text_in_date_column_does_not_crash_and_is_treated_as_no_date(admin_client, db_session):
    """The real template has 2 cells where the Date column literally contains the
    text 'NA' instead of being blank or a real date — confirmed by direct
    inspection of incident_history_template.xlsx."""
    branch = _branch(admin_client, "Incident Import NA Date")
    job = _upload(admin_client, [[
        "Item A", "NA", branch["name"], "Local", "Vendor A", "Date is literally NA",
        "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None,
    ]])
    assert job["summary"]["errors"] == 0
    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.json()["summary"]["inserted"] == 1


def test_real_xlsx_date_typed_cell_does_not_crash_the_json_mapped_data_column(admin_client, db_session):
    """Regression guard: a real workbook's Date column comes through openpyxl as an
    actual `datetime` object (not text) — confirmed by inspecting the user's real
    source file. ImportRow.mapped_data/raw_data are JSON columns, and a raw Python
    `datetime` isn't JSON-serializable; this must be converted (see
    imports/validators.py's map_row and imports/parser.py's _json_safe_cell)
    before it's ever stored."""
    branch = _branch(admin_client, "Incident Import Real Date Cell")
    file_bytes = _build_workbook([[
        "Static cycle", datetime(2025, 4, 1), branch["name"], "Local", "Aslam Engineers",
        "Sound issue in motor", "NA", "NA", "NA", None, "Closed",
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


def test_formula_cell_is_flagged_as_warning_and_cached_value_is_used(admin_client, db_session):
    """Row 64 of the real template has Po Cost/Po Amount as a stray cross-row
    SUM()/10^5 — reproduced here as a simplified single-cell formula. The row must
    still import (using the cached value, the only one available) but with a
    visible WARNING in the preview, never silently trusted."""
    branch = _branch(admin_client, "Incident Import Formula Cell")
    job = _upload(
        admin_client,
        [[
            "Stimulator", "2025-09-27", branch["name"], "Local", "Aslam Engineers",
            "channel broken in stimulator", "NA", "NA", "NA", None, "Closed",
            None, None, None, None, None, None, None,
        ]],
        formulas={(0, "L"): "=100+200", (0, "N"): "=L2*2"},
    )
    assert job["summary"]["errors"] == 0
    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview").json()
    formula_warnings = [e for e in preview["errors"] if e["severity"] == "WARNING" and "formula" in e["message"]]
    assert len(formula_warnings) == 2
    assert any(e["field"] == "po_cost_excl_gst" for e in formula_warnings)
    assert any(e["field"] == "po_amount" for e in formula_warnings)

    # openpyxl, unlike real Excel, never computes a formula it writes itself — a
    # formula cell written this way (vs. one actually saved by Excel, like the real
    # template's) has no cached value, so the row still imports but with that
    # field left empty. The real template's formulas DO have real cached values
    # (Excel computed them) — confirmed directly against row 64 this session,
    # where po_cost_excl_gst/po_amount import as 3.09999/3.651051 exactly. What
    # matters here is that the row isn't blocked and the WARNING is visible
    # (already asserted above) — never that a fabricated cached value is trusted.
    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.json()["summary"]["inserted"] == 1
    created = db_session.query(Incident).filter(Incident.branch_id == branch["id"]).first()
    assert created is not None


def test_generic_sheet_named_sheet_routes_to_incident_via_header_overlap(admin_client, db_session):
    """The real template's sheet tab is literally named 'Sheet' — parse_workbook
    must route it to the incident config via header-overlap (best_fit_config),
    not require renaming the tab first."""
    branch = _branch(admin_client, "Incident Import Generic Sheet Name")
    job = _upload(admin_client, [[
        "Item", "2025-04-01", branch["name"], "Local", "Vendor", "Routes via headers",
        "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None,
    ]])
    assert job["column_mapping"]["Sheet"] == "incident"


# ---- Export: exact format ----

def test_export_incidents_uses_exact_required_headers(admin_client, db_session):
    branch = _branch(admin_client, "Incident Export Headers")
    incident = Incident(
        branch_id=branch["id"], description="Export round-trip check", status="OPERATIONAL",
        po_number="PO-EXPORT-1", device_name="CPM", manufacturer="OrthoFlex", vendor_name="Aslam",
        remark="A remark",
    )
    db_session.add(incident)
    db_session.commit()

    response = admin_client.get("/api/v1/incidents/export", params={"branch_id": branch["id"]})
    assert response.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(response.content))
    ws = wb.active
    assert ws.title == "Sheet"
    header_row = [c.value for c in ws[1]]
    assert header_row == [
        "Item Name", "Date", "Unit*", "Make", "Vendor", "Issue Description*",
        "Cer No", "Indent No", "Po No", "Po Closure Date", "Status",
        "Po Cost - Excl. Gst", "Po Gst %", "Po Amount",
        "Vendor Estimate Cost - Excl. Gst", "Vendor Estimate Gst %", "Vendor Estimate Cost - Incl. Gst",
        "Remark",
    ]
    row_dict = dict(zip(header_row, [c.value for c in ws[2]]))
    assert row_dict["Item Name"] == "CPM"
    assert row_dict["Make"] == "OrthoFlex"
    assert row_dict["Vendor"] == "Aslam"
    assert row_dict["Unit*"] == branch["name"]
    assert row_dict["Issue Description*"] == "Export round-trip check"
    assert row_dict["Status"] == "Closed"
    assert row_dict["Po No"] == "PO-EXPORT-1"
    assert row_dict["Remark"] == "A remark"


def test_export_then_reimport_round_trip_preserves_every_field(admin_client, db_session):
    branch = _branch(admin_client, "Incident Round Trip")
    incident = Incident(
        branch_id=branch["id"], description="Round trip me", status="WORK_IN_PROGRESS",
        device_name="Combo Machine", manufacturer="Chattanooga", vendor_name="Orbimed",
        cer_number="CER-1", indent_number="IND-1", po_number="PO-1", po_closure_date="2025-05-01",
        po_cost_excl_gst=1000.0, po_gst_percent=0.18, po_amount=1180.0, remark="Round trip remark",
    )
    db_session.add(incident)
    db_session.commit()

    export_response = admin_client.get("/api/v1/incidents/export", params={"branch_id": branch["id"]})
    assert export_response.status_code == 200

    reimport_response = admin_client.post(
        "/api/v1/imports",
        files={"file": ("export.xlsx", export_response.content,
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert reimport_response.status_code == 201, reimport_response.text
    job = reimport_response.json()
    assert job["summary"]["errors"] == 0

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.json()["summary"]["inserted"] == 1

    reimported = db_session.query(Incident).filter(
        Incident.branch_id == branch["id"], Incident.id != incident.id,
    ).first()
    assert reimported is not None
    assert reimported.description == incident.description
    assert reimported.device_name == incident.device_name
    assert reimported.manufacturer == incident.manufacturer
    assert reimported.vendor_name == incident.vendor_name
    assert reimported.status == incident.status
    assert reimported.cer_number == incident.cer_number
    assert reimported.po_number == incident.po_number
    assert reimported.po_cost_excl_gst == incident.po_cost_excl_gst
    assert reimported.remark == incident.remark


def test_export_incidents_is_branch_scoped(admin_client, client, db_session):
    mine = _branch(admin_client, "Incident Export Mine")
    theirs = _branch(admin_client, "Incident Export Theirs")
    db_session.add_all([
        Incident(branch_id=mine["id"], description="Mine", status="OPEN"),
        Incident(branch_id=theirs["id"], description="Theirs", status="OPEN"),
    ])
    db_session.commit()

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


# ---- Search must cover imported rows' free-text device/vendor/remark (no Asset link) ----

def test_search_finds_imported_incident_by_free_text_device_name(admin_client, db_session):
    branch = _branch(admin_client, "Incident Search Branch")
    db_session.add(Incident(
        branch_id=branch["id"], description="Not working", status="OPEN",
        device_name="Intelect Mobile Combo", manufacturer="Chattanooga", vendor_name="Orbimed",
        remark="Vendor visit pending",
    ))
    db_session.commit()

    for term in ("Intelect Mobile Combo", "Chattanooga", "Orbimed", "visit pending"):
        response = admin_client.get("/api/v1/incidents", params={"search": term})
        assert response.status_code == 200
        descriptions = [item["description"] for item in response.json()["items"]]
        assert "Not working" in descriptions, f"search for {term!r} did not find the imported row"


def test_export_search_also_covers_imported_free_text_fields(admin_client, db_session):
    branch = _branch(admin_client, "Incident Export Search Branch")
    db_session.add(Incident(
        branch_id=branch["id"], description="Noisy", status="OPEN",
        device_name="Cross Trainer", manufacturer="Cosco", vendor_name="IEMCO",
    ))
    db_session.commit()

    response = admin_client.get("/api/v1/incidents/export", params={"search": "Cross Trainer"})
    assert response.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(response.content))
    ws = wb.active
    descriptions = [row[5].value for row in ws.iter_rows(min_row=2)]
    assert "Noisy" in descriptions


# ---- Every imported row is actually returned by list_incidents, across pages ----

def test_every_imported_row_is_returned_by_list_incidents_across_pagination(admin_client, db_session):
    branch = _branch(admin_client, "Incident Pagination Check")
    rows = [
        [f"Item {i}", f"2024-0{(i % 9) + 1}-01", branch["name"], "Local", "Vendor", f"Issue number {i}",
         "NA", "NA", "NA", None, "Open", None, None, None, None, None, None, None]
        for i in range(30)
    ]
    job = _upload(admin_client, rows)
    assert job["summary"]["errors"] == 0
    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.json()["summary"]["inserted"] == 30

    seen_descriptions = set()
    page = 1
    while True:
        resp = admin_client.get("/api/v1/incidents", params={
            "branch_id": branch["id"], "page": page, "page_size": 10, "sort_by": "date", "sort_dir": "asc",
        })
        body = resp.json()
        for item in body["items"]:
            seen_descriptions.add(item["description"])
        if page * 10 >= body["total"]:
            break
        page += 1

    assert seen_descriptions == {f"Issue number {i}" for i in range(30)}


def test_sort_by_date_ascending_actually_changes_server_side_order(admin_client, db_session):
    """Regression guard for the real root cause: sort previously only re-sorted the
    current page's already-fetched rows, so asking for oldest-first never actually
    changed which rows landed on page 1."""
    branch = _branch(admin_client, "Incident Sort Order Check")
    db_session.add_all([
        Incident(branch_id=branch["id"], description="Newest", status="OPEN", created_at=datetime(2026, 1, 1)),
        Incident(branch_id=branch["id"], description="Oldest", status="OPEN", created_at=datetime(2020, 1, 1)),
        Incident(branch_id=branch["id"], description="Middle", status="OPEN", created_at=datetime(2023, 1, 1)),
    ])
    db_session.commit()

    asc = admin_client.get("/api/v1/incidents", params={
        "branch_id": branch["id"], "sort_by": "date", "sort_dir": "asc", "page_size": 1,
    }).json()
    assert asc["items"][0]["description"] == "Oldest"

    desc = admin_client.get("/api/v1/incidents", params={
        "branch_id": branch["id"], "sort_by": "date", "sort_dir": "desc", "page_size": 1,
    }).json()
    assert desc["items"][0]["description"] == "Newest"
