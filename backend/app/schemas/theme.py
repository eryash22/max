import re

from pydantic import BaseModel, field_validator, model_validator

from ..utils.color import contrast_ratio

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
SURFACE_MODES = ["light", "dark"]

COLOR_FIELDS = (
    "primary_color", "secondary_color", "accent_color", "success_color", "warning_color", "danger_color",
)

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

# (label, fg_field, bg_field, min_ratio) — fg/bg fields are looked up first on the
# AdvancedThemeTokens payload, falling back to the top-level ThemeConfigIn color
# fields for the ones that live there (e.g. success_color doubles as success text).
# Thresholds: 4.5 = WCAG AA normal text, 3.0 = WCAG AA large/bold text (buttons,
# badges, headings) — matches Step 10G's "pay special attention to" list.
CONTRAST_PAIRS = (
    ("Primary text vs page background", "text", "bg", 4.5),
    ("Secondary text vs card background", "text_secondary", "surface", 4.5),
    ("Muted text vs card background", "text_muted", "surface", 3.0),
    ("Heading text vs page background", "text_heading", "bg", 3.0),
    ("Placeholder vs input background", "placeholder", "input_bg", 3.0),
    ("Table header text vs table header background", "table_header_text", "table_header_bg", 4.5),
    ("Body text vs table row background", "text", "surface", 4.5),
    ("Sidebar text vs sidebar background", "sidebar_text", "sidebar_bg", 4.5),
    ("Sidebar muted text vs sidebar background", "sidebar_muted", "sidebar_bg", 3.0),
    ("Sidebar active text vs sidebar active background", "sidebar_active_text", "sidebar_active_bg", 4.5),
    ("Primary button text vs primary button background", "primary_btn_text", "primary_color", 4.5),
    ("Secondary button text vs secondary button background", "secondary_btn_text", "secondary_color", 4.5),
    ("Danger button text vs danger button background", "danger_btn_text", "danger_color", 4.5),
    ("Badge text vs badge background", "badge_text", "badge_bg", 3.0),
    ("Link vs page background", "link", "bg", 4.5),
    ("Success text vs success background", "success_color", "success_bg", 3.0),
    ("Warning text vs warning background", "warning_color", "warning_bg", 3.0),
    ("Danger text vs danger background", "danger_color", "danger_bg", 3.0),
    ("Info text vs info background", "info_text", "info_bg", 3.0),
)


def _validate_hex_color(value: str) -> str:
    """Server-side hex validation (Part 11) — every color field must match
    `^#[0-9A-Fa-f]{6}$` or the request is rejected with 422, never stored. This is the
    one thing that prevents arbitrary CSS/markup injection: values are only ever
    consumed as literal CSS custom-property values downstream, never interpolated
    into markup/HTML/scripts, so a strict hex-only vocabulary closes that off
    entirely rather than trying to sanitize free text."""
    if not HEX_COLOR_RE.match(value):
        raise ValueError("must be a hex color in the form #RRGGBB")
    return value


class AdvancedThemeTokens(BaseModel):
    bg: str
    surface: str
    surface_elevated: str
    input_bg: str
    modal_bg: str
    text: str
    text_secondary: str
    text_muted: str
    text_heading: str
    label_text: str
    text_disabled: str
    placeholder: str
    border: str
    border_strong: str
    primary_btn_text: str
    secondary_btn_text: str
    danger_btn_text: str
    link: str
    link_hover: str
    focus: str
    sidebar_bg: str
    sidebar_text: str
    sidebar_muted: str
    sidebar_active_bg: str
    sidebar_active_text: str
    sidebar_hover_bg: str
    table_header_bg: str
    table_header_text: str
    table_row_hover: str
    badge_bg: str
    badge_text: str
    success_bg: str
    warning_bg: str
    danger_bg: str
    info_bg: str
    info_text: str

    @field_validator(*ADVANCED_TOKEN_FIELDS)
    @classmethod
    def _validate_all_hex(cls, value: str) -> str:
        return _validate_hex_color(value)


def _find_unsafe_pairs(payload: dict) -> list[str]:
    tokens = payload.get("advanced_tokens") or {}

    def resolve(field: str) -> str | None:
        if field in tokens:
            return tokens[field]
        return payload.get(field)

    problems = []
    for label, fg_field, bg_field, min_ratio in CONTRAST_PAIRS:
        fg, bg = resolve(fg_field), resolve(bg_field)
        if not fg or not bg:
            continue
        ratio = contrast_ratio(fg, bg)
        if ratio < min_ratio:
            problems.append(f"{label}: contrast {ratio:.2f}:1 is below the required {min_ratio:.1f}:1")
    return problems


class ThemeConfigIn(BaseModel):
    primary_color: str
    secondary_color: str
    accent_color: str
    success_color: str
    warning_color: str
    danger_color: str
    surface_mode: str = "light"
    preset_name: str | None = None
    advanced_tokens: AdvancedThemeTokens

    _validate_primary = field_validator("primary_color")(_validate_hex_color)
    _validate_secondary = field_validator("secondary_color")(_validate_hex_color)
    _validate_accent = field_validator("accent_color")(_validate_hex_color)
    _validate_success = field_validator("success_color")(_validate_hex_color)
    _validate_warning = field_validator("warning_color")(_validate_hex_color)
    _validate_danger = field_validator("danger_color")(_validate_hex_color)

    @field_validator("surface_mode")
    @classmethod
    def _valid_surface_mode(cls, value: str) -> str:
        if value not in SURFACE_MODES:
            raise ValueError(f"surface_mode must be one of {', '.join(SURFACE_MODES)}")
        return value

    @model_validator(mode="after")
    def _validate_contrast(self):
        problems = _find_unsafe_pairs({**self.model_dump(exclude={"advanced_tokens"}),
                                        "advanced_tokens": self.advanced_tokens.model_dump()})
        if problems:
            raise ValueError("Unreadable color combination(s): " + "; ".join(problems))
        return self


class ThemeConfigOut(BaseModel):
    id: str
    primary_color: str
    secondary_color: str
    accent_color: str
    success_color: str
    warning_color: str
    danger_color: str
    surface_mode: str
    preset_name: str | None = None
    advanced_tokens: AdvancedThemeTokens
    updated_by_user_id: str | None = None

    model_config = {"from_attributes": True}
