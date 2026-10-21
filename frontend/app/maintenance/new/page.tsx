"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { ErrorState } from "../../../components/ErrorState";
import { useLookup } from "../../../hooks/useLookup";
import { useAuth } from "../../../hooks/useAuth";
import { api, ApiError } from "../../../lib/api";
import type { Asset, DeviceModel, PMSchedule } from "../../../types";
import { PM_FREQUENCIES } from "../../../types";

export default function NewPMSchedulePage() {
  const router = useRouter();
  const { hasPermission } = useAuth();
  const assets = useLookup<Asset>("/api/v1/assets");
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const modelName = (id: string) => models.find((m) => m.id === id)?.name ?? id;
  const [frequency, setFrequency] = useState("MONTHLY");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Workflow consolidation pass — reached from an Incident's "Create PM Record" action
  // (/maintenance/new?asset_id=&incident_id=), same window.location.search pre-fill
  // pattern already used by /requirements/new (?item_type=) and /incidents/new
  // (?problem_category=) — no useSearchParams()/Suspense boundary, see those pages.
  // incident_id carries through to the schedule detail page so its "Execute PM" form
  // can link the resulting PMRecord back.
  const [prefillAssetId, setPrefillAssetId] = useState("");
  const [incidentId, setIncidentId] = useState<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setPrefillAssetId(params.get("asset_id") ?? "");
    setIncidentId(params.get("incident_id"));
  }, []);

  if (!hasPermission("pm.write")) {
    return (
      <ProtectedShell>
        <ErrorState message="You do not have permission to create PM schedules." />
      </ProtectedShell>
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const formData = new FormData(event.currentTarget);
    try {
      const schedule = await api.post<PMSchedule>("/api/v1/pm/schedules", {
        asset_id: formData.get("asset_id"),
        frequency: formData.get("frequency") || null,
        custom_interval_days:
          formData.get("frequency") === "CUSTOM" && formData.get("custom_interval_days")
            ? Number(formData.get("custom_interval_days"))
            : null,
        next_due_date: formData.get("next_due_date") || null,
        is_active: formData.get("is_active") === "on",
      });
      router.push(incidentId ? `/maintenance/${schedule.id}?incident_id=${incidentId}` : `/maintenance/${schedule.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create PM schedule.");
      setSubmitting(false);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>New PM schedule</h1>
          <p>Set up a recurring preventive-maintenance schedule for an asset.</p>
        </div>
      </div>

      <form className="card" onSubmit={handleSubmit} style={{ maxWidth: 640 }}>
        <div className="form-grid">
          <div className="form-field">
            <label>Asset*</label>
            <select name="asset_id" required defaultValue={prefillAssetId} key={prefillAssetId}>
              <option value="" disabled>
                Select asset
              </option>
              {assets.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.asset_number} — {modelName(a.device_model_id)}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Frequency</label>
            <select name="frequency" value={frequency} onChange={(e) => setFrequency(e.target.value)}>
              {PM_FREQUENCIES.map((f) => (
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
