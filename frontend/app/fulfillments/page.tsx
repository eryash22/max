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
import { useUserDirectory } from "../../hooks/useUserDirectory";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type { Branch, Fulfillment, HospitalNetwork, Requirement } from "../../types";
import { FULFILLMENT_STATUSES, FULFILLMENT_STATUS_LABELS, REQUIREMENT_PRIORITIES } from "../../types";

export default function FulfillmentsPage() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [branchId, setBranchId] = useState("");
  const [priority, setPriority] = useState("");
  const [inventoryManagerId, setInventoryManagerId] = useState("");

  // Hydrates filters from the URL so dashboard drill-down links (e.g.
  // /fulfillments?status_filter=OUT_FOR_DELIVERY&branch_id=X) land pre-filtered.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sf = params.get("status_filter");
    const bi = params.get("branch_id");
    if (sf) setStatusFilter(sf);
    if (bi) setBranchId(bi);
  }, []);

  const query: Record<string, string> = {};
  if (search) query.search = search;
  if (statusFilter) query.status_filter = statusFilter;
  if (branchId) query.branch_id = branchId;
  if (priority) query.priority = priority;
  if (inventoryManagerId) query.inventory_manager_id = inventoryManagerId;

  const { data, page, setPage, loading, error, reload } = usePaginatedResource<Fulfillment>("/api/v1/fulfillments", query);
  const requirements = useLookup<Requirement>("/api/v1/requirements");
  const branches = useLookup<Branch>("/api/v1/branches");
  const hospitals = useLookup<HospitalNetwork>("/api/v1/hospital-networks");
  const users = useUserDirectory();
  const { user, hasPermission } = useAuth();
  const canForceDelete = hasPermission("dev.data_cleanup");
  const [rowError, setRowError] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  async function handleForceDelete(fulfillmentId: string) {
    setRowError(null);
    setRowBusy(fulfillmentId);
    try {
      await api.delete(`/api/v1/fulfillments/${fulfillmentId}?force=true`);
      setConfirmDeleteId(null);
      await reload();
    } catch (err) {
      setConfirmDeleteId(null);
      setRowError(err instanceof ApiError ? err.message : "Failed to delete fulfillment.");
    } finally {
      setRowBusy(null);
    }
  }

  const requirementFor = (id: string) => requirements.find((r) => r.id === id);
  const branchName = (id: string | undefined) => (id ? branches.find((b) => b.id === id)?.name ?? id : "-");
  const hospitalName = (branchId: string | undefined) => {
    const branch = branchId ? branches.find((b) => b.id === branchId) : undefined;
    const hospital = branch ? hospitals.find((h) => h.id === branch.network_id) : undefined;
    return hospital?.name ?? "-";
  };
  const userName = (id: string | null) => {
    if (!id) return "-";
    if (user && id === user.id) return `${user.name} (You)`;
    return users.find((u) => u.id === id)?.name ?? id;
  };

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Fulfillment &amp; Delivery</h1>
          <p>Inventory availability, allocation, packing and delivery for approved requirements.</p>
        </div>
      </div>

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search fulfillment or request number..." />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          {FULFILLMENT_STATUSES.map((s) => (
            <option key={s} value={s}>
              {FULFILLMENT_STATUS_LABELS[s] ?? s}
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
        <select value={priority} onChange={(e) => setPriority(e.target.value)}>
          <option value="">All priorities</option>
          {REQUIREMENT_PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        {users.length > 0 && (
          <select value={inventoryManagerId} onChange={(e) => setInventoryManagerId(e.target.value)}>
            <option value="">All inventory managers</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {rowError && <ErrorState message={rowError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<Fulfillment>
            columns={[
              {
                key: "fulfillment_number",
                header: "Fulfillment Number",
                render: (f) => <Link href={`/fulfillments/${f.id}`}>{f.fulfillment_number}</Link>,
              },
              {
                key: "request_number",
                header: "Requirement Number",
                render: (f) => requirementFor(f.requirement_id)?.request_number ?? "-",
              },
              {
                key: "hospital",
                header: "Hospital",
                render: (f) => hospitalName(requirementFor(f.requirement_id)?.branch_id),
              },
              {
                key: "branch",
                header: "Branch",
                render: (f) => branchName(requirementFor(f.requirement_id)?.branch_id),
              },
              {
                key: "requester",
                header: "Requester",
                render: (f) => userName(requirementFor(f.requirement_id)?.requester_id ?? null),
              },
              {
                key: "priority",
                header: "Priority",
                render: (f) => {
                  const p = requirementFor(f.requirement_id)?.priority;
                  return p ? <StatusBadge value={p} /> : "-";
                },
              },
              {
                key: "items",
                header: "Items",
                render: (f) => f.items.length,
              },
              { key: "status", header: "Status", render: (f) => <StatusBadge value={f.status} /> },
              {
                key: "expected_delivery_date",
                header: "Expected Delivery",
                render: (f) => f.expected_delivery_date ?? "-",
              },
              {
                key: "inventory_manager",
                header: "Inventory Manager",
                render: (f) => userName(f.inventory_manager_id),
              },
              {
                key: "created_at",
                header: "Created Date",
                render: (f) => new Date(f.created_at).toLocaleDateString(),
              },
              {
                key: "actions",
                header: "Actions",
                render: (f) => (
                  <div style={{ display: "flex", gap: 6 }}>
                    <Link className="btn btn--ghost" href={`/fulfillments/${f.id}`}>
                      View
                    </Link>
                    {canForceDelete && (
                      <button
                        className="btn btn--ghost"
                        disabled={rowBusy === f.id}
                        onClick={() => setConfirmDeleteId(f.id)}
                      >
                        Delete
                      </button>
                    )}
                  </div>
                ),
              },
            ]}
            rows={data.items}
            emptyLabel="No fulfillments found."
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Force-delete fulfillment"
        message="This permanently deletes the fulfillment and its items/asset links/status history. Fulfillments have no soft-delete — cancel is the normal archive path, this is the Super Admin override. This cannot be undone. Continue?"
        confirmLabel="Force delete"
        onConfirm={() => confirmDeleteId && handleForceDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
