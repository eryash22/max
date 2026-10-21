"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { SearchBar } from "../../components/SearchBar";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { ExportButtons } from "../../components/ExportButtons";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { usePaginatedResource } from "../../hooks/usePaginatedResource";
import { useLookup } from "../../hooks/useLookup";
import { useAuth } from "../../hooks/useAuth";
import { useArchivedDeviceModels, useAssetVendorMap, coverageForAsset } from "../../hooks/useDeviceVendorInfo";
import { api, ApiError } from "../../lib/api";
import type { Asset, Branch, Contract, DeviceModel, Location, Page, QrAssetStatus, Vendor } from "../../types";

export default function AssetsPage() {
  const [search, setSearch] = useState("");
  const [branchFilter, setBranchFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  // Batch C, Part 3 — Active/Archived toggle. "Archived" pins status_filter to the
  // canonical RETIRED lifecycle status (see archive_asset in routers/devices.py);
  // "Active" leaves the free-text status_filter box below usable as before.
  const [activeTab, setActiveTab] = useState<"active" | "archived">("active");

  // Hydrates filters from the URL so dashboard/calendar drill-down links (e.g.
  // /assets?status_filter=CONDEMNED) land on a pre-filtered list, not just this page.
  // `new=1` additionally opens the existing "New asset" form pre-filled with that
  // branch — the Branch detail page's "+ Assign Device" button links here rather
  // than growing a second asset-creation form (see branches/[id]/page.tsx).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const bf = params.get("branch_id");
    const sf = params.get("status_filter");
    if (bf) setBranchFilter(bf);
    if (sf) setStatusFilter(sf);
    if (params.get("new") === "1") setShowForm(true);
  }, []);

  const effectiveStatusFilter = activeTab === "archived" ? "RETIRED" : statusFilter;
  const query: Record<string, string> = {};
  if (search) query.search = search;
  if (branchFilter) query.branch_id = branchFilter;
  if (effectiveStatusFilter) query.status_filter = effectiveStatusFilter;
  const { data, page, setPage, loading, error, reload } = usePaginatedResource<Asset>("/api/v1/assets", query);
  const branches = useLookup<Branch>("/api/v1/branches");
  // Active-only — this is the set offered in the "New asset" device-model picker
  // (Part 2 archive/delete pass: archived models must never be selectable for new
  // assets/requirements).
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  // Archived models, fetched separately since `GET /device-models` only supports a
  // binary `is_active` filter (no "all" option) — merged with `models` below purely
  // for *display* (name/manufacturer) lookups so an asset referencing an archived
  // model still resolves to a readable name instead of falling back to its raw
  // device_model_id. Never used for the picker above.
  const archivedModels = useArchivedDeviceModels();
  const allModels = [...models, ...archivedModels];
  const locations = useLookup<Location>("/api/v1/locations");
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const contracts = useLookup<Contract>("/api/v1/contracts");
  const vendorByModel = useAssetVendorMap(vendors);
  const branchName = (id: string) => branches.find((b) => b.id === id)?.name ?? id;
  const modelName = (id: string) => allModels.find((m) => m.id === id)?.name ?? id;
  const locationName = (id: string | null) => (id ? locations.find((l) => l.id === id)?.name ?? id : "-");
  const coverageFor = (asset: Asset) => coverageForAsset(asset, contracts);
  const { hasPermission } = useAuth();
  const canWriteAsset = hasPermission("asset.write");
  const canManageQr = hasPermission("qr.manage");
  const canForceDelete = hasPermission("dev.data_cleanup");

  // Inventory/Asset unification pass — QR status/actions surfaced right on the
  // Physical Assets list, not only on the detail page or the separate QR Labels
  // screen. Fetched per visible page (asset_ids filter), not globally.
  const [qrByAssetId, setQrByAssetId] = useState<Map<string, QrAssetStatus>>(new Map());
  const assetIdsKey = data?.items.map((a) => a.id).join(",") ?? "";
  useEffect(() => {
    if (!canManageQr || !assetIdsKey) {
      setQrByAssetId(new Map());
      return;
    }
    let cancelled = false;
    api
      .get<Page<QrAssetStatus>>(`/api/v1/qr/assets?asset_ids=${assetIdsKey}&page_size=${assetIdsKey.split(",").length}`)
      .then((result) => {
        if (!cancelled) setQrByAssetId(new Map(result.items.map((q) => [q.asset_id, q])));
      })
      .catch(() => {
        if (!cancelled) setQrByAssetId(new Map());
      });
    return () => {
      cancelled = true;
    };
  }, [assetIdsKey, canManageQr]);

  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmArchiveId, setConfirmArchiveId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  async function handleArchiveToggle(asset: Asset) {
    setRowError(null);
    setRowBusy(asset.id);
    try {
      const action = asset.status === "RETIRED" ? "unarchive" : "archive";
      await api.post(`/api/v1/assets/${asset.id}/${action}`);
      setConfirmArchiveId(null);
      await reload();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to change asset status.");
    } finally {
      setRowBusy(null);
    }
  }

  async function handleForceDelete(assetId: string) {
    setRowError(null);
    setRowBusy(assetId);
    try {
      await api.delete(`/api/v1/assets/${assetId}?force=true`);
      setConfirmDeleteId(null);
      await reload();
    } catch (err) {
      setConfirmDeleteId(null);
      setRowError(err instanceof ApiError ? err.message : "Failed to delete asset.");
    } finally {
      setRowBusy(null);
    }
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/assets", {
        asset_number: formData.get("asset_number") || null,
        serial_number: formData.get("serial_number") || null,
        device_model_id: formData.get("device_model_id"),
        branch_id: formData.get("branch_id"),
        status: "Pending",
      });
      setShowForm(false);
      form.reset();
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create asset.");
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Devices</h1>
          <p>Every physical device (asset) in the system — name, category, branch, status, and coverage.</p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {hasPermission("device.write") && hasPermission("asset.write") && (
            <Link className="btn btn--primary" href="/devices/new">
              Add device
            </Link>
          )}
          {hasPermission("asset.write") && (
            <button className="btn btn--ghost" onClick={() => setShowForm((v) => !v)}>
              {showForm ? "Cancel" : "New asset only (existing model)"}
            </button>
          )}
        </div>
      </div>

      {showForm && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleCreate}>
          <div className="form-grid">
            <div className="form-field">
              <label>Asset number (optional)</label>
              <input name="asset_number" placeholder="Can be added later" />
            </div>
            <div className="form-field">
              <label>Serial number</label>
              <input name="serial_number" />
            </div>
            <div className="form-field">
              <label>Device model*</label>
              <select name="device_model_id" required defaultValue="">
                <option value="" disabled>
                  Select device model
                </option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Branch*</label>
              <select name="branch_id" required defaultValue={branchFilter}>
                <option value="" disabled>
                  Select branch
                </option>
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {formError && <div className="field-error">{formError}</div>}
          <button className="btn btn--primary" type="submit">
            Save asset
          </button>
        </form>
      )}

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
          placeholder="Status (e.g. OPERATIONAL)"
          disabled={activeTab === "archived"}
        />
        <div style={{ display: "flex", gap: 4 }}>
          <button
            className={activeTab === "active" ? "btn btn--primary" : "btn btn--ghost"}
            onClick={() => setActiveTab("active")}
          >
            Active
          </button>
          <button
            className={activeTab === "archived" ? "btn btn--primary" : "btn btn--ghost"}
            onClick={() => setActiveTab("archived")}
          >
            Archived
          </button>
        </div>
      </div>

      <ExportButtons
        path="/api/v1/assets/export"
        filters={{ search, branch_id: branchFilter, status_filter: statusFilter }}
        recordCount={data?.total ?? 0}
        filenameStem="assets_export"
      />

      {rowError && <ErrorState message={rowError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<Asset>
            columns={[
              {
                key: "asset_number",
                header: "Asset number",
                render: (a) => <Link href={`/assets/${a.id}`}>{a.asset_number ?? "Not assigned"}</Link>,
              },
              { key: "serial_number", header: "Serial number", render: (a) => a.serial_number ?? "Not provided" },
              { key: "device_model", header: "Device name", render: (a) => modelName(a.device_model_id) },
              {
                key: "manufacturer",
                header: "Brand / Manufacturer",
                render: (a) => allModels.find((m) => m.id === a.device_model_id)?.manufacturer ?? "-",
              },
              { key: "branch", header: "Branch", render: (a) => branchName(a.branch_id) },
              { key: "location", header: "Location", render: (a) => locationName(a.location_id) },
              {
                key: "vendor",
                header: "Vendor",
                render: (a) => vendorByModel.get(a.device_model_id)?.name ?? "-",
              },
              {
                key: "vendor_contact",
                header: "Vendor contact",
                render: (a) => vendorByModel.get(a.device_model_id)?.phone ?? "-",
              },
              {
                key: "vendor_email",
                header: "Vendor email",
                render: (a) => vendorByModel.get(a.device_model_id)?.email ?? "-",
              },
              {
                key: "coverage",
                header: "Coverage",
                render: (a) => {
                  const c = coverageFor(a);
                  return <span className={`badge badge--${c.tone}`}>{c.label}</span>;
                },
              },
              {
                key: "operational_since",
                header: "Operational since",
                render: (a) => a.operational_since ?? "-",
              },
              { key: "status", header: "Status", render: (a) => <StatusBadge value={a.status} /> },
              ...(canManageQr
                ? [
                    {
                      key: "qr",
                      header: "QR",
                      render: (a: Asset) => {
                        const qr = qrByAssetId.get(a.id);
                        if (!qr || !qr.has_qr) {
                          return <Link href={`/assets/${a.id}`}>Not generated</Link>;
                        }
                        return (
                          <Link href={`/assets/${a.id}`}>
                            <span className={`badge badge--${qr.qr_active ? "success" : "danger"}`}>
                              {qr.qr_active ? "Active" : "Revoked"}
                            </span>
                          </Link>
                        );
                      },
                    },
                  ]
                : []),
              ...(canWriteAsset
                ? [
                    {
                      key: "actions",
                      header: "Actions",
                      render: (a: Asset) => (
                        <div style={{ display: "flex", gap: 6 }}>
                          <Link className="btn btn--ghost" href={`/assets/${a.id}`}>
                            Edit
                          </Link>
                          <button
                            className="btn btn--ghost"
                            disabled={rowBusy === a.id}
                            onClick={() =>
                              a.status === "RETIRED" ? handleArchiveToggle(a) : setConfirmArchiveId(a.id)
                            }
                          >
                            {a.status === "RETIRED" ? "Unarchive" : "Archive"}
                          </button>
                          {canForceDelete && (
                            <button
                              className="btn btn--ghost"
                              disabled={rowBusy === a.id}
                              onClick={() => setConfirmDeleteId(a.id)}
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
            rows={data.items}
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={confirmArchiveId !== null}
        title="Archive asset"
        message="Archiving sets this asset's status to Retired, hiding it from active operational views while keeping all history (incidents, PM, calibration, contracts). It can be unarchived later. Continue?"
        confirmLabel="Archive"
        onConfirm={() => {
          const asset = data?.items.find((a) => a.id === confirmArchiveId);
          if (asset) handleArchiveToggle(asset);
        }}
        onCancel={() => setConfirmArchiveId(null)}
      />
      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Force-delete asset"
        message="This permanently deletes the asset AND every record that hangs off it — PM/calibration schedules and records, service visits, warranty, AMC/CMC links, and its procurement chain (quotations, indents, POs, invoices). This cannot be undone. Continue?"
        confirmLabel="Force delete"
        onConfirm={() => confirmDeleteId && handleForceDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
