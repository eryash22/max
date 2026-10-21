"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
import { api, ApiError } from "../../lib/api";
import type { Branch, QrAssetStatus, QrBulkGenerateResult } from "../../types";

export default function QrManagementPage() {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [branchFilter, setBranchFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [missingOnly, setMissingOnly] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [bulkResult, setBulkResult] = useState<QrBulkGenerateResult | null>(null);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [confirmAction, setConfirmAction] = useState<{ type: "regenerate" | "revoke"; assetId: string } | null>(null);

  const query: Record<string, string> = {};
  if (search) query.search = search;
  if (branchFilter) query.branch_id = branchFilter;
  if (statusFilter) query.status_filter = statusFilter;
  if (missingOnly) query.missing_only = "true";
  const { data, page, setPage, loading, error, reload } = usePaginatedResource<QrAssetStatus>("/api/v1/qr/assets", query);
  const branches = useLookup<Branch>("/api/v1/branches");

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAllOnPage() {
    if (!data) return;
    setSelected((prev) => {
      const next = new Set(prev);
      const allSelected = data.items.every((a) => next.has(a.asset_id));
      for (const a of data.items) {
        if (allSelected) next.delete(a.asset_id);
        else next.add(a.asset_id);
      }
      return next;
    });
  }

  async function handleGenerateOne(assetId: string) {
    setRowError(null);
    setRowBusy(assetId);
    try {
      await api.post(`/api/v1/qr/assets/${assetId}/generate`);
      await reload();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to generate QR code.");
    } finally {
      setRowBusy(null);
    }
  }

  async function handleBulkGenerateMissing() {
    setBulkRunning(true);
    setRowError(null);
    try {
      const result = await api.post<QrBulkGenerateResult>("/api/v1/qr/bulk-generate", {});
      setBulkResult(result);
      await reload();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Bulk generation failed.");
    } finally {
      setBulkRunning(false);
    }
  }

  async function handleConfirmedAction() {
    if (!confirmAction) return;
    const { type, assetId } = confirmAction;
    setRowBusy(assetId);
    setRowError(null);
    try {
      await api.post(`/api/v1/qr/assets/${assetId}/${type}`, { confirm: true });
      await reload();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : `Failed to ${type} QR code.`);
    } finally {
      setRowBusy(null);
      setConfirmAction(null);
    }
  }

  function printSelected() {
    if (selected.size === 0) return;
    router.push(`/qr-management/print?ids=${Array.from(selected).join(",")}`);
  }

  return (
    <ProtectedShell requiredPermission="qr.manage">
      <div className="page-header">
        <div>
          <h1>Device QR management</h1>
          <p>
            Generate, print and control the stable QR identity printed on each physical asset. Scanning a label
            opens a read-only public page showing only the fields enabled in{" "}
            <Link href="/qr-management/settings">QR display settings</Link>.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn--primary" onClick={handleBulkGenerateMissing} disabled={bulkRunning}>
            {bulkRunning ? "Generating..." : "Generate missing QR codes"}
          </button>
          <button className="btn btn--ghost" disabled={selected.size === 0} onClick={printSelected}>
            Print selected ({selected.size})
          </button>
        </div>
      </div>

      {bulkResult && (
        <div className="state-panel state-panel--success" style={{ marginBottom: 16 }}>
          Generated {bulkResult.generated}, already had a QR code {bulkResult.already_existing}
          {bulkResult.failed > 0 ? `, failed ${bulkResult.failed}` : ""}.
        </div>
      )}
      {rowError && <ErrorState message={rowError} />}

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search by asset number or serial..." />
        <select value={branchFilter} onChange={(e) => setBranchFilter(e.target.value)}>
          <option value="">All branches</option>
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
        <input
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          placeholder="Status (e.g. RETIRED, CONDEMNED)"
        />
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={missingOnly} onChange={(e) => setMissingOnly(e.target.checked)} />
          Missing QR only
        </label>
        <button className="btn btn--ghost" onClick={toggleSelectAllOnPage} disabled={!data || data.items.length === 0}>
          {data && data.items.length > 0 && data.items.every((a) => selected.has(a.asset_id))
            ? "Unselect page"
            : "Select page"}
        </button>
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<QrAssetStatus & { id: string }>
            columns={[
              {
                key: "select",
                header: "Select",
                render: (a) => (
                  <input type="checkbox" checked={selected.has(a.asset_id)} onChange={() => toggleSelected(a.asset_id)} />
                ),
              },
              { key: "asset_number", header: "Asset number", render: (a) => a.asset_number ?? "Not assigned" },
              { key: "device_name", header: "Device", render: (a) => a.device_name },
              { key: "serial_number", header: "Serial number", render: (a) => a.serial_number ?? "-" },
              { key: "branch_name", header: "Branch", render: (a) => a.branch_name ?? "-" },
              { key: "vendor_name", header: "Vendor", render: (a) => a.vendor_name ?? "-" },
              { key: "status", header: "Status", render: (a) => <StatusBadge value={a.status} /> },
              {
                key: "qr_status",
                header: "QR status",
                render: (a) =>
                  !a.has_qr ? (
                    <span className="badge badge--neutral">Not generated</span>
                  ) : a.qr_active ? (
                    <span className="badge badge--success">Active</span>
                  ) : (
                    <span className="badge badge--danger">Revoked</span>
                  ),
              },
              {
                key: "actions",
                header: "Actions",
                render: (a) => (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {!a.has_qr ? (
                      <button className="btn btn--ghost" disabled={rowBusy === a.asset_id} onClick={() => handleGenerateOne(a.asset_id)}>
                        Generate
                      </button>
                    ) : (
                      <>
                        <a className="btn btn--ghost" href={`/qr-management/print?ids=${a.asset_id}`} target="_blank" rel="noreferrer">
                          Preview
                        </a>
                        <button
                          className="btn btn--ghost"
                          disabled={rowBusy === a.asset_id}
                          onClick={() => setConfirmAction({ type: "regenerate", assetId: a.asset_id })}
                        >
                          Regenerate
                        </button>
                        {a.qr_active ? (
                          <button
                            className="btn btn--ghost"
                            disabled={rowBusy === a.asset_id}
                            onClick={() => setConfirmAction({ type: "revoke", assetId: a.asset_id })}
                          >
                            Delete / Revoke QR
                          </button>
                        ) : (
                          <button
                            className="btn btn--ghost"
                            disabled={rowBusy === a.asset_id}
                            onClick={async () => {
                              setRowBusy(a.asset_id);
                              try {
                                await api.post(`/api/v1/qr/assets/${a.asset_id}/reactivate`);
                                await reload();
                              } catch (err) {
                                setRowError(err instanceof ApiError ? err.message : "Failed to reactivate QR code.");
                              } finally {
                                setRowBusy(null);
                              }
                            }}
                          >
                            Reactivate
                          </button>
                        )}
                      </>
                    )}
                  </div>
                ),
              },
            ]}
            rows={data.items.map((a) => ({ ...a, id: a.asset_id }))}
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={confirmAction !== null}
        title={confirmAction?.type === "regenerate" ? "Regenerate QR code" : "Delete / Revoke QR code"}
        message={
          confirmAction?.type === "regenerate"
            ? "This replaces the QR code with a brand new one. Any previously printed label for this asset will stop working. Continue?"
            : "This immediately stops any previously printed label for this asset from working. Continue?"
        }
        confirmLabel={confirmAction?.type === "regenerate" ? "Regenerate" : "Revoke"}
        onConfirm={handleConfirmedAction}
        onCancel={() => setConfirmAction(null)}
      />
    </ProtectedShell>
  );
}
