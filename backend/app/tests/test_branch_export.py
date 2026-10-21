"""Final admin/inventory/vendor/dashboard polish pass — Part 1: branch export."""

import csv
import io

import openpyxl

from .conftest import auth_headers, create_user


def _read_xlsx_rows(content: bytes) -> list[list]:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    sheet = wb.active
    return [list(row) for row in sheet.iter_rows(values_only=True)]


def _read_csv_rows(content: bytes) -> list[list[str]]:
    text = content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def _make_branch(admin_client, name="Export Branch", **overrides):
    payload = {"name": name, "status": "ACTIVE", "code": f"{name[:3].upper()}-001"}
    payload.update(overrides)
    response = admin_client.post("/api/v1/branches", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


EXPECTED_HEADERS = [
    "Name", "Code", "Region", "City", "State", "Address", "Status",
    "Unit Head", "TL(s)", "Central Warehouse", "Branch Email", "Branch Phone", "Branch WhatsApp",
]


def test_branch_export_xlsx(admin_client):
    _make_branch(admin_client, "XLSX Branch")
    response = admin_client.get("/api/v1/branches/export?format=xlsx")
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    assert "branches_export.xlsx" in response.headers["content-disposition"]

    rows = _read_xlsx_rows(response.content)
    assert rows[0] == EXPECTED_HEADERS
    assert any(r[0] == "XLSX Branch" for r in rows[1:])


def test_branch_export_csv(admin_client):
    _make_branch(admin_client, "CSV Branch")
    response = admin_client.get("/api/v1/branches/export?format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

    rows = _read_csv_rows(response.content)
    assert rows[0] == EXPECTED_HEADERS
    row = next(r for r in rows[1:] if r[0] == "CSV Branch")
    assert row[1].startswith("CSV")


def test_branch_export_no_internal_id_leakage(admin_client):
    branch = _make_branch(admin_client, "No Leak Branch")
    response = admin_client.get("/api/v1/branches/export?format=csv")
    rows = _read_csv_rows(response.content)
    row = next(r for r in rows[1:] if r[0] == "No Leak Branch")
    assert branch["id"] not in row


def test_branch_export_resolves_unit_head_and_tls(admin_client, db_session):
    unit_head = create_user(db_session, "unithead@example.com", "UNIT_HEAD", "Password123!")
    tl_user = create_user(db_session, "tluser@example.com", "TL", "Password123!")
    branch = _make_branch(admin_client, "Hierarchy Branch", unit_head_user_id=unit_head.id)
    admin_client.post(f"/api/v1/branches/{branch['id']}/tls", json={"user_id": tl_user.id})

    response = admin_client.get("/api/v1/branches/export?format=csv")
    rows = _read_csv_rows(response.content)
    row = next(r for r in rows[1:] if r[0] == "Hierarchy Branch")
    assert row[7] == unit_head.name
    assert row[8] == tl_user.name


def test_branch_export_requires_hospital_read(client, db_session):
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")
    # DOCTOR holds hospital.read, so this should succeed — sanity check the gate is
    # the same permission as the list endpoint.
    response = client.get("/api/v1/branches/export?format=csv", headers=headers)
    assert response.status_code == 200


def test_branch_export_invalid_format_rejected(admin_client):
    response = admin_client.get("/api/v1/branches/export?format=pdf")
    assert response.status_code == 400
