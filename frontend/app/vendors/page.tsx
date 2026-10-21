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
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type { Page, Vendor } from "../../types";
import { VENDOR_STATUSES, VENDOR_TYPES } from "../../types";

const PAGE_SIZE = 25;

export default function VendorsPage() {
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("vendor.write");
  const canForceDelete = hasPermission("dev.data_cleanup");

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [vendorType, setVendorType] = useState("");
  const [page, setPage] = useState(1);

  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [rowError, setRowError] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ id: string; message: string } | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (search) params.set("search", search);
      if (statusFilter) params.set("status_filter", statusFilter);
      if (vendorType) params.set("vendor_type", vendorType);
      const result = await api.get<Page<Vendor>>(`/api/v1/vendors?${params.toString()}`);
      setVendors(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load vendors.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, statusFilter, vendorType, page]);

  useEffect(() => {
    setPage(1);
  }, [search, statusFilter, vendorType]);

  async function handleDelete(vendorId: string, force = false) {
    setRowError(null);
    setConflict(null);
    setRowBusy(vendorId);
    try {
      await api.delete(`/api/v1/vendors/${vendorId}${force ? "?force=true" : ""}`);
      setConfirmDeleteId(null);
      await load();
    } catch (err) {
      setConfirmDeleteId(null);
      if (err instanceof ApiError && err.status === 409) {
        setConflict({ id: vendorId, message: err.message });
      } else {
        setRowError(err instanceof ApiError ? err.message : "Failed to delete vendor.");
      }
    } finally {
      setRowBusy(null);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Vendors</h1>
          <p>OEM, distributor, and service vendors, with contact and rating information.</p>
        </div>
        {hasPermission("vendor.write") && (
          <Link href="/vendors/new" className="btn btn--primary">
            New vendor
          </Link>
        )}
      </div>

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search by name, city, state, email, or phone..." />
        <select value={vendorType} onChange={(e) => setVendorType(e.target.value)}>
          <option value="">All types</option>
          {VENDOR_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          {VENDOR_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <ExportButtons
        path="/api/v1/vendors/export"
        filters={{ search, status_filter: statusFilter, vendor_type: vendorType }}
        recordCount={total}
        filenameStem="vendors_export"
      />

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
          <DataTable<Vendor>
            columns={[
              {
                key: "name",
                header: "Name",
                render: (v) => <Link href={`/vendors/${v.id}`}>{v.name}</Link>,
              },
              { key: "vendor_type", header: "Type", render: (v) => v.vendor_type ?? "-" },
              { key: "location", header: "Location", render: (v) => [v.city, v.state].filter(Boolean).join(", ") || "-" },
              { key: "contact", header: "Contact", render: (v) => v.email ?? v.phone ?? "-" },
              { key: "rating", header: "Rating", render: (v) => (v.rating ? `${v.rating} / 5` : "-") },
              { key: "status", header: "Status", render: (v) => <StatusBadge value={v.status} /> },
              ...(canWrite
                ? [
                    {
                      key: "actions",
                      header: "Actions",
                      render: (v: Vendor) => (
                        <div style={{ display: "flex", gap: 6 }}>
                          <Link className="btn btn--ghost" href={`/vendors/${v.id}`}>
                            Edit
                          </Link>
                          <button
                            className="btn btn--ghost"
                            disabled={rowBusy === v.id}
                            onClick={() => setConfirmDeleteId(v.id)}
                          >
                            Delete
                          </button>
                        </div>
                      ),
                    },
                  ]
                : []),
            ]}
            rows={vendors}
            emptyLabel="No vendors found."
          />
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete vendor"
        message={
          canForceDelete
            ? "This permanently deletes the vendor. If it's still referenced by contacts, contracts, service visits, or procurement records, you'll be offered a force-delete that cascades through those. Continue?"
            : "This permanently deletes the vendor. Only vendors with no linked contacts, device models, contracts, procurement records, or service visits can be deleted — for anything else, use Deactivate on the vendor's own page instead. Continue?"
        }
        confirmLabel="Delete"
        onConfirm={() => confirmDeleteId && handleDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
