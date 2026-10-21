"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { ErrorState } from "../../../components/ErrorState";
import { useLookup } from "../../../hooks/useLookup";
import { useAuth } from "../../../hooks/useAuth";
import { api, ApiError } from "../../../lib/api";
import type { Asset, Branch, CalibrationSchedule, DeviceModel } from "../../../types";
import { CALIBRATION_FREQUENCIES } from "../../../types";

export default function NewCalibrationSchedulePage() {
  const router = useRouter();
  const { hasPermission } = useAuth();
  const assets = useLookup<Asset>("/api/v1/assets", { fetchAll: true });
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  // status_filter=ACTIVE — a deactivated branch must not be selectable here.
  const branches = useLookup<Branch>("/api/v1/branches?status_filter=ACTIVE");
  const modelName = (id: string) => models.find((m) => m.id === id)?.name ?? id;
  const branchName = (id: string | null) => (id ? branches.find((b) => b.id === id)?.name ?? id : "Location not assigned");
  const [frequency, setFrequency] = useState("MONTHLY");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [assetSearch, setAssetSearch] = useState("");
  const [selectedAssetId, setSelectedAssetId] = useState("");

  const filteredAssets = assets.filter((a) => {
    const term = assetSearch.trim().toLowerCase();
    if (!term) return true;
    const haystack = `${a.asset_number ?? ""} ${a.serial_number ?? ""} ${modelName(a.device_model_id)} ${branchName(a.branch_id)}`.toLowerCase();
    return haystack.includes(term);
  });
  const selectedAsset = assets.find((a) => a.id === selectedAssetId) ?? null;

  // Workflow consolidation pass — reached from an Incident's "Create Calibration Record"
  // action (/calibration/new?asset_id=&incident_id=), same window.location.search
  // pre-fill pattern as /requirements/new (?item_type=) — no useSearchParams()/Suspense
  // boundary. incident_id carries through to the schedule detail page so its "Execute
  // Calibration" form can link the resulting CalibrationRecord back.
  const [prefillAssetId, setPrefillAssetId] = useState("");
  const [incidentId, setIncidentId] = useState<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const prefill = params.get("asset_id") ?? "";
    setPrefillAssetId(prefill);
    setSelectedAssetId(prefill);
    setIncidentId(params.get("incident_id"));
  }, []);

  if (!hasPermission("calibration.write")) {
    return (
      <ProtectedShell>
        <ErrorState message="You do not have permission to create calibration schedules." />
      </ProtectedShell>
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const formData = new FormData(event.currentTarget);
    try {
      const schedule = await api.post<CalibrationSchedule>("/api/v1/calibration/schedules", {
        asset_id: formData.get("asset_id"),
        frequency: formData.get("frequency") || null,
        custom_interval_days:
          formData.get("frequency") === "CUSTOM" && formData.get("custom_interval_days")
            ? Number(formData.get("custom_interval_days"))
            : null,
        next_due_date: formData.get("next_due_date") || null,
        is_active: formData.get("is_active") === "on",
      });
      router.push(incidentId ? `/calibration/${schedule.id}?incident_id=${incidentId}` : `/calibration/${schedule.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create calibration schedule.");
      setSubmitting(false);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>New calibration schedule</h1>
          <p>Set up a recurring calibration schedule for an asset.</p>
        </div>
      </div>

      <form className="card" onSubmit={handleSubmit} style={{ maxWidth: 640 }}>
        <div className="form-grid">
          <div className="form-field">
            <label>Asset*</label>
            <input
              type="text"
              placeholder="Search by asset number, model, or branch..."
              value={assetSearch}
              onChange={(e) => setAssetSearch(e.target.value)}
              style={{ marginBottom: 6 }}
            />
            <select
              name="asset_id"
              required
              value={selectedAssetId}
              key={prefillAssetId}
              onChange={(e) => setSelectedAssetId(e.target.value)}
            >
              <option value="" disabled>
                {filteredAssets.length === 0 ? "No matching assets" : "Select asset"}
              </option>
              {filteredAssets.map((a) => (
                <option key={a.id} value={a.id}>
                  {modelName(a.device_model_id)} — {a.asset_number ?? a.serial_number ?? a.id} — {branchName(a.branch_id)} ({a.status})
                </option>
              ))}
            </select>
            {selectedAsset && (
              <span className="field-hint">
                Location: {branchName(selectedAsset.branch_id)} · Status: {selectedAsset.status}
              </span>
            )}
          </div>
          <div className="form-field">
            <label>Frequency</label>
            <select name="frequency" value={frequency} onChange={(e) => setFrequency(e.target.value)}>
              {CALIBRATION_FREQUENCIES.map((f) => (
                <option key={f} value={f}>
                  {f.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </div>
          {frequency === "CUSTOM" && (
            <div className="form-field">
              <label>Custom interval (days)*</label>
              <input name="custom_interval_days" type="number" min={1} required />
            </div>
          )}
          <div className="form-field">
            <label>Start / next due date</label>
            <input name="next_due_date" type="date" />
            <span className="field-hint">Leave blank to auto-project from today using the frequency.</span>
          </div>
          <div className="form-field form-field--checkbox">
            <label>
              <input name="is_active" type="checkbox" defaultChecked /> Active
            </label>
          </div>
        </div>

        {error && <div className="field-error">{error}</div>}
        <button className="btn btn--primary" type="submit" disabled={submitting}>
          {submitting ? "Saving..." : "Create schedule"}
        </button>
      </form>
    </ProtectedShell>
  );
}
