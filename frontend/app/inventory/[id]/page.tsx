"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useParams } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { Pagination } from "../../../components/Pagination";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { api, ApiError } from "../../../lib/api";
import type {
  DeviceModel,
  InventoryItem,
  InventoryItemStock,
  InventoryLocation,
  InventoryTransaction,
  Page,
  SparePartCompatibility,
} from "../../../types";
import { INVENTORY_TRANSACTION_TYPES } from "../../../types";

const TXN_PAGE_SIZE = 10;
// Transaction types whose backend effect reduces or corrects stock downward and
// therefore warrant an "are you sure" step before hitting the API.
const CONFIRM_TRANSACTION_TYPES = ["STOCK_OUT", "ADJUSTMENT"];

export default function InventoryItemDetailPage() {
  const params = useParams<{ id: string }>();
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("inventory.write");

  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const locations = useLookup<InventoryLocation>("/api/v1/inventory/locations");
  const modelName = (id: string) => models.find((m) => m.id === id)?.name ?? id;
  const locationName = (id: string) => {
    const loc = locations.find((l) => l.id === id);
    return loc ? [loc.code, loc.name].filter(Boolean).join(" — ") : id;
  };

  const [item, setItem] = useState<InventoryItem | null>(null);
  const [stock, setStock] = useState<InventoryItemStock | null>(null);
  const [compatibility, setCompatibility] = useState<SparePartCompatibility[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [transactions, setTransactions] = useState<InventoryTransaction[]>([]);
  const [txnTotal, setTxnTotal] = useState(0);
  const [txnPage, setTxnPage] = useState(1);
  const [txnFilterType, setTxnFilterType] = useState("");
  const [txnError, setTxnError] = useState<string | null>(null);

  const [pendingTxn, setPendingTxn] = useState<Record<string, string> | null>(null);

  const latestBatch = useMemo(() => transactions.find((t) => t.batch_number) ?? null, [transactions]);

  const load = useCallback(async () => {
    try {
      const [itemResult, stockResult, compatResult] = await Promise.all([
        api.get<InventoryItem>(`/api/v1/inventory/items/${params.id}`),
        api.get<InventoryItemStock>(`/api/v1/inventory/items/${params.id}/stock`),
        api.get<SparePartCompatibility[]>(`/api/v1/inventory/items/${params.id}/compatibility`),
      ]);
      setItem(itemResult);
      setStock(stockResult);
      setCompatibility(compatResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load inventory item.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  const loadTransactions = useCallback(async () => {
    setTxnError(null);
    try {
      const query = new URLSearchParams({
        item_id: String(params.id),
        page: String(txnPage),
        page_size: String(TXN_PAGE_SIZE),
      });
      if (txnFilterType) query.set("transaction_type", txnFilterType);
      const result = await api.get<Page<InventoryTransaction>>(`/api/v1/inventory/transactions?${query.toString()}`);
      setTransactions(result.items);
      setTxnTotal(result.total);
    } catch (err) {
      setTxnError(err instanceof ApiError ? err.message : "Failed to load transaction history.");
    }
  }, [params.id, txnPage, txnFilterType]);

  useEffect(() => {
    loadTransactions();
  }, [loadTransactions]);

  useEffect(() => {
    setTxnPage(1);
  }, [txnFilterType]);

  async function handleUpdateItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.patch(`/api/v1/inventory/items/${params.id}`, {
        name: formData.get("name"),
        category: formData.get("category") || null,
        unit: formData.get("unit") || null,
        min_level: formData.get("min_level") ? Number(formData.get("min_level")) : null,
        max_level: formData.get("max_level") ? Number(formData.get("max_level")) : null,
        reorder_level: formData.get("reorder_level") ? Number(formData.get("reorder_level")) : null,
        unit_cost: formData.get("unit_cost") ? Number(formData.get("unit_cost")) : null,
        supplier: formData.get("supplier") || null,
        is_consumable: formData.get("is_consumable") === "on",
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update item.");
    } finally {
      setBusy(false);
    }
  }

  async function submitTransaction(payload: Record<string, unknown>, formToReset?: HTMLFormElement) {
    setActionError(null);
    setBusy(true);
    try {
      await api.post("/api/v1/inventory/transactions", payload);
      formToReset?.reset();
      await Promise.all([load(), loadTransactions()]);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to record transaction.");
    } finally {
      setBusy(false);
    }
  }

  async function handleTransactionSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!item) return;
    const form = event.currentTarget;
    const formData = new FormData(form);
    const transactionType = String(formData.get("transaction_type") || "STOCK_IN");
    const quantity = Number(formData.get("quantity"));
    const payload: Record<string, unknown> = {
      item_id: item.id,
      transaction_type: transactionType,
      quantity,
      location_id: formData.get("location_id") || null,
      batch_number: formData.get("batch_number") || null,
      expiry_date: formData.get("expiry_date") || null,
      reference: formData.get("reference") || null,
    };

    if (CONFIRM_TRANSACTION_TYPES.includes(transactionType)) {
      setPendingTxn({
        transactionType,
        quantity: String(quantity),
        payload: JSON.stringify(payload),
      });
      return;
    }
    await submitTransaction(payload, form);
  }

  async function handleConfirmTransaction() {
    if (!pendingTxn) return;
    const payload = JSON.parse(pendingTxn.payload);
    setPendingTxn(null);
    await submitTransaction(payload);
  }

  async function handleAddCompatibility(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post(`/api/v1/inventory/items/${params.id}/compatibility`, {
        device_model_id: formData.get("device_model_id"),
        notes: formData.get("notes") || null,
      });
      form.reset();
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to add compatibility link.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemoveCompatibility(compatibilityId: string) {
    setActionError(null);
    setBusy(true);
    try {
      await api.delete(`/api/v1/inventory/items/${params.id}/compatibility/${compatibilityId}`);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to remove compatibility link.");
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
  if (!item || !stock) {
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
            {item.item_code} — {item.name}
          </h1>
          <p>
            <StatusBadge value={stock.stock_status} />
            {item.category ? ` · ${item.category}` : ""}
            {item.is_consumable ? " · Consumable" : ""}
          </p>
        </div>
      </div>

      {actionError && <ErrorState message={actionError} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Item &amp; stock information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Item code</td>
              <td>{item.item_code}</td>
            </tr>
            <tr>
              <td>Name</td>
              <td>{item.name}</td>
            </tr>
            <tr>
              <td>Category</td>
              <td>{item.category ?? "-"}</td>
            </tr>
            <tr>
              <td>Unit</td>
              <td>{item.unit ?? "-"}</td>
            </tr>
            <tr>
              <td>Current stock</td>
              <td>{stock.current_stock}</td>
            </tr>
            <tr>
              <td>Stock status</td>
              <td>
                <StatusBadge value={stock.stock_status} />
              </td>
            </tr>
            <tr>
              <td>Min level</td>
              <td>{stock.min_level ?? "-"}</td>
            </tr>
            <tr>
              <td>Max level</td>
              <td>{stock.max_level ?? "-"}</td>
            </tr>
            <tr>
              <td>Reorder level</td>
              <td>{stock.reorder_level ?? "-"}</td>
            </tr>
            <tr>
              <td>Unit cost</td>
              <td>{item.unit_cost != null ? item.unit_cost : "-"}</td>
            </tr>
            <tr>
              <td>Supplier</td>
              <td>{item.supplier ?? "-"}</td>
            </tr>
            <tr>
              <td>Consumable</td>
              <td>{item.is_consumable ? "Yes" : "No"}</td>
            </tr>
            <tr>
              <td>Most recent batch on record</td>
              <td>
                {latestBatch
                  ? `${latestBatch.batch_number} · expiry ${latestBatch.expiry_date ?? "-"} · `
                  : "No batch-tracked transactions yet"}
                {latestBatch && <StatusBadge value={latestBatch.expiry_status} />}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Edit item</h3>
          <form onSubmit={handleUpdateItem} className="form-grid">
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required defaultValue={item.name} />
            </div>
            <div className="form-field">
              <label>Category</label>
              <input name="category" defaultValue={item.category ?? ""} />
            </div>
            <div className="form-field">
              <label>Unit</label>
              <input name="unit" defaultValue={item.unit ?? ""} />
            </div>
            <div className="form-field">
              <label>Min level</label>
              <input name="min_level" type="number" min={0} defaultValue={item.min_level ?? ""} />
            </div>
            <div className="form-field">
              <label>Max level</label>
              <input name="max_level" type="number" min={0} defaultValue={item.max_level ?? ""} />
            </div>
            <div className="form-field">
              <label>Reorder level</label>
              <input name="reorder_level" type="number" min={0} defaultValue={item.reorder_level ?? ""} />
            </div>
            <div className="form-field">
              <label>Unit cost</label>
              <input name="unit_cost" type="number" min={0} step="0.01" defaultValue={item.unit_cost ?? ""} />
            </div>
            <div className="form-field">
              <label>Supplier</label>
              <input name="supplier" defaultValue={item.supplier ?? ""} />
            </div>
            <div className="form-field form-field--checkbox">
              <label>
                <input name="is_consumable" type="checkbox" defaultChecked={item.is_consumable} /> Consumable
              </label>
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Save changes
              </button>
            </div>
          </form>
        </div>
      )}

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Record stock transaction</h3>
          <p className="field-hint">
            TRANSFER is recorded for history but does not change organization-level net stock (the backend stores a
            single location per transaction, not a from/to pair).
          </p>
          <form onSubmit={handleTransactionSubmit} className="form-grid">
            <div className="form-field">
              <label>Transaction type*</label>
              <select name="transaction_type" defaultValue="STOCK_IN">
                {INVENTORY_TRANSACTION_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Quantity*</label>
              <input name="quantity" type="number" required placeholder="Positive; ADJUSTMENT may be negative" />
            </div>
            <div className="form-field">
              <label>Location</label>
              <select name="location_id" defaultValue="">
                <option value="">Not set</option>
                {locations.map((l) => (
                  <option key={l.id} value={l.id}>
                    {[l.code, l.name].filter(Boolean).join(" — ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Batch number</label>
              <input name="batch_number" placeholder="Optional" />
            </div>
            <div className="form-field">
              <label>Expiry date</label>
              <input name="expiry_date" type="date" />
            </div>
            <div className="form-field">
              <label>Reference</label>
              <input name="reference" placeholder="e.g. PO number (optional)" />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Record transaction
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Compatible device models</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Device model</th>
              <th>Notes</th>
              {canWrite && <th />}
            </tr>
          </thead>
          <tbody>
            {compatibility.length === 0 && (
              <tr>
                <td colSpan={canWrite ? 3 : 2}>No compatible device models linked yet.</td>
              </tr>
            )}
            {compatibility.map((c) => (
              <tr key={c.id}>
                <td>{modelName(c.device_model_id)}</td>
                <td>{c.notes ?? "-"}</td>
                {canWrite && (
                  <td>
                    <button className="btn btn--ghost" onClick={() => handleRemoveCompatibility(c.id)} disabled={busy}>
                      Remove
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>

        {canWrite && (
          <form onSubmit={handleAddCompatibility} className="form-grid" style={{ marginTop: 16 }}>
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
              <label>Notes</label>
              <input name="notes" placeholder="Optional" />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Link device model
              </button>
            </div>
          </form>
        )}
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Transaction history</h3>
        <p className="field-hint">Transactions are historical records and cannot be deleted.</p>
        <div className="toolbar" style={{ marginBottom: 8 }}>
          <select value={txnFilterType} onChange={(e) => setTxnFilterType(e.target.value)}>
            <option value="">All transaction types</option>
            {INVENTORY_TRANSACTION_TYPES.map((t) => (
              <option key={t} value={t}>
                {t.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
        {txnError && <ErrorState message={txnError} />}
        <table className="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Type</th>
              <th>Quantity</th>
              <th>Location</th>
              <th>Batch</th>
              <th>Expiry</th>
              <th>Reference</th>
              <th>Performed by</th>
            </tr>
          </thead>
          <tbody>
            {transactions.length === 0 && (
              <tr>
                <td colSpan={8}>No transactions recorded yet.</td>
              </tr>
            )}
            {transactions.map((t) => (
              <tr key={t.id}>
                <td>{t.created_at}</td>
                <td>
                  <StatusBadge value={t.transaction_type} />
                </td>
                <td>{t.quantity}</td>
                <td>{t.location_id ? locationName(t.location_id) : "-"}</td>
                <td>{t.batch_number ?? "-"}</td>
                <td>
                  {t.expiry_date ?? "-"} {t.expiry_status !== "NOT_SET" && <StatusBadge value={t.expiry_status} />}
                </td>
                <td>{t.reference ?? "-"}</td>
                <td>{t.performed_by ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Pagination page={txnPage} pageSize={TXN_PAGE_SIZE} total={txnTotal} onPageChange={setTxnPage} />
      </div>

      <ConfirmDialog
        open={pendingTxn !== null}
        title={pendingTxn?.transactionType === "ADJUSTMENT" ? "Confirm stock adjustment" : "Confirm stock out"}
        message={
          pendingTxn
            ? `Record a ${pendingTxn.transactionType.replace(/_/g, " ")} of ${pendingTxn.quantity} units? ${
                pendingTxn.transactionType === "STOCK_OUT"
                  ? "This reduces current stock and will be rejected by the backend if it would go below zero."
                  : "A negative adjustment reduces current stock; the backend rejects adjustments that would go below zero."
              }`
            : ""
        }
        confirmLabel="Record transaction"
        onConfirm={handleConfirmTransaction}
        onCancel={() => setPendingTxn(null)}
      />
    </ProtectedShell>
  );
}
