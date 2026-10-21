"use client";

import { useEffect, useMemo, useState } from "react";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { useAuth } from "../../../hooks/useAuth";
import { applyTheme, useTheme } from "../../../hooks/useTheme";
import { contrastRatio } from "../../../lib/color";
import { api, ApiError } from "../../../lib/api";
import { CONTRAST_PAIRS, THEME_PRESETS } from "../../../lib/theming";
import type { AdvancedThemeTokens, ThemeConfig, ThemeConfigInput } from "../../../types";

const HEX_RE = /^#[0-9A-Fa-f]{6}$/;

type FieldSource = "base" | "advanced";
type Field = { key: string; label: string; source: FieldSource };

const SECTIONS: { title: string; hint?: string; fields: Field[] }[] = [
  {
    title: "Backgrounds & surfaces",
    fields: [
      { key: "bg", label: "App background", source: "advanced" },
      { key: "surface", label: "Card background", source: "advanced" },
      { key: "surface_elevated", label: "Elevated surface", source: "advanced" },
      { key: "input_bg", label: "Input background", source: "advanced" },
      { key: "modal_bg", label: "Modal / dialog background", source: "advanced" },
    ],
  },
  {
    title: "Text",
    fields: [
      { key: "text", label: "Primary text", source: "advanced" },
      { key: "text_secondary", label: "Secondary text", source: "advanced" },
      { key: "text_muted", label: "Muted text", source: "advanced" },
      { key: "text_heading", label: "Heading text", source: "advanced" },
      { key: "label_text", label: "Label text", source: "advanced" },
      { key: "text_disabled", label: "Disabled text", source: "advanced" },
      { key: "placeholder", label: "Placeholder text", source: "advanced" },
    ],
  },
  {
    title: "Borders",
    fields: [
      { key: "border", label: "Default border", source: "advanced" },
      { key: "border_strong", label: "Strong border", source: "advanced" },
    ],
  },
  {
    title: "Buttons & links",
    fields: [
      { key: "primary_color", label: "Primary button", source: "base" },
      { key: "primary_btn_text", label: "Primary button text", source: "advanced" },
      { key: "secondary_color", label: "Secondary button", source: "base" },
      { key: "secondary_btn_text", label: "Secondary button text", source: "advanced" },
      { key: "danger_btn_text", label: "Danger button text", source: "advanced" },
      { key: "accent_color", label: "Accent", source: "base" },
      { key: "link", label: "Link", source: "advanced" },
      { key: "link_hover", label: "Link hover", source: "advanced" },
      { key: "focus", label: "Focus ring", source: "advanced" },
    ],
  },
  {
    title: "Status",
    fields: [
      { key: "success_color", label: "Success text", source: "base" },
      { key: "success_bg", label: "Success background", source: "advanced" },
      { key: "warning_color", label: "Warning text", source: "base" },
      { key: "warning_bg", label: "Warning background", source: "advanced" },
      { key: "danger_color", label: "Danger text", source: "base" },
      { key: "danger_bg", label: "Danger background", source: "advanced" },
      { key: "info_text", label: "Info text", source: "advanced" },
      { key: "info_bg", label: "Info background", source: "advanced" },
    ],
  },
  {
    title: "Sidebar",
    fields: [
      { key: "sidebar_bg", label: "Sidebar background", source: "advanced" },
      { key: "sidebar_text", label: "Sidebar primary text", source: "advanced" },
      { key: "sidebar_muted", label: "Sidebar muted text", source: "advanced" },
      { key: "sidebar_active_bg", label: "Sidebar active background", source: "advanced" },
      { key: "sidebar_active_text", label: "Sidebar active text", source: "advanced" },
      { key: "sidebar_hover_bg", label: "Sidebar hover background", source: "advanced" },
    ],
  },
  {
    title: "Tables",
    fields: [
      { key: "table_header_bg", label: "Table header background", source: "advanced" },
      { key: "table_header_text", label: "Table header text", source: "advanced" },
      { key: "table_row_hover", label: "Table row hover", source: "advanced" },
    ],
  },
  {
    title: "Advanced",
    hint: "Default badge/chip colors — status badges (success/warning/danger/info) use the Status section above.",
    fields: [
      { key: "badge_bg", label: "Default badge background", source: "advanced" },
      { key: "badge_text", label: "Default badge text", source: "advanced" },
    ],
  },
];

function getField(draft: ThemeConfigInput, field: Field): string {
  return field.source === "base" ? (draft as unknown as Record<string, string>)[field.key] : draft.advanced_tokens[field.key as keyof AdvancedThemeTokens];
}

function setField(draft: ThemeConfigInput, field: Field, value: string): ThemeConfigInput {
  if (field.source === "base") {
    return { ...draft, [field.key]: value, preset_name: null };
  }
  return { ...draft, advanced_tokens: { ...draft.advanced_tokens, [field.key]: value }, preset_name: null };
}

function resolveContrastField(draft: ThemeConfigInput, field: string): string | undefined {
  if (field in draft.advanced_tokens) return draft.advanced_tokens[field as keyof AdvancedThemeTokens];
  return (draft as unknown as Record<string, string>)[field];
}

function findContrastProblems(draft: ThemeConfigInput): { label: string; ratio: number; minRatio: number }[] {
  const problems: { label: string; ratio: number; minRatio: number }[] = [];
  for (const [label, fgField, bgField, minRatio] of CONTRAST_PAIRS) {
    const fg = resolveContrastField(draft, fgField);
    const bg = resolveContrastField(draft, bgField);
    if (!fg || !bg || !HEX_RE.test(fg) || !HEX_RE.test(bg)) continue;
    const ratio = contrastRatio(fg, bg);
    if (ratio < minRatio) problems.push({ label, ratio, minRatio });
  }
  return problems;
}

export default function ThemeSettingsPage() {
  const { hasPermission } = useAuth();
  const canManage = hasPermission("settings.manage");
  const { theme, error: loadError, reload } = useTheme();

  const [draft, setDraft] = useState<ThemeConfigInput | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (theme) {
      setDraft({
        primary_color: theme.primary_color,
        secondary_color: theme.secondary_color,
        accent_color: theme.accent_color,
        success_color: theme.success_color,
        warning_color: theme.warning_color,
        danger_color: theme.danger_color,
        surface_mode: theme.surface_mode,
        preset_name: theme.preset_name,
        advanced_tokens: theme.advanced_tokens,
      });
    }
  }, [theme]);

  // Live preview: applies the in-progress (unsaved) draft to the whole page as the
  // admin edits it, so the preview panel and every real component around it (nav,
  // badges, buttons) reflect the change immediately — reverted to the saved theme on
  // unmount so navigating away without saving doesn't leave a stray theme applied.
  useEffect(() => {
    if (draft) applyTheme(draft);
    return () => {
      if (theme) applyTheme(theme);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  const contrastProblems = useMemo(() => (draft ? findContrastProblems(draft) : []), [draft]);

  function updateField(field: Field, value: string) {
    setDraft((prev) => (prev ? setField(prev, field, value) : prev));
    setSaved(false);
  }

  function applyPreset(name: string) {
    const preset = THEME_PRESETS[name];
    if (preset) {
      setDraft(preset);
      setSaved(false);
    }
  }

  function resetToDefault() {
    applyPreset("Clinical Blue");
  }

  async function handleSave() {
    if (!draft) return;
    setSaveError(null);
    setSaved(false);
    for (const section of SECTIONS) {
      for (const field of section.fields) {
        if (!HEX_RE.test(getField(draft, field))) {
          setSaveError(`${field.label} must be a valid hex color (#RRGGBB).`);
          return;
        }
      }
    }
    if (contrastProblems.length > 0) {
      setSaveError("Fix the unreadable color combinations flagged below before saving.");
      return;
    }
    setSaving(true);
    try {
      const updated = await api.put<ThemeConfig>("/api/v1/theme", draft);
      setSaved(true);
      applyTheme(updated);
      await reload();
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Failed to save theme.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Theme settings</h1>
          <p>
            Centrally-stored branding applied to every user&apos;s dashboard on next load. Only Super Admin can
            change it — everyone else only ever consumes what&apos;s saved here.
          </p>
        </div>
      </div>

      {loadError && <ErrorState message={loadError} />}
      {!draft && !loadError && <LoadingState />}

      {draft && canManage && (
        <>
          <div className="card" style={{ marginBottom: 16 }}>
            <h3 style={{ marginTop: 0 }}>Preset</h3>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {Object.keys(THEME_PRESETS).map((name) => (
                <button
                  key={name}
                  className={draft.preset_name === name ? "btn btn--primary" : "btn btn--ghost"}
                  onClick={() => applyPreset(name)}
                  type="button"
                >
                  {name}
                </button>
              ))}
            </div>
            <div className="form-field" style={{ marginTop: 16, maxWidth: 220 }}>
              <label>Surface mode</label>
              <select
                value={draft.surface_mode}
                onChange={(e) => {
                  setDraft((prev) => (prev ? { ...prev, surface_mode: e.target.value as "light" | "dark", preset_name: null } : prev));
                  setSaved(false);
                }}
              >
                <option value="light">Light</option>
                <option value="dark">Dark</option>
              </select>
            </div>
          </div>

          {SECTIONS.map((section) => (
            <div className="card" style={{ marginBottom: 16 }} key={section.title}>
              <h3 style={{ marginTop: 0 }}>{section.title}</h3>
              {section.hint && (
                <p className="dashboard-note" style={{ marginTop: 0 }}>
                  {section.hint}
                </p>
              )}
              <div className="form-grid">
                {section.fields.map((field) => (
                  <div className="form-field" key={field.key}>
                    <label>{field.label}</label>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <input
                        type="color"
                        value={getField(draft, field)}
                        onChange={(e) => updateField(field, e.target.value.toUpperCase())}
                        style={{ width: 44, height: 36, padding: 2 }}
                        aria-label={`${field.label} color picker`}
                      />
                      <input
                        value={getField(draft, field)}
                        onChange={(e) => updateField(field, e.target.value)}
                        placeholder="#RRGGBB"
                        style={{ maxWidth: 120 }}
                        aria-label={`${field.label} hex value`}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}

          <div className="card" style={{ marginBottom: 16 }}>
            <h3 style={{ marginTop: 0 }}>Contrast check</h3>
            {contrastProblems.length === 0 ? (
              <p className="dashboard-note" style={{ marginTop: 0 }}>
                All checked foreground/background pairs meet WCAG AA contrast.
              </p>
            ) : (
              <div className="alert-list">
                {contrastProblems.map((problem) => (
                  <div className="alert-row alert-row--danger" key={problem.label}>
                    <span className="alert-row__label">⚠ {problem.label}</span>
                    <span className="alert-row__count">
                      {problem.ratio.toFixed(2)}:1 (needs {problem.minRatio.toFixed(1)}:1)
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card" style={{ marginBottom: 16 }}>
            {saveError && <div className="field-error" style={{ marginBottom: 12 }}>{saveError}</div>}
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn btn--primary" onClick={handleSave} disabled={saving || contrastProblems.length > 0}>
                {saving ? "Saving..." : "Save & Apply"}
              </button>
              <button className="btn btn--ghost" onClick={resetToDefault} disabled={saving} type="button">
                Reset to default
              </button>
            </div>
            {saved && <p className="dashboard-note">Saved — every user picks this up on their next page load.</p>}
          </div>

          <div className="card">
            <h3 style={{ marginTop: 0 }}>Live preview</h3>
            <p className="dashboard-note" style={{ marginTop: 0 }}>
              Reflects the unsaved draft above — nothing here is persisted until you click Save.
            </p>
            <ThemePreviewPanel />
          </div>
        </>
      )}

      {!canManage && draft && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Current theme</h3>
          <p className="dashboard-note" style={{ marginTop: 0 }}>
            Read-only — only Super Admin (settings.manage) can change this.
          </p>
          <ThemePreviewPanel />
        </div>
      )}
    </ProtectedShell>
  );
}

function ThemePreviewPanel() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn btn--primary" type="button">
          Primary button
        </button>
        <button className="btn btn--secondary" type="button">
          Secondary button
        </button>
        <button className="btn btn--ghost" type="button">
          Ghost button
        </button>
        <button className="btn btn--danger" type="button">
          Danger button
        </button>
        <StatusBadge value="APPROVED" />
        <StatusBadge value="PENDING" />
        <StatusBadge value="REJECTED" />
        <StatusBadge value="ACTIVE" />
        <span className="badge badge--neutral">Default badge</span>
      </div>

      <div className="kpi-card kpi-card--tone-success" style={{ maxWidth: 240 }}>
        <div className="kpi-card__top">
          <span className="kpi-card__label">Operational assets</span>
        </div>
        <div className="kpi-card__value">1,204</div>
      </div>

      <div className="state-panel state-panel--success" style={{ maxWidth: 480 }}>
        Sample success alert — theme colors apply live.
      </div>

      <div className="form-field" style={{ maxWidth: 240 }}>
        <label>Sample input</label>
        <input placeholder="Placeholder text" />
      </div>
      <div className="form-field" style={{ maxWidth: 240 }}>
        <label>Disabled input</label>
        <input placeholder="Disabled" disabled />
      </div>

      <div className="table-wrap" style={{ maxWidth: 480 }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Asset</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Ventilator VN-102</td>
              <td>
                <StatusBadge value="OPERATIONAL" />
              </td>
            </tr>
            <tr>
              <td>Infusion Pump IP-054</td>
              <td>
                <StatusBadge value="UNDER_MAINTENANCE" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="sidebar" style={{ maxWidth: 240, borderRadius: "var(--radius)" }}>
        <div className="sidebar__section">
          <div className="sidebar__section-title">Navigation</div>
          <a className="sidebar__link sidebar__link--active">Dashboard</a>
          <a className="sidebar__link">Inventory</a>
        </div>
      </div>
    </div>
  );
}
