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
import { MultiSelect } from "../../components/MultiSelect";
import { ExportButtons } from "../../components/ExportButtons";
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
  // Multiple statuses can be selected at once (e.g. Open + Work in Progress) —
  // joined into one comma-separated status_filter query param the backend splits.
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [severity, setSeverity] = useState("");
  const [branchId, setBranchId] = useState("");
  const [vendorId, setVendorId] = useState("");
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
    const vf = params.get("vendor_id");
    if (bf) setBranchId(bf);
    if (sf) setStatusFilter(sf.split(",").filter(Boolean));
    if (sev) setSeverity(sev);
    if (vf) setVendorId(vf);
  }, []);

  // date/status/severity sort is real server-side ordering (see backend
  // list_incidents' sort_by/sort_dir) — previously this whole dropdown only
  // re-sorted the 25 rows already on the current page, so a bulk import of
  // older-dated incidents landed on a late page under "Date (newest)" and
  // looked like it never imported. branch/vendor/device stay page-only
  // (client-side below) since they need a join the backend's plain
  // .distinct() query can't combine with ORDER BY in Postgres.
  const SERVER_SORT_FIELDS: Record<string, { sort_by: string; sort_dir: string }> = {
    date_desc: { sort_by: "date", sort_dir: "desc" },
    date_asc: { sort_by: "date", sort_dir: "asc" },
    status: { sort_by: "status", sort_dir: "asc" },
    severity: { sort_by: "severity", sort_dir: "asc" },
  };

  const query: Record<string, string> = {};
  if (search) query.search = search;
  if (statusFilter.length > 0) query.status_filter = statusFilter.join(",");
  if (severity) query.severity = severity;
  if (branchId) query.branch_id = branchId;
  if (vendorId) query.vendor_id = vendorId;
  const serverSort = SERVER_SORT_FIELDS[sortBy];
  if (serverSort) {
    query.sort_by = serverSort.sort_by;
    query.sort_dir = serverSort.sort_dir;
  }

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

  // date/status/severity are already correctly ordered by the server (see
  // SERVER_SORT_FIELDS above) across the whole dataset, not just this page —
  // re-sorting them here again would only reorder the 25 already-fetched
  // rows and silently undo that. branch/vendor/device remain page-only
  // client-side sorts (see the comment above SERVER_SORT_FIELDS).
  const sortedItems = data
    ? [...data.items].sort((a, b) => {
        switch (sortBy) {
          case "branch":
            return branchName(a.branch_id).localeCompare(branchName(b.branch_id));
          case "vendor":
            return vendorNameFor(a).localeCompare(vendorNameFor(b));
          case "device":
            return deviceNameFor(a).localeCompare(deviceNameFor(b));
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

  // ---- Bulk delete ----
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [selectAllMatchingFilter, setSelectAllMatchingFilter] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [bulkPreview, setBulkPreview] = useState<{
    total_selected: number;
    deletable_count: number;
    blocked: { incident_id: string; description: string; blocked_by: string }[];
  } | null>(null);
  const [bulkResult, setBulkResult] = useState<{ deleted: number; skipped: number } | null>(null);

  function toggleSelected(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setSelectAllMatchingFilter(false);
  }

  function toggleSelectAllOnPage() {
    const pageIds = sortedItems.map((i) => i.id);
    const allSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id));
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allSelected) {
        pageIds.forEach((id) => next.delete(id));
      } else {
        pageIds.forEach((id) => next.add(id));
      }
      return next;
    });
    setSelectAllMatchingFilter(false);
  }

  function bulkSelectionPayload() {
    return selectAllMatchingFilter
      ? {
          select_all_matching_filter: true,
          search: search || undefined,
          branch_id: branchId || undefined,
          status_filter: statusFilter.length > 0 ? statusFilter.join(",") : undefined,
          severity: severity || undefined,
          vendor_id: vendorId || undefined,
        }
      : { incident_ids: Array.from(selectedIds) };
  }

  async function handlePreviewBulkDelete() {
    setBulkError(null);
    setBulkBusy(true);
    try {
      const result = await api.post<typeof bulkPreview>("/api/v1/incidents/bulk-delete/preview", bulkSelectionPayload());
      setBulkPreview(result);
    } catch (err) {
      setBulkError(err instanceof ApiError ? err.message : "Failed to preview bulk delete.");
    } finally {
      setBulkBusy(false);
    }
  }

  async function handleConfirmBulkDelete() {
    setBulkBusy(true);
    try {
      const result = await api.post<{ deleted: number; skipped: number }>("/api/v1/incidents/bulk-delete", bulkSelectionPayload());
      setBulkResult(result);
      setBulkPreview(null);
      setSelectedIds(new Set());
      setSelectAllMatchingFilter(false);
      await reload();
    } catch (err) {
      setBulkError(err instanceof ApiError ? err.message : "Failed to delete selected incidents.");
      setBulkPreview(null);
    } finally {
      setBulkBusy(false);
    }
  }

  const selectionCount = selectAllMatchingFilter ? data?.total ?? 0 : selectedIds.size;

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
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {hasPermission("import.create") && (
            <Link href="/imports" className="btn btn--ghost">
              Import
            </Link>
          )}
          {canManage && (
            <Link href="/incidents/replace-all" className="btn btn--ghost">
              Replace all from Excel
            </Link>
          )}
          {hasPermission("incident.create") && (
            <Link href="/incidents/new" className="btn btn--primary">
              Report incident
            </Link>
          )}
        </div>
      </div>

      <ExportButtons
        path="/api/v1/incidents/export"
        filters={{ search, status_filter: statusFilter.join(","), severity, branch_id: branchId, vendor_id: vendorId }}
        recordCount={data?.total ?? 0}
        filenameStem="incidents_export"
      />

      <div className="toolbar">
        <SearchBar
          value={search}
          onChange={setSearch}
          placeholder="Search device, branch, vendor, description, remark, CER/Indent/PO no..."
        />
        <MultiSelect
          label="Statuses"
          options={INCIDENT_STATUSES.map((s) => ({ value: s, label: s.replace(/_/g, " ") }))}
          selected={statusFilter}
          onChange={setStatusFilter}
        />
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
        <select value={vendorId} onChange={(e) => setVendorId(e.target.value)}>
          <option value="">All vendors</option>
          {vendors.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
          <option value="date_desc">Sort: Date (newest)</option>
          <option value="date_asc">Sort: Date (oldest)</option>
          <option value="status">Sort: Status</option>
          <option value="severity">Sort: Severity</option>
          <option value="branch">Sort: Branch</option>
          <option value="vendor">Sort: Vendor</option>
          <option value="device">Sort: Device name</option>
        </select>
      </div>

      {rowError && <ErrorState message={rowError} />}
      {bulkError && <ErrorState message={bulkError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}

      {canManage && (selectedIds.size > 0 || selectAllMatchingFilter) && (
        <div className="card" style={{ marginBottom: 16, display: "flex", alignItems: "center", gap: 12 }}>
          <strong>{selectionCount} incident{selectionCount === 1 ? "" : "s"} selected</strong>
          {!selectAllMatchingFilter && (data?.total ?? 0) > selectedIds.size && (
            <button className="btn btn--ghost" onClick={() => setSelectAllMatchingFilter(true)}>
              Select all {data?.total} matching the current filter
            </button>
          )}
          <button
            className="btn btn--ghost"
            onClick={() => {
              setSelectedIds(new Set());
              setSelectAllMatchingFilter(false);
            }}
          >
            Clear selection
          </button>
          <button className="btn btn--danger" disabled={bulkBusy} onClick={handlePreviewBulkDelete}>
            Delete selected
          </button>
        </div>
      )}

      {data && (
        <>
          <DataTable<Incident>
            columns={[
              ...(canManage
                ? [
                    {
                      key: "select",
                      header: (
                        <input
                          type="checkbox"
                          aria-label="Select all on this page"
                          checked={sortedItems.length > 0 && sortedItems.every((i) => selectedIds.has(i.id))}
                          onChange={toggleSelectAllOnPage}
                        />
                      ),
                      render: (i: Incident) => (
                        <input
                          type="checkbox"
                          aria-label={`Select ${i.description}`}
                          checked={selectAllMatchingFilter || selectedIds.has(i.id)}
                          onChange={() => toggleSelected(i.id)}
                        />
                      ),
                    },
                  ]
                : []),
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
                render: (i) => (i.po_cost_excl_gst != null ? `₹${i.po_cost_excl_gst.toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "-"),
              },
              {
                key: "po_amount",
                header: "PO Amount (incl. GST)",
                render: (i) => (i.po_amount != null ? `₹${i.po_amount.toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "-"),
              },
              {
                key: "vendor_estimate_excl",
                header: "Vendor Estimate (excl. GST)",
                render: (i) =>
                  i.vendor_estimate_cost_excl_gst != null
                    ? `₹${i.vendor_estimate_cost_excl_gst.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
                    : "-",
              },
              {
                key: "vendor_estimate_incl",
                header: "Vendor Estimate (incl. GST)",
                render: (i) =>
                  i.vendor_estimate_cost_incl_gst != null
                    ? `₹${i.vendor_estimate_cost_incl_gst.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
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

      <ConfirmDialog
        open={bulkPreview !== null}
        title="Delete selected incidents"
        message={
          bulkPreview
            ? bulkPreview.blocked.length === 0
              ? `${bulkPreview.deletable_count} incident(s) will be permanently deleted (status history, ` +
                `escalation log and document links removed with them; any linked service visit/PM/calibration ` +
                `record is kept, just unlinked). This cannot be undone. Continue?`
              : `${bulkPreview.deletable_count} of ${bulkPreview.total_selected} incident(s) will be deleted. ` +
                `${bulkPreview.blocked.length} cannot be deleted — still referenced by a real procurement record ` +
                `(e.g. ${bulkPreview.blocked[0].blocked_by}) — those will be skipped, not forced through. Continue?`
            : ""
        }
        confirmLabel={bulkBusy ? "Deleting…" : "Delete"}
        onConfirm={handleConfirmBulkDelete}
        onCancel={() => setBulkPreview(null)}
      />

      {bulkResult && (
        <div className="card" style={{ marginTop: 16 }}>
          <p style={{ margin: 0 }}>
            Deleted {bulkResult.deleted} incident{bulkResult.deleted === 1 ? "" : "s"}
            {bulkResult.skipped > 0 ? `, skipped ${bulkResult.skipped} (still referenced by a procurement record)` : ""}.
          </p>
          <button className="btn btn--ghost" style={{ marginTop: 8 }} onClick={() => setBulkResult(null)}>
            Dismiss
          </button>
        </div>
      )}
    </ProtectedShell>
  );
}
