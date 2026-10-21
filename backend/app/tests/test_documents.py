from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _engineer_headers(client, db_session, email="doceng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _make_asset(admin_client, asset_number="ASSET-DOC-001"):
    branch = admin_client.post("/api/v1/branches", json={"name": f"Branch-{asset_number}", "status": "ACTIVE"}).json()
    model = admin_client.post(
        "/api/v1/device-models", json={"name": f"Model-{asset_number}", "category": "Imaging"}
    ).json()
    asset = admin_client.post(
        "/api/v1/assets",
        json={"asset_number": asset_number, "device_model_id": model["id"], "branch_id": branch["id"]},
    ).json()
    return branch, model, asset


def _make_incident(admin_client, branch_id):
    return admin_client.post(
        "/api/v1/incidents", json={"branch_id": branch_id, "description": "Device not powering on"}
    ).json()


def _doc_payload(**overrides):
    payload = {
        "title": "Service Manual",
        "document_type": "SERVICE_MANUAL",
        "manufacturer": "Acme",
        "model_number": "X100",
        "access_level": "INTERNAL",
    }
    payload.update(overrides)
    return payload


# ---- Create ----

def test_engineer_can_create_document(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post("/api/v1/documents", json=_doc_payload(), headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Service Manual"
    assert body["document_type"] == "SERVICE_MANUAL"
    assert body["approval_status"] == "PENDING"
    assert body["is_active"] is True
    assert body["version_count"] == 0


def test_doctor_cannot_create_document(client, db_session):
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.post("/api/v1/documents", json=_doc_payload(), headers=headers)
    assert response.status_code == 403


def test_doctor_can_read_documents(client, db_session):
    headers = _engineer_headers(client, db_session)
    client.post("/api/v1/documents", json=_doc_payload(), headers=headers)

    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.get("/api/v1/documents", headers=doc_headers)
    assert response.status_code == 200


def test_blank_title_rejected(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post("/api/v1/documents", json=_doc_payload(title="   "), headers=headers)
    assert response.status_code == 422


def test_invalid_document_type_rejected(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post("/api/v1/documents", json=_doc_payload(document_type="NOT_A_TYPE"), headers=headers)
    assert response.status_code == 422


def test_invalid_access_level_rejected(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post("/api/v1/documents", json=_doc_payload(access_level="SECRET"), headers=headers)
    assert response.status_code == 422


def test_invalid_expiry_date_rejected(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post("/api/v1/documents", json=_doc_payload(expiry_date="not-a-date"), headers=headers)
    assert response.status_code == 422


def test_document_requires_valid_asset_fk(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post("/api/v1/documents", json=_doc_payload(asset_id="does-not-exist"), headers=headers)
    assert response.status_code == 422


def test_document_requires_valid_vendor_fk(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post("/api/v1/documents", json=_doc_payload(vendor_id="does-not-exist"), headers=headers)
    assert response.status_code == 422


# ---- Read / update / delete ----

def test_get_document(client, db_session):
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    response = client.get(f"/api/v1/documents/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_missing_document_404(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.get("/api/v1/documents/does-not-exist", headers=headers)
    assert response.status_code == 404


def test_update_document(client, db_session):
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    response = client.patch(f"/api/v1/documents/{created['id']}", json={"title": "Updated Manual"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["title"] == "Updated Manual"


def test_approve_document_sets_approved_by(client, db_session):
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    response = client.patch(f"/api/v1/documents/{created['id']}", json={"approval_status": "APPROVED"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["approval_status"] == "APPROVED"
    assert body["approved_by"] is not None


def test_doctor_cannot_update_document(client, db_session):
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()

    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    doc_headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.patch(f"/api/v1/documents/{created['id']}", json={"title": "x"}, headers=doc_headers)
    assert response.status_code == 403


def test_delete_document(client, db_session):
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    response = client.delete(f"/api/v1/documents/{created['id']}", headers=headers)
    assert response.status_code == 204
    assert client.get(f"/api/v1/documents/{created['id']}", headers=headers).status_code == 404


def test_delete_linked_document_conflict(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    branch, _, _ = _make_asset(admin_client)
    incident = _make_incident(admin_client, branch["id"])
    created = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()

    link = client.post(
        f"/api/v1/documents/{created['id']}/links",
        json={"entity_type": "incident", "entity_id": incident["id"]},
        headers=headers,
    )
    assert link.status_code == 201

    response = client.delete(f"/api/v1/documents/{created['id']}", headers=headers)
    assert response.status_code == 409


# ---- Search / filter / pagination ----

def test_search_by_title(client, db_session):
    headers = _engineer_headers(client, db_session)
    client.post("/api/v1/documents", json=_doc_payload(title="Ventilator Manual"), headers=headers)
    client.post("/api/v1/documents", json=_doc_payload(title="Infusion Pump SOP"), headers=headers)

    response = client.get("/api/v1/documents", params={"search": "Ventilator"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Ventilator Manual"


def test_filter_by_document_type(client, db_session):
    headers = _engineer_headers(client, db_session)
    client.post("/api/v1/documents", json=_doc_payload(document_type="SOP"), headers=headers)
    client.post("/api/v1/documents", json=_doc_payload(document_type="USER_MANUAL"), headers=headers)

    response = client.get("/api/v1/documents", params={"document_type": "SOP"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["document_type"] == "SOP"


def test_invalid_document_type_filter_rejected(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.get("/api/v1/documents", params={"document_type": "NOPE"}, headers=headers)
    assert response.status_code == 400


def test_pagination(client, db_session):
    headers = _engineer_headers(client, db_session)
    for i in range(5):
        client.post("/api/v1/documents", json=_doc_payload(title=f"Doc {i}"), headers=headers)

    response = client.get("/api/v1/documents", params={"page": 1, "page_size": 2}, headers=headers)
    body = response.json()
    assert response.status_code == 200
    assert len(body["items"]) == 2
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["page_size"] == 2


def test_filter_by_linked_entity(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    branch, _, _ = _make_asset(admin_client)
    incident = _make_incident(admin_client, branch["id"])

    linked = client.post("/api/v1/documents", json=_doc_payload(title="Linked doc"), headers=headers).json()
    client.post("/api/v1/documents", json=_doc_payload(title="Unlinked doc"), headers=headers)

    client.post(
        f"/api/v1/documents/{linked['id']}/links",
        json={"entity_type": "incident", "entity_id": incident["id"]},
        headers=headers,
    )

    response = client.get(
        "/api/v1/documents", params={"entity_type": "incident", "entity_id": incident["id"]}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == linked["id"]


def test_entity_filter_requires_both_params(client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.get("/api/v1/documents", params={"entity_type": "incident"}, headers=headers)
    assert response.status_code == 400


# ---- Linkage ----

def test_link_document_to_asset(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    _, _, asset = _make_asset(admin_client)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()

    response = client.post(
        f"/api/v1/documents/{document['id']}/links",
        json={"entity_type": "asset", "entity_id": asset["id"]},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["entity_type"] == "asset"

    listing = client.get(f"/api/v1/documents/{document['id']}/links", headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_link_invalid_entity_type_rejected(client, db_session):
    headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    response = client.post(
        f"/api/v1/documents/{document['id']}/links",
        json={"entity_type": "spaceship", "entity_id": "whatever"},
        headers=headers,
    )
    assert response.status_code == 422


def test_link_requires_existing_entity(client, db_session):
    headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    response = client.post(
        f"/api/v1/documents/{document['id']}/links",
        json={"entity_type": "asset", "entity_id": "does-not-exist"},
        headers=headers,
    )
    assert response.status_code == 404


def test_duplicate_link_rejected(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    _, _, asset = _make_asset(admin_client)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()

    first = client.post(
        f"/api/v1/documents/{document['id']}/links",
        json={"entity_type": "asset", "entity_id": asset["id"]},
        headers=headers,
    )
    assert first.status_code == 201

    duplicate = client.post(
        f"/api/v1/documents/{document['id']}/links",
        json={"entity_type": "asset", "entity_id": asset["id"]},
        headers=headers,
    )
    assert duplicate.status_code == 409


def test_unlink_document(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    _, _, asset = _make_asset(admin_client)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    link = client.post(
        f"/api/v1/documents/{document['id']}/links",
        json={"entity_type": "asset", "entity_id": asset["id"]},
        headers=headers,
    ).json()

    response = client.delete(f"/api/v1/documents/{document['id']}/links/{link['id']}", headers=headers)
    assert response.status_code == 204

    listing = client.get(f"/api/v1/documents/{document['id']}/links", headers=headers)
    assert listing.json() == []


# ---- Versions / upload / download ----

def test_upload_document_version(client, db_session):
    headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()

    response = client.post(
        f"/api/v1/documents/{document['id']}/versions",
        data={"version_label": "v1"},
        files={"file": ("manual.pdf", b"%PDF-1.4 fake content", "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["version_label"] == "v1"
    assert body["file_name"].endswith("manual.pdf")

    fetched = client.get(f"/api/v1/documents/{document['id']}", headers=headers).json()
    assert fetched["active_version_id"] == body["id"]
    assert fetched["version_count"] == 1


def test_upload_rejects_empty_file(client, db_session):
    headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()

    response = client.post(
        f"/api/v1/documents/{document['id']}/versions",
        data={"version_label": "v1"},
        files={"file": ("empty.pdf", b"", "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 400


def test_download_active_version(client, db_session):
    headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    client.post(
        f"/api/v1/documents/{document['id']}/versions",
        data={"version_label": "v1"},
        files={"file": ("manual.pdf", b"hello world", "application/pdf")},
        headers=headers,
    )

    response = client.get(f"/api/v1/documents/{document['id']}/download", headers=headers)
    assert response.status_code == 200
    assert response.content == b"hello world"


def test_download_without_version_404(client, db_session):
    headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    response = client.get(f"/api/v1/documents/{document['id']}/download", headers=headers)
    assert response.status_code == 404


def test_path_traversal_filename_is_sanitized(client, db_session):
    headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()

    response = client.post(
        f"/api/v1/documents/{document['id']}/versions",
        data={"version_label": "v1"},
        files={"file": ("../../etc/passwd.txt", b"malicious", "text/plain")},
        headers=headers,
    )
    assert response.status_code == 201
    file_name = response.json()["file_name"]
    assert "/" not in file_name and ".." not in file_name


# ---------------------------------------------------------------------------
# Security hardening pass — upload validation
# ---------------------------------------------------------------------------

def test_upload_rejects_executable_extension(client, db_session):
    headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    response = client.post(
        f"/api/v1/documents/{document['id']}/versions",
        data={"version_label": "v1"},
        files={"file": ("payload.exe", b"MZ\x90\x00fake-pe", "application/octet-stream")},
        headers=headers,
    )
    assert response.status_code == 400


def test_upload_rejects_html_extension(client, db_session):
    """SVG/HTML uploads can carry embedded <script> — reject at the extension
    allowlist rather than relying on downstream rendering to be safe."""
    headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    response = client.post(
        f"/api/v1/documents/{document['id']}/versions",
        data={"version_label": "v1"},
        files={"file": ("page.html", b"<script>alert(1)</script>", "text/html")},
        headers=headers,
    )
    assert response.status_code == 400


def test_upload_still_accepts_allowed_types(client, db_session):
    headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    for name, content_type in (("manual.pdf", "application/pdf"), ("sheet.xlsx", "application/vnd.ms-excel"),
                                ("data.csv", "text/csv"), ("photo.jpg", "image/jpeg")):
        response = client.post(
            f"/api/v1/documents/{document['id']}/versions",
            data={"version_label": name},
            files={"file": (name, b"content", content_type)},
            headers=headers,
        )
        assert response.status_code == 201, response.text


def test_upload_rejects_oversized_file(client, db_session):
    from app.routers.documents import MAX_DOCUMENT_UPLOAD_BYTES

    headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    oversized = b"a" * (MAX_DOCUMENT_UPLOAD_BYTES + 1)
    response = client.post(
        f"/api/v1/documents/{document['id']}/versions",
        data={"version_label": "v1"},
        files={"file": ("big.pdf", oversized, "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 413


# ---------------------------------------------------------------------------
# Security hardening pass — branch isolation for UNIT_HEAD/TL
# ---------------------------------------------------------------------------

def _scoped_unit_head_headers(client, db_session, *, branch_id, email="uh.doc@example.com"):
    user = create_user(db_session, email, "UNIT_HEAD", "Password123!")
    user.hospital_scope_id = branch_id
    db_session.add(user)
    db_session.commit()
    return auth_headers(client, email, "Password123!")


def test_unit_head_cannot_read_another_branchs_asset_linked_document(admin_client, client, db_session):
    branch_a, _, asset_a = _make_asset(admin_client, "DOC-SCOPE-A")
    branch_b, _, _ = _make_asset(admin_client, "DOC-SCOPE-B")
    engineer_headers = _engineer_headers(client, db_session)
    document = client.post(
        "/api/v1/documents", json=_doc_payload(asset_id=asset_a["id"]), headers=engineer_headers
    ).json()

    other_branch_headers = _scoped_unit_head_headers(client, db_session, branch_id=branch_b["id"])
    assert client.get(f"/api/v1/documents/{document['id']}", headers=other_branch_headers).status_code == 404
    assert client.get(f"/api/v1/documents/{document['id']}/versions", headers=other_branch_headers).status_code == 404
    assert client.get(f"/api/v1/documents/{document['id']}/links", headers=other_branch_headers).status_code == 404
    assert client.get(f"/api/v1/documents/{document['id']}/download", headers=other_branch_headers).status_code == 404

    own_branch_headers = _scoped_unit_head_headers(
        client, db_session, branch_id=branch_a["id"], email="uh.doc.own@example.com"
    )
    ok = client.get(f"/api/v1/documents/{document['id']}", headers=own_branch_headers)
    assert ok.status_code == 200


def test_unit_head_cannot_read_another_branchs_incident_linked_document(admin_client, client, db_session):
    branch_a, _, _ = _make_asset(admin_client, "DOC-INC-A")
    branch_b, _, _ = _make_asset(admin_client, "DOC-INC-B")
    incident_a = _make_incident(admin_client, branch_a["id"])
    engineer_headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=engineer_headers).json()
    client.post(
        f"/api/v1/documents/{document['id']}/links",
        json={"entity_type": "incident", "entity_id": incident_a["id"]},
        headers=engineer_headers,
    )

    other_branch_headers = _scoped_unit_head_headers(client, db_session, branch_id=branch_b["id"])
    response = client.get(f"/api/v1/documents/{document['id']}", headers=other_branch_headers)
    assert response.status_code == 404


def test_unit_head_can_read_global_catalog_document(admin_client, client, db_session):
    """A document with no asset/incident link (only tied to a device_model, or
    standalone) is global reference material, not branch data — it must stay
    visible to every branch-scoped caller, not just the one who happens to match."""
    branch_a, _, _ = _make_asset(admin_client, "DOC-GLOBAL-A")
    branch_b, _, _ = _make_asset(admin_client, "DOC-GLOBAL-B")
    engineer_headers = _engineer_headers(client, db_session)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=engineer_headers).json()

    for branch, email in ((branch_a, "uh.doc.g.a@example.com"), (branch_b, "uh.doc.g.b@example.com")):
        headers = _scoped_unit_head_headers(client, db_session, branch_id=branch["id"], email=email)
        response = client.get(f"/api/v1/documents/{document['id']}", headers=headers)
        assert response.status_code == 200


def test_unit_head_document_list_excludes_other_branches(admin_client, client, db_session):
    branch_a, _, asset_a = _make_asset(admin_client, "DOC-LIST-A")
    branch_b, _, asset_b = _make_asset(admin_client, "DOC-LIST-B")
    engineer_headers = _engineer_headers(client, db_session)
    doc_a = client.post(
        "/api/v1/documents", json=_doc_payload(title="Doc A", asset_id=asset_a["id"]), headers=engineer_headers
    ).json()
    doc_b = client.post(
        "/api/v1/documents", json=_doc_payload(title="Doc B", asset_id=asset_b["id"]), headers=engineer_headers
    ).json()
    global_doc = client.post(
        "/api/v1/documents", json=_doc_payload(title="Doc Global"), headers=engineer_headers
    ).json()

    headers = _scoped_unit_head_headers(client, db_session, branch_id=branch_b["id"])
    response = client.get("/api/v1/documents", headers=headers)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert doc_b["id"] in ids
    assert global_doc["id"] in ids
    assert doc_a["id"] not in ids


# ---- Audit logging ----

def test_create_document_writes_audit_log(client, db_session):
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()

    log = db_session.query(AuditLog).filter(AuditLog.entity == "document", AuditLog.entity_id == created["id"]).first()
    assert log is not None
    assert log.action == "CREATE"


def test_update_document_writes_audit_log(client, db_session):
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    client.patch(f"/api/v1/documents/{created['id']}", json={"title": "New"}, headers=headers)

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "document", AuditLog.entity_id == created["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None


def test_delete_document_writes_audit_log(client, db_session):
    headers = _engineer_headers(client, db_session)
    created = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    client.delete(f"/api/v1/documents/{created['id']}", headers=headers)

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "document", AuditLog.entity_id == created["id"], AuditLog.action == "DELETE"
    ).first()
    assert log is not None


def test_link_document_writes_audit_log(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    _, _, asset = _make_asset(admin_client)
    document = client.post("/api/v1/documents", json=_doc_payload(), headers=headers).json()
    client.post(
        f"/api/v1/documents/{document['id']}/links",
        json={"entity_type": "asset", "entity_id": asset["id"]},
        headers=headers,
    )

    log = db_session.query(AuditLog).filter(AuditLog.entity == "document_link").first()
    assert log is not None
    assert log.action == "CREATE"
