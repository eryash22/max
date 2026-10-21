from app.models.audit import AuditLog

from .conftest import auth_headers, create_user


def _engineer_headers(client, db_session, email="eng@example.com"):
    create_user(db_session, email, "BIOMEDICAL_ENGINEER", "Password123!")
    return auth_headers(client, email, "Password123!")


def _make_template(admin_client, code="INCIDENT_CREATED_EMAIL", channel="EMAIL"):
    return admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": code, "channel": channel, "event": "INCIDENT_CREATED", "body_template": "A new incident was reported."},
    ).json()


def _make_asset(admin_client, asset_number="AST-NOTIF-001"):
    branch = admin_client.post("/api/v1/branches", json={"name": f"Branch-{asset_number}", "status": "ACTIVE"}).json()
    model = admin_client.post("/api/v1/device-models", json={"name": f"Model-{asset_number}", "category": "Infusion Pump"}).json()
    return admin_client.post(
        "/api/v1/assets", json={"asset_number": asset_number, "device_model_id": model["id"], "branch_id": branch["id"]}
    ).json()


# ---- Templates ----

def test_create_template(admin_client):
    template = _make_template(admin_client)
    assert template["code"] == "INCIDENT_CREATED_EMAIL"
    assert template["channel"] == "EMAIL"
    assert template["is_active"] is True


def test_duplicate_template_code_rejected(admin_client):
    _make_template(admin_client, "DUP_CODE")
    response = admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": "DUP_CODE", "channel": "SMS", "event": "PM_DUE", "body_template": "PM due"},
    )
    assert response.status_code == 409


def test_invalid_channel_rejected(admin_client):
    response = admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": "BAD_CHANNEL", "channel": "PAGER", "event": "PM_DUE", "body_template": "PM due"},
    )
    assert response.status_code == 422


def test_blank_body_template_rejected(admin_client):
    response = admin_client.post(
        "/api/v1/notifications/templates",
        json={"code": "BLANK_BODY", "channel": "SMS", "event": "PM_DUE", "body_template": "   "},
    )
    assert response.status_code == 422


def test_get_template_404(admin_client):
    response = admin_client.get("/api/v1/notifications/templates/does-not-exist")
    assert response.status_code == 404


def test_update_template(admin_client):
    template = _make_template(admin_client)
    response = admin_client.patch(
        f"/api/v1/notifications/templates/{template['id']}",
        json={"code": template["code"], "channel": "SMS", "event": "INCIDENT_CREATED", "body_template": "Updated body", "is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["channel"] == "SMS"
    assert response.json()["is_active"] is False


def test_engineer_cannot_create_template(admin_client, client, db_session):
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/notifications/templates",
        json={"code": "ENG_TEMPLATE", "channel": "EMAIL", "event": "PM_DUE", "body_template": "PM due"},
        headers=headers,
    )
    assert response.status_code == 403


def test_engineer_can_read_templates(admin_client, client, db_session):
    _make_template(admin_client)
    headers = _engineer_headers(client, db_session)
    response = client.get("/api/v1/notifications/templates", headers=headers)
    assert response.status_code == 200


def test_template_create_records_audit_log(admin_client, db_session):
    _make_template(admin_client)
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "notification_template").all()
    assert any(log.action == "CREATE" for log in logs)


# ---- Delete (Batch F — Item 7) ----

def test_delete_template_succeeds_and_template_is_gone(admin_client):
    template = _make_template(admin_client)
    response = admin_client.delete(f"/api/v1/notifications/templates/{template['id']}")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert admin_client.get(f"/api/v1/notifications/templates/{template['id']}").status_code == 404


def test_delete_last_active_template_for_event_returns_warning(admin_client):
    template = _make_template(admin_client, code="ONLY_ONE", channel="EMAIL")
    response = admin_client.delete(f"/api/v1/notifications/templates/{template['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["warning"] is not None
    assert "INCIDENT_CREATED" in body["warning"] or "Incident" in body["warning"]


def test_delete_template_no_warning_when_another_active_template_covers_event(admin_client):
    _make_template(admin_client, code="FIRST_ONE", channel="EMAIL")
    second = _make_template(admin_client, code="SECOND_ONE", channel="EMAIL")
    response = admin_client.delete(f"/api/v1/notifications/templates/{second['id']}")
    assert response.status_code == 200
    assert response.json()["warning"] is None


def test_delete_template_rejected_for_non_super_admin(admin_client, client, db_session):
    template = _make_template(admin_client)
    # BIOMEDICAL_ADMIN holds notification.approve (create/update templates) but not the
    # Super-Admin-only notification_template.delete code.
    create_user(db_session, "biomed-admin@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    headers = auth_headers(client, "biomed-admin@example.com", "Password123!")
    response = client.delete(f"/api/v1/notifications/templates/{template['id']}", headers=headers)
    assert response.status_code == 403


def test_delete_template_404(admin_client):
    response = admin_client.delete("/api/v1/notifications/templates/does-not-exist")
    assert response.status_code == 404


def test_delete_template_records_audit_log(admin_client, db_session):
    template = _make_template(admin_client)
    admin_client.delete(f"/api/v1/notifications/templates/{template['id']}")
    logs = db_session.query(AuditLog).filter(
        AuditLog.entity == "notification_template", AuditLog.action == "DELETE"
    ).all()
    assert any(log.entity_id == template["id"] for log in logs)


# ---- Notifications: create / drafting ----

def test_create_in_app_notification_defaults_no_approval_required(admin_client):
    response = admin_client.post(
        "/api/v1/notifications",
        json={"recipient": "user@example.com", "channel": "IN_APP", "event": "PM_DUE"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "DRAFT"
    assert body["approval_required"] is False


def test_create_email_notification_defaults_approval_required(admin_client):
    response = admin_client.post(
        "/api/v1/notifications",
        json={"recipient": "user@example.com", "channel": "EMAIL", "event": "PM_DUE"},
    )
    assert response.status_code == 201
    assert response.json()["approval_required"] is True


def test_create_notification_validates_template_fk(admin_client):
    response = admin_client.post(
        "/api/v1/notifications",
        json={"recipient": "user@example.com", "channel": "EMAIL", "event": "PM_DUE", "template_id": "does-not-exist"},
    )
    assert response.status_code == 404


def test_create_notification_blank_recipient_rejected(admin_client):
    response = admin_client.post(
        "/api/v1/notifications", json={"recipient": "  ", "channel": "EMAIL", "event": "PM_DUE"}
    )
    assert response.status_code == 422


def test_create_notification_validates_related_entity(admin_client):
    asset = _make_asset(admin_client)
    response = admin_client.post(
        "/api/v1/notifications",
        json={
            "recipient": "user@example.com", "channel": "IN_APP", "event": "ASSET_STATUS_CHANGED",
            "related_entity_type": "asset", "related_entity_id": asset["id"],
        },
    )
    assert response.status_code == 201
    assert response.json()["related_entity_id"] == asset["id"]


def test_create_notification_related_entity_must_exist(admin_client):
    response = admin_client.post(
        "/api/v1/notifications",
        json={"recipient": "user@example.com", "channel": "IN_APP", "event": "ASSET_STATUS_CHANGED",
              "related_entity_type": "asset", "related_entity_id": "does-not-exist"},
    )
    assert response.status_code == 404


def test_create_notification_related_entity_requires_pair(admin_client):
    response = admin_client.post(
        "/api/v1/notifications",
        json={"recipient": "user@example.com", "channel": "IN_APP", "event": "PM_DUE", "related_entity_type": "asset"},
    )
    assert response.status_code == 400


def test_notification_create_records_audit_log(admin_client, db_session):
    admin_client.post("/api/v1/notifications", json={"recipient": "user@example.com", "channel": "IN_APP", "event": "PM_DUE"})
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "notification").all()
    assert any(log.action == "CREATE" for log in logs)


# ---- Submit / approve / reject lifecycle ----

def test_submit_in_app_notification_goes_straight_to_sent(admin_client):
    notification = admin_client.post(
        "/api/v1/notifications", json={"recipient": "user@example.com", "channel": "IN_APP", "event": "PM_DUE"}
    ).json()
    response = admin_client.post(f"/api/v1/notifications/{notification['id']}/submit")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SENT"
    assert body["sent_at"] is not None


def test_submit_email_notification_requires_approval(admin_client):
    notification = admin_client.post(
        "/api/v1/notifications", json={"recipient": "user@example.com", "channel": "EMAIL", "event": "PM_DUE"}
    ).json()
    response = admin_client.post(f"/api/v1/notifications/{notification['id']}/submit")
    assert response.status_code == 200
    assert response.json()["status"] == "PENDING_APPROVAL"


def test_cannot_submit_twice(admin_client):
    notification = admin_client.post(
        "/api/v1/notifications", json={"recipient": "user@example.com", "channel": "IN_APP", "event": "PM_DUE"}
    ).json()
    admin_client.post(f"/api/v1/notifications/{notification['id']}/submit")
    response = admin_client.post(f"/api/v1/notifications/{notification['id']}/submit")
    assert response.status_code == 409


def test_approve_pending_notification(admin_client, db_session):
    notification = admin_client.post(
        "/api/v1/notifications", json={"recipient": "user@example.com", "channel": "EMAIL", "event": "PM_DUE"}
    ).json()
    admin_client.post(f"/api/v1/notifications/{notification['id']}/submit")
    response = admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SENT"
    assert body["approved_by"] is not None
    assert body["sent_at"] is not None


def test_cannot_approve_draft_notification(admin_client):
    notification = admin_client.post(
        "/api/v1/notifications", json={"recipient": "user@example.com", "channel": "EMAIL", "event": "PM_DUE"}
    ).json()
    response = admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    assert response.status_code == 409


def test_reject_pending_notification(admin_client):
    notification = admin_client.post(
        "/api/v1/notifications", json={"recipient": "user@example.com", "channel": "EMAIL", "event": "PM_DUE"}
    ).json()
    admin_client.post(f"/api/v1/notifications/{notification['id']}/submit")
    response = admin_client.post(
        f"/api/v1/notifications/{notification['id']}/reject", json={"failure_reason": "Recipient opted out"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["failure_reason"] == "Recipient opted out"


def test_reject_requires_blank_reason_rejected(admin_client):
    notification = admin_client.post(
        "/api/v1/notifications", json={"recipient": "user@example.com", "channel": "EMAIL", "event": "PM_DUE"}
    ).json()
    admin_client.post(f"/api/v1/notifications/{notification['id']}/submit")
    response = admin_client.post(f"/api/v1/notifications/{notification['id']}/reject", json={"failure_reason": "  "})
    assert response.status_code == 422


def test_engineer_cannot_approve_notification(admin_client, client, db_session):
    notification = admin_client.post(
        "/api/v1/notifications", json={"recipient": "user@example.com", "channel": "EMAIL", "event": "PM_DUE"}
    ).json()
    admin_client.post(f"/api/v1/notifications/{notification['id']}/submit")
    headers = _engineer_headers(client, db_session)
    response = client.post(f"/api/v1/notifications/{notification['id']}/approve", headers=headers)
    assert response.status_code == 403


def test_admin_can_create_and_submit_notification(admin_client):
    """create_notification is gated on notification.approve (Security hardening pass
    — see test_engineer_cannot_create_notification_with_arbitrary_recipient below for
    why), so this now exercises it through admin_client rather than an Engineer."""
    created = admin_client.post(
        "/api/v1/notifications", json={"recipient": "user@example.com", "channel": "IN_APP", "event": "PM_DUE"}
    )
    assert created.status_code == 201
    response = admin_client.post(f"/api/v1/notifications/{created.json()['id']}/submit")
    assert response.status_code == 200


def test_engineer_cannot_create_notification_with_arbitrary_recipient(client, db_session):
    """Security hardening pass — this manual-create endpoint takes a free-text
    `recipient` instead of resolving one from a configured communication rule the way
    every dispatch_event auto-send path does. Before this fix, notification.read
    (held by the non-admin BIOMEDICAL_ENGINEER role) was enough to create a
    notification pointed at any address of the caller's choosing — it must now
    require notification.approve, the same gate every other notification-mutating
    endpoint already uses."""
    headers = _engineer_headers(client, db_session)
    response = client.post(
        "/api/v1/notifications",
        json={"recipient": "attacker-controlled@example.com", "channel": "IN_APP", "event": "PM_DUE"},
        headers=headers,
    )
    assert response.status_code == 403


def test_approve_records_audit_log(admin_client, db_session):
    notification = admin_client.post(
        "/api/v1/notifications", json={"recipient": "user@example.com", "channel": "EMAIL", "event": "PM_DUE"}
    ).json()
    admin_client.post(f"/api/v1/notifications/{notification['id']}/submit")
    admin_client.post(f"/api/v1/notifications/{notification['id']}/approve")
    logs = db_session.query(AuditLog).filter(AuditLog.entity == "notification", AuditLog.action == "UPDATE").all()
    assert len(logs) >= 2  # submit + approve


# ---- List / filter / pagination ----

def test_list_notifications_filters_by_status_and_channel(admin_client):
    a = admin_client.post(
        "/api/v1/notifications", json={"recipient": "a@example.com", "channel": "IN_APP", "event": "PM_DUE"}
    ).json()
    admin_client.post("/api/v1/notifications", json={"recipient": "b@example.com", "channel": "EMAIL", "event": "PM_DUE"})
    admin_client.post(f"/api/v1/notifications/{a['id']}/submit")

    response = admin_client.get("/api/v1/notifications", params={"status_filter": "SENT"})
    assert response.json()["total"] == 1
    response = admin_client.get("/api/v1/notifications", params={"channel": "EMAIL"})
    assert response.json()["total"] == 1


def test_notifications_pagination(admin_client):
    for i in range(3):
        admin_client.post("/api/v1/notifications", json={"recipient": f"user{i}@example.com", "channel": "IN_APP", "event": "PM_DUE"})
    response = admin_client.get("/api/v1/notifications", params={"page": 1, "page_size": 2})
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


def test_list_notifications_related_entity_requires_pair(admin_client):
    response = admin_client.get("/api/v1/notifications", params={"related_entity_type": "asset"})
    assert response.status_code == 400


def test_get_notification_404(admin_client):
    response = admin_client.get("/api/v1/notifications/does-not-exist")
    assert response.status_code == 404


def test_doctor_can_read_but_not_create_notifications(admin_client, client, db_session):
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")
    # DOCTOR has neither notification.read nor notification.write per the seeded
    # RBAC matrix (notifications weren't granted to clinical roles) — verify 403.
    response = client.get("/api/v1/notifications", headers=headers)
    assert response.status_code == 403
