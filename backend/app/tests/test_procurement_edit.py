"""STEP 10D Part 2 — Procurement PATCH/edit endpoints.

Segregation-of-duties tests (Procurement Officer cannot self-approve a quotation /
cannot self-verify an invoice) already exist and pass in test_procurement.py
(test_procurement_officer_cannot_approve_quotation,
test_procurement_officer_cannot_verify_invoice, test_self_approval_blocked) — not
duplicated here, since edit happens strictly before any decision and doesn't touch
that check. This file covers only the new edit capability itself: allowed/forbidden
states, RBAC, audit, and rejected invalid mutations.
"""
from app.models.audit import AuditLog

from .conftest import auth_headers, create_user
from .test_procurement import (
    _approved_quotation,
    _iso,
    _make_asset,
    _make_incident,
    _make_vendor,
    _new_inventory_manager,
    _new_manager,
    _new_procurement_officer,
    _po_ready_for_invoice,
    _quotation_payload,
    _verify_invoice,
)


# ---- 1/2. Quotation edit — allowed (DRAFT) / forbidden (SUBMITTED+) ----

def test_edit_draft_quotation_allowed(admin_client, db_session):
    branch_id, asset = _make_asset(admin_client, "AST-EDIT-001")
    vendor = _make_vendor(admin_client, "QA TEST Edit Vendor 1")
    incident = _make_incident(admin_client, branch_id, asset["id"])
    lines = [{"description": "QA TEST original line", "quantity": 1, "unit_price": 1000, "covered": False}]
    quotation = admin_client.post(
        "/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines)
    ).json()

    response = admin_client.patch(
        f"/api/v1/procurement/quotations/{quotation['id']}",
        json={
            "coverage_note": "QA TEST updated note",
            "lines": [
                {"description": "QA TEST revised line", "quantity": 2, "unit_price": 750, "taxes": 0, "covered": False},
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["coverage_note"] == "QA TEST updated note"
    assert body["total"] == 1500
    assert len(body["lines"]) == 1
    assert body["lines"][0]["description"] == "QA TEST revised line"

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "quotation", AuditLog.entity_id == quotation["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None


def test_edit_submitted_quotation_forbidden(admin_client):
    branch_id, asset = _make_asset(admin_client, "AST-EDIT-002")
    vendor = _make_vendor(admin_client, "QA TEST Edit Vendor 2")
    incident = _make_incident(admin_client, branch_id, asset["id"])
    lines = [{"description": "QA TEST line", "quantity": 1, "unit_price": 500, "covered": False}]
    quotation = admin_client.post(
        "/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines)
    ).json()
    admin_client.post(f"/api/v1/procurement/quotations/{quotation['id']}/submit")

    response = admin_client.patch(
        f"/api/v1/procurement/quotations/{quotation['id']}", json={"coverage_note": "should not apply"}
    )
    assert response.status_code == 409, response.text
    assert "cannot be edited" in response.json()["detail"]


def test_edit_approved_quotation_forbidden(admin_client, client, db_session):
    """Invalid financial/state mutation rejected — an APPROVED quotation is a
    Manager decision and must not change under them."""
    _, _, _, _, quotation = _approved_quotation(admin_client, client, db_session, "editappr")
    response = admin_client.patch(
        f"/api/v1/procurement/quotations/{quotation['id']}", json={"coverage_note": "should not apply"}
    )
    assert response.status_code == 409, response.text


def test_edit_quotation_unauthorized_forbidden(admin_client, client, db_session):
    branch_id, asset = _make_asset(admin_client, "AST-EDIT-003")
    vendor = _make_vendor(admin_client, "QA TEST Edit Vendor 3")
    incident = _make_incident(admin_client, branch_id, asset["id"])
    lines = [{"description": "QA TEST line", "quantity": 1, "unit_price": 500, "covered": False}]
    quotation = admin_client.post(
        "/api/v1/procurement/quotations", json=_quotation_payload(incident, asset, vendor, lines)
    ).json()

    create_user(db_session, "viewer-edit-q@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "viewer-edit-q@example.com", "Password123!")
    response = client.patch(f"/api/v1/procurement/quotations/{quotation['id']}", json={"coverage_note": "x"}, headers=headers)
    assert response.status_code == 403


# ---- 3. Indent edit — allowed state ----

def test_edit_draft_indent_allowed(admin_client, client, db_session):
    _, _, _, _, quotation = _approved_quotation(admin_client, client, db_session, "editind")
    indent = admin_client.post("/api/v1/procurement/indents", json={"quotation_id": quotation["id"]}).json()

    response = admin_client.patch(f"/api/v1/procurement/indents/{indent['id']}", json={"notes": "QA TEST revised notes"})
    assert response.status_code == 200, response.text
    assert response.json()["notes"] == "QA TEST revised notes"
    # amount/quotation_id untouched — not editable fields
    assert response.json()["amount"] == indent["amount"]

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "indent", AuditLog.entity_id == indent["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None


def test_edit_completed_indent_forbidden(admin_client, client, db_session):
    """Invalid state mutation rejected — once a PO exists the indent auto-completes."""
    _, _, _, _, quotation = _approved_quotation(admin_client, client, db_session, "editindcomp")
    indent = admin_client.post("/api/v1/procurement/indents", json={"quotation_id": quotation["id"]}).json()
    admin_client.post(f"/api/v1/procurement/indents/{indent['id']}/submit")
    admin_client.post("/api/v1/procurement/purchase-orders", json={"indent_id": indent["id"]})

    response = admin_client.patch(f"/api/v1/procurement/indents/{indent['id']}", json={"notes": "too late"})
    assert response.status_code == 409, response.text


# ---- 4. Purchase order edit — allowed state ----

def test_edit_draft_purchase_order_allowed(admin_client, client, db_session):
    _, _, _, _, quotation = _approved_quotation(admin_client, client, db_session, "editpo")
    indent = admin_client.post("/api/v1/procurement/indents", json={"quotation_id": quotation["id"]}).json()
    admin_client.post(f"/api/v1/procurement/indents/{indent['id']}/submit")
    po = admin_client.post("/api/v1/procurement/purchase-orders", json={"indent_id": indent["id"]}).json()
    assert po["status"] == "DRAFT"

    response = admin_client.patch(
        f"/api/v1/procurement/purchase-orders/{po['id']}",
        json={
            "issue_date": _iso(1),
            "lines": [{"description": "QA TEST revised PO line", "quantity": 3, "unit_price": 200}],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["issue_date"] == _iso(1)
    assert body["total"] == 600
    assert len(body["lines"]) == 1

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "purchase_order", AuditLog.entity_id == po["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None


def test_edit_issued_purchase_order_forbidden(admin_client, client, db_session):
    """Invalid state mutation rejected — once ISSUED the vendor has received these
    terms; changing them afterward would misrepresent what was communicated."""
    _, _, _, _, quotation = _approved_quotation(admin_client, client, db_session, "editpoiss")
    indent = admin_client.post("/api/v1/procurement/indents", json={"quotation_id": quotation["id"]}).json()
    admin_client.post(f"/api/v1/procurement/indents/{indent['id']}/submit")
    po = admin_client.post("/api/v1/procurement/purchase-orders", json={"indent_id": indent["id"]}).json()
    admin_client.post(f"/api/v1/procurement/purchase-orders/{po['id']}/status", json={"to_status": "ISSUED"})

    response = admin_client.patch(f"/api/v1/procurement/purchase-orders/{po['id']}", json={"issue_date": _iso(5)})
    assert response.status_code == 409, response.text


# ---- 5. Invoice edit — allowed state ----

def test_edit_pending_invoice_allowed(admin_client, client, db_session):
    asset, vendor, incident, quotation, indent, po = _po_ready_for_invoice(admin_client, client, db_session, "editinv")
    invoice = admin_client.post(
        "/api/v1/procurement/invoices", json={"purchase_order_id": po["id"], "amount": 1000}
    ).json()

    response = admin_client.patch(f"/api/v1/procurement/invoices/{invoice['id']}", json={"amount": 1250})
    assert response.status_code == 200, response.text
    assert response.json()["amount"] == 1250

    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "invoice", AuditLog.entity_id == invoice["id"], AuditLog.action == "UPDATE"
    ).first()
    assert log is not None


def test_edit_verified_invoice_forbidden(admin_client, client, db_session):
    """Invalid financial mutation rejected — once VERIFIED it is financial closure
    history and must not change."""
    asset, vendor, incident, quotation, indent, po = _po_ready_for_invoice(admin_client, client, db_session, "editinvver")
    invoice = admin_client.post(
        "/api/v1/procurement/invoices", json={"purchase_order_id": po["id"], "amount": 1000}
    ).json()
    _verify_invoice(admin_client, client, db_session, invoice["id"], "editinvver")

    response = admin_client.patch(f"/api/v1/procurement/invoices/{invoice['id']}", json={"amount": 5})
    assert response.status_code == 409, response.text


def test_edit_invoice_unauthorized_forbidden(admin_client, client, db_session):
    asset, vendor, incident, quotation, indent, po = _po_ready_for_invoice(admin_client, client, db_session, "editinvrbac")
    invoice = admin_client.post(
        "/api/v1/procurement/invoices", json={"purchase_order_id": po["id"], "amount": 1000}
    ).json()

    create_user(db_session, "viewer-edit-inv@example.com", "VIEWER", "Password123!")
    headers = auth_headers(client, "viewer-edit-inv@example.com", "Password123!")
    response = client.patch(f"/api/v1/procurement/invoices/{invoice['id']}", json={"amount": 1}, headers=headers)
    assert response.status_code == 403
