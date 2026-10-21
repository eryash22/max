"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { api, ApiError } from "../../../lib/api";
import type { Incident, IncidentStatusHistoryEntry, Vendor } from "../../../types";
import { INCIDENT_STATUSES } from "../../../types";

export default function IncidentDetailPage() {
  const params = useParams<{ id: string }>();
  const { hasPermission } = useAuth();
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const vendorName = (id: string | null) => (id ? vendors.find((v) => v.id === id)?.name ?? id : "-");
  const [incident, setIncident] = useState<Incident | null>(null);
  const [history, setHistory] = useState<IncidentStatusHistoryEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const canManage = hasPermission("incident.manage");
  // No DELETE endpoint exists for incidents by design — they can be referenced by
  // asset history, repair, procurement, and audit. CANCELLED is the safe "archive"
  // equivalent: it preserves the incident and its full status/audit history. Not
  // offered once the incident is already resolved/closed/cancelled.
  const canCancel = canManage && !["RESOLVED", "PENDING_VERIFICATION", "CLOSED", "CANCELLED"].includes(incident?.status ?? "");

  // Workflow consolidation pass — Phase 9: an Incident raised as "Maintenance Request" /
  // "Calibration Request" / "Service Visit Request" (see /raise's INCIDENT_TYPES) is a
  // hand-off point into the real PM/Calibration/Service Visit modules, not a duplicate
  // workflow inside Incident. This maps the pre-filled problem_category label to the
  // matching linked-record action, same pre-fill-via-querystring pattern used elsewhere.
  const LINKED_RECORD_ACTIONS: Record<string, { label: string; href: (assetId: string, incidentId: string) => string }> = {
    "Maintenance Request": {
      label: "Create PM Record",
      href: (assetId, incidentId) => `/maintenance/new?asset_id=${assetId}&incident_id=${incidentId}`,
    },
    "Calibration Request": {
      label: "Create Calibration Record",
      href: (assetId, incidentId) => `/calibration/new?asset_id=${assetId}&incident_id=${incidentId}`,
    },
    "Service Visit Request": {
      label: "Create Service Visit",
      href: (assetId, incidentId) => `/service-visits?asset_id=${assetId}&incident_id=${incidentId}`,
    },
  };

  const load = useCallback(async () => {
    try {
      const [incidentResult, historyResult] = await Promise.all([
        api.get<Incident>(`/api/v1/incidents/${params.id}`),
        api.get<IncidentStatusHistoryEntry[]>(`/api/v1/incidents/${params.id}/history`),
      ]);
      setIncident(incidentResult);
      setHistory(historyResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load incident.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleAssign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post(`/api/v1/incidents/${params.id}/assign`, {
        assigned_engineer_id: formData.get("assigned_engineer_id") || null,
        assigned_vendor_id: formData.get("assigned_vendor_id") || null,
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to assign incident.");
    } finally {
      setBusy(false);
    }
  }

  async function handleStatusChange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post(`/api/v1/incidents/${params.id}/status`, {
        to_status: formData.get("to_status"),
        notes: formData.get("notes") || null,
      });
      await load();
      form.reset();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to change status.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    setConfirmCancel(false);
    setActionError(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/incidents/${params.id}/status`, { to_status: "CANCELLED", notes: "Cancelled by user" });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to cancel incident.");
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.patch(`/api/v1/incidents/${params.id}`, {
        root_cause: formData.get("root_cause") || null,
        corrective_action: formData.get("corrective_action") || null,
        preventive_action: formData.get("preventive_action") || null,
        resolution: formData.get("resolution") || null,
        parts_used: formData.get("parts_used") || null,
        downtime_hours: formData.get("downtime_hours") ? Number(formData.get("downtime_hours")) : null,
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update incident.");
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return (
      <ProtectedShell>
        <ErrorState message={error} />
      </ProtectedShell>
    );
  }
  if (!incident) {
    return (
      <ProtectedShell>
        <LoadingState />
      </ProtectedShell>
    );
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>{incident.description}</h1>
          <p>
            <StatusBadge value={incident.status} /> · <StatusBadge value={incident.severity} />
            {incident.problem_category ? ` · ${incident.problem_category}` : ""}
          </p>
        </div>
        {canCancel && (
          <button className="btn btn--ghost" disabled={busy} onClick={() => setConfirmCancel(true)}>
            Cancel incident
          </button>
        )}
      </div>

      {actionError && <ErrorState message={actionError} />}

      <ConfirmDialog
        open={confirmCancel}
        title="Cancel incident"
        message="This marks the incident CANCELLED (duplicate report, false alarm, or withdrawn). It preserves the full record and status history — nothing is deleted. Continue?"
        confirmLabel="Cancel incident"
        onConfirm={handleCancel}
        onCancel={() => setConfirmCancel(false)}
      />

      {canManage && incident.asset_id && incident.problem_category && LINKED_RECORD_ACTIONS[incident.problem_category] && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Linked record</h3>
          <p className="field-hint">
            This incident was raised as &ldquo;{incident.problem_category}&rdquo; — hand it off to the matching
            operational module instead of resolving it as a generic incident.
          </p>
          <a
            className="btn btn--primary"
            href={LINKED_RECORD_ACTIONS[incident.problem_category].href(incident.asset_id, incident.id)}
          >
            {LINKED_RECORD_ACTIONS[incident.problem_category].label}
          </a>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Details</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Branch</td>
              <td>{incident.branch_id}</td>
            </tr>
            <tr>
              <td>Asset</td>
              <td>
                {incident.asset_id ? (
                  <Link href={`/assets/${incident.asset_id}`}>{incident.asset_id}</Link>
                ) : (
                  "Not linked"
                )}
              </td>
            </tr>
            <tr>
              <td>Clinical impact</td>
              <td>{incident.clinical_impact ?? "-"}</td>
            </tr>
            <tr>
              <td>Patient-care impact</td>
              <td>{incident.patient_care_impact ?? "-"}</td>
            </tr>
            <tr>
              <td>Assigned engineer</td>
              <td>{incident.assigned_engineer_id ?? "Unassigned"}</td>
            </tr>
            <tr>
              <td>Assigned vendor</td>
              <td>{incident.assigned_vendor_id ?? "-"}</td>
            </tr>
            <tr>
              <td>Root cause</td>
              <td>{incident.root_cause ?? "Not recorded"}</td>
            </tr>
            <tr>
              <td>Resolution</td>
              <td>{incident.resolution ?? "Not recorded"}</td>
            </tr>
            <tr>
              <td>Downtime (hours)</td>
              <td>{incident.downtime_hours ?? "Not recorded"}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Business-workflow audit — read-only, derived live from the existing
          Contracts module (same functions the asset detail page's AMC/CMC/Warranty
          table uses). Nothing here is stored on Incident; only rendered when the
          backend actually returned a coverage block (asset-linked incidents). */}
      {incident.coverage && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Coverage</h3>
          {incident.coverage.coverage_status === "NOT_COVERED" ? (
            <p>
              <StatusBadge value="NOT_COVERED" /> — no active AMC, CMC, or Warranty for this asset. A repair here
              needs a vendor quotation.
            </p>
          ) : (
            <p>
              <StatusBadge value={incident.coverage.coverage_status} /> · Vendor: {vendorName(incident.coverage.vendor_id)}
            </p>
          )}
          <table className="data-table">
            <thead>
              <tr>
                <th>Type</th>
                <th>Vendor</th>
                <th>Reference</th>
                <th>Valid until</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ["AMC", incident.coverage.amc],
                  ["CMC", incident.coverage.cmc],
                  ["Warranty", incident.coverage.warranty],
                ] as const
              ).map(([label, coverage]) => (
                <tr key={label}>
                  <td>{label}</td>
                  <td>{coverage ? vendorName(coverage.vendor_id) : "-"}</td>
                  <td>
                    {coverage?.contract_id ? (
                      <Link href={`/contracts/${coverage.contract_id}`}>{coverage.contract_number ?? coverage.contract_id}</Link>
                    ) : (
                      "Not covered"
                    )}
                  </td>
                  <td>{coverage?.end_date ?? "-"}</td>
                  <td>{coverage ? <StatusBadge value={coverage.expiry_status} /> : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Module 21 — Procurement status rollup, same read-only/detail-only discipline
          as the Coverage card above. Only quotation.create-permission holders (BME/
          Procurement Officer) see the "raise a quotation" prompt when nothing exists
          yet, since only they can actually create one. */}
      {incident.procurement && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Procurement</h3>
          {incident.procurement.quotations.length === 0 ? (
            <p>
              No procurement activity yet.
              {hasPermission("quotation.create") && (
                <>
                  {" "}
                  If a vendor repair is needed and the asset is not fully covered, <Link href="/procurement/quotations">raise a quotation</Link>.
                </>
              )}
            </p>
          ) : (
            <>
              <p>
                Financial closure:{" "}
                <StatusBadge value={incident.procurement.financially_closed ? "COMPLETED" : "PENDING"} /> — independent
                of this incident&apos;s own technical closure above.
              </p>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Quotation</th>
                    <th>Coverage</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {incident.procurement.quotations.map((q) => (
                    <tr key={q.id}>
                      <td>
                        <Link href={`/procurement/quotations/${q.id}`}>{q.quotation_number}</Link>
                      </td>
                      <td>
                        <StatusBadge value={q.coverage_status} />
                      </td>
                      <td>
                        <StatusBadge value={q.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {incident.procurement.purchase_orders.length > 0 && (
                <table className="data-table" style={{ marginTop: 12 }}>
                  <thead>
                    <tr>
                      <th>Purchase order</th>
                      <th>Status</th>
                      <th>Invoice</th>
                    </tr>
                  </thead>
                  <tbody>
                    {incident.procurement.purchase_orders.map((po) => {
                      const relatedInvoice = incident.procurement!.invoices.find((inv) => inv.purchase_order_id === po.id);
                      return (
                        <tr key={po.id}>
                          <td>
                            <Link href={`/procurement/purchase-orders/${po.id}`}>{po.po_number}</Link>
                          </td>
                          <td>
                            <StatusBadge value={po.status} />
                          </td>
                          <td>
                            {relatedInvoice ? (
                              <Link href={`/procurement/invoices/${relatedInvoice.id}`}>
                                <StatusBadge value={relatedInvoice.verification_status} />
                              </Link>
                            ) : (
                              "Not yet recorded"
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
      )}

      {canManage && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Assign</h3>
          <form onSubmit={handleAssign} className="form-grid">
            <div className="form-field">
              <label>Engineer user ID</label>
              <input name="assigned_engineer_id" defaultValue={incident.assigned_engineer_id ?? ""} />
            </div>
            <div className="form-field">
              <label>Vendor ID</label>
              <input name="assigned_vendor_id" defaultValue={incident.assigned_vendor_id ?? ""} />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Assign
              </button>
            </div>
          </form>

          <h3>Change status</h3>
          <form onSubmit={handleStatusChange} className="form-grid">
            <div className="form-field">
              <label>New status</label>
              <select name="to_status" defaultValue={incident.status}>
                {INCIDENT_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Notes</label>
              <input name="notes" placeholder="Optional" />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Update status
              </button>
            </div>
          </form>

          <h3>Diagnosis / resolution</h3>
          <form onSubmit={handleUpdate} className="form-grid">
            <div className="form-field">
              <label>Root cause</label>
              <input name="root_cause" defaultValue={incident.root_cause ?? ""} />
            </div>
            <div className="form-field">
              <label>Corrective action</label>
              <input name="corrective_action" defaultValue={incident.corrective_action ?? ""} />
            </div>
            <div className="form-field">
              <label>Preventive action</label>
              <input name="preventive_action" defaultValue={incident.preventive_action ?? ""} />
            </div>
            <div className="form-field">
              <label>Resolution</label>
              <input name="resolution" defaultValue={incident.resolution ?? ""} />
            </div>
            <div className="form-field">
              <label>Parts used</label>
              <input name="parts_used" defaultValue={incident.parts_used ?? ""} />
            </div>
            <div className="form-field">
              <label>Downtime (hours)</label>
              <input name="downtime_hours" type="number" step="0.1" defaultValue={incident.downtime_hours ?? ""} />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Save
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Status history</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>From</th>
              <th>To</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.id}>
                <td>{h.from_status ?? "-"}</td>
                <td>
                  <StatusBadge value={h.to_status} />
                </td>
                <td>{h.notes ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ProtectedShell>
  );
}
