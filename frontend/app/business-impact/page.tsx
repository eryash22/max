"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { useAuth } from "../../hooks/useAuth";
import { useLookup } from "../../hooks/useLookup";
import { useAssetLabel } from "../../hooks/useAssetLabel";
import { api, ApiError } from "../../lib/api";
import type {
  BusinessImpactAssumption,
  FailureSummary,
  Incident,
  IncidentImpactEstimate,
  Page,
} from "../../types";
import { DEVICE_CATEGORIES } from "../../types";

const PAGE_SIZE = 25;

export default function BusinessImpactPage() {
  const { hasPermission } = useAuth();
  const canRead = hasPermission("business_impact.read");
  const canWrite = hasPermission("hospital.write");

  const { labelForAssetId: assetNumber } = useAssetLabel();
  const incidents = useLookup<Incident>("/api/v1/incidents");

  const [assumptions, setAssumptions] = useState<BusinessImpactAssumption[]>([]);
  const [assumptionsError, setAssumptionsError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [summaries, setSummaries] = useState<FailureSummary[]>([]);
  const [summariesTotal, setSummariesTotal] = useState(0);
  const [summariesPage, setSummariesPage] = useState(1);
  const [repeatOnly, setRepeatOnly] = useState(false);
  const [summariesError, setSummariesError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [estimateIncidentId, setEstimateIncidentId] = useState("");
  const [estimate, setEstimate] = useState<IncidentImpactEstimate | null>(null);
  const [estimateError, setEstimateError] = useState<string | null>(null);

  async function loadAssumptions() {
    try {
      const result = await api.get<Page<BusinessImpactAssumption>>("/api/v1/business-impact/assumptions?page=1&page_size=100");
      setAssumptions(result.items);
    } catch (err) {
      setAssumptionsError(err instanceof ApiError ? err.message : "Failed to load assumptions.");
    }
  }

  async function loadSummaries() {
    setLoading(true);
    setSummariesError(null);
    try {
      const params = new URLSearchParams({ page: String(summariesPage), page_size: String(PAGE_SIZE) });
      if (repeatOnly) params.set("repeat_failure_only", "true");
      const result = await api.get<Page<FailureSummary>>(`/api/v1/analytics/failure-summary?${params.toString()}`);
      setSummaries(result.items);
      setSummariesTotal(result.total);
    } catch (err) {
      setSummariesError(err instanceof ApiError ? err.message : "Failed to load failure analytics.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAssumptions();
  }, []);

  useEffect(() => {
    loadSummaries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summariesPage, repeatOnly]);

  useEffect(() => {
    setSummariesPage(1);
  }, [repeatOnly]);

  async function handleCreateAssumption(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/business-impact/assumptions", {
        name: formData.get("name"),
        device_category: formData.get("device_category") || null,
        revenue_per_hour: formData.get("revenue_per_hour") ? Number(formData.get("revenue_per_hour")) : null,
        currency: formData.get("currency") || "INR",
        notes: formData.get("notes") || null,
      });
      setShowForm(false);
      form.reset();
      await loadAssumptions();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create assumption.");
    }
  }

  async function handleEstimate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setEstimateError(null);
    setEstimate(null);
    if (!estimateIncidentId) return;
    try {
      const result = await api.get<IncidentImpactEstimate>(`/api/v1/business-impact/incidents/${estimateIncidentId}/estimate`);
      setEstimate(result);
    } catch (err) {
      setEstimateError(err instanceof ApiError ? err.message : "Failed to compute estimate.");
    }
  }

  if (!canRead) {
    return (
      <ProtectedShell>
        <ErrorState message="You do not have permission to view business impact and analytics." />
      </ProtectedShell>
    );
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Business impact &amp; analytics</h1>
          <p>
            Estimated/projected figures only — never actual financials — derived from
            configurable revenue-per-hour assumptions and incident downtime history.
            Failure analytics (MTBF/MTTR/repeat-failure) are computed on the fly from
            incident records, never stored.
          </p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="page-header" style={{ marginBottom: 0 }}>
          <h3 style={{ marginTop: 0 }}>Revenue-per-hour assumptions</h3>
          {canWrite && (
            <button className="btn btn--primary" onClick={() => setShowForm((v) => !v)}>
              {showForm ? "Cancel" : "New assumption"}
            </button>
          )}
        </div>
        {showForm && (
          <form onSubmit={handleCreateAssumption} className="form-grid" style={{ marginTop: 16 }}>
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required />
            </div>
            <div className="form-field">
              <label>Device category</label>
              <select name="device_category" defaultValue="">
                <option value="">Any (applies to all device categories)</option>
                {DEVICE_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Revenue per hour</label>
              <input name="revenue_per_hour" type="number" min={0} step="0.01" placeholder="Optional" />
            </div>
            <div className="form-field">
              <label>Currency</label>
              <input name="currency" defaultValue="INR" />
            </div>
            <div className="form-field" style={{ gridColumn: "1 / -1" }}>
              <label>Notes</label>
              <textarea name="notes" rows={2} placeholder="Optional" />
            </div>
            {formError && <div className="field-error">{formError}</div>}
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit">
                Save assumption
              </button>
            </div>
          </form>
        )}
        {assumptionsError && <ErrorState message={assumptionsError} />}
        <table className="data-table" style={{ marginTop: 16 }}>
          <thead>
            <tr>
              <th>Name</th>
              <th>Device category</th>
              <th>Revenue / hour</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {assumptions.length === 0 && (
              <tr>
                <td colSpan={4}>No assumptions configured yet.</td>
              </tr>
            )}
            {assumptions.map((a) => (
              <tr key={a.id}>
                <td>{a.name}</td>
                <td>{a.device_category ?? "Any"}</td>
                <td>{a.revenue_per_hour != null ? `${a.currency} ${a.revenue_per_hour}` : "-"}</td>
                <td>
                  <StatusBadge value={a.is_active ? "ACTIVE" : "INACTIVE"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Incident impact estimate</h3>
        <form onSubmit={handleEstimate} className="form-grid">
          <div className="form-field">
            <label>Incident</label>
            <select value={estimateIncidentId} onChange={(e) => setEstimateIncidentId(e.target.value)}>
              <option value="">Select incident</option>
              {incidents.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.description.slice(0, 50)} ({i.severity})
                </option>
              ))}
            </select>
          </div>
          <div className="form-field" style={{ justifyContent: "flex-end" }}>
            <button className="btn btn--primary" type="submit" disabled={!estimateIncidentId}>
              Estimate impact
            </button>
          </div>
        </form>
        {estimateError && <ErrorState message={estimateError} />}
        {estimate && (
          <table className="data-table" style={{ marginTop: 16 }}>
            <tbody>
              <tr>
                <td>Downtime</td>
                <td>{estimate.downtime_hours != null ? `${estimate.downtime_hours} hours` : "Not recorded"}</td>
              </tr>
              <tr>
                <td>Estimated impact</td>
                <td>
                  {estimate.estimated_impact != null
                    ? `${estimate.currency} ${estimate.estimated_impact} (Estimated)`
                    : "Not computable"}
                </td>
              </tr>
              <tr>
                <td>Basis</td>
                <td>{estimate.basis}</td>
              </tr>
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <div className="toolbar">
          <h3 style={{ marginTop: 0 }}>Failure analytics (MTBF / MTTR)</h3>
          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={repeatOnly} onChange={(e) => setRepeatOnly(e.target.checked)} />
            Repeat failures only
          </label>
        </div>
        {loading && <LoadingState />}
        {summariesError && <ErrorState message={summariesError} />}
        {!loading && !summariesError && (
          <>
            <DataTable<FailureSummary & { id: string }>
              columns={[
                { key: "asset", header: "Asset", render: (s) => assetNumber(s.asset_id) },
                { key: "incident_count", header: "Incident count", render: (s) => s.incident_count },
                { key: "total_downtime_hours", header: "Total downtime (hours)", render: (s) => s.total_downtime_hours },
                { key: "mttr_hours", header: "MTTR (hours, estimated)", render: (s) => s.mttr_hours ?? "Insufficient data" },
                { key: "mtbf_hours", header: "MTBF (hours, estimated)", render: (s) => s.mtbf_hours ?? "Insufficient data" },
                {
                  key: "repeat_failure",
                  header: "Repeat failure",
                  render: (s) => (s.repeat_failure ? <StatusBadge value="REPEAT FAILURE" /> : "No"),
                },
              ]}
              rows={summaries.map((s) => ({ ...s, id: s.asset_id }))}
              emptyLabel="No assets with recorded incidents yet."
            />
            <Pagination page={summariesPage} pageSize={PAGE_SIZE} total={summariesTotal} onPageChange={setSummariesPage} />
          </>
        )}
      </div>
    </ProtectedShell>
  );
}
