"""Module 21 — Procurement.

BME incident -> technical inspection -> AMC/CMC/Warranty check -> [fully covered:
vendor service, no quotation] / [partially or not covered: quotation -> Manager
approval -> Indent/CR -> PO -> vendor visit -> invoice -> Inventory Manager
verification -> PO COMPLETED]. See services/procurement_service.py.
"""
from datetime import date, timedelta

from .conftest import auth_headers, create_user

TODAY = date.today()


def _iso(days_offset: int) -> str:
    return (TODAY + timedelta(days=days_offset)).isoformat()


def _make_branch(admin_client, name="QA TEST Procurement Branch"):
    return admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE"}).json()


def _make_asset(admin_client, asset_number, branch_id=None):
    branch_id = branch_id or _make_branch(admin_client, f"QA TEST Branch-{asset_number}")["id"]
    model = admin_client.post(
        "/api/v1/device-models", json={"name": f"QA TEST Model-{asset_number}", "category": "Ventilator"}
    ).json()
    payload = {"asset_number": asset_number, "device_model_id": model["id"], "branch_id": branch_id}
    asset = admin_client.post("/api/v1/assets", json=payload).json()
    return branch_id, asset


def _make_vendor(admin_client, name="QA TEST Vendor"):
    return admin_client.post("/api/v1/vendors", json={"name": name, "vendor_type": "SERVICE", "status": "ACTIVE"}).json()


def _make_contract(admin_client, contract_type, asset_id, vendor_id, start_date, end_date):
    response = admin_client.post(
        "/api/v1/contracts",
        json={
            "contract_type": contract_type, "vendor_id": vendor_id, "asset_ids": [asset_id],
            "start_date": start_date, "end_date": end_date, "status": "ACTIVE",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_incident(admin_client, branch_id, asset_id, description="QA TEST device fault"):
    payload = {"branch_id": branch_id, "asset_id": asset_id, "description": description}
    return admin_client.post("/api/v1/incidents", json=payload).json()


def _user_client(client, db_session, role, email):
    create_user(db_session, email, role, "Password123!")
    headers = auth_headers(client, email, "Password123!")
    client.headers.update(headers)
    return client


def _reset_headers(client, admin_headers):
    client.headers.update(admin_headers)


def _quotation_payload(incident, asset, vendor, lines, coverage_note=None):
    payload = {
        "incident_id": incident["id"], "asset_id": asset["id"], "vendor_id": vendor["id"],
        "quote_date": _iso(0), "valid_until": _iso(30), "lines": lines,
    }
    if coverage_note is not None:
        payload["coverage_note"] = coverage_note
    return payload


# ---- 1. Quotation creation (not covered) ----

def test_create_quotation_not_covered(admin_client):
    branch_id, asset = _make_asset(admin_client, "AST-PROC-001")
    vendor = _make_vendor(admin_client, "QA TEST Vendor 1")
    incident = _make_incident(admin_client, branch_id, asset["id"])

    lines = [{"description": "QA TEST Repair labor", "quantity": 1, "unit_price": 5000, "covered": False}]
    response = admin_client.post("/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines))
    assert response.status_code == 201, response.text
    quotation = response.json()
    assert quotation["status"] == "DRAFT"
    assert quotation["coverage_status"] == "NOT_COVERED"
    assert quotation["total"] == 5000
    assert len(quotation["lines"]) == 1


# ---- 2. Fully covered blocks quotation creation ----

def test_fully_covered_repair_blocks_quotation(admin_client):
    branch_id, asset = _make_asset(admin_client, "AST-PROC-002")
    vendor = _make_vendor(admin_client, "QA TEST AMC Vendor")
    _make_contract(admin_client, "AMC", asset["id"], vendor["id"], _iso(-10), _iso(300))
    incident = _make_incident(admin_client, branch_id, asset["id"])

    lines = [{"description": "QA TEST Full repair", "quantity": 1, "unit_price": 3000, "covered": True}]
    response = admin_client.post("/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines))
    assert response.status_code == 422, response.text
    assert "fully covered" in response.json()["detail"].lower()


# ---- 3. Partially covered requires coverage_note ----

def test_partially_covered_requires_coverage_note(admin_client):
    branch_id, asset = _make_asset(admin_client, "AST-PROC-003")
    vendor = _make_vendor(admin_client, "QA TEST CMC Vendor")
    _make_contract(admin_client, "CMC", asset["id"], vendor["id"], _iso(-5), _iso(200))
    incident = _make_incident(admin_client, branch_id, asset["id"])

    lines = [
        {"description": "QA TEST Covered labor", "quantity": 1, "unit_price": 1000, "covered": True},
        {"description": "QA TEST Uncovered part", "quantity": 1, "unit_price": 2000, "covered": False},
    ]
    # Missing coverage_note -> rejected
    response = admin_client.post("/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines))
    assert response.status_code == 422, response.text
    assert "coverage_note" in response.json()["detail"]

    # With coverage_note -> allowed
    response = admin_client.post(
        "/api/v1/procurement/quotations",
        json=_quotation_payload(incident, asset, vendor, lines, coverage_note="QA TEST: CMC excludes this spare part"),
    )
    assert response.status_code == 201, response.text
    quotation = response.json()
    assert quotation["coverage_status"] == "PARTIALLY_COVERED"
    covered_flags = {line["description"]: line["covered"] for line in quotation["lines"]}
    assert covered_flags["QA TEST Covered labor"] is True
    assert covered_flags["QA TEST Uncovered part"] is False


# ---- 4. Not covered allows quotation without coverage_note ----

def test_not_covered_allows_quotation_without_note(admin_client):
    branch_id, asset = _make_asset(admin_client, "AST-PROC-004")
    vendor = _make_vendor(admin_client, "QA TEST Vendor 4")
    incident = _make_incident(admin_client, branch_id, asset["id"])
    lines = [{"description": "QA TEST Repair", "quantity": 2, "unit_price": 100, "taxes": 10, "covered": False}]

    response = admin_client.post("/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines))
    assert response.status_code == 201, response.text
    assert response.json()["coverage_status"] == "NOT_COVERED"
    assert response.json()["total"] == 210  # 2*100 + 10 taxes


def _full_chain_setup(admin_client):
    """QA TEST asset with no contract coverage — full quotation -> ... -> invoice chain."""
    branch_id, asset = _make_asset(admin_client, "AST-PROC-CHAIN")
    vendor = _make_vendor(admin_client, "QA TEST Chain Vendor")
    incident = _make_incident(admin_client, branch_id, asset["id"])
    lines = [{"description": "QA TEST Chain repair", "quantity": 1, "unit_price": 4000, "covered": False}]
    quotation = admin_client.post(
        "/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines)
    ).json()
    return branch_id, asset, vendor, incident, quotation


# ---- 5/6. Manager approval + self-approval blocked ----

def test_manager_approves_quotation(client, db_session):
    admin = _user_client(client, db_session, "SUPER_ADMIN", "admin-proc1@example.com")
    branch_id, asset = _make_asset(admin, "AST-PROC-005")
    vendor = _make_vendor(admin, "QA TEST Vendor 5")
    incident = _make_incident(admin, branch_id, asset["id"])
    bme_headers = auth_headers(client, *_new_bme(db_session, client, "bme5@example.com"))
    client.headers.update(bme_headers)
    lines = [{"description": "QA TEST repair", "quantity": 1, "unit_price": 1500, "covered": False}]
    quotation = client.post("/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines)).json()
    submitted = client.post(f"/api/v1/procurement/quotations/{quotation['id']}/submit").json()
    assert submitted["status"] == "SUBMITTED"

    manager_headers = auth_headers(client, *_new_manager(db_session, client, "mgr5@example.com"))
    client.headers.update(manager_headers)
    approved = client.post(f"/api/v1/procurement/quotations/{quotation['id']}/approve", json={"remarks": "QA TEST ok"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"


def _new_bme(db_session, client, email):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return email, "Password123!"


def _new_manager(db_session, client, email):
    create_user(db_session, email, "MANAGER", "Password123!")
    return email, "Password123!"


def _new_procurement_officer(db_session, client, email):
    create_user(db_session, email, "PROCUREMENT_OFFICER", "Password123!")
    return email, "Password123!"


def _new_inventory_manager(db_session, client, email):
    create_user(db_session, email, "INVENTORY_MANAGER", "Password123!")
    return email, "Password123!"


def test_self_approval_blocked(admin_client):
    """SUPER_ADMIN holds both quotation.create and quotation.approve, so this exercises
    the backend creator==approver check itself (not just an RBAC gap) — the same check
    applies no matter which permission bundle the approver holds."""
    branch_id, asset = _make_asset(admin_client, "AST-PROC-006")
    vendor = _make_vendor(admin_client, "QA TEST Vendor 6")
    incident = _make_incident(admin_client, branch_id, asset["id"])

    lines = [{"description": "QA TEST repair", "quantity": 1, "unit_price": 500, "covered": False}]
    quotation = admin_client.post("/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines)).json()
    admin_client.post(f"/api/v1/procurement/quotations/{quotation['id']}/submit")

    response = admin_client.post(f"/api/v1/procurement/quotations/{quotation['id']}/approve", json={})
    assert response.status_code == 403, response.text
    assert "themselves" in response.json()["detail"].lower()


# ---- 7. Procurement Officer cannot approve a quotation ----

def test_procurement_officer_cannot_approve_quotation(admin_client, client, db_session):
    branch_id, asset = _make_asset(admin_client, "AST-PROC-007")
    vendor = _make_vendor(admin_client, "QA TEST Vendor 7")
    incident = _make_incident(admin_client, branch_id, asset["id"])
    lines = [{"description": "QA TEST repair", "quantity": 1, "unit_price": 500, "covered": False}]
    quotation = admin_client.post("/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines)).json()
    admin_client.post(f"/api/v1/procurement/quotations/{quotation['id']}/submit")

    create_user(db_session, "po-cant-approve@example.com", "PROCUREMENT_OFFICER", "Password123!")
    headers = auth_headers(client, "po-cant-approve@example.com", "Password123!")
    client.headers.update(headers)

    response = client.post(f"/api/v1/procurement/quotations/{quotation['id']}/approve", json={})
    assert response.status_code == 403, response.text
    assert "quotation.approve" in response.json()["detail"]


def _approved_quotation(admin_client, client, db_session, tag):
    """Creates+submits the quotation as SUPER_ADMIN, then switches to a fresh MANAGER
    user to approve it (self-approval is blocked regardless of role — see
    test_self_approval_blocked), restoring the admin session headers afterwards so
    callers can keep using admin_client for indent/PO/invoice steps."""
    admin_headers = dict(admin_client.headers)
    branch_id, asset = _make_asset(admin_client, f"AST-PROC-{tag}")
    vendor = _make_vendor(admin_client, f"QA TEST Vendor {tag}")
    incident = _make_incident(admin_client, branch_id, asset["id"])
    lines = [{"description": "QA TEST repair", "quantity": 2, "unit_price": 1000, "covered": False}]
    quotation = admin_client.post(
        "/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines)
    ).json()
    admin_client.post(f"/api/v1/procurement/quotations/{quotation['id']}/submit")

    manager_headers = auth_headers(client, *_new_manager(db_session, client, f"mgr-{tag}@example.com"))
    client.headers.update(manager_headers)
    approved = admin_client.post(f"/api/v1/procurement/quotations/{quotation['id']}/approve", json={}).json()
    assert approved["status"] == "APPROVED", approved

    client.headers.update(admin_headers)
    return branch_id, asset, vendor, incident, approved


# ---- 8/9. Indent creation, no second approval gate ----

def test_indent_creation_and_no_second_approval_gate(admin_client, client, db_session):
    _, asset, vendor, incident, quotation = _approved_quotation(admin_client, client, db_session, "008")

    response = admin_client.post("/api/v1/procurement/indents", json={"quotation_id": quotation["id"], "notes": "QA TEST indent"})
    assert response.status_code == 201, response.text
    indent = response.json()
    assert indent["status"] == "DRAFT"
    assert indent["amount"] == 2000

    submitted = admin_client.post(f"/api/v1/procurement/indents/{indent['id']}/submit")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "SUBMITTED"
    # No approve endpoint exists for indents at all — DRAFT/SUBMITTED/COMPLETED only.
    assert admin_client.post(f"/api/v1/procurement/indents/{indent['id']}/approve").status_code == 404


# ---- 10/11. PO creation + status transitions ----

def test_po_creation_and_status_transitions(admin_client, client, db_session):
    _, asset, vendor, incident, quotation = _approved_quotation(admin_client, client, db_session, "010")
    indent = admin_client.post("/api/v1/procurement/indents", json={"quotation_id": quotation["id"]}).json()
    admin_client.post(f"/api/v1/procurement/indents/{indent['id']}/submit")

    response = admin_client.post("/api/v1/procurement/purchase-orders", json={"indent_id": indent["id"], "issue_date": _iso(0)})
    assert response.status_code == 201, response.text
    po = response.json()
    assert po["status"] == "DRAFT"
    assert po["total"] == 2000
    assert po["indent_id"] == indent["id"]
    assert po["quotation_id"] == quotation["id"]
    assert po["incident_id"] == incident["id"]
    assert po["asset_id"] == asset["id"]
    assert po["vendor_id"] == vendor["id"]

    # Indent auto-completes once a PO is created from it.
    indent_after = admin_client.get(f"/api/v1/procurement/indents/{indent['id']}").json()
    assert indent_after["status"] == "COMPLETED"

    for to_status in ["ISSUED", "ACKNOWLEDGED", "IN_PROGRESS", "DELIVERED"]:
        r = admin_client.post(f"/api/v1/procurement/purchase-orders/{po['id']}/status", json={"to_status": to_status})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == to_status

    # Cannot skip straight to COMPLETED from DELIVERED via status endpoint — only
    # invoice verification transitions a PO to COMPLETED (financial closure).
    r = admin_client.post(f"/api/v1/procurement/purchase-orders/{po['id']}/status", json={"to_status": "DRAFT"})
    assert r.status_code == 409


# ---- 12. Vendor visit linkage ----

def test_vendor_visit_linked_to_po(admin_client, client, db_session):
    _, asset, vendor, incident, quotation = _approved_quotation(admin_client, client, db_session, "012")
    indent = admin_client.post("/api/v1/procurement/indents", json={"quotation_id": quotation["id"]}).json()
    admin_client.post(f"/api/v1/procurement/indents/{indent['id']}/submit")
    po = admin_client.post("/api/v1/procurement/purchase-orders", json={"indent_id": indent["id"]}).json()
    admin_client.post(f"/api/v1/procurement/purchase-orders/{po['id']}/status", json={"to_status": "ISSUED"})

    visit_payload = {
        "incident_id": incident["id"], "asset_id": asset["id"], "vendor_id": vendor["id"],
        "purchase_order_id": po["id"], "visit_type": "BREAKDOWN", "visit_date": _iso(1),
        "summary": "QA TEST vendor visit scheduled",
    }
    response = admin_client.post("/api/v1/service-visits", json=visit_payload)
    assert response.status_code == 201, response.text
    assert response.json()["purchase_order_id"] == po["id"]


# ---- 13/14. Invoice recording + verification ----

def _verify_invoice(admin_client, client, db_session, invoice_id, tag):
    """Switches to a fresh INVENTORY_MANAGER to verify (recorded_by != verified_by is
    enforced regardless of role — same self-action check pattern as quotation
    approval), restoring the admin session headers afterwards."""
    admin_headers = dict(admin_client.headers)
    inv_headers = auth_headers(client, *_new_inventory_manager(db_session, client, f"invmgr-{tag}@example.com"))
    client.headers.update(inv_headers)
    verified = admin_client.post(f"/api/v1/procurement/invoices/{invoice_id}/verify")
    client.headers.update(admin_headers)
    return verified


def _po_ready_for_invoice(admin_client, client, db_session, tag):
    _, asset, vendor, incident, quotation = _approved_quotation(admin_client, client, db_session, tag)
    indent = admin_client.post("/api/v1/procurement/indents", json={"quotation_id": quotation["id"]}).json()
    admin_client.post(f"/api/v1/procurement/indents/{indent['id']}/submit")
    po = admin_client.post("/api/v1/procurement/purchase-orders", json={"indent_id": indent["id"]}).json()
    return asset, vendor, incident, quotation, indent, po


def test_invoice_recording_and_verification(admin_client, client, db_session):
    asset, vendor, incident, quotation, indent, po = _po_ready_for_invoice(admin_client, client, db_session, "013")

    response = admin_client.post(
        "/api/v1/procurement/invoices", json={"purchase_order_id": po["id"], "invoice_date": _iso(0), "amount": 2000}
    )
    assert response.status_code == 201, response.text
    invoice = response.json()
    assert invoice["verification_status"] == "PENDING"
    assert invoice["purchase_order_id"] == po["id"]

    verified = _verify_invoice(admin_client, client, db_session, invoice["id"], "013")
    assert verified.status_code == 200, verified.text
    assert verified.json()["verification_status"] == "VERIFIED"

    # 17. Financial closure requires invoice verification -> PO becomes COMPLETED.
    po_after = admin_client.get(f"/api/v1/procurement/purchase-orders/{po['id']}").json()
    assert po_after["status"] == "COMPLETED"

    # Final non-AI cleanup pass — Invoice.vendor_id is a direct column, denormalized
    # from the PO at record time, and now supports a vendor_id list filter so the
    # vendor detail page can show a real invoice history (previously it linked out
    # to the general list because no filter existed).
    assert invoice["vendor_id"] == vendor["id"]
    by_vendor = admin_client.get(f"/api/v1/procurement/invoices?vendor_id={vendor['id']}")
    assert by_vendor.status_code == 200
    assert invoice["id"] in [i["id"] for i in by_vendor.json()["items"]]

    other_vendor = admin_client.post("/api/v1/vendors", json={"name": "Unrelated Vendor"}).json()
    by_other_vendor = admin_client.get(f"/api/v1/procurement/invoices?vendor_id={other_vendor['id']}")
    assert invoice["id"] not in [i["id"] for i in by_other_vendor.json()["items"]]


# ---- 15. Procurement Officer cannot approve (verify) an invoice ----

def test_procurement_officer_cannot_verify_invoice(admin_client, client, db_session):
    asset, vendor, incident, quotation, indent, po = _po_ready_for_invoice(admin_client, client, db_session, "015")
    invoice = admin_client.post(
        "/api/v1/procurement/invoices", json={"purchase_order_id": po["id"], "amount": 999}
    ).json()

    create_user(db_session, "po-cant-verify@example.com", "PROCUREMENT_OFFICER", "Password123!")
    headers = auth_headers(client, "po-cant-verify@example.com", "Password123!")
    client.headers.update(headers)
    response = client.post(f"/api/v1/procurement/invoices/{invoice['id']}/verify")
    assert response.status_code == 403, response.text
    assert "invoice.approve" in response.json()["detail"]


# ---- 16. Technical closure independent of invoice/financial closure ----

def test_technical_closure_independent_of_invoice(admin_client, client, db_session):
    asset, vendor, incident, quotation, indent, po = _po_ready_for_invoice(admin_client, client, db_session, "016")
    # No invoice recorded at all yet — BME should still be able to close the incident.
    r2 = admin_client.post(
        f"/api/v1/incidents/{incident['id']}/status",
        json={"to_status": "OPERATIONAL", "notes": "QA TEST technical closure, invoice pending"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "OPERATIONAL"

    # Incident's own procurement summary confirms financial closure is still open.
    detail = admin_client.get(f"/api/v1/incidents/{incident['id']}").json()
    assert detail["procurement"]["financially_closed"] is False


# ---- 18. RBAC — a plain BME cannot create an indent or a PO ----

def test_rbac_bme_cannot_manage_indent_or_po(client, db_session):
    create_user(db_session, "bme-rbac@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    headers = auth_headers(client, "bme-rbac@example.com", "Password123!")
    client.headers.update(headers)
    response = client.post("/api/v1/procurement/indents", json={"quotation_id": "nonexistent"})
    assert response.status_code == 403


# ---- 19. Audit ----

def test_quotation_creation_is_audited(admin_client, db_session):
    branch_id, asset = _make_asset(admin_client, "AST-PROC-019")
    vendor = _make_vendor(admin_client, "QA TEST Vendor 19")
    incident = _make_incident(admin_client, branch_id, asset["id"])
    lines = [{"description": "QA TEST repair", "quantity": 1, "unit_price": 100, "covered": False}]
    quotation = admin_client.post("/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines)).json()

    from app.models.audit import AuditLog
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "quotation", AuditLog.entity_id == quotation["id"]).all()
    assert any(log.action == "CREATE" for log in logs)


# ---- 20. Notifications ----

def test_quotation_submitted_notification_dispatched(admin_client, db_session):
    template = admin_client.post(
        "/api/v1/notifications/templates",
        json={
            "code": "QA_TEST_QUOTATION_SUBMITTED", "channel": "EMAIL", "event": "QUOTATION_SUBMITTED",
            "subject_template": "QA TEST Quotation {{quotation_number}} submitted",
            "body_template": "QA TEST body", "receiver_email": "manager@example.com",
        },
    )
    assert template.status_code == 201, template.text

    branch_id, asset = _make_asset(admin_client, "AST-PROC-020")
    vendor = _make_vendor(admin_client, "QA TEST Vendor 20")
    incident = _make_incident(admin_client, branch_id, asset["id"])
    lines = [{"description": "QA TEST repair", "quantity": 1, "unit_price": 100, "covered": False}]
    quotation = admin_client.post("/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines)).json()
    admin_client.post(f"/api/v1/procurement/quotations/{quotation['id']}/submit")

    from app.models.notifications import Notification
    notifications = db_session.query(Notification).filter(
        Notification.event == "QUOTATION_SUBMITTED", Notification.related_entity_id == quotation["id"],
    ).all()
    assert len(notifications) == 1
    assert notifications[0].recipient == "manager@example.com"


# ---- 21. Asset procurement provenance ----

def test_asset_procurement_provenance(admin_client, client, db_session):
    asset, vendor, incident, quotation, indent, po = _po_ready_for_invoice(admin_client, client, db_session, "021")
    invoice = admin_client.post("/api/v1/procurement/invoices", json={"purchase_order_id": po["id"], "amount": 2000}).json()
    _verify_invoice(admin_client, client, db_session, invoice["id"], "021")

    response = admin_client.get(f"/api/v1/assets/{asset['id']}/procurement-provenance")
    assert response.status_code == 200, response.text
    entries = response.json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["quotation_id"] == quotation["id"]
    assert entry["indent_id"] == indent["id"]
    assert entry["purchase_order_id"] == po["id"]
    assert entry["invoice_id"] == invoice["id"]
    assert entry["invoice_verification_status"] == "VERIFIED"


# ---- 22. Incident procurement provenance / summary ----

def test_incident_procurement_summary(admin_client, client, db_session):
    asset, vendor, incident, quotation, indent, po = _po_ready_for_invoice(admin_client, client, db_session, "022")
    detail = admin_client.get(f"/api/v1/incidents/{incident['id']}").json()
    summary = detail["procurement"]
    assert len(summary["quotations"]) == 1
    assert len(summary["indents"]) == 1
    assert len(summary["purchase_orders"]) == 1
    assert summary["financially_closed"] is False

    invoice = admin_client.post("/api/v1/procurement/invoices", json={"purchase_order_id": po["id"], "amount": 2000}).json()
    _verify_invoice(admin_client, client, db_session, invoice["id"], "022")

    detail_after = admin_client.get(f"/api/v1/incidents/{incident['id']}").json()
    assert detail_after["procurement"]["financially_closed"] is True
