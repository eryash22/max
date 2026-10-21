// Step 10G — derives the complete "advanced tokens" set from the six brand colors +
// surface_mode. Mirrors backend/app/theming.py field-for-field so the live preview
// on the Theme Settings page (and the fallback for a legacy saved theme missing
// advanced_tokens) matches what the server will compute and validate.
import { bgForMinContrast, blend, darken, ensureMinContrast, mix, pickContrastingText } from "./color";
import { THEME_PRESET_BASE } from "../types";
import type { AdvancedThemeTokens, ThemeConfigInput } from "../types";

export function deriveDefaultAdvancedTokens(
  base: { primary_color: string; secondary_color: string; accent_color: string; success_color: string; warning_color: string; danger_color: string },
  surfaceMode: "light" | "dark"
): AdvancedThemeTokens {
  const dark = surfaceMode === "dark";
  const { primary_color: primary, secondary_color: secondary, accent_color: accent } = base;

  let bg: string, surface: string, border: string, text: string, text_muted: string;
  let table_header_bg: string, table_header_text: string;
  if (dark) {
    [bg, surface, border, text, text_muted] = ["#0b1220", "#131b2c", "#26314a", "#e8edf5", "#94a3b8"];
    [table_header_bg, table_header_text] = ["#1c2438", "#c2cbdb"];
  } else {
    [bg, surface, border, text, text_muted] = ["#f4f6f8", "#ffffff", "#e0e4e9", "#1a2233", "#5b6675"];
    [table_header_bg, table_header_text] = ["#eef1f5", "#445066"];
  }

  const sidebar_bg = darken(primary, 0.18);
  const sidebar_text = pickContrastingText(sidebar_bg);
  const sidebar_active_bg = accent;
  const tintTarget: "white" | "black" = dark ? "black" : "white";
  const link = ensureMinContrast(primary, bg, 4.5, pickContrastingText(bg));

  return {
    bg,
    surface,
    surface_elevated: mix(surface, "white", dark ? 0.1 : 0.06),
    input_bg: surface,
    modal_bg: surface,
    text,
    text_secondary: mix(text, tintTarget, 0.25),
    text_muted,
    text_heading: text,
    label_text: text_muted,
    text_disabled: mix(text_muted, tintTarget, 0.35),
    placeholder: text_muted,
    border,
    border_strong: mix(border, dark ? "white" : "black", 0.28),
    primary_btn_text: pickContrastingText(primary),
    secondary_btn_text: pickContrastingText(secondary),
    danger_btn_text: pickContrastingText(base.danger_color),
    link,
    link_hover: dark ? mix(link, "white", 0.15) : darken(link, 0.15),
    focus: accent,
    sidebar_bg,
    sidebar_text,
    sidebar_muted: ensureMinContrast(blend(sidebar_text, sidebar_bg, 0.35), sidebar_bg, 3.0, sidebar_text),
    sidebar_active_bg,
    sidebar_active_text: pickContrastingText(sidebar_active_bg),
    sidebar_hover_bg: mix(sidebar_bg, "white", 0.08),
    table_header_bg,
    table_header_text,
    table_row_hover: blend(surface, text, 0.05),
    badge_bg: table_header_bg,
    badge_text: table_header_text,
    success_bg: bgForMinContrast(base.success_color, dark, 3.0, dark ? 0.7 : 0.88),
    warning_bg: bgForMinContrast(base.warning_color, dark, 3.0, dark ? 0.7 : 0.88),
    danger_bg: bgForMinContrast(base.danger_color, dark, 3.0, dark ? 0.7 : 0.88),
    info_bg: bgForMinContrast(accent, dark, 3.0, dark ? 0.7 : 0.88),
    info_text: accent,
  };
}

// Full presets (base colors + derived, contrast-checked advanced_tokens). Built here
// rather than hand-written per preset so every preset always has a complete set.
export const THEME_PRESETS: Record<string, ThemeConfigInput> = Object.fromEntries(
  Object.entries(THEME_PRESET_BASE).map(([name, base]) => [
    name,
    { ...base, advanced_tokens: deriveDefaultAdvancedTokens(base, base.surface_mode) },
  ])
);

// (label, fg field, bg field, min ratio) — mirrors backend/app/schemas/theme.py
// CONTRAST_PAIRS exactly. fg/bg field names are looked up first on the resolved
// AdvancedThemeTokens object, falling back to the six base color fields.
export const CONTRAST_PAIRS: [string, string, string, number][] = [
  ["Primary text vs page background", "text", "bg", 4.5],
  ["Secondary text vs card background", "text_secondary", "surface", 4.5],
  ["Muted text vs card background", "text_muted", "surface", 3.0],
  ["Heading text vs page background", "text_heading", "bg", 3.0],
  ["Placeholder vs input background", "placeholder", "input_bg", 3.0],
  ["Table header text vs table header background", "table_header_text", "table_header_bg", 4.5],
  ["Body text vs table row background", "text", "surface", 4.5],
  ["Sidebar text vs sidebar background", "sidebar_text", "sidebar_bg", 4.5],
  ["Sidebar muted text vs sidebar background", "sidebar_muted", "sidebar_bg", 3.0],
  ["Sidebar active text vs sidebar active background", "sidebar_active_text", "sidebar_active_bg", 4.5],
  ["Primary button text vs primary button background", "primary_btn_text", "primary_color", 4.5],
  ["Secondary button text vs secondary button background", "secondary_btn_text", "secondary_color", 4.5],
  ["Danger button text vs danger button background", "danger_btn_text", "danger_color", 4.5],
  ["Badge text vs badge background", "badge_text", "badge_bg", 3.0],
  ["Link vs page background", "link", "bg", 4.5],
  ["Success text vs success background", "success_color", "success_bg", 3.0],
  ["Warning text vs warning background", "warning_color", "warning_bg", 3.0],
  ["Danger text vs danger background", "danger_color", "danger_bg", 3.0],
  ["Info text vs info background", "info_text", "info_bg", 3.0],
];
