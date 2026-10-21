"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { Pagination } from "../../../components/Pagination";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { api, ApiError } from "../../../lib/api";
import type { Branch, Consumable, ConsumableTransaction, Page, Vendor } from "../../../types";
import { CONSUMABLE_CATEGORIES } from "../../../types";

const TXN_PAGE_SIZE = 10;

export default function ConsumableDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("inventory.write");

  const branches = useLookup<Branch>("/api/v1/branches");
  const branchName = (id: string | null) => (id ? branches.find((b) => b.id === id)?.name ?? id : "Central / hub stock");
  // Vendor contact fields arrive already masked server-side for callers without full
  // vendor.read (see backend/app/services/vendor_service.py) — this page never
  // re-implements that check, same convention as assets/inventory pages.
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const vendorById = (id: string | null) => (id ? vendors.find((v) => v.id === id) ?? null : null);
  // Transaction source/destination columns: null genuinely means "not applicable"
  // (e.g. an OUT transaction has no destination), not "central" — the central pool
  // is only implied for the consumable's own branch_id, shown separately above.
  const txnLocationLabel = (id: string | null) => (id ? branches.find((b) => b.id === id)?.name ?? id : "-");

  const [consumable, setConsumable] = useState<Consumable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const [transactions, setTransactions] = useState<ConsumableTransaction[]>([]);
  const [txnTotal, setTxnTotal] = useState(0);
  const [txnPage, setTxnPage] = useState(1);
  const [txnError, setTxnError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await api.get<Consumable>(`/api/v1/consumables/${params.id}`);
      setConsumable(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load consumable.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  const loadTransactions = useCallback(async () => {
    setTxnError(null);
    try {
      const query = new URLSearchParams({ page: String(txnPage), page_size: String(TXN_PAGE_SIZE) });
      const result = await api.get<Page<ConsumableTransaction>>(
        `/api/v1/consumables/${params.id}/transactions?${query.toString()}`
      );
      setTransactions(result.items);
      setTxnTotal(result.total);
    } catch (err) {
      setTxnError(err instanceof ApiError ? err.message : "Failed to load stock transaction history.");
    }
  }, [params.id, txnPage]);

  useEffect(() => {
    loadTransactions();
  }, [loadTransactions]);

  async function handleUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.patch(`/api/v1/consumables/${params.id}`, {
        name: formData.get("name"),
        category: formData.get("category") || null,
        manufacturer: formData.get("manufacturer") || null,
        unit: formData.get("unit") || null,
        min_level: formData.get("min_level") ? Number(formData.get("min_level")) : null,
        reorder_level: formData.get("reorder_level") ? Number(formData.get("reorder_level")) : null,
        vendor_id: formData.get("vendor_id") || null,
        status: formData.get("status"),
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update consumable.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!consumable) return;
    setConfirmDelete(false);
    setActionError(null);
    setBusy(true);
    try {
      await api.delete(`/api/v1/consumables/${consumable.id}`);
      router.push("/consumables");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to delete consumable.");
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
  if (!consumable) {
    return (
      <ProtectedShell>
        <LoadingState />
      </ProtectedShell>
    );
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>
            {consumable.item_code} — {consumable.name}
          </h1>
          <p>
            <StatusBadge value={consumable.stock_status} /> · {branchName(consumable.branch_id)}
            {consumable.category ? ` · ${consumable.category}` : ""}
          </p>
        </div>
        {canWrite && (
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--danger" onClick={() => setConfirmDelete(true)} disabled={busy}>
              Delete consumable
            </button>
          </div>
        )}
      </div>

      {actionError && <ErrorState message={actionError} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Stock information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Item code</td>
              <td>{consumable.item_code}</td>
            </tr>
            <tr>
              <td>Name</td>
              <td>{consumable.name}</td>
            </tr>
            <tr>
              <td>Location</td>
              <td>{branchName(consumable.branch_id)}</td>
            </tr>
            <tr>
              <td>Category</td>
              <td>{consumable.category ?? "-"}</td>
            </tr>
            <tr>
              <td>Manufacturer</td>
              <td>{consumable.manufacturer ?? "-"}</td>
            </tr>
            <tr>
              <td>Unit</td>
              <td>{consumable.unit ?? "-"}</td>
            </tr>
            <tr>
              <td>Quantity on hand</td>
              <td>{consumable.opening_quantity ?? 0}</td>
            </tr>
            <tr>
              <td>Stock status</td>
              <td>
                <StatusBadge value={consumable.stock_status} />
              </td>
            </tr>
            <tr>
              <td>Min level</td>
              <td>{consumable.min_level ?? "-"}</td>
            </tr>
            <tr>
              <td>Reorder level</td>
              <td>{consumable.reorder_level ?? "-"}</td>
            </tr>
            <tr>
              <td>Vendor</td>
              <td>
                {vendorById(consumable.vendor_id)?.name ?? consumable.supplier ?? "Not set"}
                {vendorById(consumable.vendor_id) &&
                  (vendorById(consumable.vendor_id)?.email || vendorById(consumable.vendor_id)?.phone) && (
                    <span className="field-hint" style={{ display: "block" }}>
                      {[vendorById(consumable.vendor_id)?.email, vendorById(consumable.vendor_id)?.phone]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  )}
              </td>
            </tr>
            <tr>
              <td>Status</td>
              <td>
                <StatusBadge value={consumable.status} />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Edit consumable</h3>
          <p className="field-hint">
            Quantity on hand changes only via a fulfillment&apos;s stock transfer (see history below) — it cannot be
            edited directly here.
          </p>
          <form onSubmit={handleUpdate} className="form-grid">
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required defaultValue={consumable.name} />
            </div>
            <div className="form-field">
              <label>Category</label>
              <input name="category" list="consumable-category-options" defaultValue={consumable.category ?? ""} />
              <datalist id="consumable-category-options">
                {CONSUMABLE_CATEGORIES.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </div>
            <div className="form-field">
              <label>Manufacturer</label>
              <input name="manufacturer" defaultValue={consumable.manufacturer ?? ""} />
            </div>
            <div className="form-field">
              <label>Unit</label>
              <input name="unit" defaultValue={consumable.unit ?? ""} />
            </div>
            <div className="form-field">
              <label>Min level</label>
              <input name="min_level" type="number" min={0} defaultValue={consumable.min_level ?? ""} />
            </div>
            <div className="form-field">
              <label>Reorder level</label>
              <input name="reorder_level" type="number" min={0} defaultValue={consumable.reorder_level ?? ""} />
            </div>
            <div className="form-field">
              <label>Vendor</label>
              {hasPermission("vendor.write") ? (
                <select name="vendor_id" defaultValue={consumable.vendor_id ?? ""}>
                  <option value="">Not set</option>
                  {vendors.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name}
                    </option>
                  ))}
                </select>
              ) : (
                <span className="field-hint">
                  You don&apos;t have permission to link a vendor here — an admin can add it from the vendor page.
                </span>
              )}
            </div>
            <div className="form-field">
              <label>Status</label>
              <select name="status" defaultValue={consumable.status}>
                <option value="ACTIVE">ACTIVE</option>
                <option value="INACTIVE">INACTIVE</option>
              </select>
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Save changes
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Stock transaction history</h3>
        <p className="field-hint">
          Every transfer/issue affecting this location&apos;s stock, recorded automatically when a consumable
          requirement&apos;s fulfillment is completed. Transactions are historical records and cannot be deleted.
        </p>
        {txnError && <ErrorState message={txnError} />}
        <table className="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Type</th>
              <th>Quantity</th>
              <th>Source</th>
              <th>Destination</th>
              <th>Reference</th>
              <th>Performed by</th>
            </tr>
          </thead>
          <tbody>
            {transactions.length === 0 && (
              <tr>
                <td colSpan={7}>No stock transactions recorded yet.</td>
              </tr>
            )}
            {transactions.map((t) => (
              <tr key={t.id}>
                <td>{t.created_at}</td>
                <td>
                  <StatusBadge value={t.transaction_type} />
                </td>
                <td>{t.quantity}</td>
                <td>{branchName(t.source_branch_id)}</td>
                <td>{txnLocationLabel(t.destination_branch_id)}</td>
                <td>{t.reference ?? "-"}</td>
                <td>{t.performed_by ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Pagination page={txnPage} pageSize={TXN_PAGE_SIZE} total={txnTotal} onPageChange={setTxnPage} />
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete consumable"
        message="This permanently deletes the consumable. Only a consumable with no stock transaction history and no requirement requests referencing it can be deleted — for anything else, set Status to INACTIVE instead. Continue?"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </ProtectedShell>
  );
}
