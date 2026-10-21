"""Step 10G — derives the complete "advanced tokens" set (surfaces, text, borders,
interactive, sidebar, tables, badges, status backgrounds) from the six brand colors +
surface_mode. Mirrored in `frontend/lib/theming.ts` so both sides compute the same
defaults; this is the single source of truth for what an unconfigured token resolves
to, which is what closes off the "reference a token that was never defined" bug class
that caused the original dark-mode readability issues.
"""
from .utils.color import bg_for_min_contrast, blend, darken, ensure_min_contrast, mix, pick_contrasting_text

ADVANCED_TOKEN_FIELDS = (
    "bg", "surface", "surface_elevated", "input_bg", "modal_bg",
    "text", "text_secondary", "text_muted", "text_heading", "label_text", "text_disabled", "placeholder",
    "border", "border_strong",
    "primary_btn_text", "secondary_btn_text", "danger_btn_text", "link", "link_hover", "focus",
    "sidebar_bg", "sidebar_text", "sidebar_muted", "sidebar_active_bg", "sidebar_active_text", "sidebar_hover_bg",
    "table_header_bg", "table_header_text", "table_row_hover",
    "badge_bg", "badge_text",
    "success_bg", "warning_bg", "danger_bg", "info_bg", "info_text",
)


def derive_default_advanced_tokens(base: dict, surface_mode: str) -> dict:
    dark = surface_mode == "dark"
    primary = base["primary_color"]
    secondary = base["secondary_color"]
    accent = base["accent_color"]

    if dark:
        bg, surface, border, text, text_muted = "#0b1220", "#131b2c", "#26314a", "#e8edf5", "#94a3b8"
        table_header_bg, table_header_text = "#1c2438", "#c2cbdb"
    else:
        bg, surface, border, text, text_muted = "#f4f6f8", "#ffffff", "#e0e4e9", "#1a2233", "#5b6675"
        table_header_bg, table_header_text = "#eef1f5", "#445066"

    sidebar_bg = darken(primary, 0.18)
    sidebar_text = pick_contrasting_text(sidebar_bg)
    sidebar_active_bg = accent
    tint_target = "black" if dark else "white"

    # Link reuses the brand primary color where possible, but a dark primary on a
    # dark page background (or a light primary on a light one) would be unreadable —
    # nudge it toward whichever of black/white already contrasts well against `bg`.
    link = ensure_min_contrast(primary, bg, 4.5, pick_contrasting_text(bg))

    return {
        "bg": bg,
        "surface": surface,
        "surface_elevated": mix(surface, "white", 0.06 if not dark else 0.10),
        "input_bg": surface,
        "modal_bg": surface,
        "text": text,
        "text_secondary": mix(text, tint_target, 0.25),
        "text_muted": text_muted,
        "text_heading": text,
        "label_text": text_muted,
        "text_disabled": mix(text_muted, tint_target, 0.35),
        "placeholder": text_muted,
        "border": border,
        "border_strong": mix(border, "black" if not dark else "white", 0.28),
        "primary_btn_text": pick_contrasting_text(primary),
        "secondary_btn_text": pick_contrasting_text(secondary),
        "danger_btn_text": pick_contrasting_text(base["danger_color"]),
        "link": link,
        "link_hover": darken(link, 0.15) if not dark else mix(link, "white", 0.15),
        "focus": accent,
        "sidebar_bg": sidebar_bg,
        "sidebar_text": sidebar_text,
        "sidebar_muted": ensure_min_contrast(blend(sidebar_text, sidebar_bg, 0.35), sidebar_bg, 3.0, sidebar_text),
        "sidebar_active_bg": sidebar_active_bg,
        "sidebar_active_text": pick_contrasting_text(sidebar_active_bg),
        "sidebar_hover_bg": mix(sidebar_bg, "white", 0.08),
        "table_header_bg": table_header_bg,
        "table_header_text": table_header_text,
        "table_row_hover": blend(surface, text, 0.05),
        "badge_bg": table_header_bg,
        "badge_text": table_header_text,
        "success_bg": bg_for_min_contrast(base["success_color"], dark, 3.0, 0.7 if dark else 0.88),
        "warning_bg": bg_for_min_contrast(base["warning_color"], dark, 3.0, 0.7 if dark else 0.88),
        "danger_bg": bg_for_min_contrast(base["danger_color"], dark, 3.0, 0.7 if dark else 0.88),
        "info_bg": bg_for_min_contrast(accent, dark, 3.0, 0.7 if dark else 0.88),
        "info_text": accent,
    }
