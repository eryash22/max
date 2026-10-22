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
import { useUserDirectory } from "../../../hooks/useUserDirectory";
import { useAssetVendorMap } from "../../../hooks/useDeviceVendorInfo";
import { api, ApiError } from "../../../lib/api";
import type { Asset, Branch, DeviceModel, Incident, IncidentStatusHistoryEntry, Vendor } from "../../../types";
import { INCIDENT_STATUSES } from "../../../types";

export default function IncidentDetailPage() {
  const params = useParams<{ id: string }>();
  const { hasPermission } = useAuth();
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const vendorName = (id: string | null) => (id ? vendors.find((v) => v.id === id)?.name ?? id : "-");
  const branches = useLookup<Branch>("/api/v1/branches");
  const branchName = (id: string | null) => (id ? branches.find((b) => b.id === id)?.name ?? id : "-");
  const assets = useLookup<Asset>("/api/v1/assets");
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const assetLabel = (id: string | null) => {
    if (!id) return null;
    const asset = assets.find((a) => a.id === id);
    const model = asset ? models.find((m) => m.id === asset.device_model_id) : undefined;
    return model?.name ?? asset?.asset_number ?? id;
  };
  const users = useUserDirectory();
  const userName = (id: string | null) => (id ? users.find((u) => u.id === id)?.name ?? id : "Unassigned");
  // The device's own primary/alternative vendor (DeviceModelVendor, rank 0 = primary)
  // — used to pre-select Assign's vendor dropdown so a BME doesn't have to look up
  // and pick the vendor manually every time; they can still override it.
  const assetVendorByModel = useAssetVendorMap(vendors);
  const [incident, setIncident] = useState<Incident | null>(null);
  const [history, setHistory] = useState<IncidentStatusHistoryEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [editingDetails, setEditingDetails] = useState(false);
  const deviceVendorId = (() => {
    const asset = incident?.asset_id ? assets.find((a) => a.id === incident.asset_id) : undefined;
    return asset ? assetVendorByModel.get(asset.device_model_id)?.id : undefined;
  })();

  const canManage = hasPermission("incident.manage");
  // No DELETE endpoint exists for incidents by design — they can be referenced by
  // asset history, repair, procurement, and audit. CANCELLED is the safe "archive"
  // equivalent: it preserves the incident and its full status/audit history. Not
  // offered once the incident is already operational/cancelled.
  const canCancel = canManage && !["OPERATIONAL", "CANCELLED"].includes(incident?.status ?? "");

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

  async function handleDetailsUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      const dateValue = formData.get("created_at");
      await api.patch(`/api/v1/incidents/${params.id}`, {
        branch_id: formData.get("branch_id") || undefined,
        asset_id: formData.get("asset_id") || null,
        created_at: dateValue ? `${dateValue}T00:00:00` : undefined,
        clinical_impact: formData.get("clinical_impact") || null,
        patient_care_impact: formData.get("patient_care_impact") || null,
        device_status_at_report: formData.get("device_status_at_report") || null,
      });
      setEditingDetails(false);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update incident details.");
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
        cer_number: formData.get("cer_number") || null,
        indent_number: formData.get("indent_number") || null,
        po_number: formData.get("po_number") || null,
        po_closure_date: formData.get("po_closure_date") || null,
        po_cost_excl_gst: formData.get("po_cost_excl_gst") ? Number(formData.get("po_cost_excl_gst")) : null,
        po_gst_percent: formData.get("po_gst_percent") ? Number(formData.get("po_gst_percent")) : null,
        po_amount: formData.get("po_amount") ? Number(formData.get("po_amount")) : null,
        vendor_estimate_cost_excl_gst: formData.get("vendor_estimate_cost_excl_gst")
          ? Number(formData.get("vendor_estimate_cost_excl_gst"))
          : null,
        vendor_estimate_gst_percent: formData.get("vendor_estimate_gst_percent")
          ? Number(formData.get("vendor_estimate_gst_percent"))
          : null,
        vendor_estimate_cost_incl_gst: formData.get("vendor_estimate_cost_incl_gst")
          ? Number(formData.get("vendor_estimate_cost_incl_gst"))
          : null,
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
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ marginTop: 0 }}>Details</h3>
          {canManage && !editingDetails && (
            <button type="button" className="btn btn--ghost" onClick={() => setEditingDetails(true)}>
              Edit
            </button>
          )}
        </div>

        {editingDetails ? (
          <form onSubmit={handleDetailsUpdate} className="form-grid">
            <div className="form-field">
              <label>Branch</label>
              <select name="branch_id" defaultValue={incident.branch_id} required>
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Asset</label>
              <select name="asset_id" defaultValue={incident.asset_id ?? ""}>
                <option value="">Not linked</option>
                {assets.map((a) => (
                  <option key={a.id} value={a.id}>
                    {assetLabel(a.id)} ({a.asset_number ?? "no asset number"})
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Date reported</label>
              <input name="created_at" type="date" defaultValue={incident.created_at.slice(0, 10)} />
            </div>
            <div className="form-field">
              <label>Clinical impact</label>
              <input name="clinical_impact" defaultValue={incident.clinical_impact ?? ""} />
            </div>
            <div className="form-field">
              <label>Patient-care impact</label>
              <input name="patient_care_impact" defaultValue={incident.patient_care_impact ?? ""} />
            </div>
            <div className="form-field">
              <label>Device status at report</label>
              <input name="device_status_at_report" defaultValue={incident.device_status_at_report ?? ""} />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end", gap: 8 }}>
              <button type="button" className="btn btn--ghost" onClick={() => setEditingDetails(false)} disabled={busy}>
                Cancel
              </button>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Save
              </button>
            </div>
          </form>
        ) : (
          <table className="data-table">
            <tbody>
              <tr>
                <td>Branch</td>
                <td>{branchName(incident.branch_id)}</td>
              </tr>
              <tr>
                <td>Asset</td>
                <td>
                  {incident.asset_id ? (
                    <Link href={`/assets/${incident.asset_id}`}>{assetLabel(incident.asset_id)}</Link>
                  ) : (
                    "Not linked"
                  )}
                </td>
              </tr>
              <tr>
                <td>Date reported</td>
                <td>{incident.created_at.slice(0, 10)}</td>
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
                <td>Device status at report</td>
                <td>{incident.device_status_at_report ?? "-"}</td>
              </tr>
              <tr>
                <td>Assigned engineer</td>
                <td>{userName(incident.assigned_engineer_id)}</td>
              </tr>
              <tr>
                <td>Assigned vendor</td>
                <td>{vendorName(incident.assigned_vendor_id)}</td>
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
              <tr>
                <td>CER No.</td>
                <td>{incident.cer_number ?? "-"}</td>
              </tr>
              <tr>
                <td>Indent No.</td>
                <td>{incident.indent_number ?? "-"}</td>
              </tr>
              <tr>
                <td>PO No.</td>
                <td>{incident.po_number ?? "-"}</td>
              </tr>
              <tr>
                <td>PO closure date</td>
                <td>{incident.po_closure_date ?? "-"}</td>
              </tr>
              <tr>
                <td>PO cost (excl. GST)</td>
                <td>{incident.po_cost_excl_gst != null ? `₹${incident.po_cost_excl_gst.toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "-"}</td>
              </tr>
              <tr>
                <td>PO GST %</td>
                <td>{incident.po_gst_percent ?? "-"}</td>
              </tr>
              <tr>
                <td>PO amount (incl. GST)</td>
                <td>{incident.po_amount != null ? `₹${incident.po_amount.toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "-"}</td>
              </tr>
              <tr>
                <td>Vendor estimate (excl. GST)</td>
                <td>
                  {incident.vendor_estimate_cost_excl_gst != null
                    ? `₹${incident.vendor_estimate_cost_excl_gst.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
                    : "-"}
                </td>
              </tr>
              <tr>
                <td>Vendor estimate (incl. GST)</td>
                <td>
                  {incident.vendor_estimate_cost_incl_gst != null
                    ? `₹${incident.vendor_estimate_cost_incl_gst.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
                    : "-"}
                </td>
              </tr>
            </tbody>
          </table>
        )}
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
              <label>Engineer</label>
              <select name="assigned_engineer_id" defaultValue={incident.assigned_engineer_id ?? ""}>
                <option value="">Unassigned</option>
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Vendor</label>
              <select
                key={incident.assigned_vendor_id ?? deviceVendorId ?? "none"}
                name="assigned_vendor_id"
                defaultValue={incident.assigned_vendor_id ?? deviceVendorId ?? ""}
              >
                <option value="">None</option>
                {vendors.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
              {!incident.assigned_vendor_id && deviceVendorId && (
                <p className="field-hint">Pre-selected from this device&apos;s own vendor — change it if needed.</p>
              )}
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
            <div className="form-field">
              <label>CER No.</label>
              <input name="cer_number" defaultValue={incident.cer_number ?? ""} />
            </div>
            <div className="form-field">
              <label>Indent No.</label>
              <input name="indent_number" defaultValue={incident.indent_number ?? ""} />
            </div>
            <div className="form-field">
              <label>PO No.</label>
              <input name="po_number" defaultValue={incident.po_number ?? ""} />
            </div>
            <div className="form-field">
              <label>PO closure date</label>
              <input name="po_closure_date" defaultValue={incident.po_closure_date ?? ""} />
            </div>
            <div className="form-field">
              <label>PO cost (excl. GST)</label>
              <input name="po_cost_excl_gst" type="number" step="0.01" defaultValue={incident.po_cost_excl_gst ?? ""} />
            </div>
            <div className="form-field">
              <label>PO GST %</label>
              <input name="po_gst_percent" type="number" step="0.01" defaultValue={incident.po_gst_percent ?? ""} />
            </div>
            <div className="form-field">
              <label>PO amount (incl. GST)</label>
              <input name="po_amount" type="number" step="0.01" defaultValue={incident.po_amount ?? ""} />
            </div>
            <div className="form-field">
              <label>Vendor estimate (excl. GST)</label>
              <input
                name="vendor_estimate_cost_excl_gst"
                type="number"
                step="0.01"
                defaultValue={incident.vendor_estimate_cost_excl_gst ?? ""}
              />
            </div>
            <div className="form-field">
              <label>Vendor estimate GST %</label>
              <input
                name="vendor_estimate_gst_percent"
                type="number"
                step="0.01"
                defaultValue={incident.vendor_estimate_gst_percent ?? ""}
              />
            </div>
            <div className="form-field">
              <label>Vendor estimate (incl. GST)</label>
              <input
                name="vendor_estimate_cost_incl_gst"
                type="number"
                step="0.01"
                defaultValue={incident.vendor_estimate_cost_incl_gst ?? ""}
              />
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
