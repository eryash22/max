"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { Pagination } from "../../../components/Pagination";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { useArchivedDeviceModels } from "../../../hooks/useDeviceVendorInfo";
import { api, ApiError } from "../../../lib/api";
import type {
  Asset,
  AssetHistory,
  AssetLifecycleSummary,
  AssetProcurementProvenanceEntry,
  AssetProvenanceEntry,
  Branch,
  Department,
  DeviceModel,
  Incident,
  Location,
  MaintenanceHistoryEntry,
  Page,
  QrAssetStatus,
  Vendor,
} from "../../../types";
import { ASSET_HISTORY_EVENT_TYPES, LIFECYCLE_STATUSES } from "../../../types";

const MAINTENANCE_PAGE_SIZE = 100;

export default function AssetDetailPage() {
  const params = useParams<{ id: string }>();
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("asset.write");

  const branches = useLookup<Branch>("/api/v1/branches");
  // Active-only — this is what the "Device model" edit dropdown offers as choices
  // (archived models must never be selectable). See `allModels` below for display.
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  // Archived models, merged with `models` purely for *display* (name resolution) so
  // an asset whose model has since been archived still shows a readable name here
  // instead of falling back to the raw device_model_id — same fix already applied on
  // the assets list page (see frontend/app/assets/page.tsx `allModels`).
  const archivedModels = useArchivedDeviceModels();
  const allModels = useMemo(() => [...models, ...archivedModels], [models, archivedModels]);
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const departments = useLookup<Department>("/api/v1/departments");
  const locations = useLookup<Location>("/api/v1/locations");
  const branchName = (id: string) => branches.find((b) => b.id === id)?.name ?? id;
  const modelName = (id: string) => allModels.find((m) => m.id === id)?.name ?? id;
  const modelById = useMemo(() => new Map(allModels.map((m) => [m.id, m])), [allModels]);
  const vendorName = (id: string | null) => (id ? vendors.find((v) => v.id === id)?.name ?? id : "-");
  const departmentName = (id: string | null) => (id ? departments.find((d) => d.id === id)?.name ?? id : "Not set");
  const locationName = (id: string | null) => (id ? locations.find((l) => l.id === id)?.name ?? id : "Not set");

  const [asset, setAsset] = useState<Asset | null>(null);
  const [lifecycle, setLifecycle] = useState<AssetLifecycleSummary | null>(null);
  const [maintenance, setMaintenance] = useState<MaintenanceHistoryEntry[]>([]);
  const [history, setHistory] = useState<Page<AssetHistory> | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [provenance, setProvenance] = useState<AssetProvenanceEntry[]>([]);
  const [procurementProvenance, setProcurementProvenance] = useState<AssetProcurementProvenanceEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Inventory/Asset unification pass — QR identity belongs to this physical Asset;
  // these actions live here (not only in /qr-management) so staff registering,
  // viewing or allocating a device find QR controls in the same place.
  const canManageQr = hasPermission("qr.manage");
  const [qrStatus, setQrStatus] = useState<QrAssetStatus | null>(null);
  const [qrBusy, setQrBusy] = useState(false);
  const [qrError, setQrError] = useState<string | null>(null);
  const [qrConfirmAction, setQrConfirmAction] = useState<"regenerate" | "revoke" | null>(null);

  const loadQrStatus = useCallback(async () => {
    if (!canManageQr) return;
    try {
      const result = await api.get<Page<QrAssetStatus>>(`/api/v1/qr/assets?asset_ids=${params.id}`);
      setQrStatus(result.items[0] ?? null);
    } catch (err) {
      setQrError(err instanceof ApiError ? err.message : "Failed to load QR status.");
    }
  }, [params.id, canManageQr]);

  useEffect(() => {
    loadQrStatus();
  }, [loadQrStatus]);

  async function handleGenerateQr() {
    setQrBusy(true);
    setQrError(null);
    try {
      await api.post(`/api/v1/qr/assets/${params.id}/generate`);
      await loadQrStatus();
    } catch (err) {
      setQrError(err instanceof ApiError ? err.message : "Failed to generate QR code.");
    } finally {
      setQrBusy(false);
    }
  }

  async function handleQrConfirmedAction() {
    if (!qrConfirmAction) return;
    setQrBusy(true);
    setQrError(null);
    try {
      await api.post(`/api/v1/qr/assets/${params.id}/${qrConfirmAction}`, { confirm: true });
      await loadQrStatus();
    } catch (err) {
      setQrError(err instanceof ApiError ? err.message : `Failed to ${qrConfirmAction} QR code.`);
    } finally {
      setQrBusy(false);
      setQrConfirmAction(null);
    }
  }

  async function handleReactivateQr() {
    setQrBusy(true);
    setQrError(null);
    try {
      await api.post(`/api/v1/qr/assets/${params.id}/reactivate`);
      await loadQrStatus();
    } catch (err) {
      setQrError(err instanceof ApiError ? err.message : "Failed to reactivate QR code.");
    } finally {
      setQrBusy(false);
    }
  }

  const loadAsset = useCallback(async () => {
    try {
      const result = await api.get<Asset>(`/api/v1/assets/${params.id}`);
      setAsset(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load asset.");
    }
  }, [params.id]);

  const loadLifecycle = useCallback(async () => {
    try {
      const result = await api.get<AssetLifecycleSummary>(`/api/v1/assets/${params.id}/lifecycle`);
      setLifecycle(result);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to load lifecycle summary.");
    }
  }, [params.id]);

  const loadMaintenance = useCallback(async () => {
    try {
      const query = new URLSearchParams({ page: "1", page_size: String(MAINTENANCE_PAGE_SIZE) });
      const result = await api.get<Page<MaintenanceHistoryEntry>>(`/api/v1/assets/${params.id}/maintenance-history?${query.toString()}`);
      setMaintenance(result.items);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to load maintenance history.");
    }
  }, [params.id]);

  const loadHistory = useCallback(async () => {
    try {
      const query = new URLSearchParams({ page: String(historyPage), page_size: "10" });
      if (eventTypeFilter) query.set("event_type", eventTypeFilter);
      const result = await api.get<Page<AssetHistory>>(`/api/v1/assets/${params.id}/history?${query.toString()}`);
      setHistory(result);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to load asset history.");
    }
  }, [params.id, historyPage, eventTypeFilter]);

  const loadIncidents = useCallback(async () => {
    try {
      const query = new URLSearchParams({ asset_id: params.id, page: "1", page_size: "50" });
      const result = await api.get<Page<Incident>>(`/api/v1/incidents?${query.toString()}`);
      setIncidents(result.items);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to load incident history.");
    }
  }, [params.id]);

  const loadProvenance = useCallback(async () => {
    try {
      const result = await api.get<AssetProvenanceEntry[]>(`/api/v1/assets/${params.id}/provenance`);
      setProvenance(result);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to load requirement/fulfillment history.");
    }
  }, [params.id]);

  useEffect(() => {
    loadAsset();
  }, [loadAsset]);

  useEffect(() => {
    loadLifecycle();
  }, [loadLifecycle]);

  useEffect(() => {
    loadMaintenance();
  }, [loadMaintenance]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  useEffect(() => {
    loadIncidents();
  }, [loadIncidents]);

  useEffect(() => {
    loadProvenance();
  }, [loadProvenance]);

  const loadProcurementProvenance = useCallback(async () => {
    try {
      const result = await api.get<AssetProcurementProvenanceEntry[]>(`/api/v1/assets/${params.id}/procurement-provenance`);
      setProcurementProvenance(result);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to load procurement history.");
    }
  }, [params.id]);

  useEffect(() => {
    loadProcurementProvenance();
  }, [loadProcurementProvenance]);

  async function handleUpdateAsset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!asset) return;
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.patch(`/api/v1/assets/${params.id}`, {
        asset_number: formData.get("asset_number") || null,
        serial_number: formData.get("serial_number") || null,
        device_model_id: formData.get("device_model_id"),
        branch_id: formData.get("branch_id"),
        department_id: formData.get("department_id") || null,
        location_id: formData.get("location_id") || null,
        status: formData.get("status"),
        criticality: formData.get("criticality") || null,
        purchase_date: formData.get("purchase_date") || null,
        installation_date: formData.get("installation_date") || null,
        operational_since: formData.get("operational_since") || null,
      });
      await loadAsset();
      await loadLifecycle();
      await loadHistory();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update asset.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddHistoryEntry(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post(`/api/v1/assets/${params.id}/history`, {
        event_type: formData.get("event_type"),
        from_value: formData.get("from_value") || null,
        to_value: formData.get("to_value") || null,
        notes: formData.get("notes") || null,
      });
      form.reset();
      setHistoryPage(1);
      await loadHistory();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to add history entry.");
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
  if (!asset) {
    return (
      <ProtectedShell>
        <LoadingState />
      </ProtectedShell>
    );
  }

  const deviceModel = modelById.get(asset.device_model_id);
  const pmEntries = maintenance.filter((m) => m.source_type === "PM");
  const calibrationEntries = maintenance.filter((m) => m.source_type === "CALIBRATION");
  const serviceVisitEntries = maintenance.filter((m) => m.source_type === "SERVICE_VISIT");

  return (
    <ProtectedShell>
      {/* HEADER */}
      <div className="page-header">
        <div>
          <h1>
            {modelName(asset.device_model_id)} — {asset.asset_number ?? "Asset number not assigned"}
          </h1>
          <p>
            {asset.serial_number ? `S/N ${asset.serial_number} · ` : ""}
            <StatusBadge value={asset.status} /> · {branchName(asset.branch_id)}
          </p>
        </div>
        {hasPermission("asset.write") && (
          <Link className="btn btn--ghost" href={`/inventory/allocate`}>
            Allocate to another branch
          </Link>
        )}
      </div>

      {actionError && <ErrorState message={actionError} />}

      {/* 1. ASSET OVERVIEW */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Asset overview</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Device model</td>
              <td>{modelName(asset.device_model_id)}</td>
            </tr>
            <tr>
              <td>Manufacturer</td>
              <td>{deviceModel?.manufacturer ?? "Not set"}</td>
            </tr>
            <tr>
              <td>Category</td>
              <td>{deviceModel?.category ?? "Not set"}</td>
            </tr>
            <tr>
              <td>Serial number</td>
              <td>{asset.serial_number ?? "Not provided"}</td>
            </tr>
            <tr>
              <td>Asset number</td>
              <td>{asset.asset_number ?? "Not assigned"}</td>
            </tr>
            <tr>
              <td>Criticality</td>
              <td>{asset.criticality ?? "Not set"}</td>
            </tr>
            <tr>
              <td>Purchase date</td>
              <td>{asset.purchase_date ?? "-"}</td>
            </tr>
            <tr>
              <td>Installation date</td>
              <td>{asset.installation_date ?? "-"}</td>
            </tr>
            <tr>
              <td>Operational since</td>
              <td>{asset.operational_since ?? "-"}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* 2. CURRENT LOCATION */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Current location</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Branch</td>
              <td>{branchName(asset.branch_id)}</td>
            </tr>
            <tr>
              <td>Department</td>
              <td>{departmentName(asset.department_id)}</td>
            </tr>
            <tr>
              <td>Location</td>
              <td>{locationName(asset.location_id)}</td>
            </tr>
            <tr>
              <td>Operational status</td>
              <td>
                <StatusBadge value={asset.status} />
              </td>
            </tr>
          </tbody>
        </table>
        <p className="field-hint">
          Branch/department/location transfers are recorded automatically in Lifecycle history below.
        </p>
      </div>

      {/* QR IDENTITY — belongs to this physical asset, stays stable across branch transfers */}
      {canManageQr && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>QR code</h3>
          {qrError && <div className="field-error" style={{ marginBottom: 12 }}>{qrError}</div>}
          {!qrStatus ? (
            <p className="dashboard-note">Loading QR status...</p>
          ) : !qrStatus.has_qr ? (
            <>
              <p className="dashboard-note">No QR code generated yet for this asset.</p>
              <button className="btn btn--primary" disabled={qrBusy} onClick={handleGenerateQr}>
                Generate QR code
              </button>
            </>
          ) : (
            <>
              <p>
                Status:{" "}
                {qrStatus.qr_active ? (
                  <span className="badge badge--success">Active</span>
                ) : (
                  <span className="badge badge--danger">Revoked</span>
                )}
                {qrStatus.qr_generated_at && ` · Generated ${new Date(qrStatus.qr_generated_at).toLocaleString()}`}
              </p>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <a className="btn btn--ghost" href={`/qr-management/print?ids=${asset.id}`} target="_blank" rel="noreferrer">
                  View / print / download
                </a>
                <button className="btn btn--ghost" disabled={qrBusy} onClick={() => setQrConfirmAction("regenerate")}>
                  Regenerate QR code
                </button>
                {qrStatus.qr_active ? (
                  <button className="btn btn--ghost" disabled={qrBusy} onClick={() => setQrConfirmAction("revoke")}>
                    Delete / Revoke QR
                  </button>
                ) : (
                  <button className="btn btn--ghost" disabled={qrBusy} onClick={handleReactivateQr}>
                    Reactivate QR code
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}

      <ConfirmDialog
        open={qrConfirmAction !== null}
        title={qrConfirmAction === "regenerate" ? "Regenerate QR code" : "Delete / Revoke QR code"}
        message={
          qrConfirmAction === "regenerate"
            ? "This replaces the QR code with a brand new one. Any previously printed label for this asset will stop working. The physical asset, its history and all related records are never affected."
            : "This immediately stops any previously printed label for this asset from working. The physical asset, its Asset Number, history and all related records are never deleted — only the public QR link is disabled. You can generate a new QR code later."
        }
        confirmLabel={qrConfirmAction === "regenerate" ? "Regenerate" : "Revoke"}
        onConfirm={handleQrConfirmedAction}
        onCancel={() => setQrConfirmAction(null)}
      />

      {/* 3. REQUIREMENT & FULFILLMENT */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Requirement &amp; fulfillment</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Requirement</th>
              <th>Requesting branch</th>
              <th>Requirement status</th>
              <th>Fulfillment</th>
              <th>Fulfillment status</th>
              <th>Delivery / completion date</th>
            </tr>
          </thead>
          <tbody>
            {provenance.length === 0 && (
              <tr>
                <td colSpan={6}>Not delivered through a requirement/fulfillment — created directly.</td>
              </tr>
            )}
            {provenance.map((entry) => (
              <tr key={`${entry.requirement_id}-${entry.fulfillment_id}`}>
                <td>
                  <Link href={`/requirements/${entry.requirement_id}`}>{entry.requirement_number}</Link>
                </td>
                <td>{branchName(entry.requirement_branch_id)}</td>
                <td>
                  <StatusBadge value={entry.requirement_status} />
                </td>
                <td>
                  <Link href={`/fulfillments/${entry.fulfillment_id}`}>{entry.fulfillment_number}</Link>
                </td>
                <td>
                  <StatusBadge value={entry.fulfillment_status} />
                </td>
                <td>{entry.actual_delivery_date ?? entry.expected_delivery_date ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Module 21 — Procurement provenance. Same read-only, computed-fresh discipline
          as the Requirement & fulfillment card above. */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Procurement</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Quotation</th>
              <th>Coverage</th>
              <th>Indent</th>
              <th>Purchase order</th>
              <th>Invoice</th>
            </tr>
          </thead>
          <tbody>
            {procurementProvenance.length === 0 && (
              <tr>
                <td colSpan={5}>No procurement activity for this asset.</td>
              </tr>
            )}
            {procurementProvenance.map((entry) => (
              <tr key={entry.quotation_id}>
                <td>
                  <Link href={`/procurement/quotations/${entry.quotation_id}`}>{entry.quotation_number}</Link> —{" "}
                  <StatusBadge value={entry.quotation_status} />
                </td>
                <td>
                  <StatusBadge value={entry.coverage_status} />
                </td>
                <td>
                  {entry.indent_id ? (
                    <Link href={`/procurement/indents/${entry.indent_id}`}>{entry.indent_number}</Link>
                  ) : (
                    "-"
                  )}
                </td>
                <td>
                  {entry.purchase_order_id ? (
                    <>
                      <Link href={`/procurement/purchase-orders/${entry.purchase_order_id}`}>{entry.po_number}</Link> —{" "}
                      <StatusBadge value={entry.po_status ?? ""} />
                    </>
                  ) : (
                    "-"
                  )}
                </td>
                <td>
                  {entry.invoice_id ? (
                    <>
                      <Link href={`/procurement/invoices/${entry.invoice_id}`}>{entry.invoice_number}</Link> —{" "}
                      <StatusBadge value={entry.invoice_verification_status ?? ""} />
                    </>
                  ) : (
                    "-"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 4. INCIDENTS */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Incidents</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Category</th>
              <th>Severity</th>
              <th>Status</th>
              <th>Resolution</th>
              <th>Closed</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {incidents.length === 0 && (
              <tr>
                <td colSpan={6}>No incidents recorded for this asset.</td>
              </tr>
            )}
            {incidents.map((incident) => (
              <tr key={incident.id}>
                <td>{incident.problem_category ?? "-"}</td>
                <td>
                  <StatusBadge value={incident.severity} />
                </td>
                <td>
                  <StatusBadge value={incident.status} />
                </td>
                <td>{incident.resolution ?? "-"}</td>
                <td>{incident.closed_at ?? "-"}</td>
                <td>
                  <Link href={`/incidents/${incident.id}`}>View</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 5. PREVENTIVE MAINTENANCE */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Preventive maintenance</h3>
        {lifecycle && (
          <p className="field-hint">
            Last PM: {lifecycle.last_pm_date ?? "-"} · Next PM due: {lifecycle.next_pm_due_date ?? "-"}
          </p>
        )}
        <table className="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Performed by</th>
              <th>Result</th>
              <th>Next due</th>
              <th>Remarks</th>
            </tr>
          </thead>
          <tbody>
            {pmEntries.length === 0 && (
              <tr>
                <td colSpan={5}>No PM records for this asset.</td>
              </tr>
            )}
            {pmEntries.map((entry) => (
              <tr key={entry.id}>
                <td>{entry.service_date ?? "-"}</td>
                <td>{entry.performed_by_label ?? "-"}</td>
                <td>{entry.result ?? "-"}</td>
                <td>{entry.next_maintenance_date ?? "-"}</td>
                <td>{entry.remarks ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 6. CALIBRATION */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Calibration</h3>
        {lifecycle && (
          <p className="field-hint">
            Last calibration: {lifecycle.last_calibration_date ?? "-"} · Next due: {lifecycle.next_calibration_due_date ?? "-"}
          </p>
        )}
        <table className="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Agency</th>
              <th>Result</th>
              <th>Next due</th>
              <th>Remarks</th>
            </tr>
          </thead>
          <tbody>
            {calibrationEntries.length === 0 && (
              <tr>
                <td colSpan={5}>No calibration records for this asset.</td>
              </tr>
            )}
            {calibrationEntries.map((entry) => (
              <tr key={entry.id}>
                <td>{entry.service_date ?? "-"}</td>
                <td>{entry.performed_by_label ?? "-"}</td>
                <td>{entry.result ?? "-"}</td>
                <td>{entry.next_maintenance_date ?? "-"}</td>
                <td>{entry.remarks ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 7. SERVICE VISITS */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Service visits</h3>
        {lifecycle && <p className="field-hint">Last service visit: {lifecycle.last_service_visit_date ?? "-"}</p>}
        <table className="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Engineer / vendor</th>
              <th>Issue</th>
              <th>Resolution</th>
              <th>Downtime (hrs)</th>
            </tr>
          </thead>
          <tbody>
            {serviceVisitEntries.length === 0 && (
              <tr>
                <td colSpan={5}>No service visits for this asset.</td>
              </tr>
            )}
            {serviceVisitEntries.map((entry) => (
              <tr key={entry.id}>
                <td>{entry.service_date ?? "-"}</td>
                <td>{entry.performed_by_label ?? "-"}</td>
                <td>{entry.issue ?? "-"}</td>
                <td>{entry.action_taken ?? "-"}</td>
                <td>{entry.downtime_hours ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 8. AMC / CMC / WARRANTY */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>AMC / CMC / Warranty</h3>
        <p className="field-hint">
          Coverage and current vendor are derived live from the Contracts module — nothing here is stored on the asset itself.
        </p>
        {lifecycle && (
          <table className="data-table">
            <thead>
              <tr>
                <th>Type</th>
                <th>Vendor</th>
                <th>Reference</th>
                <th>Start</th>
                <th>End</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {([
                ["Warranty", lifecycle.warranty],
                ["AMC", lifecycle.amc],
                ["CMC", lifecycle.cmc],
              ] as const).map(([label, coverage]) => (
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
                  <td>{coverage?.start_date ?? "-"}</td>
                  <td>{coverage?.end_date ?? "-"}</td>
                  <td>{coverage ? <StatusBadge value={coverage.expiry_status} /> : "-"}</td>
                </tr>
              ))}
              <tr>
                <td>Current vendor</td>
                <td colSpan={5}>{vendorName(lifecycle.current_vendor_id)}</td>
              </tr>
            </tbody>
          </table>
        )}
      </div>

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Edit asset</h3>
          <form onSubmit={handleUpdateAsset} className="form-grid">
            <div className="form-field">
              <label>Asset number (optional)</label>
              <input name="asset_number" placeholder="Can be added or changed here" defaultValue={asset.asset_number ?? ""} />
            </div>
            <div className="form-field">
              <label>Serial number</label>
              <input name="serial_number" defaultValue={asset.serial_number ?? ""} />
            </div>
            <div className="form-field">
              <label>Device model*</label>
              <select name="device_model_id" required defaultValue={asset.device_model_id}>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
                {/* If the asset's current model was archived after assignment, it
                    won't be in `models` (active-only) — without this, the browser
                    would silently fall back to the first active option and saving
                    unrelated fields would corrupt device_model_id. Shown but not
                    re-selectable as "new" once changed away from. */}
                {!models.some((m) => m.id === asset.device_model_id) &&
                  archivedModels
                    .filter((m) => m.id === asset.device_model_id)
                    .map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name} (archived)
                      </option>
                    ))}
              </select>
            </div>
            <div className="form-field">
              <label>Branch*</label>
              <select name="branch_id" required defaultValue={asset.branch_id}>
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Status</label>
              <input name="status" defaultValue={asset.status} list="lifecycle-status-options" />
              <datalist id="lifecycle-status-options">
                {LIFECYCLE_STATUSES.map((s) => (
                  <option key={s} value={s} />
                ))}
              </datalist>
            </div>
            <div className="form-field">
              <label>Criticality</label>
              <input name="criticality" defaultValue={asset.criticality ?? ""} />
            </div>
            <div className="form-field">
              <label>Purchase date</label>
              <input name="purchase_date" type="date" defaultValue={asset.purchase_date ?? ""} />
            </div>
            <div className="form-field">
              <label>Installation date</label>
              <input name="installation_date" type="date" defaultValue={asset.installation_date ?? ""} />
            </div>
            <div className="form-field">
              <label>Operational since</label>
              <input name="operational_since" type="date" defaultValue={asset.operational_since ?? ""} />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Save changes
              </button>
            </div>
          </form>
          <p className="field-hint">
            Changes to status, branch, department, or location are automatically logged below as
            lifecycle history — no extra step needed.
          </p>
        </div>
      )}

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Add lifecycle event</h3>
          <p className="field-hint">
            For events the edit form above can&apos;t capture directly, e.g. an ownership change or a
            procurement-to-commissioned transition.
          </p>
          <form onSubmit={handleAddHistoryEntry} className="form-grid">
            <div className="form-field">
              <label>Event type*</label>
              <select name="event_type" required defaultValue="">
                <option value="" disabled>
                  Select event type
                </option>
                {ASSET_HISTORY_EVENT_TYPES.map((e) => (
                  <option key={e} value={e}>
                    {e.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>From</label>
              <input name="from_value" placeholder="Optional" />
            </div>
            <div className="form-field">
              <label>To</label>
              <input name="to_value" placeholder="Optional" />
            </div>
            <div className="form-field" style={{ gridColumn: "1 / -1" }}>
              <label>Notes</label>
              <textarea name="notes" rows={2} placeholder="Optional" />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Add event
              </button>
            </div>
          </form>
        </div>
      )}

      {/* 9. LIFECYCLE HISTORY */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="toolbar">
          <h3 style={{ marginTop: 0 }}>Lifecycle history</h3>
          <select
            value={eventTypeFilter}
            onChange={(e) => {
              setEventTypeFilter(e.target.value);
              setHistoryPage(1);
            }}
          >
            <option value="">All event types</option>
            {ASSET_HISTORY_EVENT_TYPES.map((e) => (
              <option key={e} value={e}>
                {e.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Event type</th>
              <th>From</th>
              <th>To</th>
              <th>Actor</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {history && history.items.length === 0 && (
              <tr>
                <td colSpan={6}>No lifecycle events recorded yet.</td>
              </tr>
            )}
            {history?.items.map((h) => (
              <tr key={h.id}>
                <td>{h.created_at ? new Date(h.created_at).toLocaleString() : "-"}</td>
                <td>
                  <StatusBadge value={h.event_type} />
                </td>
                <td>{h.from_value ?? "-"}</td>
                <td>{h.to_value ?? "-"}</td>
                <td>{h.changed_by ?? "-"}</td>
                <td>{h.notes ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {history && (
          <Pagination page={historyPage} pageSize={history.page_size} total={history.total} onPageChange={setHistoryPage} />
        )}
      </div>

      {/* 10 & 11. MAINTENANCE HISTORY (unified) + AUDIT HISTORY note */}
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Maintenance history (unified)</h3>
        <p className="field-hint">
          Unified view across Preventive Maintenance, Calibration, and Service Visit records — the same
          source used for the sections above. Generic audit events (create/update) for this asset are
          available via Audit Logs, filtered by entity &quot;asset&quot; and this asset&apos;s ID.
        </p>
        <table className="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Source</th>
              <th>Type</th>
              <th>Performed by</th>
              <th>Issue</th>
              <th>Action taken</th>
              <th>Parts used</th>
              <th>Downtime (hrs)</th>
              <th>Result</th>
              <th>Next due</th>
              <th>Remarks</th>
            </tr>
          </thead>
          <tbody>
            {maintenance.length === 0 && (
              <tr>
                <td colSpan={11}>No maintenance history recorded yet.</td>
              </tr>
            )}
            {maintenance.map((entry) => (
              <tr key={`${entry.source_type}-${entry.id}`}>
                <td>{entry.service_date ?? "-"}</td>
                <td>
                  <StatusBadge value={entry.source_type} />
                </td>
                <td>{entry.maintenance_type ?? "-"}</td>
                <td>{entry.performed_by_label ?? "-"}</td>
                <td>{entry.issue ?? "-"}</td>
                <td>{entry.action_taken ?? "-"}</td>
                <td>{entry.parts_used ?? "-"}</td>
                <td>{entry.downtime_hours ?? "-"}</td>
                <td>{entry.result ?? "-"}</td>
                <td>{entry.next_maintenance_date ?? "-"}</td>
                <td>{entry.remarks ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ProtectedShell>
  );
}
