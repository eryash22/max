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
import { useAssetLabel } from "../../../../hooks/useAssetLabel";
import { api, ApiError } from "../../../../lib/api";
import type { Incident, Indent, Page, PurchaseOrder, Vendor } from "../../../../types";

export default function IndentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { hasPermission } = useAuth();
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const { labelForAssetId } = useAssetLabel();
  const incidents = useLookup<Incident>("/api/v1/incidents");

  const [indent, setIndent] = useState<Indent | null>(null);
  const [po, setPo] = useState<PurchaseOrder | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [issueDate, setIssueDate] = useState("");
  const [expectedVendorDate, setExpectedVendorDate] = useState("");
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await api.get<Indent>(`/api/v1/procurement/indents/${params.id}`);
      setIndent(result);
      const pos = await api.get<Page<PurchaseOrder>>(`/api/v1/procurement/purchase-orders?indent_id=${result.id}`);
      setPo(pos.items[0] ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load indent.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  const vendorName = (id: string) => vendors.find((v) => v.id === id)?.name ?? id;
  const assetLabel = (id: string) => labelForAssetId(id);
  const incidentLabel = (id: string) => incidents.find((i) => i.id === id)?.description ?? id;

  const canManage = hasPermission("procurement.manage");
  const canSubmit = canManage && indent?.status === "DRAFT";
  const canCreatePo = hasPermission("po.manage") && indent?.status === "SUBMITTED" && !po;
  const canCancel = canManage && (indent?.status === "DRAFT" || indent?.status === "SUBMITTED");
  const canDelete = canManage;
  // Only DRAFT/SUBMITTED — see procurement_service.update_indent. Only `notes` is
  // editable (amount is derived from the quotation's uncovered lines; structural
  // fields are fixed at creation).
  const canEdit = canManage && (indent?.status === "DRAFT" || indent?.status === "SUBMITTED");
  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  async function handleSubmit() {
    if (!indent) return;
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/procurement/indents/${indent.id}/submit`);
      setActionMessage("Indent submitted — ready for PO creation.");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to submit indent.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (!indent) return;
    setConfirmCancel(false);
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/procurement/indents/${indent.id}/cancel`);
      setActionMessage("Indent cancelled.");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to cancel indent.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!indent) return;
    setConfirmDelete(false);
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.delete(`/api/v1/procurement/indents/${indent.id}`);
      router.push("/procurement/indents");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError(`${err.message} Use Cancel instead to archive this indent.`);
      } else {
        setActionError(err instanceof ApiError ? err.message : "Failed to delete indent.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!indent) return;
    setEditError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    setBusy(true);
    try {
      await api.patch(`/api/v1/procurement/indents/${indent.id}`, { notes: formData.get("notes") || null });
      setEditing(false);
      setActionMessage("Indent updated.");
      await load();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : "Failed to update indent.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreatePo() {
    if (!indent) return;
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      const created = await api.post<PurchaseOrder>("/api/v1/procurement/purchase-orders", {
        indent_id: indent.id,
        issue_date: issueDate || null,
        expected_vendor_date: expectedVendorDate || null,
      });
      window.location.href = `/procurement/purchase-orders/${created.id}`;
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to create purchase order.");
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
  if (!indent) {
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
          <h1>{indent.indent_number}</h1>
          <p>
            <StatusBadge value={indent.status} />
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {canEdit && !editing && (
            <button className="btn btn--ghost" onClick={() => { setEditError(null); setEditing(true); }} disabled={busy}>
              Edit
            </button>
          )}
          {canSubmit && (
            <button className="btn btn--primary" onClick={handleSubmit} disabled={busy}>
              Submit indent
            </button>
          )}
          {canCancel && (
            <button className="btn btn--ghost" onClick={() => setConfirmCancel(true)} disabled={busy}>
              Cancel
            </button>
          )}
          {canDelete && (
            <button className="btn btn--danger" onClick={() => setConfirmDelete(true)} disabled={busy}>
              Delete
            </button>
          )}
          {po && (
            <a className="btn btn--ghost" href={`/procurement/purchase-orders/${po.id}`}>
              View PO ({po.po_number})
            </a>
          )}
        </div>
      </div>

      {actionMessage && <div className="state-panel state-panel--success">{actionMessage}</div>}
      {actionError && <ErrorState message={actionError} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Indent information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Quotation</td>
              <td>
                <a href={`/procurement/quotations/${indent.quotation_id}`}>{indent.quotation_id}</a>
              </td>
            </tr>
            <tr>
              <td>Incident</td>
              <td>
                <a href={`/incidents/${indent.incident_id}`}>{incidentLabel(indent.incident_id)}</a>
              </td>
            </tr>
            <tr>
              <td>Asset</td>
              <td>
                <a href={`/assets/${indent.asset_id}`}>{assetLabel(indent.asset_id)}</a>
              </td>
            </tr>
            <tr>
              <td>Vendor</td>
              <td>{vendorName(indent.vendor_id)}</td>
            </tr>
            <tr>
              <td>Amount (uncovered lines only)</td>
              <td>{indent.amount.toLocaleString()}</td>
            </tr>
            <tr>
              <td>Notes</td>
              <td>
                {editing ? (
                  <form onSubmit={handleSaveEdit} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                    <textarea name="notes" rows={2} defaultValue={indent.notes ?? ""} style={{ flex: 1 }} />
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <button className="btn btn--primary" type="submit" disabled={busy}>
                        Save
                      </button>
                      <button className="btn btn--ghost" type="button" onClick={() => setEditing(false)} disabled={busy}>
                        Cancel
                      </button>
                    </div>
                  </form>
                ) : (
                  indent.notes ?? "-"
                )}
                {editError && <div className="field-error">{editError}</div>}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {canCreatePo && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Create purchase order</h3>
          <div className="form-grid" style={{ marginBottom: 12 }}>
            <div className="form-field">
              <label>Issue date</label>
              <input type="date" value={issueDate} onChange={(e) => setIssueDate(e.target.value)} />
            </div>
            <div className="form-field">
              <label>Expected vendor date</label>
              <input type="date" value={expectedVendorDate} onChange={(e) => setExpectedVendorDate(e.target.value)} />
            </div>
          </div>
          <button className="btn btn--primary" onClick={handleCreatePo} disabled={busy}>
            Create PO
          </button>
        </div>
      )}
      <ConfirmDialog
        open={confirmCancel}
        title="Cancel indent"
        message="This cancels the indent / change request. Continue?"
        confirmLabel="Cancel indent"
        onConfirm={handleCancel}
        onCancel={() => setConfirmCancel(false)}
      />
      <ConfirmDialog
        open={confirmDelete}
        title="Delete indent"
        message="This permanently deletes the indent. Only a DRAFT/SUBMITTED indent with no purchase order raised against it can be deleted — for anything else, use Cancel instead. Continue?"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
      <LinkedDocuments entityType="indent" entityId={params.id} />
    </ProtectedShell>
  );
}
