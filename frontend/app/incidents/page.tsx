"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { SearchBar } from "../../components/SearchBar";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { usePaginatedResource } from "../../hooks/usePaginatedResource";
import { useLookup } from "../../hooks/useLookup";
import { useAuth } from "../../hooks/useAuth";
import { useAssetVendorMap } from "../../hooks/useDeviceVendorInfo";
import { api, ApiError } from "../../lib/api";
import type { Asset, Branch, DeviceModel, Incident, Vendor } from "../../types";
import { INCIDENT_SEVERITIES, INCIDENT_STATUSES } from "../../types";

// DD/MM/YYYY (and a month name for extra clarity) instead of the browser-locale
// default `toLocaleDateString()` would give — that silently renders as MM/DD/YYYY
// for an en-US browser, which misreads a day-of-month above 12 as a different month
// entirely (the exact confusion this was built to avoid).
function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  const day = String(d.getDate()).padStart(2, "0");
  const month = d.toLocaleString("en-GB", { month: "short" });
  return `${day} ${month} ${d.getFullYear()}`;
}

export default function IncidentsPage() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [severity, setSeverity] = useState("");
  const [branchId, setBranchId] = useState("");
  const [sortBy, setSortBy] = useState("date_desc");

  // Restructure consolidation pass, Part B — hydrate filters from the URL on mount so
  // drill-down links (e.g. from the Branch detail page's "Open incidents" tile, or the
  // Dashboard's "Critical incidents" KPI) apply their filter, matching the same idiom
  // Module 18B already added to 8 other list pages (no useSearchParams()/Suspense
  // restructuring needed for a handful of query params).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const bf = params.get("branch_id");
    const sf = params.get("status_filter");
    const sev = params.get("severity");
    if (bf) setBranchId(bf);
    if (sf) setStatusFilter(sf);
    if (sev) setSeverity(sev);
  }, []);

  const query: Record<string, string> = {};
  if (search) query.search = search;
  if (statusFilter) query.status_filter = statusFilter;
  if (severity) query.severity = severity;
  if (branchId) query.branch_id = branchId;

  const { data, page, setPage, loading, error, reload } = usePaginatedResource<Incident>("/api/v1/incidents", query);
  const branches = useLookup<Branch>("/api/v1/branches");
  const branchName = (id: string) => branches.find((b) => b.id === id)?.name ?? id;

  // Device name + vendor aren't stored on Incident directly — both are resolved via
  // the reported Asset's DeviceModel (same "derive, don't duplicate" discipline the
  // rest of the app uses for vendor/coverage). Device Catalog admin visibility isn't
  // required here — only the handful of display fields already safe for any
  // incident.read holder.
  const assets = useLookup<Asset>("/api/v1/assets");
  const assetById = new Map(assets.map((a) => [a.id, a]));
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const modelById = new Map(models.map((m) => [m.id, m]));
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const vendorByModel = useAssetVendorMap(vendors);
  const deviceNameFor = (i: Incident) => {
    const asset = i.asset_id ? assetById.get(i.asset_id) : undefined;
    const model = asset ? modelById.get(asset.device_model_id) : undefined;
    return model?.name ?? "-";
  };
  const vendorNameFor = (i: Incident) => {
    const asset = i.asset_id ? assetById.get(i.asset_id) : undefined;
    return (asset ? vendorByModel.get(asset.device_model_id)?.name : undefined) ?? "-";
  };

  // Client-side sort over the current page's rows — the backend already orders by
  // newest-first, so "Date (newest)" needs no extra work; the other options just
  // re-sort what's already loaded rather than adding a second server-side ordering
  // param for a handful of columns.
  const sortedItems = data
    ? [...data.items].sort((a, b) => {
        switch (sortBy) {
          case "date_asc":
            return a.created_at.localeCompare(b.created_at);
          case "date_desc":
            return b.created_at.localeCompare(a.created_at);
          case "status":
            return a.status.localeCompare(b.status);
          case "severity":
            return a.severity.localeCompare(b.severity);
          case "branch":
            return branchName(a.branch_id).localeCompare(branchName(b.branch_id));
          default:
            return 0;
        }
      })
    : [];
  const { hasPermission } = useAuth();
  const canManage = hasPermission("incident.manage");
  const canForceDelete = hasPermission("dev.data_cleanup");
  const [rowError, setRowError] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  async function handleForceDelete(incidentId: string) {
    setRowError(null);
    setRowBusy(incidentId);
    try {
      await api.delete(`/api/v1/incidents/${incidentId}?force=true`);
      setConfirmDeleteId(null);
      await reload();
    } catch (err) {
      setConfirmDeleteId(null);
      setRowError(err instanceof ApiError ? err.message : "Failed to delete incident.");
    } finally {
      setRowBusy(null);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Incidents</h1>
          <p>Device breakdown / complaint reports. Doctors and staff see only what they reported.</p>
        </div>
        {hasPermission("incident.create") && (
          <Link href="/incidents/new" className="btn btn--primary">
            Report incident
          </Link>
        )}
      </div>

      <div className="toolbar">
        <SearchBar
          value={search}
          onChange={setSearch}
          placeholder="Search device, branch, vendor, description, remark, CER/Indent/PO no..."
        />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          {INCIDENT_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
          <option value="">All severities</option>
          {INCIDENT_SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select value={branchId} onChange={(e) => setBranchId(e.target.value)}>
          <option value="">All branches</option>
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
          <option value="date_desc">Sort: Date (newest)</option>
          <option value="date_asc">Sort: Date (oldest)</option>
          <option value="status">Sort: Status</option>
          <option value="severity">Sort: Severity</option>
          <option value="branch">Sort: Branch</option>
        </select>
      </div>

      {rowError && <ErrorState message={rowError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<Incident>
            columns={[
              {
                key: "date",
                header: "Date",
                render: (i) => <span style={{ whiteSpace: "nowrap" }}>{formatDate(i.created_at)}</span>,
              },
              { key: "device", header: "Device Name", render: (i) => deviceNameFor(i) },
              { key: "branch", header: "Branch", render: (i) => branchName(i.branch_id) },
              { key: "vendor", header: "Vendor", render: (i) => vendorNameFor(i) },
              {
                key: "description",
                header: "Issue Description",
                render: (i) => (
                  <Link
                    href={`/incidents/${i.id}`}
                    style={{ display: "inline-block", whiteSpace: "normal", wordBreak: "break-word", minWidth: 260 }}
                  >
                    {i.description}
                  </Link>
                ),
              },
              { key: "severity", header: "Severity", render: (i) => <StatusBadge value={i.severity} /> },
              { key: "status", header: "Status", render: (i) => <StatusBadge value={i.status} /> },
              { key: "category", header: "Category", render: (i) => i.problem_category ?? "-" },
              {
                key: "remark",
                header: "Remark",
                render: (i) => (
                  <span style={{ whiteSpace: "normal", wordBreak: "break-word", minWidth: 160, display: "inline-block" }}>
                    {i.resolution ?? "-"}
                  </span>
                ),
              },
              { key: "cer", header: "CER No.", render: (i) => i.cer_number ?? "-" },
              { key: "indent", header: "Indent No.", render: (i) => i.indent_number ?? "-" },
              { key: "po_number", header: "PO No.", render: (i) => i.po_number ?? "-" },
              { key: "po_closure", header: "PO Closure Date", render: (i) => i.po_closure_date ?? "-" },
              {
                key: "po_cost_excl",
                header: "PO Cost (excl. GST)",
                render: (i) => (i.po_cost_excl_gst != null ? `₹${i.po_cost_excl_gst.toLocaleString("en-IN")}` : "-"),
              },
              {
                key: "po_amount",
                header: "PO Amount (incl. GST)",
                render: (i) => (i.po_amount != null ? `₹${i.po_amount.toLocaleString("en-IN")}` : "-"),
              },
              {
                key: "vendor_estimate_excl",
                header: "Vendor Estimate (excl. GST)",
                render: (i) =>
                  i.vendor_estimate_cost_excl_gst != null
                    ? `₹${i.vendor_estimate_cost_excl_gst.toLocaleString("en-IN")}`
                    : "-",
              },
              {
                key: "vendor_estimate_incl",
                header: "Vendor Estimate (incl. GST)",
                render: (i) =>
                  i.vendor_estimate_cost_incl_gst != null
                    ? `₹${i.vendor_estimate_cost_incl_gst.toLocaleString("en-IN")}`
                    : "-",
              },
              ...(canManage
                ? [
                    {
                      key: "actions",
                      header: "Actions",
                      render: (i: Incident) => (
                        <div style={{ display: "flex", gap: 6 }}>
                          <Link className="btn btn--ghost" href={`/incidents/${i.id}`}>
                            Edit
                          </Link>
                          {canForceDelete && (
                            <button
                              className="btn btn--ghost"
                              disabled={rowBusy === i.id}
                              onClick={() => setConfirmDeleteId(i.id)}
                            >
                              Delete
                            </button>
                          )}
                        </div>
                      ),
                    },
                  ]
                : []),
            ]}
            rows={sortedItems}
            emptyLabel="No incidents found."
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Force-delete incident"
        message="This permanently deletes the incident and everything tied to it — status history, service visits, and its own quotation/indent/PO/invoice chain. Incidents have no hard delete otherwise — close/cancel is the normal archive path. This cannot be undone. Continue?"
        confirmLabel="Force delete"
        onConfirm={() => confirmDeleteId && handleForceDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
