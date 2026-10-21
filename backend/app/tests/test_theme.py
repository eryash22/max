"""Final admin/inventory/vendor/dashboard polish pass — Part 11: admin-controlled
theme configuration. Extended in Step 10G with the advanced token set (surfaces,
text, borders, interactive, sidebar, tables, badges) and WCAG contrast validation."""

from app.models.audit import AuditLog
from app.schemas.theme import CONTRAST_PAIRS
from app.theming import derive_default_advanced_tokens
from app.utils.color import contrast_ratio

from .conftest import auth_headers, create_user

BASE_COLORS = {
    "primary_color": "#112233",
    "secondary_color": "#445566",
    "accent_color": "#778899",
    "success_color": "#00AA00",
    "warning_color": "#B36B00",
    "danger_color": "#CC0000",
}

VALID_PAYLOAD = {
    **BASE_COLORS,
    "surface_mode": "dark",
    "preset_name": "Custom",
    "advanced_tokens": derive_default_advanced_tokens(BASE_COLORS, "dark"),
}


def test_get_theme_returns_default_before_any_save(admin_client):
    response = admin_client.get("/api/v1/theme")
    assert response.status_code == 200
    body = response.json()
    assert body["primary_color"].startswith("#")
    assert body["surface_mode"] in ("light", "dark")
    # The whole point of Step 10G: no token is ever missing/undefined.
    for field in body["advanced_tokens"]:
        assert body["advanced_tokens"][field].startswith("#")


def test_get_theme_available_to_any_authenticated_user(client, db_session):
    create_user(db_session, "doc@example.com", "DOCTOR", "Password123!")
    headers = auth_headers(client, "doc@example.com", "Password123!")
    response = client.get("/api/v1/theme", headers=headers)
    assert response.status_code == 200


def test_super_admin_can_save_valid_theme(admin_client):
    response = admin_client.put("/api/v1/theme", json=VALID_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["primary_color"] == "#112233"
    assert body["surface_mode"] == "dark"
    assert body["preset_name"] == "Custom"
    assert body["advanced_tokens"]["sidebar_bg"] == VALID_PAYLOAD["advanced_tokens"]["sidebar_bg"]


def test_theme_save_persists_and_is_reflected_on_get(admin_client):
    admin_client.put("/api/v1/theme", json=VALID_PAYLOAD)
    response = admin_client.get("/api/v1/theme")
    assert response.status_code == 200
    body = response.json()
    assert body["primary_color"] == "#112233"
    assert body["danger_color"] == "#CC0000"
    assert body["advanced_tokens"]["table_header_bg"] == VALID_PAYLOAD["advanced_tokens"]["table_header_bg"]


def test_non_settings_manage_user_gets_403_on_put(client, db_session):
    create_user(db_session, "biomedadmin@example.com", "BIOMEDICAL_ADMIN", "Password123!")
    headers = auth_headers(client, "biomedadmin@example.com", "Password123!")
    response = client.put("/api/v1/theme", json=VALID_PAYLOAD, headers=headers)
    assert response.status_code == 403

    # Confirm the advanced-token payload didn't leak through either.
    get_response = client.get("/api/v1/theme", headers=headers)
    assert get_response.json()["advanced_tokens"] != VALID_PAYLOAD["advanced_tokens"]


def test_invalid_color_string_rejected_and_not_stored(admin_client):
    bad_payload = dict(VALID_PAYLOAD)
    bad_payload["primary_color"] = "red"
    response = admin_client.put("/api/v1/theme", json=bad_payload)
    assert response.status_code == 422

    # Confirm nothing was stored.
    get_response = admin_client.get("/api/v1/theme")
    assert get_response.json()["primary_color"] != "red"


def test_invalid_color_script_like_input_rejected(admin_client):
    bad_payload = dict(VALID_PAYLOAD)
    bad_payload["primary_color"] = "<script>alert(1)</script>"
    response = admin_client.put("/api/v1/theme", json=bad_payload)
    assert response.status_code == 422


def test_invalid_color_hex_without_hash_rejected(admin_client):
    bad_payload = dict(VALID_PAYLOAD)
    bad_payload["danger_color"] = "#zzzzzz"
    response = admin_client.put("/api/v1/theme", json=bad_payload)
    assert response.status_code == 422


def test_invalid_surface_mode_rejected(admin_client):
    bad_payload = dict(VALID_PAYLOAD)
    bad_payload["surface_mode"] = "sepia"
    response = admin_client.put("/api/v1/theme", json=bad_payload)
    assert response.status_code == 422


def test_invalid_advanced_token_hex_rejected(admin_client):
    bad_payload = {**VALID_PAYLOAD, "advanced_tokens": {**VALID_PAYLOAD["advanced_tokens"], "sidebar_bg": "not-a-color"}}
    response = admin_client.put("/api/v1/theme", json=bad_payload)
    assert response.status_code == 422

    get_response = admin_client.get("/api/v1/theme")
    assert get_response.json()["advanced_tokens"]["sidebar_bg"] != "not-a-color"


def test_unreadable_contrast_combination_rejected_and_not_stored(admin_client):
    # White-on-white text: guaranteed to fail every contrast pair that touches it.
    unsafe_tokens = {**VALID_PAYLOAD["advanced_tokens"], "text": "#ffffff", "bg": "#ffffff"}
    bad_payload = {**VALID_PAYLOAD, "advanced_tokens": unsafe_tokens}
    response = admin_client.put("/api/v1/theme", json=bad_payload)
    assert response.status_code == 422
    assert "contrast" in response.text.lower()

    get_response = admin_client.get("/api/v1/theme")
    assert get_response.json()["advanced_tokens"]["text"] != "#ffffff"


def test_theme_update_is_audited(admin_client, db_session):
    admin_client.put("/api/v1/theme", json=VALID_PAYLOAD)
    log = db_session.query(AuditLog).filter(
        AuditLog.entity == "theme_config", AuditLog.action == "UPDATE"
    ).first()
    assert log is not None
    assert log.new_value["primary_color"] == "#112233"


def test_all_presets_pass_contrast_validation():
    """Regression guard: every shipped preset's derived token set must clear the
    same WCAG thresholds an admin's custom theme is held to."""
    from app.theming import derive_default_advanced_tokens as derive

    presets = {
        "Clinical Blue": {
            "primary_color": "#2563EB", "secondary_color": "#0EA5E9", "accent_color": "#7C3AED",
            "success_color": "#16A34A", "warning_color": "#D97706", "danger_color": "#DC2626",
        },
        "Biomedical Teal": {
            "primary_color": "#0F766E", "secondary_color": "#14B8A6", "accent_color": "#0EA5E9",
            "success_color": "#15803D", "warning_color": "#B45309", "danger_color": "#B91C1C",
        },
        "Professional Navy": {
            "primary_color": "#163A5F", "secondary_color": "#3B5A80", "accent_color": "#0F766E",
            "success_color": "#147A45", "warning_color": "#92650A", "danger_color": "#A3231F",
        },
        "Clean Green": {
            "primary_color": "#15803D", "secondary_color": "#22C55E", "accent_color": "#0D9488",
            "success_color": "#16A34A", "warning_color": "#CA8A04", "danger_color": "#DC2626",
        },
        "Dark Command Center": {
            "primary_color": "#3B82F6", "secondary_color": "#38BDF8", "accent_color": "#A78BFA",
            "success_color": "#4ADE80", "warning_color": "#FBBF24", "danger_color": "#F87171",
        },
    }
    modes = {"Dark Command Center": "dark"}

    for name, base in presets.items():
        mode = modes.get(name, "light")
        tokens = derive(base, mode)
        payload = {**base, "advanced_tokens": tokens}
        failures = []
        for label, fg_field, bg_field, min_ratio in CONTRAST_PAIRS:
            fg = tokens.get(fg_field, payload.get(fg_field))
            bg = tokens.get(bg_field, payload.get(bg_field))
            ratio = contrast_ratio(fg, bg)
            if ratio < min_ratio:
                failures.append(f"{name} — {label}: {ratio:.2f} < {min_ratio}")
        assert not failures, "\n".join(failures)
