"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { SearchBar } from "../../components/SearchBar";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useLookup } from "../../hooks/useLookup";
import { useAssetLabel } from "../../hooks/useAssetLabel";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type { Contract, Page, Vendor } from "../../types";
import { CONTRACT_EXPIRY_STATUSES, CONTRACT_TYPES } from "../../types";

const PAGE_SIZE = 25;

interface SummaryCounts {
  total: number;
  amc: number;
  cmc: number;
  warranty: number;
  expiringSoon: number;
  expired: number;
}

async function totalOf(path: string): Promise<number> {
  const result = await api.get<Page<unknown>>(`${path}${path.includes("?") ? "&" : "?"}page=1&page_size=1`);
  return result.total;
}

export default function ContractsPage() {
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("contract.write");
  const canForceDelete = hasPermission("dev.data_cleanup");

  const [summary, setSummary] = useState<SummaryCounts | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [contractType, setContractType] = useState("");
  const [expiryStatus, setExpiryStatus] = useState("");
  const [vendorId, setVendorId] = useState("");
  const [assetId, setAssetId] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);

  const [contracts, setContracts] = useState<Contract[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [rowError, setRowError] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ id: string; message: string } | null>(null);

  // Hydrates filters from the URL so dashboard/calendar drill-down links (e.g.
  // /contracts?expiry_status=EXPIRING_SOON&contract_type=AMC) land pre-filtered.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const ct = params.get("contract_type");
    const es = params.get("expiry_status");
    const sf = params.get("status_filter");
    if (ct) setContractType(ct);
    if (es) setExpiryStatus(es);
    if (sf) setStatusFilter(sf);
  }, []);

  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const vendorById = useMemo(() => new Map(vendors.map((v) => [v.id, v])), [vendors]);
  const { assets, labelForAssetId } = useAssetLabel();

  useEffect(() => {
    let cancelled = false;
    async function loadSummary() {
      try {
        const [total, amc, cmc, warranty, expiringSoon, expired] = await Promise.all([
          totalOf("/api/v1/contracts"),
          totalOf("/api/v1/contracts?contract_type=AMC"),
          totalOf("/api/v1/contracts?contract_type=CMC"),
          totalOf("/api/v1/contracts?contract_type=WARRANTY"),
          totalOf("/api/v1/contracts?expiry_status=EXPIRING_SOON"),
          totalOf("/api/v1/contracts?expiry_status=EXPIRED"),
        ]);
        if (!cancelled) setSummary({ total, amc, cmc, warranty, expiringSoon, expired });
      } catch (err) {
        if (!cancelled) setSummaryError(err instanceof ApiError ? err.message : "Failed to load contract summary.");
      }
    }
    loadSummary();
    return () => {
      cancelled = true;
    };
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (search) params.set("search", search);
      if (contractType) params.set("contract_type", contractType);
      if (expiryStatus) params.set("expiry_status", expiryStatus);
      if (vendorId) params.set("vendor_id", vendorId);
      if (assetId) params.set("asset_id", assetId);
      if (statusFilter) params.set("status_filter", statusFilter);
      const result = await api.get<Page<Contract>>(`/api/v1/contracts?${params.toString()}`);
      setContracts(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load contracts.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, contractType, expiryStatus, vendorId, assetId, statusFilter, page]);

  useEffect(() => {
    setPage(1);
  }, [search, contractType, expiryStatus, vendorId, assetId, statusFilter]);

  async function handleDelete(contractId: string, force = false) {
    setRowError(null);
    setConflict(null);
    setRowBusy(contractId);
    try {
      await api.delete(`/api/v1/contracts/${contractId}${force ? "?force=true" : ""}`);
      setConfirmDeleteId(null);
      await load();
    } catch (err) {
      setConfirmDeleteId(null);
      if (err instanceof ApiError && err.status === 409) {
        setConflict({ id: contractId, message: err.message });
      } else {
        setRowError(err instanceof ApiError ? err.message : "Failed to delete contract.");
      }
    } finally {
      setRowBusy(null);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>AMC / CMC / Warranty contracts</h1>
          <p>Unified view of annual maintenance, comprehensive maintenance, and warranty coverage.</p>
        </div>
        {hasPermission("contract.write") && (
          <Link href="/contracts/new" className="btn btn--primary">
            New contract
          </Link>
        )}
      </div>

      {summaryError && <ErrorState message={summaryError} />}
      {summary && (
        <div className="stat-grid" style={{ marginBottom: 16 }}>
          <StatTile label="Total contracts" value={summary.total} />
          <StatTile label="AMC" value={summary.amc} />
          <StatTile label="CMC" value={summary.cmc} />
          <StatTile label="Warranty" value={summary.warranty} />
          <StatTile label="Expiring soon" value={summary.expiringSoon} />
          <StatTile label="Expired" value={summary.expired} />
        </div>
      )}

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search by contract number..." />
        <select value={contractType} onChange={(e) => setContractType(e.target.value)}>
          <option value="">All types</option>
          {CONTRACT_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <select value={expiryStatus} onChange={(e) => setExpiryStatus(e.target.value)}>
          <option value="">All expiry statuses</option>
          {CONTRACT_EXPIRY_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
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
        <select value={assetId} onChange={(e) => setAssetId(e.target.value)}>
          <option value="">All assets</option>
          {assets.map((a) => (
            <option key={a.id} value={a.id}>
              {labelForAssetId(a.id)}
            </option>
          ))}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="ACTIVE">ACTIVE</option>
          <option value="EXPIRED">EXPIRED</option>
          <option value="RENEWAL_PENDING">RENEWAL PENDING</option>
          <option value="CANCELLED">CANCELLED</option>
          <option value="UNKNOWN">UNKNOWN</option>
        </select>
      </div>

      {conflict && (
        <div
          className="state-panel state-panel--error"
          style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 16 }}
        >
          <span>{conflict.message}</span>
          {canForceDelete && (
            <button className="btn btn--danger" disabled={rowBusy === conflict.id} onClick={() => handleDelete(conflict.id, true)}>
              Force delete anyway
            </button>
          )}
        </div>
      )}
      {rowError && <ErrorState message={rowError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {!loading && !error && (
        <>
          <DataTable<Contract>
            columns={[
              {
                key: "contract_number",
                header: "Contract #",
                render: (c) => <Link href={`/contracts/${c.id}`}>{c.contract_number ?? c.id}</Link>,
              },
              { key: "contract_type", header: "Type", render: (c) => <StatusBadge value={c.contract_type} /> },
              { key: "vendor", header: "Vendor", render: (c) => (c.vendor_id ? vendorById.get(c.vendor_id)?.name ?? c.vendor_id : "-") },
              {
                key: "assets",
                header: "Assets covered",
                render: (c) =>
                  c.asset_ids.length === 0
                    ? "-"
                    : c.asset_ids.map((id) => labelForAssetId(id)).join(", "),
              },
              { key: "start_date", header: "Start", render: (c) => c.start_date ?? "-" },
              { key: "end_date", header: "End", render: (c) => c.end_date ?? "-" },
              { key: "expiry_status", header: "Expiry", render: (c) => <StatusBadge value={c.expiry_status} /> },
              { key: "status", header: "Status", render: (c) => <StatusBadge value={c.status} /> },
              ...(canWrite
                ? [
                    {
                      key: "actions",
                      header: "Actions",
                      render: (c: Contract) => (
                        <div style={{ display: "flex", gap: 6 }}>
                          <Link className="btn btn--ghost" href={`/contracts/${c.id}`}>
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
            ]}
            rows={contracts}
            emptyLabel="No contracts found."
          />
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete contract"
        message={
          canForceDelete
            ? "This permanently deletes the contract. If it's still linked to assets, you'll be offered a force-delete that cascades through those links. Continue?"
            : "This permanently deletes the contract. This only succeeds if it isn't linked to any asset — otherwise deactivate it instead. Continue?"
        }
        confirmLabel="Delete"
        onConfirm={() => confirmDeleteId && handleDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}

function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat-tile">
      <div className="stat-tile__label">{label}</div>
      <div className="stat-tile__value">{value}</div>
    </div>
  );
}
