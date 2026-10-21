"use client";

import { useEffect, useState } from "react";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { api, ApiError } from "../../../lib/api";
import type { QrSettings } from "../../../types";

const SECTION_LABELS: Record<string, string> = {
  identity: "Device details",
  location: "Location",
  lifecycle: "Lifecycle",
  maintenance: "Maintenance",
  coverage: "Coverage",
};

export default function QrDisplaySettingsPage() {
  const [settings, setSettings] = useState<QrSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api
      .get<QrSettings>("/api/v1/qr/settings")
      .then(setSettings)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : "Failed to load QR settings."));
  }, []);

  function toggleField(key: string) {
    setSettings((prev) =>
      prev ? { ...prev, fields: prev.fields.map((f) => (f.key === key ? { ...f, enabled: !f.enabled } : f)) } : prev
    );
    setSaved(false);
  }

  function moveField(key: string, direction: -1 | 1) {
    setSettings((prev) => {
      if (!prev) return prev;
      const fields = [...prev.fields];
      const index = fields.findIndex((f) => f.key === key);
      const target = index + direction;
      if (target < 0 || target >= fields.length) return prev;
      [fields[index], fields[target]] = [fields[target], fields[index]];
      return { ...prev, fields: fields.map((f, i) => ({ ...f, order: i })) };
    });
    setSaved(false);
  }

  async function handleSave() {
    if (!settings) return;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await api.put<QrSettings>("/api/v1/qr/settings", {
        enabled: settings.enabled,
        fields: settings.fields.map((f) => ({ key: f.key, enabled: f.enabled })),
      });
      setSettings(updated);
      setSaved(true);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Failed to save QR settings.");
    } finally {
      setSaving(false);
    }
  }

  const grouped = settings
    ? settings.fields.reduce<Record<string, typeof settings.fields>>((acc, f) => {
        (acc[f.section] = acc[f.section] || []).push(f);
        return acc;
      }, {})
    : {};

  return (
    <ProtectedShell requiredPermission="qr.manage">
      <div className="page-header">
        <div>
          <h1>QR display settings</h1>
          <p>
            Controls exactly what the public QR scan page shows for every device. Changes apply immediately to every
            printed label — no relabeling needed.
          </p>
        </div>
      </div>

      {loadError && <ErrorState message={loadError} />}
      {!settings && !loadError && <LoadingState />}

      {settings && (
        <>
          <div className="card" style={{ marginBottom: 16 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="checkbox"
                checked={settings.enabled}
                onChange={(e) => {
                  setSettings({ ...settings, enabled: e.target.checked });
                  setSaved(false);
                }}
              />
              Public QR scan page enabled
            </label>
            <p className="dashboard-note" style={{ marginTop: 8 }}>
              When off, every scanned QR code shows "temporarily unavailable" instead of device data — the tokens
              themselves stay valid for when it's switched back on.
            </p>
          </div>

          {Object.entries(grouped).map(([section, fields]) => (
            <div className="card" style={{ marginBottom: 16 }} key={section}>
              <h3 style={{ marginTop: 0 }}>{SECTION_LABELS[section] ?? section}</h3>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th>Visible on scan page</th>
                      <th>Order</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fields.map((field) => (
                      <tr key={field.key}>
                        <td>{field.label}</td>
                        <td>
                          <input type="checkbox" checked={field.enabled} onChange={() => toggleField(field.key)} />
                        </td>
                        <td style={{ display: "flex", gap: 4 }}>
                          <button className="btn btn--ghost" onClick={() => moveField(field.key, -1)} type="button">
                            Up
                          </button>
                          <button className="btn btn--ghost" onClick={() => moveField(field.key, 1)} type="button">
                            Down
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}

          <div className="card">
            {saveError && <div className="field-error" style={{ marginBottom: 12 }}>{saveError}</div>}
            <button className="btn btn--primary" onClick={handleSave} disabled={saving}>
              {saving ? "Saving..." : "Save settings"}
            </button>
            {saved && <p className="dashboard-note">Saved — every QR label reflects this immediately.</p>}
          </div>
        </>
      )}
    </ProtectedShell>
  );
}
