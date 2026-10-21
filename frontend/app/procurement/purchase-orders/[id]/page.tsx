"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import { ProtectedShell } from "../../../../components/ProtectedShell";
import { LinkedDocuments } from "../../../../components/LinkedDocuments";
import { LoadingState } from "../../../../components/LoadingState";
import { ErrorState } from "../../../../components/ErrorState";
import { StatusBadge } from "../../../../components/StatusBadge";
import { ConfirmDialog } from "../../../../components/ConfirmDialog";
import { useAuth } from "../../../../hooks/useAuth";
import { useLookup } from "../../../../hooks/useLookup";
import { api, ApiError } from "../../../../lib/api";
import type { Asset, Incident, Invoice, Page, PurchaseOrder, ServiceVisit, Vendor } from "../../../../types";
import { PO_NEXT_STATUS } from "../../../../types";

export default function PurchaseOrderDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { hasPermission } = useAuth();
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const assets = useLookup<Asset>("/api/v1/assets");
  const incidents = useLookup<Incident>("/api/v1/incidents");

  const [po, setPo] = useState<PurchaseOrder | null>(null);
  const [visits, setVisits] = useState<ServiceVisit[]>([]);
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const [visitDate, setVisitDate] = useState("");
  const [visitSummary, setVisitSummary] = useState("");
  const [invoiceDate, setInvoiceDate] = useState("");
  const [invoiceAmount, setInvoiceAmount] = useState("");

  const load = useCallback(async () => {
    try {
      const result = await api.get<PurchaseOrder>(`/api/v1/procurement/purchase-orders/${params.id}`);
      setPo(result);
      const [visitResult, invoiceResult] = await Promise.all([
        api.get<Page<ServiceVisit>>(`/api/v1/service-visits?purchase_order_id=${result.id}`),
        api.get<Page<Invoice>>(`/api/v1/procurement/invoices?purchase_order_id=${result.id}`),
      ]);
      setVisits(visitResult.items);
      setInvoice(invoiceResult.items[0] ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load purchase order.");
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

  const canManagePo = hasPermission("po.manage");
  const canScheduleVisit = hasPermission("asset.write") && po && ["ISSUED", "ACKNOWLEDGED"].includes(po.status);
  const canRecordInvoice =
    hasPermission("invoice.record") && !!po && !["DRAFT", "COMPLETED"].includes(po.status) && !invoice;
  const nextStatus = po ? PO_NEXT_STATUS[po.status] : null;
  const canCancel = canManagePo && (po?.status === "DRAFT" || po?.status === "ISSUED");
  const canDelete = canManagePo;
  // Only DRAFT — once ISSUED the vendor has received these terms; see
  // procurement_service.update_purchase_order.
  const canEdit = canManagePo && po?.status === "DRAFT";
  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [editLines, setEditLines] = useState<
    { quotation_line_id: string | null; description: string; quantity: number; unit_price: number }[]
  >([]);

  function startEdit() {
    if (!po) return;
    setEditLines(
      po.lines.map((l) => ({
        quotation_line_id: l.quotation_line_id, description: l.description, quantity: l.quantity, unit_price: l.unit_price,
      }))
    );
    setEditError(null);
    setEditing(true);
  }

  function updateEditLine(index: number, field: "description" | "quantity" | "unit_price", value: string | number) {
    setEditLines((lines) => lines.map((l, i) => (i === index ? { ...l, [field]: value } : l)));
  }

  async function handleSaveEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!po) return;
    setEditError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    setBusy(true);
    try {
      await api.patch(`/api/v1/procurement/purchase-orders/${po.id}`, {
        issue_date: formData.get("issue_date") || null,
        expected_vendor_date: formData.get("expected_vendor_date") || null,
        lines: editLines,
      });
      setEditing(false);
      setActionMessage("Purchase order updated.");
      await load();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : "Failed to update purchase order.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (!po) return;
    setConfirmCancel(false);
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/procurement/purchase-orders/${po.id}/cancel`);
      setActionMessage("Purchase order cancelled.");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to cancel purchase order.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!po) return;
    setConfirmDelete(false);
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.delete(`/api/v1/procurement/purchase-orders/${po.id}`);
      router.push("/procurement/purchase-orders");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError(`${err.message} Use Cancel instead to archive this purchase order.`);
      } else {
        setActionError(err instanceof ApiError ? err.message : "Failed to delete purchase order.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleAdvanceStatus() {
    if (!po || !nextStatus) return;
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/procurement/purchase-orders/${po.id}/status`, { to_status: nextStatus });
      setActionMessage(`Purchase order moved to ${nextStatus}.`);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update purchase order status.");
    } finally {
      setBusy(false);
    }
  }

  async function handleScheduleVisit() {
    if (!po) return;
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.post("/api/v1/service-visits", {
        incident_id: po.incident_id,
        asset_id: po.asset_id,
        vendor_id: po.vendor_id,
        purchase_order_id: po.id,
        visit_type: "BREAKDOWN",
        visit_date: visitDate || null,
        summary: visitSummary || null,
      });
      setActionMessage("Vendor visit scheduled — the assigned BME has been notified.");
      setVisitDate("");
      setVisitSummary("");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to schedule vendor visit.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRecordInvoice() {
    if (!po) return;
    setActionError(null);
    setActionMessage(null);
    const amount = Number(invoiceAmount);
    if (!amount || amount <= 0) {
      setActionError("Invoice amount must be a positive number.");
      return;
    }
    setBusy(true);
    try {
      await api.post("/api/v1/procurement/invoices", {
        purchase_order_id: po.id, invoice_date: invoiceDate || null, amount,
      });
      setActionMessage("Invoice recorded — the Inventory Manager has been notified for verification.");
      setInvoiceDate("");
      setInvoiceAmount("");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to record invoice.");
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
  if (!po) {
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
          <h1>{po.po_number}</h1>
          <p>
            <StatusBadge value={po.status} />
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {canEdit && !editing && (
            <button className="btn btn--ghost" onClick={startEdit} disabled={busy}>
              Edit
            </button>
          )}
          {canManagePo && nextStatus && (
            <button className="btn btn--primary" onClick={handleAdvanceStatus} disabled={busy}>
              {po.status === "DRAFT" ? "Send to vendor" : `Mark ${nextStatus.replace(/_/g, " ")}`}
            </button>
          )}
          {invoice && (
            <a className="btn btn--ghost" href={`/procurement/invoices/${invoice.id}`}>
              View invoice ({invoice.invoice_number})
            </a>
          )}
        </div>
      </div>

      {actionMessage && <div className="state-panel state-panel--success">{actionMessage}</div>}
      {actionError && <ErrorState message={actionError} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Purchase order information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Indent</td>
              <td>
                <a href={`/procurement/indents/${po.indent_id}`}>{po.indent_id}</a>
              </td>
            </tr>
            <tr>
              <td>Quotation</td>
              <td>
                <a href={`/procurement/quotations/${po.quotation_id}`}>{po.quotation_id}</a>
              </td>
            </tr>
            <tr>
              <td>Incident</td>
              <td>
                <a href={`/incidents/${po.incident_id}`}>{incidentLabel(po.incident_id)}</a>
              </td>
            </tr>
            <tr>
              <td>Asset</td>
              <td>
                <a href={`/assets/${po.asset_id}`}>{assetLabel(po.asset_id)}</a>
              </td>
            </tr>
            <tr>
              <td>Vendor</td>
              <td>{vendorName(po.vendor_id)}</td>
            </tr>
            <tr>
              <td>Issue date</td>
              <td>{po.issue_date ?? "-"}</td>
            </tr>
            <tr>
              <td>Expected vendor date</td>
              <td>{po.expected_vendor_date ?? "-"}</td>
            </tr>
            <tr>
              <td>Total</td>
              <td>{po.total.toLocaleString()}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {editing ? (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleSaveEdit}>
          <h3 style={{ marginTop: 0 }}>Edit purchase order</h3>
          <div className="form-grid">
            <div className="form-field">
              <label>Issue date</label>
              <input name="issue_date" type="date" defaultValue={po.issue_date ?? ""} />
            </div>
            <div className="form-field">
              <label>Expected vendor date</label>
              <input name="expected_vendor_date" type="date" defaultValue={po.expected_vendor_date ?? ""} />
            </div>
          </div>

          <h4>Lines</h4>
          <table className="data-table" style={{ marginBottom: 12 }}>
            <thead>
              <tr>
                <th>Description</th>
                <th>Qty</th>
                <th>Unit price</th>
              </tr>
            </thead>
            <tbody>
              {editLines.map((line, i) => (
                <tr key={i}>
                  <td>
                    <input value={line.description} onChange={(e) => updateEditLine(i, "description", e.target.value)} required />
                  </td>
                  <td>
                    <input
                      type="number"
                      min={1}
                      value={line.quantity}
                      onChange={(e) => updateEditLine(i, "quantity", Number(e.target.value))}
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      min={0}
                      value={line.unit_price}
                      onChange={(e) => updateEditLine(i, "unit_price", Number(e.target.value))}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {editError && <div className="field-error">{editError}</div>}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--primary" type="submit" disabled={busy}>
              Save changes
            </button>
            <button className="btn btn--ghost" type="button" onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Lines</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Description</th>
                <th>Qty</th>
                <th>Unit price</th>
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {po.lines.map((line) => (
                <tr key={line.id}>
                  <td>{line.description}</td>
                  <td>{line.quantity}</td>
                  <td>{line.unit_price.toLocaleString()}</td>
                  <td>{line.total.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Vendor visits</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Visit date</th>
              <th>Summary</th>
              <th>Issue / outcome</th>
            </tr>
          </thead>
          <tbody>
            {visits.length === 0 && (
              <tr>
                <td colSpan={3}>No vendor visit scheduled yet.</td>
              </tr>
            )}
            {visits.map((v) => (
              <tr key={v.id}>
                <td>{v.visit_date ?? "-"}</td>
                <td>{v.summary ?? "-"}</td>
                <td>{v.issue ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {canScheduleVisit && (
          <div style={{ marginTop: 16 }}>
            <div className="form-grid" style={{ marginBottom: 12 }}>
              <div className="form-field">
                <label>Scheduled visit date</label>
                <input type="date" value={visitDate} onChange={(e) => setVisitDate(e.target.value)} />
              </div>
              <div className="form-field">
                <label>Summary</label>
                <input value={visitSummary} onChange={(e) => setVisitSummary(e.target.value)} placeholder="Vendor visit notes" />
              </div>
            </div>
            <button className="btn btn--primary" onClick={handleScheduleVisit} disabled={busy}>
              Schedule vendor visit
            </button>
          </div>
        )}
      </div>

      {canRecordInvoice && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Record invoice</h3>
          <div className="form-grid" style={{ marginBottom: 12 }}>
            <div className="form-field">
              <label>Invoice date</label>
              <input type="date" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} />
            </div>
            <div className="form-field">
              <label>Amount*</label>
              <input type="number" min={0} step="0.01" value={invoiceAmount} onChange={(e) => setInvoiceAmount(e.target.value)} />
            </div>
          </div>
          <button className="btn btn--primary" onClick={handleRecordInvoice} disabled={busy}>
            Record invoice
          </button>
        </div>
      )}
      <LinkedDocuments entityType="purchase_order" entityId={params.id} />
    </ProtectedShell>
  );
}
