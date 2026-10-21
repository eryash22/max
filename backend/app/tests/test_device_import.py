import io

import openpyxl

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, auth_headers, create_user
from app.models.device import Asset, DeviceCategory, DeviceModel


def _device_model_csv(rows: list[list[str]]) -> bytes:
    lines = ["Manufacturer,Model Number,Device Name*,Category*"]
    for r in rows:
        lines.append(",".join(r))
    return ("\n".join(lines)).encode("utf-8")


def _device_model_workbook(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Device Model Master"
    sheet.append(["Catalog"])
    sheet.append(["Manufacturer", "Model Number", "Device Name*", "Category*"])
    for r in rows:
        sheet.append(r)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_csv_upload_is_accepted(admin_client):
    file_bytes = _device_model_csv([["QA Mfr", "QA-CSV-001", "QA CSV Device", "ICU"]])
    response = admin_client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.csv", file_bytes, "text/csv")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "READY_FOR_REVIEW"


def test_xlsx_upload_is_accepted(admin_client):
    file_bytes = _device_model_workbook([["QA Mfr", "QA-XLSX-001", "QA XLSX Device", "ICU"]])
    response = admin_client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.xlsx", file_bytes,
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "READY_FOR_REVIEW"


def test_missing_required_field_flagged_as_error(admin_client):
    # Blank Device Name (required) -> validation ERROR, row not ready.
    file_bytes = _device_model_csv([["QA Mfr", "QA-ERR-001", "", "ICU"]])
    response = admin_client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.csv", file_bytes, "text/csv")},
    )
    job = response.json()
    assert job["summary"]["errors"] >= 1

    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview")
    errors = preview.json()["errors"]
    assert any(e["severity"] == "ERROR" for e in errors)


def test_category_not_in_taxonomy_flagged_as_warning_not_blocked(admin_client):
    file_bytes = _device_model_csv([["QA Mfr", "QA-CAT-001", "QA Category Device", "Not A Real Category"]])
    response = admin_client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.csv", file_bytes, "text/csv")},
    )
    job = response.json()
    # A category warning must not turn the row into an ERROR — it's still importable.
    assert job["summary"]["errors"] == 0
    assert job["summary"]["ready"] == 1

    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview")
    warnings = [e for e in preview.json()["errors"] if e["severity"] == "WARNING"]
    assert any("category" in (w["field"] or "") for w in warnings)


def test_category_matching_taxonomy_resolves_category_id(admin_client, db_session):
    category = DeviceCategory(name="QA Taxonomy Category")
    db_session.add(category)
    db_session.commit()

    file_bytes = _device_model_csv([["QA Mfr", "QA-CAT-002", "QA Matched Category Device", "qa taxonomy category"]])
    upload = admin_client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.csv", file_bytes, "text/csv")},
    )
    job = upload.json()
    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview")
    # Case-insensitive match against the real taxonomy -> no warning this time.
    warnings = [e for e in preview.json()["errors"] if e["severity"] == "WARNING"]
    assert not any("category" in (w["field"] or "") for w in warnings)

    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200

    model = db_session.query(DeviceModel).filter(DeviceModel.model_number == "QA-CAT-002").first()
    assert model is not None
    assert model.category_id == category.id


def test_duplicate_within_file_flagged_as_duplicate_candidate(admin_client):
    file_bytes = _device_model_csv([
        ["QA Mfr", "QA-DUP-001", "QA Duplicate Device", "ICU"],
        ["QA Mfr", "QA-DUP-001", "QA Duplicate Device", "ICU"],
    ])
    response = admin_client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.csv", file_bytes, "text/csv")},
    )
    job = response.json()
    assert job["summary"]["duplicate_candidates"] >= 1


def test_existing_device_model_is_updated_not_duplicated(admin_client, db_session):
    existing = DeviceModel(name="QA Existing Model", manufacturer="QA Existing Mfr",
                            model_number="QA-EXISTING-001", category="Old Category")
    db_session.add(existing)
    db_session.commit()
    before_count = db_session.query(DeviceModel).count()

    file_bytes = _device_model_csv([["QA Existing Mfr", "QA-EXISTING-001", "QA Existing Model", "ICU"]])
    upload = admin_client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.csv", file_bytes, "text/csv")},
    )
    job = upload.json()
    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200
    assert approval.json()["summary"]["updated"] == 1
    assert approval.json()["summary"]["inserted"] == 0

    after_count = db_session.query(DeviceModel).count()
    assert after_count == before_count  # no duplicate row created

    db_session.refresh(existing)
    assert existing.category == "ICU"  # updated in place


def test_import_without_device_master_sheet_creates_no_assets(admin_client, db_session):
    before_assets = db_session.query(Asset).count()
    file_bytes = _device_model_csv([["QA Mfr", "QA-NOASSET-001", "QA No Asset Device", "ICU"]])
    upload = admin_client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.csv", file_bytes, "text/csv")},
    )
    job = upload.json()
    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200

    after_assets = db_session.query(Asset).count()
    assert after_assets == before_assets  # a Device Model Master-only upload never creates Asset rows


def test_import_never_fabricates_asset_number_or_serial(admin_client, db_session):
    # Even when a "device master" sheet IS present, only rows with a real asset
    # number get an Asset row — nothing here should show a generated placeholder
    # asset number or serial number.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Device Master"
    ws.append(["Assets"])
    ws.append([
        "Asset Number*", "Device Name", "Category", "Manufacturer*", "Model Number*",
        "Serial Number", "Hospital Code*", "Department Code", "Location Code", "Status*",
    ])
    ws.append([
        "QA-ASSET-REAL-001", "QA Real Asset", "ICU", "QA Mfr", "QA-REAL-MODEL", None,
        "QAHOSP", None, None, "OPERATIONAL",
    ])
    buffer = io.BytesIO()
    wb.save(buffer)

    upload = admin_client.post(
        "/api/v1/imports",
        files={"file": ("device_master.xlsx", buffer.getvalue(),
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    job = upload.json()
    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview")
    rows = preview.json()["rows"]
    for row in rows:
        mapped = row["mapped_data"] or {}
        if row["entity_type"] == "asset":
            # asset_number is exactly what was supplied, never invented
            assert mapped.get("asset_number") == "QA-ASSET-REAL-001"
            assert mapped.get("serial_number") is None  # left blank, never fabricated


def test_import_requires_import_create_permission(client, db_session):
    create_user(db_session, ADMIN_EMAIL, "SUPER_ADMIN", ADMIN_PASSWORD)
    admin_headers = auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)  # noqa: F841 (parity w/ other tests)

    create_user(db_session, "viewer.import@example.com", "VIEWER", "Password123!")
    viewer_headers = auth_headers(client, "viewer.import@example.com", "Password123!")
    file_bytes = _device_model_csv([["QA Mfr", "QA-403-001", "QA 403 Device", "ICU"]])
    response = client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.csv", file_bytes, "text/csv")},
        headers=viewer_headers,
    )
    assert response.status_code == 403


def test_inventory_manager_can_import(client, db_session):
    create_user(db_session, "inv.mgr@example.com", "INVENTORY_MANAGER", "Password123!")
    headers = auth_headers(client, "inv.mgr@example.com", "Password123!")
    file_bytes = _device_model_csv([["QA Mfr", "QA-INVMGR-001", "QA InvMgr Device", "ICU"]])
    response = client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.csv", file_bytes, "text/csv")},
        headers=headers,
    )
    assert response.status_code == 201


def test_unit_head_cannot_import(client, db_session):
    create_user(db_session, "unit.head@example.com", "UNIT_HEAD", "Password123!")
    headers = auth_headers(client, "unit.head@example.com", "Password123!")
    file_bytes = _device_model_csv([["QA Mfr", "QA-UH-001", "QA Unit Head Device", "ICU"]])
    response = client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.csv", file_bytes, "text/csv")},
        headers=headers,
    )
    assert response.status_code == 403


def test_import_audit_records_actor_and_row_counts(admin_client, db_session):
    file_bytes = _device_model_csv([["QA Mfr", "QA-AUDIT-001", "QA Audit Device", "ICU"]])
    upload = admin_client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.csv", file_bytes, "text/csv")},
    )
    job = upload.json()
    admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})

    audit = admin_client.get(f"/api/v1/audit-logs?entity_id={job['id']}")
    assert audit.status_code == 200
    actions = [row["action"] for row in audit.json()["items"]]
    assert "UPLOAD" in actions


def test_download_template_xlsx(admin_client):
    response = admin_client.get("/api/v1/imports/templates/device model master?format=xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_download_template_csv(admin_client):
    response = admin_client.get("/api/v1/imports/templates/device model master?format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    body = response.content.decode("utf-8-sig")
    assert "Device Name*" in body
    assert "Category*" in body


def test_download_template_unknown_sheet_404s(admin_client):
    response = admin_client.get("/api/v1/imports/templates/not a real sheet?format=xlsx")
    assert response.status_code == 404


def test_existing_catalog_remains_intact_after_unrelated_import(admin_client, db_session):
    existing = DeviceModel(name="QA Untouched Model", manufacturer="QA Untouched Mfr",
                            model_number="QA-UNTOUCHED-001", category="ICU")
    db_session.add(existing)
    db_session.commit()

    file_bytes = _device_model_csv([["QA Other Mfr", "QA-OTHER-001", "QA Other Device", "ICU"]])
    upload = admin_client.post(
        "/api/v1/imports",
        files={"file": ("device_model_master.csv", file_bytes, "text/csv")},
    )
    job = upload.json()
    admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})

    db_session.refresh(existing)
    assert existing.name == "QA Untouched Model"
    assert existing.manufacturer == "QA Untouched Mfr"
    assert existing.category == "ICU"
