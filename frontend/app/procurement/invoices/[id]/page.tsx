"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useParams } from "next/navigation";
import { ProtectedShell } from "../../../../components/ProtectedShell";
import { LinkedDocuments } from "../../../../components/LinkedDocuments";
import { LoadingState } from "../../../../components/LoadingState";
import { ErrorState } from "../../../../components/ErrorState";
import { StatusBadge } from "../../../../components/StatusBadge";
import { ConfirmDialog } from "../../../../components/ConfirmDialog";
import { useAuth } from "../../../../hooks/useAuth";
import { useLookup } from "../../../../hooks/useLookup";
import { api, ApiError } from "../../../../lib/api";
import type { Asset, Incident, Invoice, Vendor } from "../../../../types";

export default function InvoiceDetailPage() {
  const params = useParams<{ id: string }>();
  const { hasPermission } = useAuth();
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const assets = useLookup<Asset>("/api/v1/assets");
  const incidents = useLookup<Incident>("/api/v1/incidents");

  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [rejectionReason, setRejectionReason] = useState("");
  const [confirmReject, setConfirmReject] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await api.get<Invoice>(`/api/v1/procurement/invoices/${params.id}`);
      setInvoice(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load invoice.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  const vendorName = (id: string) => vendors.find((v) => v.id === id)?.name ?? id;
  const assetLabel = (id: string) => {
    const a = assets.find((x) => x.id === id);
    return a ? a.asset_number ?? a.serial_number ?? a.id : id;
  };
  const incidentLabel = (id: string) => incidents.find((i) => i.id === id)?.description ?? id;

  const canVerify = hasPermission("invoice.approve") && invoice?.verification_status === "PENDING";
  // Same permission as recording (data-entry) — NOT invoice.approve, preserving
  // segregation between who records an invoice and who verifies it. Only PENDING —
  // see procurement_service.update_invoice.
  const canEdit = hasPermission("invoice.record") && invoice?.verification_status === "PENDING";
  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  async function handleSaveEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!invoice) return;
    setEditError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    const amountRaw = formData.get("amount");
    setBusy(true);
    try {
      await api.patch(`/api/v1/procurement/invoices/${invoice.id}`, {
        invoice_date: formData.get("invoice_date") || null,
        amount: amountRaw ? Number(amountRaw) : undefined,
      });
      setEditing(false);
      setActionMessage("Invoice updated.");
      await load();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : "Failed to update invoice.");
    } finally {
      setBusy(false);
    }
  }

  async function handleVerify() {
    if (!invoice) return;
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/procurement/invoices/${invoice.id}/verify`);
      setActionMessage("Invoice verified — financial closure recorded, PO marked Completed.");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to verify invoice.");
    } finally {
      setBusy(false);
    }
  }

  async function handleReject() {
    if (!invoice) return;
    setConfirmReject(false);
    setActionError(null);
    setActionMessage(null);
    if (!rejectionReason.trim()) {
      setActionError("Rejection reason is required.");
      return;
    }
    setBusy(true);
    try {
      await api.post(`/api/v1/procurement/invoices/${invoice.id}/reject`, { rejection_reason: rejectionReason });
      setActionMessage("Invoice rejected.");
      setRejectionReason("");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to reject invoice.");
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
  if (!invoice) {
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
          <h1>{invoice.invoice_number}</h1>
          <p>
            <StatusBadge value={invoice.verification_status} />
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {canEdit && !editing && (
            <button className="btn btn--ghost" onClick={() => { setEditError(null); setEditing(true); }} disabled={busy}>
              Edit
            </button>
          )}
          {canVerify && (
            <>
              <button className="btn btn--primary" onClick={handleVerify} disabled={busy}>
                Verify invoice
              </button>
              <button className="btn btn--ghost" onClick={() => setConfirmReject(true)} disabled={busy}>
                Reject
              </button>
            </>
          )}
        </div>
      </div>

      {actionMessage && <div className="state-panel state-panel--success">{actionMessage}</div>}
      {actionError && <ErrorState message={actionError} />}

      {canVerify && (
        <div className="form-field" style={{ maxWidth: 480, marginBottom: 16 }}>
          <label>Rejection reason (only needed if rejecting)</label>
          <textarea rows={2} value={rejectionReason} onChange={(e) => setRejectionReason(e.target.value)} />
        </div>
      )}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Invoice information</h3>
        {editing ? (
          <form onSubmit={handleSaveEdit} className="form-grid" style={{ marginBottom: 12 }}>
            <div className="form-field">
              <label>Invoice date</label>
              <input name="invoice_date" type="date" defaultValue={invoice.invoice_date ?? ""} />
            </div>
            <div className="form-field">
              <label>Amount*</label>
              <input name="amount" type="number" min={0} step="0.01" defaultValue={invoice.amount} required />
            </div>
            {editError && <div className="field-error" style={{ gridColumn: "1 / -1" }}>{editError}</div>}
            <div className="form-field" style={{ justifyContent: "flex-end", flexDirection: "row", gap: 8 }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Save changes
              </button>
              <button className="btn btn--ghost" type="button" onClick={() => setEditing(false)} disabled={busy}>
                Cancel
              </button>
            </div>
          </form>
        ) : null}
        <table className="data-table">
          <tbody>
            <tr>
              <td>Purchase order</td>
              <td>
                <a href={`/procurement/purchase-orders/${invoice.purchase_order_id}`}>{invoice.purchase_order_id}</a>
              </td>
            </tr>
            <tr>
              <td>Incident</td>
              <td>
                <a href={`/incidents/${invoice.incident_id}`}>{incidentLabel(invoice.incident_id)}</a>
              </td>
            </tr>
            <tr>
              <td>Asset</td>
              <td>
                <a href={`/assets/${invoice.asset_id}`}>{assetLabel(invoice.asset_id)}</a>
              </td>
            </tr>
            <tr>
              <td>Vendor</td>
              <td>{vendorName(invoice.vendor_id)}</td>
            </tr>
            <tr>
              <td>Invoice date</td>
              <td>{invoice.invoice_date ?? "-"}</td>
            </tr>
            <tr>
              <td>Amount</td>
              <td>{invoice.amount.toLocaleString()}</td>
            </tr>
            <tr>
              <td>Recorded</td>
              <td>{invoice.recorded_at ? new Date(invoice.recorded_at).toLocaleString() : "-"}</td>
            </tr>
            <tr>
              <td>Verification status</td>
              <td>
                <StatusBadge value={invoice.verification_status} />
              </td>
            </tr>
            <tr>
              <td>Verified at</td>
              <td>{invoice.verified_at ? new Date(invoice.verified_at).toLocaleString() : "-"}</td>
            </tr>
            {invoice.rejection_reason && (
              <tr>
                <td>Rejection reason</td>
                <td>{invoice.rejection_reason}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={confirmReject}
        title="Reject invoice"
        message="This rejects the invoice. Continue?"
        confirmLabel="Reject"
        onConfirm={handleReject}
        onCancel={() => setConfirmReject(false)}
      />
      <LinkedDocuments entityType="invoice" entityId={params.id} />
    </ProtectedShell>
  );
}
