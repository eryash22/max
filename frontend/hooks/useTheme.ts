"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { darken } from "../lib/color";
import { deriveDefaultAdvancedTokens } from "../lib/theming";
import type { AdvancedThemeTokens, ThemeConfig } from "../types";

/** Applies a `ThemeConfig` (or draft of one) as CSS custom properties on
 * `document.documentElement`, extending the exact `--color-*` token names defined in
 * styles/globals.css — never a parallel token scheme. Step 10G: the six brand colors
 * still map onto their existing tokens; every other surface/text/border/interactive/
 * sidebar/table/badge/status token comes from `advanced_tokens`, falling back to the
 * derived defaults for that surface_mode if a field is somehow missing (a legacy
 * saved theme from before Step 10G) — so no CSS variable used anywhere in the app is
 * ever left undefined. */
export function applyTheme(theme: {
  primary_color: string;
  secondary_color: string;
  accent_color: string;
  success_color: string;
  warning_color: string;
  danger_color: string;
  surface_mode: "light" | "dark";
  advanced_tokens?: Partial<AdvancedThemeTokens>;
}) {
  if (typeof document === "undefined") return;
  const root = document.documentElement.style;
  const tokens = { ...deriveDefaultAdvancedTokens(theme, theme.surface_mode), ...(theme.advanced_tokens ?? {}) };

  root.setProperty("--color-primary", theme.primary_color);
  root.setProperty("--color-primary-dark", darken(theme.primary_color, 0.18));
  root.setProperty("--color-secondary", theme.secondary_color);
  root.setProperty("--color-accent", theme.accent_color);

  root.setProperty("--color-success-text", theme.success_color);
  root.setProperty("--color-success-bg", tokens.success_bg);
  root.setProperty("--color-warning-text", theme.warning_color);
  root.setProperty("--color-warning-bg", tokens.warning_bg);
  root.setProperty("--color-danger-text", theme.danger_color);
  root.setProperty("--color-danger-bg", tokens.danger_bg);
  root.setProperty("--color-info-text", tokens.info_text);
  root.setProperty("--color-info-bg", tokens.info_bg);

  root.setProperty("--color-bg", tokens.bg);
  root.setProperty("--color-surface", tokens.surface);
  root.setProperty("--color-surface-elevated", tokens.surface_elevated);
  root.setProperty("--color-input-bg", tokens.input_bg);
  root.setProperty("--color-modal-bg", tokens.modal_bg);
  root.setProperty("--color-border", tokens.border);
  root.setProperty("--color-border-strong", tokens.border_strong);

  root.setProperty("--color-text", tokens.text);
  root.setProperty("--color-text-secondary", tokens.text_secondary);
  root.setProperty("--color-text-muted", tokens.text_muted);
  root.setProperty("--color-text-heading", tokens.text_heading);
  root.setProperty("--color-label-text", tokens.label_text);
  root.setProperty("--color-text-disabled", tokens.text_disabled);
  root.setProperty("--color-placeholder", tokens.placeholder);

  root.setProperty("--color-primary-btn-text", tokens.primary_btn_text);
  root.setProperty("--color-secondary-btn-text", tokens.secondary_btn_text);
  root.setProperty("--color-danger-btn-text", tokens.danger_btn_text);
  root.setProperty("--color-link", tokens.link);
  root.setProperty("--color-link-hover", tokens.link_hover);
  root.setProperty("--color-focus", tokens.focus);

  root.setProperty("--color-sidebar-bg", tokens.sidebar_bg);
  root.setProperty("--color-sidebar-text", tokens.sidebar_text);
  root.setProperty("--color-sidebar-muted", tokens.sidebar_muted);
  root.setProperty("--color-sidebar-active-bg", tokens.sidebar_active_bg);
  root.setProperty("--color-sidebar-active-text", tokens.sidebar_active_text);
  root.setProperty("--color-sidebar-hover-bg", tokens.sidebar_hover_bg);

  root.setProperty("--color-table-header-bg", tokens.table_header_bg);
  root.setProperty("--color-table-header-text", tokens.table_header_text);
  root.setProperty("--color-table-row-hover", tokens.table_row_hover);

  root.setProperty("--color-badge-bg", tokens.badge_bg);
  root.setProperty("--color-badge-text", tokens.badge_text);

  // Kept for any remaining .neutral-bg/-text consumers (e.g. calendar "outside day")
  // that intentionally want a plain neutral tone rather than the table/badge tokens.
  root.setProperty("--color-neutral-bg", tokens.table_header_bg);
  root.setProperty("--color-neutral-text", tokens.table_header_text);
}

/** Fetches the centrally-stored theme (`GET /api/v1/theme`, open to any authenticated
 * user per the backend's "non-admin users only consume" rule) and applies it on load.
 * Never writes — saving is settings/theme/page.tsx's job, gated on `settings.manage`. */
export function useTheme() {
  const [theme, setTheme] = useState<ThemeConfig | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await api.get<ThemeConfig>("/api/v1/theme");
      setTheme(result);
      applyTheme(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load theme.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return { theme, error, reload: load };
}
