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
import { ExportButtons } from "../../components/ExportButtons";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { usePaginatedResource } from "../../hooks/usePaginatedResource";
import { useLookup } from "../../hooks/useLookup";
import { useAuth } from "../../hooks/useAuth";
import { useArchivedDeviceModels, useAssetVendorMap, coverageForAsset } from "../../hooks/useDeviceVendorInfo";
import { api, ApiError } from "../../lib/api";
import { locationLabel } from "../../lib/locationLabel";
import type { Asset, Branch, CentralDeviceStock, Consumable, ConsumableTransaction, Contract, DeviceModel, Vendor } from "../../types";
import { CONSUMABLE_CATEGORIES, CONSUMABLE_STOCK_STATUSES, DEVICE_CATEGORIES } from "../../types";

// Batch D, Part 4 — unified Devices + Consumables inventory view. This is a
// frontend presentation layer only: it composes two already-existing, independently
// authorized API resources (Assets and Consumables) side by side. No backend model
// merge, no new resource.
//
// "Consumables" here is the `Consumable` model (branch_id NULL = central/hub pool,
// per-branch rows, its own transaction ledger — see /consumables, which this tab
// mirrors) rather than the `InventoryItem` "spare parts" catalog the old version of
// this page listed (with an `is_consumable` flag and a 500-row client-side sample).
// That choice is deliberate: every column this tab is asked for — Department/
// category, Current quantity, Stock status, Central/Branch context, Last
// transaction — is a native `Consumable` concept (and `Consumable` has no Asset
// Number/Serial Number columns to begin with, by construction). Plain "spare parts"
// (InventoryItem rows with is_consumable=false) are a distinct concern the dashboard
// already tracks separately (see dashboard/page.tsx's "Section 5" comment) and are
// out of scope for this redesign; their detail page (/inventory/[id]) is untouched
// and still reachable directly.

type Tab = "ALL" | "DEVICES" | "CONSUMABLES";
const PAGE_SIZE = 25;

// Union of both domains' real category vocabularies (Batch D, Part 4 spec: reuse
// existing values, never invent new ones). A value only meaningful for one tab
// simply narrows that tab's results to zero when applied to the other — no special
// casing needed since both /assets and /consumables now accept `category` server-side.
const ALL_CATEGORIES = Array.from(new Set([...DEVICE_CATEGORIES, ...CONSUMABLE_CATEGORIES])).sort();

function LastTransactionCell({ consumableId }: { consumableId: string }) {
  const [txn, setTxn] = useState<ConsumableTransaction | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    api
      .get<ConsumableTransaction | null>(`/api/v1/consumables/${consumableId}/last-transaction`)
      .then((result) => {
        if (!cancelled) setTxn(result);
      })
      .catch(() => {
        if (!cancelled) setTxn(null);
      });
    return () => {
      cancelled = true;
    };
  }, [consumableId]);

  if (txn === undefined) return <span className="dashboard-note">Loading…</span>;
  if (!txn) return <span>-</span>;
  return (
    <span title={new Date(txn.created_at).toLocaleString()}>
      {txn.transaction_type.replace(/_/g, " ")} · {txn.quantity} · {new Date(txn.created_at).toLocaleDateString()}
    </span>
  );
}

// Part B — "Central warehouse stock". Extends this existing unified inventory view
// (rather than a new page) with the two central pools side by side: central
// Consumables (Consumable.branch_id IS NULL, reusing the same `central_only` filter
// the Requirement picker fix added) and central Devices (a physical-Asset count per
// DeviceModel at the is_central_warehouse Branch — see GET /assets/central-device-stock).
// Only rendered for callers holding inventory.read (Inventory Manager/Super Admin) —
// the same permission that already gates full stock-quantity visibility elsewhere;
// a requester role (UNIT_HEAD/TL, inventory.picker_read only) gets a 403 from either
// endpoint, so the section is hidden rather than shown empty/erroring.
function CentralWarehouseStock() {
  const [consumables, setConsumables] = useState<Consumable[] | null>(null);
  const [devices, setDevices] = useState<CentralDeviceStock[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.get<{ items: Consumable[] }>("/api/v1/consumables?central_only=true&page=1&page_size=200"),
      api.get<CentralDeviceStock[]>("/api/v1/assets/central-device-stock"),
    ])
      .then(([consumableResult, deviceResult]) => {
        if (cancelled) return;
        setConsumables(consumableResult.items);
        setDevices(deviceResult);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load central warehouse stock.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error || consumables === null || devices === null) return null;

  return (
    <div className="card" style={{ marginBottom: 24 }}>
      <h3 style={{ marginTop: 0 }}>Central warehouse stock</h3>
      <div className="form-grid" style={{ alignItems: "start" }}>
        <div>
          <strong>Consumables</strong>
          <table className="table" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>Item</th>
                <th>Qty</th>
              </tr>
            </thead>
            <tbody>
              {consumables.length === 0 && (
                <tr>
                  <td colSpan={2}>No central consumable stock.</td>
                </tr>
              )}
              {consumables.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link href={`/consumables/${c.id}`}>{c.name}</Link>
                  </td>
                  <td>{c.opening_quantity ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <strong>Physical devices / assets</strong>
          <table className="table" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>Device / model</th>
                <th>Available qty</th>
              </tr>
            </thead>
            <tbody>
              {devices.length === 0 && (
                <tr>
                  <td colSpan={2}>No central physical device stock.</td>
                </tr>
              )}
              {devices.map((d) => (
                <tr key={d.device_model_id}>
                  <td>{d.name}</td>
                  <td>{d.available_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default function InventoryPage() {
  const { hasPermission } = useAuth();
  const canWriteConsumables = hasPermission("inventory.write");
  const canForceDelete = hasPermission("dev.data_cleanup");
  const canViewCentralStock = hasPermission("inventory.read");

  const [rowError, setRowError] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ id: string; message: string } | null>(null);

  const [tab, setTab] = useState<Tab>("ALL");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [deviceStatus, setDeviceStatus] = useState("");
  const [stockStatus, setStockStatus] = useState("");
  const [branchFilter, setBranchFilter] = useState("");

  // Hydrates filters from the URL so dashboard drill-down links (e.g.
  // /inventory?stock_status=LOW_STOCK) land pre-filtered — same convention
  // /consumables and /assets already use. A stock_status param is a consumables-only
  // concept here (Assets have no stock status), so it also selects that tab.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const ss = params.get("stock_status");
    if (ss) {
      setStockStatus(ss);
      setTab("CONSUMABLES");
    }
    const bf = params.get("branch_id");
    if (bf) setBranchFilter(bf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const branches = useLookup<Branch>("/api/v1/branches");
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const archivedModels = useArchivedDeviceModels();
  const allModels = [...models, ...archivedModels];
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const contracts = useLookup<Contract>("/api/v1/contracts");
  const vendorByModel = useAssetVendorMap(vendors);
  const modelName = (id: string) => allModels.find((m) => m.id === id)?.name ?? id;
  const modelManufacturer = (id: string) => allModels.find((m) => m.id === id)?.manufacturer ?? "-";
  const modelNumber = (id: string) => allModels.find((m) => m.id === id)?.model_number ?? "-";

  const deviceQuery: Record<string, string> = {};
  if (search) deviceQuery.search = search;
  if (category) deviceQuery.category = category;
  if (deviceStatus) deviceQuery.status_filter = deviceStatus;
  if (branchFilter) deviceQuery.branch_id = branchFilter;
  const devicesRes = usePaginatedResource<Asset>("/api/v1/assets", deviceQuery);

  const consumableQuery: Record<string, string> = {};
  if (category) consumableQuery.category = category;
  if (branchFilter) consumableQuery.branch_id = branchFilter;
  const consumablesRes = usePaginatedResource<Consumable>("/api/v1/consumables", consumableQuery);
  const visibleConsumables = (consumablesRes.data?.items ?? []).filter((c) => {
    if (stockStatus && c.stock_status !== stockStatus) return false;
    if (search) {
      const needle = search.toLowerCase();
      if (!c.item_code.toLowerCase().includes(needle) && !c.name.toLowerCase().includes(needle)) return false;
    }
    return true;
  });

  useEffect(() => {
    devicesRes.setPage(1);
    consumablesRes.setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, category, deviceStatus, stockStatus, branchFilter, tab]);

  function coverageFor(asset: Asset) {
    return coverageForAsset(asset, contracts);
  }

  async function handleDeleteConsumable(consumableId: string, force = false) {
    setRowError(null);
    setConflict(null);
    setRowBusy(consumableId);
    try {
      await api.delete(`/api/v1/consumables/${consumableId}${force ? "?force=true" : ""}`);
      setConfirmDeleteId(null);
      await consumablesRes.reload();
    } catch (err) {
      setConfirmDeleteId(null);
      if (err instanceof ApiError && err.status === 409) {
        setConflict({ id: consumableId, message: err.message });
      } else {
        setRowError(err instanceof ApiError ? err.message : "Failed to delete consumable.");
      }
    } finally {
      setRowBusy(null);
    }
  }

  const deviceColumns = [
    {
      key: "name",
      header: "Device name",
      render: (a: Asset) => <Link href={`/assets/${a.id}`}>{modelName(a.device_model_id)}</Link>,
    },
    { key: "manufacturer", header: "Manufacturer / Brand", render: (a: Asset) => modelManufacturer(a.device_model_id) },
    { key: "model_number", header: "Model number", render: (a: Asset) => modelNumber(a.device_model_id) },
    { key: "asset_number", header: "Asset number", render: (a: Asset) => a.asset_number ?? "Not assigned" },
    { key: "serial_number", header: "Serial number", render: (a: Asset) => a.serial_number ?? "Not provided" },
    { key: "branch", header: "Branch / Warehouse", render: (a: Asset) => locationLabel(a.branch_id, branches) },
    { key: "status", header: "Current status", render: (a: Asset) => <StatusBadge value={a.status} /> },
    { key: "vendor", header: "Vendor", render: (a: Asset) => vendorByModel.get(a.device_model_id)?.name ?? "-" },
    {
      key: "vendor_contact",
      header: "Vendor contact",
      render: (a: Asset) => vendorByModel.get(a.device_model_id)?.phone ?? "-",
    },
    {
      key: "vendor_email",
      header: "Vendor email",
      render: (a: Asset) => vendorByModel.get(a.device_model_id)?.email ?? "-",
    },
    {
      key: "coverage",
      header: "Coverage",
      render: (a: Asset) => {
        const c = coverageFor(a);
        return <span className={`badge badge--${c.tone}`}>{c.label}</span>;
      },
    },
    ...(hasPermission("asset.write")
      ? [
          {
            key: "actions",
            header: "Actions",
            render: (a: Asset) => (
              <Link className="btn btn--ghost" href={`/assets/${a.id}`}>
                Edit
              </Link>
            ),
          },
        ]
      : []),
  ];

  const consumableColumns = [
    {
      key: "name",
      header: "Consumable name",
      render: (c: Consumable) => <Link href={`/consumables/${c.id}`}>{c.name}</Link>,
    },
    { key: "category", header: "Department / category", render: (c: Consumable) => c.category ?? "-" },
    { key: "opening_quantity", header: "Current quantity", render: (c: Consumable) => c.opening_quantity ?? 0 },
    { key: "stock_status", header: "Stock status", render: (c: Consumable) => <StatusBadge value={c.stock_status} /> },
    { key: "location", header: "Central / Branch context", render: (c: Consumable) => locationLabel(c.branch_id, branches) },
    {
      key: "vendor",
      header: "Vendor",
      // Vendor contact fields (name/email/phone) arrive already masked server-side
      // for callers without full vendor.read — see backend/app/services/vendor_service.py.
      render: (c: Consumable) => vendors.find((v) => v.id === c.vendor_id)?.name ?? c.supplier ?? "-",
    },
    {
      key: "last_transaction",
      header: "Last transaction",
      render: (c: Consumable) => <LastTransactionCell consumableId={c.id} />,
    },
    ...(canWriteConsumables
      ? [
          {
            key: "actions",
            header: "Actions",
            render: (c: Consumable) => (
              <div style={{ display: "flex", gap: 6 }}>
                <Link className="btn btn--ghost" href={`/consumables/${c.id}`}>
                  Edit
                </Link>
                <button
                  className="btn btn--ghost"
                  disabled={rowBusy === c.id}
                  onClick={() => setConfirmDeleteId(c.id)}
                >
                  Delete
                </button>
              </div>
            ),
          },
        ]
      : []),
  ];

  const loading = tab === "DEVICES" ? devicesRes.loading : tab === "CONSUMABLES" ? consumablesRes.loading : devicesRes.loading || consumablesRes.loading;
  const error = tab === "DEVICES" ? devicesRes.error : tab === "CONSUMABLES" ? consumablesRes.error : devicesRes.error || consumablesRes.error;

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Inventory</h1>
          <p>Unified view of physical devices and consumable stock — devices from the Assets register, consumables from branch/central stock.</p>
        </div>
        {(hasPermission("inventory.write") || (hasPermission("device.write") && hasPermission("asset.write"))) && (
          <Link href="/inventory/new" className="btn btn--primary">
            New item
          </Link>
        )}
      </div>

      {canViewCentralStock && <CentralWarehouseStock />}

      <div className="toolbar" style={{ marginBottom: 8 }}>
        {(["ALL", "DEVICES", "CONSUMABLES"] as Tab[]).map((t) => (
          <button
            key={t}
            className={tab === t ? "btn btn--primary" : "btn btn--ghost"}
            onClick={() => setTab(t)}
          >
            {t === "ALL" ? "All" : t === "DEVICES" ? "Devices" : "Consumables"}
          </button>
        ))}
      </div>

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search by name, item code, asset or serial number..." />
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories / departments</option>
          {ALL_CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select value={branchFilter} onChange={(e) => setBranchFilter(e.target.value)}>
          <option value="">All branches</option>
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.is_central_warehouse ? `${b.name} (Central warehouse)` : b.name}
            </option>
          ))}
        </select>
        {tab !== "CONSUMABLES" && (
          <input
            value={deviceStatus}
            onChange={(e) => setDeviceStatus(e.target.value)}
            placeholder="Device status (e.g. OPERATIONAL)"
          />
        )}
        {tab !== "DEVICES" && (
          <select value={stockStatus} onChange={(e) => setStockStatus(e.target.value)}>
            <option value="">All stock statuses</option>
            {CONSUMABLE_STOCK_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        )}
      </div>

      {tab === "DEVICES" && (
        <ExportButtons
          path="/api/v1/assets/export"
          filters={{ search, branch_id: branchFilter, status_filter: deviceStatus }}
          recordCount={devicesRes.data?.total ?? 0}
          filenameStem="devices_export"
        />
      )}

      {conflict && (
        <div
          className="state-panel state-panel--error"
          style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 16 }}
        >
          <span>{conflict.message}</span>
          {canForceDelete && (
            <button className="btn btn--danger" disabled={rowBusy === conflict.id} onClick={() => handleDeleteConsumable(conflict.id, true)}>
              Force delete anyway
            </button>
          )}
        </div>
      )}
      {rowError && <ErrorState message={rowError} />}
      {error && <ErrorState message={error} />}
      {loading && <LoadingState />}

      {!loading && !error && (tab === "ALL" || tab === "DEVICES") && (
        <>
          {tab === "ALL" && <h3>Devices</h3>}
          <DataTable<Asset> columns={deviceColumns} rows={devicesRes.data?.items ?? []} emptyLabel="No devices found." />
          {tab === "DEVICES" && (
            <Pagination
              page={devicesRes.page}
              pageSize={devicesRes.data?.page_size ?? PAGE_SIZE}
              total={devicesRes.data?.total ?? 0}
              onPageChange={devicesRes.setPage}
            />
          )}
        </>
      )}

      {!loading && !error && (tab === "ALL" || tab === "CONSUMABLES") && (
        <>
          {tab === "ALL" && (
            <h3 style={{ marginTop: 24 }}>
              Consumables
            </h3>
          )}
          <DataTable<Consumable> columns={consumableColumns} rows={visibleConsumables} emptyLabel="No consumables found." />
          {tab === "CONSUMABLES" && (
            <Pagination
              page={consumablesRes.page}
              pageSize={consumablesRes.data?.page_size ?? PAGE_SIZE}
              total={consumablesRes.data?.total ?? 0}
              onPageChange={consumablesRes.setPage}
            />
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete consumable"
        message={
          canForceDelete
            ? "This permanently deletes the consumable. If it's still referenced by transaction history or requirements, you'll be offered a force-delete that cascades through those. Continue?"
            : "This permanently deletes the consumable. This only succeeds if it has no transaction history and no requirement references it — otherwise deactivate it instead (PATCH status=INACTIVE on its detail page). Continue?"
        }
        confirmLabel="Delete"
        onConfirm={() => confirmDeleteId && handleDeleteConsumable(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
