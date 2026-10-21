"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { SearchBar } from "../../components/SearchBar";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useAuth } from "../../hooks/useAuth";
import { useLookup } from "../../hooks/useLookup";
import { api, ApiError } from "../../lib/api";
import type { Branch, Consumable, ConsumableTransaction, Page } from "../../types";
import { CONSUMABLE_CATEGORIES, CONSUMABLE_STOCK_STATUSES } from "../../types";

const PAGE_SIZE = 25;

/** Fetches and renders the single most recent stock movement for one consumable, via
 * the new `GET /consumables/{id}/last-transaction` endpoint. Lazily loaded per visible
 * row (not bulk-fetched) — the endpoint is per-item and this page's page size is small
 * (25), matching how other per-row detail cells are loaded elsewhere in this app. */
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

export default function ConsumablesPage() {
  const { hasPermission } = useAuth();
  const branches = useLookup<Branch>("/api/v1/branches");
  const branchName = (id: string | null) => (id ? branches.find((b) => b.id === id)?.name ?? id : "Central / hub stock");

  const [search, setSearch] = useState("");
  const [branchId, setBranchId] = useState("");
  const [stockStatus, setStockStatus] = useState("");
  const [page, setPage] = useState(1);

  // Hydrates the branch filter from the URL so the branch hub's "Consumables" tile
  // (/consumables?branch_id=...) lands pre-filtered, same convention as
  // /inventory's stock_status hydration.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const bid = params.get("branch_id");
    if (bid) setBranchId(bid);
  }, []);

  const [data, setData] = useState<Page<Consumable> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const canWrite = hasPermission("inventory.write");
  const canForceDelete = hasPermission("dev.data_cleanup");
  const [rowError, setRowError] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ id: string; message: string } | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (branchId) params.set("branch_id", branchId);
      const result = await api.get<Page<Consumable>>(`/api/v1/consumables?${params.toString()}`);
      setData(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load consumables.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [branchId, page]);

  useEffect(() => {
    setPage(1);
  }, [branchId]);

  const visibleRows = (data?.items ?? []).filter((c) => {
    if (stockStatus && c.stock_status !== stockStatus) return false;
    if (search) {
      const needle = search.toLowerCase();
      if (!c.item_code.toLowerCase().includes(needle) && !c.name.toLowerCase().includes(needle)) return false;
    }
    return true;
  });

  if (!hasPermission("inventory.read")) {
    return (
      <ProtectedShell>
        <ErrorState message="You do not have permission to view inventory/consumables." />
      </ProtectedShell>
    );
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/consumables", {
        item_code: formData.get("item_code"),
        name: formData.get("name"),
        branch_id: formData.get("branch_id") || null,
        category: formData.get("category") || null,
        unit: formData.get("unit") || null,
        opening_quantity: formData.get("opening_quantity") ? Number(formData.get("opening_quantity")) : null,
        min_level: formData.get("min_level") ? Number(formData.get("min_level")) : null,
        reorder_level: formData.get("reorder_level") ? Number(formData.get("reorder_level")) : null,
      });
      setShowForm(false);
      form.reset();
      await load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create consumable.");
    }
  }

  async function handleDelete(consumableId: string, force = false) {
    setRowError(null);
    setConflict(null);
    setRowBusy(consumableId);
    try {
      await api.delete(`/api/v1/consumables/${consumableId}${force ? "?force=true" : ""}`);
      setConfirmDeleteId(null);
      await load();
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

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Consumables</h1>
          <p>
            Consumable stock — a NULL branch is the central/hub pool a requirement is normally fulfilled from; a
            branch row is that branch&apos;s own on-hand quantity.
          </p>
        </div>
        {hasPermission("inventory.write") && (
          <button className="btn btn--primary" onClick={() => setShowForm((v) => !v)}>
            {showForm ? "Cancel" : "New consumable"}
          </button>
        )}
      </div>

      {showForm && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleCreate}>
          <div className="form-grid">
            <div className="form-field">
              <label>Item code*</label>
              <input name="item_code" required />
            </div>
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required />
            </div>
            <div className="form-field">
              <label>Branch</label>
              <select name="branch_id" defaultValue="">
                <option value="">Central / hub stock</option>
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Category</label>
              <input name="category" list="consumable-category-options" placeholder="e.g. ICU Consumables" />
              <datalist id="consumable-category-options">
                {CONSUMABLE_CATEGORIES.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </div>
            <div className="form-field">
              <label>Unit</label>
              <input name="unit" placeholder="e.g. pcs" />
            </div>
            <div className="form-field">
              <label>Opening quantity</label>
              <input name="opening_quantity" type="number" min={0} />
            </div>
            <div className="form-field">
              <label>Min level</label>
              <input name="min_level" type="number" min={0} />
            </div>
            <div className="form-field">
              <label>Reorder level</label>
              <input name="reorder_level" type="number" min={0} />
            </div>
          </div>
          {formError && <div className="field-error">{formError}</div>}
          <button className="btn btn--primary" type="submit">
            Save consumable
          </button>
        </form>
      )}

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search by item code or name..." />
        <select value={branchId} onChange={(e) => setBranchId(e.target.value)}>
          <option value="">All locations</option>
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
        <select value={stockStatus} onChange={(e) => setStockStatus(e.target.value)}>
          <option value="">All stock statuses</option>
          {CONSUMABLE_STOCK_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
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
      {data && (
        <>
          <DataTable<Consumable>
            columns={[
              {
                key: "item_code",
                header: "Item code",
                render: (c) => <Link href={`/consumables/${c.id}`}>{c.item_code}</Link>,
              },
              { key: "name", header: "Name", render: (c) => c.name },
              { key: "branch_id", header: "Location", render: (c) => branchName(c.branch_id) },
              { key: "category", header: "Category", render: (c) => c.category ?? "-" },
              { key: "opening_quantity", header: "Quantity", render: (c) => c.opening_quantity ?? 0 },
              { key: "unit", header: "Unit", render: (c) => c.unit ?? "-" },
              { key: "stock_status", header: "Stock status", render: (c) => <StatusBadge value={c.stock_status} /> },
              { key: "status", header: "Status", render: (c) => <StatusBadge value={c.status} /> },
              {
                key: "last_transaction",
                header: "Last transaction",
                render: (c) => <LastTransactionCell consumableId={c.id} />,
              },
              ...(canWrite
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
            ]}
            rows={visibleRows}
            emptyLabel="No consumables found."
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete consumable"
        message={
          canForceDelete
            ? "This permanently deletes the consumable. If it's still referenced by transaction history or requirements, you'll be offered a force-delete that cascades through those. Continue?"
            : "This permanently deletes the consumable. This only succeeds if it has no transaction history and no requirement references it — otherwise deactivate it instead. Continue?"
        }
        confirmLabel="Delete"
        onConfirm={() => confirmDeleteId && handleDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
