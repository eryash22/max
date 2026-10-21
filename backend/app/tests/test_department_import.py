"""Data Import Center — Department import now participates in the central
DepartmentMaster architecture instead of writing a free-text name/code directly
onto a branch's Department row. See import_service.py's
_resolve_department_master (mirrors routers/hospitals.py's create_department_master
matching) and the updated _import_department.
"""
import io

import openpyxl

from app.models.hospital import Department, DepartmentMaster


def _build_workbook(department_rows: list[list], branches: list[list] | None = None) -> bytes:
    wb = openpyxl.Workbook()

    hospital = wb.active
    hospital.title = "Hospital Master"
    hospital.append(["Hospital master", None, None, None])
    hospital.append(["Hospital Code", "Hospital Name*", "Status*", "Region"])
    for row in branches or [["SAKET", "Saket", "ACTIVE", "Delhi/NCR"], ["MAXBLK", "Max BLK", "ACTIVE", "Delhi/NCR"]]:
        hospital.append(row)

    departments = wb.create_sheet("Department Master")
    departments.append(["Departments", None, None, None])
    departments.append(["Department Code", "Department Name*", "Hospital Code*", "Status"])
    for row in department_rows:
        departments.append(row)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _upload_and_approve(admin_client, department_rows, branches=None):
    file_bytes = _build_workbook(department_rows, branches)
    upload = admin_client.post(
        "/api/v1/imports",
        files={"file": ("departments.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert upload.status_code == 201, upload.text
    job = upload.json()
    approval = admin_client.post(f"/api/v1/imports/{job['id']}/approve", json={"decision": "APPROVED"})
    assert approval.status_code == 200, approval.text
    return job, approval.json()


# ---- 1-3: matching / creation ----

def test_import_links_to_existing_active_master(admin_client, db_session):
    master = admin_client.post("/api/v1/department-masters", json={"name": "Radiology", "code": "RAD"}).json()

    _upload_and_approve(admin_client, [["RAD", "Radiology", "SAKET", "ACTIVE"]])

    db_session.expire_all()
    dept = db_session.query(Department).filter(Department.department_master_id == master["id"]).first()
    assert dept is not None
    assert dept.name == "Radiology"


def test_same_department_imported_to_another_branch_reuses_master(admin_client, db_session):
    _upload_and_approve(admin_client, [
        ["PHY", "Physiotherapy", "SAKET", "ACTIVE"],
        ["PHY", "Physiotherapy", "MAXBLK", "ACTIVE"],
    ])

    db_session.expire_all()
    masters = db_session.query(DepartmentMaster).filter(DepartmentMaster.name == "Physiotherapy").all()
    assert len(masters) == 1, "one master must serve both branches, not two"
    assignments = db_session.query(Department).filter(Department.department_master_id == masters[0].id).all()
    assert {a.branch_id for a in assignments} == {
        _branch_id(db_session, "Saket"), _branch_id(db_session, "Max BLK"),
    }


def test_import_creates_new_master_when_none_matches(admin_client, db_session):
    before = db_session.query(DepartmentMaster).filter(DepartmentMaster.name == "Oncology").count()
    assert before == 0

    _upload_and_approve(admin_client, [["ONC", "Oncology", "SAKET", "ACTIVE"]])

    db_session.expire_all()
    masters = db_session.query(DepartmentMaster).filter(DepartmentMaster.name == "Oncology").all()
    assert len(masters) == 1


# ---- 4-6: idempotency / id preservation / FK integrity ----

def test_reimporting_same_data_does_not_duplicate_master(admin_client, db_session):
    rows = [["ICU", "Intensive Care", "SAKET", "ACTIVE"]]
    _upload_and_approve(admin_client, rows)
    _upload_and_approve(admin_client, rows)

    db_session.expire_all()
    masters = db_session.query(DepartmentMaster).filter(DepartmentMaster.name == "Intensive Care").all()
    assert len(masters) == 1


def test_reimport_preserves_existing_department_id(admin_client, db_session):
    _upload_and_approve(admin_client, [["EMR", "Emergency", "SAKET", "ACTIVE"]])
    db_session.expire_all()
    first = db_session.query(Department).filter(Department.code == "EMR", Department.branch_id == _branch_id(db_session, "Saket")).first()
    first_id = first.id

    _upload_and_approve(admin_client, [["EMR", "Emergency", "SAKET", "INACTIVE"]])
    db_session.expire_all()
    second = db_session.get(Department, first_id)
    assert second is not None
    assert second.id == first_id
    assert second.status == "INACTIVE"


def test_imported_department_downstream_fk_intact(admin_client, db_session):
    _upload_and_approve(admin_client, [["NEU", "Neurology", "SAKET", "ACTIVE"]])
    db_session.expire_all()
    dept = db_session.query(Department).filter(Department.code == "NEU").first()

    location = admin_client.post("/api/v1/locations", json={"department_id": dept.id, "name": "Ward N1"})
    assert location.status_code == 201, location.text
    assert location.json()["department_id"] == dept.id


# ---- 7: normalization ----

def test_name_and_code_normalization(admin_client, db_session):
    admin_client.post("/api/v1/department-masters", json={"name": "Cardiology", "code": "CARD"})

    # Different case and stray whitespace on both fields must still match.
    _upload_and_approve(admin_client, [["  card  ", "  CARDIOLOGY  ", "SAKET", "ACTIVE"]])

    db_session.expire_all()
    masters = db_session.query(DepartmentMaster).filter(DepartmentMaster.name == "Cardiology").all()
    assert len(masters) == 1
    dept = db_session.query(Department).filter(Department.department_master_id == masters[0].id).first()
    assert dept is not None


# ---- 8: conflicting name/code is never silently resolved ----

def test_conflicting_code_between_row_and_existing_master_is_not_merged(admin_client, db_session):
    admin_client.post("/api/v1/department-masters", json={"name": "Physiotherapy", "code": "PHY"})

    job, result = _upload_and_approve(admin_client, [["PHYSIO", "Physiotherapy", "SAKET", "ACTIVE"]])

    db_session.expire_all()
    masters = db_session.query(DepartmentMaster).filter(DepartmentMaster.name == "Physiotherapy").all()
    assert len(masters) == 1, "a name+conflicting-code row must not create a second master"
    dept = db_session.query(Department).filter(Department.branch_id == _branch_id(db_session, "Saket")).all()
    assert not any(d.department_master_id == masters[0].id for d in dept), "the conflicting row must not have been linked either"

    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview").json()
    assert any(e["severity"] == "ERROR" and "conflict" in e["message"].lower() for e in preview["errors"])


# ---- 9: inactive master is not silently reused or reactivated ----

def test_import_does_not_silently_reactivate_inactive_master(admin_client, db_session):
    master = admin_client.post("/api/v1/department-masters", json={"name": "Dermatology", "code": "DERM"}).json()
    admin_client.post(f"/api/v1/department-masters/{master['id']}/deactivate")

    job, _ = _upload_and_approve(admin_client, [["DERM", "Dermatology", "SAKET", "ACTIVE"]])

    db_session.expire_all()
    refreshed = db_session.get(DepartmentMaster, master["id"])
    assert refreshed.status == "INACTIVE", "an import must never reactivate a master on its own"
    linked = db_session.query(Department).filter(Department.department_master_id == master["id"]).count()
    assert linked == 0

    preview = admin_client.get(f"/api/v1/imports/{job['id']}/preview").json()
    assert any(e["severity"] == "ERROR" and "inactive" in e["message"].lower() for e in preview["errors"])


# ---- 10: other import types unaffected ----

def test_branch_import_unaffected_by_department_master_change(admin_client, db_session):
    from app.models.hospital import Branch

    _upload_and_approve(admin_client, [["GEN", "General Medicine", "SAKET", "ACTIVE"]])
    db_session.expire_all()
    saket = db_session.query(Branch).filter(Branch.code == "SAKET").first()
    assert saket is not None
    assert saket.name == "Saket"


def _branch_id(db_session, name):
    from app.models.hospital import Branch

    return db_session.query(Branch).filter(Branch.name == name).first().id
