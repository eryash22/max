"""Tests for the reusable Data Export system (Import & Export Enhancement task).

Exercised primarily against the Vendors export (simplest, no cross-module joins) for
the eight generic categories this task asks for, plus one test each against Assets
(the rich, cross-module-joined example this task specified in detail) and Inventory
Items — proving the shared `exports/writer.py` + `services/export_service.py` core is
genuinely reused across modules, not a one-off.
"""

import csv
import io

import openpyxl

from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _make_vendor(admin_client, name="Export Test Vendor", **overrides):
    payload = {"name": name, "vendor_type": "OEM", "status": "ACTIVE", "city": "Pune"}
    payload.update(overrides)
    response = admin_client.post("/api/v1/vendors", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _make_branch(admin_client, name="Export-Branch"):
    response = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"})
    assert response.status_code == 201, response.text
    return response.json()


def _read_xlsx_rows(content: bytes) -> list[list]:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    sheet = wb.active
    return [list(row) for row in sheet.iter_rows(values_only=True)]


def _read_csv_rows(content: bytes) -> list[list[str]]:
    text = content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def _engineer_headers(client, db_session, email="exporteng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _doctor_headers(client, db_session, email="exportdoc@example.com"):
    create_user(db_session, email, "DOCTOR", "Password123!")
    return auth_headers(client, email, "Password123!")


# ---- 1/2: XLSX / CSV export ----

def test_vendor_export_xlsx(admin_client):
    _make_vendor(admin_client, "XLSX Export Vendor")
    response = admin_client.get("/api/v1/vendors/export?format=xlsx")
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    assert "vendors_export.xlsx" in response.headers["content-disposition"]

    rows = _read_xlsx_rows(response.content)
    assert rows[0] == [
        "Vendor Name", "Vendor Type", "OEM", "Authorized Distributor", "Service Vendor",
        "City", "State", "Email", "Phone", "Website", "Support Coverage", "SLA Response Hours",
        "Status",
    ]
    assert any(r[0] == "XLSX Export Vendor" for r in rows[1:])


def test_vendor_export_csv(admin_client):
    _make_vendor(admin_client, "CSV Export Vendor")
    response = admin_client.get("/api/v1/vendors/export?format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "vendors_export.csv" in response.headers["content-disposition"]

    rows = _read_csv_rows(response.content)
    assert rows[0][0] == "Vendor Name"
    assert any(r[0] == "CSV Export Vendor" for r in rows[1:])


# ---- 3/4: filtered / search export — only matching records, not the visible page ----

def test_vendor_export_respects_status_filter(admin_client):
    _make_vendor(admin_client, "Active Export Vendor", status="ACTIVE")
    inactive = _make_vendor(admin_client, "Inactive Export Vendor", status="INACTIVE")

    response = admin_client.get("/api/v1/vendors/export?format=csv&status_filter=ACTIVE")
    rows = _read_csv_rows(response.content)
    names = [r[0] for r in rows[1:]]
    assert "Active Export Vendor" in names
    assert "Inactive Export Vendor" not in names
    assert inactive["status"] == "INACTIVE"  # sanity check on the fixture itself


def test_vendor_export_respects_search(admin_client):
    _make_vendor(admin_client, "Zephyr Medical Supplies")
    _make_vendor(admin_client, "Totally Different Co")

    response = admin_client.get("/api/v1/vendors/export?format=csv&search=Zephyr")
    rows = _read_csv_rows(response.content)
    names = [r[0] for r in rows[1:]]
    assert names == ["Zephyr Medical Supplies"]


def test_vendor_export_not_limited_to_one_page(admin_client):
    for i in range(30):
        _make_vendor(admin_client, f"Bulk Export Vendor {i}")

    # The list endpoint's default page size is 25 — the export must not be capped there.
    list_response = admin_client.get("/api/v1/vendors?page_size=25")
    assert list_response.json()["total"] >= 30

    export_response = admin_client.get("/api/v1/vendors/export?format=csv&search=Bulk Export Vendor")
    rows = _read_csv_rows(export_response.content)
    assert len(rows) - 1 == 30  # header + all 30, not just the first page


# ---- 5: RBAC export restriction ----

def test_export_requires_same_permission_as_list(client, db_session):
    # Final admin/inventory/vendor/dashboard polish pass — Part 5: Doctor now holds
    # `vendor.read_limited` (net-new grant) and so no longer 403s here — see
    # test_vendor_visibility.py for that role's masked-read coverage. MANAGER holds
    # neither `vendor.read` nor `vendor.read_limited`, so it's the role that still
    # demonstrates "export requires the same permission gate as list."
    create_user(db_session, "mgr-export@example.com", "MANAGER", "Password123!")
    headers = auth_headers(client, "mgr-export@example.com", "Password123!")
    response = client.get("/api/v1/vendors/export?format=csv", headers=headers)
    assert response.status_code == 403

    list_response = client.get("/api/v1/vendors", headers=headers)
    assert list_response.status_code == 403  # same permission gate, same outcome


# ---------------------------------------------------------------------------
# Security hardening pass — CSV/XLSX formula injection
# ---------------------------------------------------------------------------

def test_cell_neutralizes_formula_trigger_characters():
    from app.exports.writer import _cell

    for dangerous in ("=cmd|'/c calc'!A1", "+SUM(1+1)", "-2+3", "@SUM(1+1)", "\tSUM(1+1)"):
        assert _cell(dangerous) == "'" + dangerous

    # Ordinary values are passed through untouched.
    assert _cell("Acme Vendor") == "Acme Vendor"
    assert _cell(None) == ""
    assert _cell(42) == 42
    assert _cell(3.14) == 3.14
    assert _cell(True) is True


def test_vendor_export_neutralizes_formula_injection_in_csv(admin_client):
    _make_vendor(admin_client, "=cmd|'/c calc'!A1")
    response = admin_client.get("/api/v1/vendors/export?format=csv")
    assert response.status_code == 200
    rows = _read_csv_rows(response.content)
    names = [r[0] for r in rows[1:]]
    # Never the raw, still-a-formula value...
    assert "=cmd|'/c calc'!A1" not in names
    # ...but neutralized with a leading quote so Excel/Sheets treats it as literal text.
    assert "'=cmd|'/c calc'!A1" in names


def test_vendor_export_neutralizes_formula_injection_in_xlsx(admin_client):
    _make_vendor(admin_client, "@SUM(1+1)*evil()")
    response = admin_client.get("/api/v1/vendors/export?format=xlsx")
    assert response.status_code == 200
    rows = _read_xlsx_rows(response.content)
    names = [r[0] for r in rows[1:]]
    assert "@SUM(1+1)*evil()" not in names
    assert "'@SUM(1+1)*evil()" in names


def test_engineer_can_export_vendors_read_only(client, db_session):
    headers = _engineer_headers(client, db_session)  # Engineer holds vendor.read
    response = client.get("/api/v1/vendors/export?format=csv", headers=headers)
    assert response.status_code == 200


# ---- 6: export audit log ----

def test_export_writes_audit_log_with_user_module_filters_format_count(admin_client, db_session):
    _make_vendor(admin_client, "Audited Export Vendor")
    response = admin_client.get("/api/v1/vendors/export?format=xlsx&status_filter=ACTIVE")
    assert response.status_code == 200

    log = db_session.query(AuditLog).filter(AuditLog.action == "EXPORT", AuditLog.entity == "vendors_export").first()
    assert log is not None
    assert log.user_id is not None
    assert log.new_value["module"] == "vendors"
    assert log.new_value["format"] == "xlsx"
    assert log.new_value["filters"]["status_filter"] == "ACTIVE"
    assert log.new_value["record_count"] >= 1


# ---- 7: empty export ----

def test_export_with_no_matching_records_returns_headers_only(admin_client):
    response = admin_client.get("/api/v1/vendors/export?format=csv&search=NoVendorMatchesThisSearchTerm")
    assert response.status_code == 200
    rows = _read_csv_rows(response.content)
    assert len(rows) == 1  # header row only
    assert rows[0][0] == "Vendor Name"


# ---- 8: invalid filter / invalid format ----

def test_export_invalid_format_rejected(admin_client):
    response = admin_client.get("/api/v1/vendors/export?format=pdf")
    assert response.status_code == 400


def test_export_invalid_status_filter_value_returns_empty_not_error(admin_client):
    # status_filter is a plain equality filter (same as the list endpoint) — an
    # unrecognized value simply matches nothing, it isn't a 422/500.
    _make_vendor(admin_client, "Valid Status Vendor")
    response = admin_client.get("/api/v1/vendors/export?format=csv&status_filter=NOT_A_REAL_STATUS")
    assert response.status_code == 200
    rows = _read_csv_rows(response.content)
    assert len(rows) == 1


# ---- Reusability: the same shared core wired into other modules ----

def test_asset_export_includes_joined_columns(admin_client):
    branch = _make_branch(admin_client, "Asset-Export-Branch")
    model_response = admin_client.post("/api/v1/device-models", json={"name": "Export Model", "category": "PATIENT_MONITOR"})
    model = model_response.json()
    asset_response = admin_client.post("/api/v1/assets", json={
        "asset_number": "EXPORT-ASSET-0001", "device_model_id": model["id"], "branch_id": branch["id"], "status": "OPERATIONAL",
    })
    assert asset_response.status_code == 201

    response = admin_client.get(f"/api/v1/assets/export?format=csv&branch_id={branch['id']}")
    assert response.status_code == 200
    rows = _read_csv_rows(response.content)
    assert rows[0] == [
        "Asset Number", "Serial Number", "Device Name", "Manufacturer", "Model",
        "Branch", "Department", "Location", "Status", "Installation Date",
        "AMC Status", "CMC Status", "Warranty Status",
        "Last PM", "Next PM", "Last Calibration", "Next Calibration",
    ]
    row = next(r for r in rows[1:] if r[0] == "EXPORT-ASSET-0001")
    assert row[2] == "Export Model"
    assert row[5] == "Asset-Export-Branch"
    assert row[10] == "Not Covered"  # AMC Status — no contract linked


def test_inventory_items_export(admin_client):
    admin_client.post("/api/v1/inventory/items", json={"item_code": "EXPORT-ITM-1", "name": "Export Item"})
    response = admin_client.get("/api/v1/inventory/items/export?format=csv&search=Export Item")
    assert response.status_code == 200
    rows = _read_csv_rows(response.content)
    assert rows[0][0] == "Item Code"
    assert any(r[0] == "EXPORT-ITM-1" for r in rows[1:])
