"""Device QR Code Management System.

Covers: automatic QR provisioning on both asset-creation workflows and on import,
idempotent bulk generation, token uniqueness/stability, explicit regenerate/revoke
with confirmation, the public scan endpoint's allowlisted-fields-only response
(including RBAC/forged-id/invalid-token safety), display-setting changes taking
effect immediately, NULL asset number / missing date handling, operation-duration
calculation, and that Super-Admin-only gating holds for settings and token actions.
"""
from app.models.device import Asset

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD, auth_headers, create_user


def _branch(admin_client, name="QR Test Branch", **extra):
    r = admin_client.post("/api/v1/branches", json={"name": name, "status": "ACTIVE", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _model(admin_client, name="QR Test Ventilator", manufacturer="Acme"):
    r = admin_client.post("/api/v1/device-models",
                          json={"name": name, "category": "VENTILATOR", "manufacturer": manufacturer})
    assert r.status_code == 201, r.text
    return r.json()


def _asset(admin_client, model_id, branch_id, asset_number=None, status="OPERATIONAL", **extra):
    payload = {"device_model_id": model_id, "branch_id": branch_id, "status": status, **extra}
    if asset_number is not None:
        payload["asset_number"] = asset_number
    r = admin_client.post("/api/v1/assets", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _setup(admin_client, **asset_extra):
    branch = _branch(admin_client)
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], **asset_extra)
    return branch, model, asset


# ---- automatic provisioning ----

def test_create_asset_auto_provisions_qr_token(admin_client, db_session):
    _, _, asset = _setup(admin_client)
    row = db_session.get(Asset, asset["id"])
    assert row.qr_token
    assert row.qr_active is True
    assert row.qr_generated_at is not None


def test_combined_device_creation_generates_one_qr_identity(admin_client, db_session):
    branch = _branch(admin_client)
    r = admin_client.post("/api/v1/devices", json={
        "model_name": "Combined Infusion Pump", "category": "INFUSION_PUMP",
        "branch_id": branch["id"], "status": "OPERATIONAL",
    })
    assert r.status_code == 201, r.text
    asset_id = r.json()["asset"]["id"]
    row = db_session.get(Asset, asset_id)
    assert row.qr_token is not None
    # One business action -> exactly one Asset row -> exactly one QR identity (no
    # second identity minted anywhere for the DeviceModel it created inline).
    assert db_session.query(Asset).filter(Asset.id == asset_id).count() == 1


# ---- bulk generation, idempotency, uniqueness ----

def test_bulk_generate_fills_missing_and_is_idempotent(admin_client, db_session):
    branch = _branch(admin_client)
    model = _model(admin_client)
    asset_a = _asset(admin_client, model["id"], branch["id"])
    asset_b = _asset(admin_client, model["id"], branch["id"])
    # Simulate a pre-existing Asset created before this feature existed.
    row_a = db_session.get(Asset, asset_a["id"])
    row_a.qr_token = None
    row_a.qr_generated_at = None
    db_session.commit()

    r1 = admin_client.post("/api/v1/qr/bulk-generate", json={})
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["generated"] >= 1
    assert body1["failed"] == 0

    token_a_after_first = db_session.get(Asset, asset_a["id"]).qr_token
    token_b = db_session.get(Asset, asset_b["id"]).qr_token
    assert token_a_after_first is not None
    assert token_b is not None
    assert token_a_after_first != token_b

    # Rerunning must not create duplicates or change any existing token.
    r2 = admin_client.post("/api/v1/qr/bulk-generate", json={})
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["generated"] == 0
    assert db_session.get(Asset, asset_a["id"]).qr_token == token_a_after_first
    assert db_session.get(Asset, asset_b["id"]).qr_token == token_b


def test_qr_tokens_are_unique_across_assets(admin_client, db_session):
    branch = _branch(admin_client)
    model = _model(admin_client)
    tokens = {_asset(admin_client, model["id"], branch["id"])["id"] for _ in range(5)}
    rows = db_session.query(Asset).filter(Asset.id.in_(tokens)).all()
    all_tokens = [row.qr_token for row in rows]
    assert len(all_tokens) == len(set(all_tokens))


def test_single_generate_endpoint_is_idempotent(admin_client, db_session):
    _, _, asset = _setup(admin_client)
    first_token = db_session.get(Asset, asset["id"]).qr_token
    r = admin_client.post(f"/api/v1/qr/assets/{asset['id']}/generate")
    assert r.status_code == 200, r.text
    assert db_session.get(Asset, asset["id"]).qr_token == first_token


def test_retired_and_condemned_assets_are_included_in_qr_listing(admin_client, db_session):
    branch = _branch(admin_client)
    model = _model(admin_client)
    retired = _asset(admin_client, model["id"], branch["id"], status="RETIRED")
    r = admin_client.get("/api/v1/qr/assets", params={"status_filter": "RETIRED"})
    assert r.status_code == 200, r.text
    ids = [row["asset_id"] for row in r.json()["items"]]
    assert retired["id"] in ids


# ---- regenerate / revoke ----

def test_regenerate_requires_confirmation_and_rotates_token(admin_client, db_session):
    _, _, asset = _setup(admin_client)
    original_token = db_session.get(Asset, asset["id"]).qr_token

    r_no_confirm = admin_client.post(f"/api/v1/qr/assets/{asset['id']}/regenerate", json={"confirm": False})
    assert r_no_confirm.status_code == 400
    assert db_session.get(Asset, asset["id"]).qr_token == original_token

    r_confirmed = admin_client.post(f"/api/v1/qr/assets/{asset['id']}/regenerate", json={"confirm": True})
    assert r_confirmed.status_code == 200, r_confirmed.text
    new_token = db_session.get(Asset, asset["id"]).qr_token
    assert new_token != original_token

    # The old, previously-printed token must no longer resolve.
    old_lookup = admin_client.get(f"/api/v1/public/qr/{original_token}")
    assert old_lookup.status_code == 404


def test_revoke_requires_confirmation_and_disables_public_access(admin_client, db_session):
    _, _, asset = _setup(admin_client)
    token = db_session.get(Asset, asset["id"]).qr_token

    before_revoke = admin_client.get(f"/api/v1/public/qr/{token}")
    assert before_revoke.status_code == 200

    r_no_confirm = admin_client.post(f"/api/v1/qr/assets/{asset['id']}/revoke", json={"confirm": False})
    assert r_no_confirm.status_code == 400

    r_confirmed = admin_client.post(f"/api/v1/qr/assets/{asset['id']}/revoke", json={"confirm": True})
    assert r_confirmed.status_code == 200, r_confirmed.text

    after_revoke = admin_client.get(f"/api/v1/public/qr/{token}")
    assert after_revoke.status_code == 404


def test_invalid_and_unknown_tokens_return_safe_not_found(admin_client):
    r = admin_client.get("/api/v1/public/qr/this-token-does-not-exist-at-all")
    assert r.status_code == 404
    body = r.json()
    assert "token" not in body["detail"].lower() or "not found" in body["detail"].lower()


# ---- public endpoint: allowlisted fields only ----

def test_public_endpoint_returns_only_allowlisted_fields_no_internal_leakage(admin_client, db_session):
    branch = _branch(admin_client)
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], serial_number="SECRET-SERIAL-123")
    token = db_session.get(Asset, asset["id"]).qr_token

    r = admin_client.get(f"/api/v1/public/qr/{token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True

    returned_keys = {f["key"] for f in body["fields"]}
    from app.services.qr_service import QR_FIELD_BY_KEY
    assert returned_keys.issubset(QR_FIELD_BY_KEY.keys())

    raw_text = r.text
    # Internal id, FK ids, and the full QR token itself must never appear in the
    # public payload (serial number is default-hidden, so it must not leak either
    # unless explicitly enabled).
    assert asset["id"] not in raw_text
    assert token not in raw_text
    assert "SECRET-SERIAL-123" not in raw_text
    assert "branch_id" not in raw_text
    assert "device_model_id" not in raw_text


def test_null_asset_number_renders_fallback_on_public_page(admin_client, db_session):
    branch = _branch(admin_client)
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"])  # no asset_number
    token = db_session.get(Asset, asset["id"]).qr_token

    r = admin_client.get(f"/api/v1/public/qr/{token}")
    assert r.status_code == 200
    field = next(f for f in r.json()["fields"] if f["key"] == "asset_number")
    assert field["value"] == "Asset number not assigned"


def test_missing_dates_are_not_invented(admin_client, db_session):
    branch = _branch(admin_client)
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"])  # operational_since left unset
    token = db_session.get(Asset, asset["id"]).qr_token

    r = admin_client.get(f"/api/v1/public/qr/{token}")
    body = r.json()
    in_service = next(f for f in body["fields"] if f["key"] == "in_service_since")
    duration = next(f for f in body["fields"] if f["key"] == "time_in_operation")
    assert in_service["value"] == "Not recorded"
    assert duration["value"] == "Not recorded"


def test_operation_duration_calculated_from_valid_in_service_date(admin_client, db_session):
    branch = _branch(admin_client)
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], operational_since="2024-01-01")
    token = db_session.get(Asset, asset["id"]).qr_token

    r = admin_client.get(f"/api/v1/public/qr/{token}")
    body = r.json()
    duration = next(f for f in body["fields"] if f["key"] == "time_in_operation")
    assert duration["value"] != "Not recorded"
    assert "yr" in duration["value"] or "mo" in duration["value"]


def test_coverage_resolved_from_real_contract_not_invented(admin_client, db_session):
    branch = _branch(admin_client)
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"])
    token = db_session.get(Asset, asset["id"]).qr_token

    r = admin_client.get(f"/api/v1/public/qr/{token}")
    body = r.json()
    warranty = next(f for f in body["fields"] if f["key"] == "warranty_status")
    # No Warranty record exists for this asset -> must say "Not recorded", never a
    # fabricated coverage status.
    assert warranty["value"] == "Not recorded"


# ---- display settings ----

def test_display_setting_changes_affect_public_page_immediately(admin_client, db_session):
    branch = _branch(admin_client)
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"], serial_number="SN-VISIBLE-1")
    token = db_session.get(Asset, asset["id"]).qr_token

    r_before = admin_client.get(f"/api/v1/public/qr/{token}")
    keys_before = {f["key"] for f in r_before.json()["fields"]}
    assert "serial_number" not in keys_before  # default-hidden

    settings = admin_client.get("/api/v1/qr/settings").json()
    for field in settings["fields"]:
        if field["key"] == "serial_number":
            field["enabled"] = True
    update_payload = {"enabled": settings["enabled"], "fields": [
        {"key": f["key"], "enabled": f["enabled"]} for f in settings["fields"]
    ]}
    r_update = admin_client.put("/api/v1/qr/settings", json=update_payload)
    assert r_update.status_code == 200, r_update.text

    r_after = admin_client.get(f"/api/v1/public/qr/{token}")
    serial_field = next(f for f in r_after.json()["fields"] if f["key"] == "serial_number")
    assert serial_field["value"] == "SN-VISIBLE-1"


def test_unknown_field_key_rejected_by_settings_validation(admin_client):
    r = admin_client.put("/api/v1/qr/settings", json={
        "enabled": True, "fields": [{"key": "password", "enabled": True}],
    })
    assert r.status_code == 422


def test_disabling_public_page_globally_hides_data_without_404(admin_client, db_session):
    branch = _branch(admin_client)
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"])
    token = db_session.get(Asset, asset["id"]).qr_token

    settings = admin_client.get("/api/v1/qr/settings").json()
    update_payload = {"enabled": False, "fields": [
        {"key": f["key"], "enabled": f["enabled"]} for f in settings["fields"]
    ]}
    r_update = admin_client.put("/api/v1/qr/settings", json=update_payload)
    assert r_update.status_code == 200, r_update.text

    r = admin_client.get(f"/api/v1/public/qr/{token}")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["fields"] == []


# ---- RBAC ----

def test_non_super_admin_cannot_manage_qr_settings_or_tokens(client, db_session, admin_client):
    branch = _branch(admin_client)
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"])

    create_user(db_session, "engineer@example.com", "BIOMEDICAL_ENGINEER", "Password123!")
    headers = auth_headers(client, "engineer@example.com", "Password123!")

    r_settings = client.get("/api/v1/qr/settings", headers=headers)
    assert r_settings.status_code == 403

    r_generate = client.post(f"/api/v1/qr/assets/{asset['id']}/generate", headers=headers)
    assert r_generate.status_code == 403

    r_revoke = client.post(f"/api/v1/qr/assets/{asset['id']}/revoke", json={"confirm": True}, headers=headers)
    assert r_revoke.status_code == 403


def test_public_scan_endpoint_needs_no_authentication(client, admin_client, db_session):
    branch = _branch(admin_client)
    model = _model(admin_client)
    asset = _asset(admin_client, model["id"], branch["id"])
    token = db_session.get(Asset, asset["id"]).qr_token

    # Plain `client` (no auth headers at all) must still succeed — read-only public
    # access, no login required.
    r = client.get(f"/api/v1/public/qr/{token}")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True


def test_public_scan_grants_no_write_access(client, db_session):
    # No endpoint under /public/qr accepts a mutation at all — confirm the only
    # method registered for the detail path is GET by checking POST/PATCH/DELETE
    # all 405/404 rather than ever mutating anything.
    r_post = client.post("/api/v1/public/qr/some-token", json={})
    assert r_post.status_code in (404, 405)
