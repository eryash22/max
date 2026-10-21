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
import type { Asset, Incident, Indent, Page, Quotation, Vendor } from "../../../../types";

export default function QuotationDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { hasPermission, user } = useAuth();
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const assets = useLookup<Asset>("/api/v1/assets");
  const incidents = useLookup<Incident>("/api/v1/incidents");

  const [quotation, setQuotation] = useState<Quotation | null>(null);
  const [indent, setIndent] = useState<Indent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [rejectionReason, setRejectionReason] = useState("");
  const [remarks, setRemarks] = useState("");
  const [confirmReject, setConfirmReject] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [editLines, setEditLines] = useState<
    { description: string; quantity: number; unit_price: number; taxes: number; covered: boolean }[]
  >([]);

  const load = useCallback(async () => {
    try {
      const result = await api.get<Quotation>(`/api/v1/procurement/quotations/${params.id}`);
      setQuotation(result);
      if (result.status === "APPROVED") {
        const indents = await api.get<Page<Indent>>(`/api/v1/procurement/indents?quotation_id=${result.id}`);
        setIndent(indents.items[0] ?? null);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load quotation.");
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

  const isCreator = !!quotation && !!user && quotation.created_by === user.id;
  const canCreate = hasPermission("quotation.create");
  const canApprove = hasPermission("quotation.approve");
  const canSubmit = canCreate && isCreator && quotation?.status === "DRAFT";
  const canCancel = canCreate && isCreator && (quotation?.status === "DRAFT" || quotation?.status === "SUBMITTED");
  const canDecide = canApprove && quotation?.status === "SUBMITTED" && !isCreator;
  const canCreateIndent = hasPermission("procurement.manage") && quotation?.status === "APPROVED" && !indent;
  // Same permission as create (data-entry, not approval) — only a DRAFT quotation
  // can be edited (see procurement_service.update_quotation); mirrors the
  // isCreator convention canSubmit/canCancel already use for button visibility
  // (the backend itself only checks quotation.create, not created_by, for edit).
  const canEdit = canCreate && isCreator && quotation?.status === "DRAFT";

  function startEdit() {
    if (!quotation) return;
    setEditLines(
      quotation.lines.map((l) => ({
        description: l.description, quantity: l.quantity, unit_price: l.unit_price, taxes: l.taxes, covered: l.covered,
      }))
    );
    setEditError(null);
    setEditing(true);
  }

  async function handleSaveEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!quotation) return;
    setEditError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    setBusy(true);
    try {
      await api.patch(`/api/v1/procurement/quotations/${quotation.id}`, {
        quote_date: formData.get("quote_date") || null,
        valid_until: formData.get("valid_until") || null,
        coverage_note: formData.get("coverage_note") || null,
        lines: editLines,
      });
      setEditing(false);
      setActionMessage("Quotation updated.");
      await load();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : "Failed to update quotation.");
    } finally {
      setBusy(false);
    }
  }

  function updateEditLine(index: number, field: keyof (typeof editLines)[number], value: string | number | boolean) {
    setEditLines((lines) => lines.map((l, i) => (i === index ? { ...l, [field]: value } : l)));
  }

  async function handleSubmit() {
    if (!quotation) return;
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/procurement/quotations/${quotation.id}/submit`);
      setActionMessage("Quotation submitted for Manager approval.");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to submit quotation.");
    } finally {
      setBusy(false);
    }
  }

  async function handleApprove() {
    if (!quotation) return;
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/procurement/quotations/${quotation.id}/approve`, { remarks: remarks || null });
      setActionMessage("Quotation approved.");
      setRemarks("");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to approve quotation.");
    } finally {
      setBusy(false);
    }
  }

  async function handleReject() {
    if (!quotation) return;
    setConfirmReject(false);
    setActionError(null);
    setActionMessage(null);
    if (!rejectionReason.trim()) {
      setActionError("Rejection reason is required.");
      return;
    }
    setBusy(true);
    try {
      await api.post(`/api/v1/procurement/quotations/${quotation.id}/reject`, { rejection_reason: rejectionReason });
      setActionMessage("Quotation rejected.");
      setRejectionReason("");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to reject quotation.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (!quotation) return;
    setConfirmCancel(false);
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/procurement/quotations/${quotation.id}/cancel`);
      setActionMessage("Quotation cancelled.");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to cancel quotation.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!quotation) return;
    setConfirmDelete(false);
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.delete(`/api/v1/procurement/quotations/${quotation.id}`);
      router.push("/procurement/quotations");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setActionError(`${err.message} Use Cancel instead to archive this quotation.`);
      } else {
        setActionError(err instanceof ApiError ? err.message : "Failed to delete quotation.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateIndent() {
    if (!quotation) return;
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      const created = await api.post<Indent>("/api/v1/procurement/indents", { quotation_id: quotation.id });
      window.location.href = `/procurement/indents/${created.id}`;
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to create indent.");
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
  if (!quotation) {
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
          <h1>{quotation.quotation_number}</h1>
          <p>
            <StatusBadge value={quotation.status} /> · <StatusBadge value={quotation.coverage_status} />
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {canEdit && !editing && (
            <button className="btn btn--ghost" onClick={startEdit} disabled={busy}>
              Edit
            </button>
          )}
          {canSubmit && (
            <button className="btn btn--primary" onClick={handleSubmit} disabled={busy}>
              Submit for approval
            </button>
          )}
          {canCancel && (
            <button className="btn btn--ghost" onClick={() => setConfirmCancel(true)} disabled={busy}>
              Cancel
            </button>
          )}
          {canCreate && (
            <button className="btn btn--danger" onClick={() => setConfirmDelete(true)} disabled={busy}>
              Delete
            </button>
          )}
          {canCreateIndent && (
            <button className="btn btn--primary" onClick={handleCreateIndent} disabled={busy}>
              Create indent / CR
            </button>
          )}
          {indent && (
            <a className="btn btn--ghost" href={`/procurement/indents/${indent.id}`}>
              View indent ({indent.indent_number})
            </a>
          )}
        </div>
      </div>

      {actionMessage && <div className="state-panel state-panel--success">{actionMessage}</div>}
      {actionError && <ErrorState message={actionError} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Quotation information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Incident</td>
              <td>
                <a href={`/incidents/${quotation.incident_id}`}>{incidentLabel(quotation.incident_id)}</a>
              </td>
            </tr>
            <tr>
              <td>Asset</td>
              <td>
                <a href={`/assets/${quotation.asset_id}`}>{assetLabel(quotation.asset_id)}</a>
              </td>
            </tr>
            <tr>
              <td>Vendor</td>
              <td>{vendorName(quotation.vendor_id)}</td>
            </tr>
            <tr>
              <td>Quote date</td>
              <td>{quotation.quote_date ?? "-"}</td>
            </tr>
            <tr>
              <td>Valid until</td>
              <td>{quotation.valid_until ?? "-"}</td>
            </tr>
            <tr>
              <td>Coverage status</td>
              <td>
                <StatusBadge value={quotation.coverage_status} />
              </td>
            </tr>
            <tr>
              <td>Coverage note</td>
              <td>{quotation.coverage_note ?? "-"}</td>
            </tr>
            <tr>
              <td>Total</td>
              <td>{quotation.total.toLocaleString()}</td>
            </tr>
            <tr>
              <td>Decision</td>
              <td>{quotation.decided_at ? `${quotation.status} on ${new Date(quotation.decided_at).toLocaleString()}` : "Pending"}</td>
            </tr>
            {quotation.rejection_reason && (
              <tr>
                <td>Rejection reason</td>
                <td>{quotation.rejection_reason}</td>
              </tr>
            )}
          </tbody>
        </table>

        {canDecide && (
          <div style={{ marginTop: 16 }}>
            <div className="form-field" style={{ maxWidth: 480, marginBottom: 12 }}>
              <label>Remarks / rejection reason</label>
              <textarea
                rows={2}
                value={remarks || rejectionReason}
                onChange={(e) => {
                  setRemarks(e.target.value);
                  setRejectionReason(e.target.value);
                }}
                placeholder="Notes for this decision"
              />
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn btn--primary" onClick={handleApprove} disabled={busy}>
                Approve
              </button>
              <button className="btn btn--ghost" onClick={() => setConfirmReject(true)} disabled={busy}>
                Reject
              </button>
            </div>
          </div>
        )}
      </div>

      {editing ? (
        <form className="card" onSubmit={handleSaveEdit}>
          <h3 style={{ marginTop: 0 }}>Edit quotation</h3>
          <div className="form-grid">
            <div className="form-field">
              <label>Quote date</label>
              <input name="quote_date" type="date" defaultValue={quotation.quote_date ?? ""} />
            </div>
            <div className="form-field">
              <label>Valid until</label>
              <input name="valid_until" type="date" defaultValue={quotation.valid_until ?? ""} />
            </div>
            <div className="form-field" style={{ gridColumn: "1 / -1" }}>
              <label>Coverage note</label>
              <input name="coverage_note" defaultValue={quotation.coverage_note ?? ""} />
            </div>
          </div>

          <h4>Lines</h4>
          <table className="data-table" style={{ marginBottom: 12 }}>
            <thead>
              <tr>
                <th>Description</th>
                <th>Qty</th>
                <th>Unit price</th>
                <th>Taxes</th>
                <th>Covered by contract</th>
              </tr>
            </thead>
            <tbody>
              {editLines.map((line, i) => (
                <tr key={i}>
                  <td>
                    <input
                      value={line.description}
                      onChange={(e) => updateEditLine(i, "description", e.target.value)}
                      required
                    />
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
                  <td>
                    <input
                      type="number"
                      min={0}
                      value={line.taxes}
                      onChange={(e) => updateEditLine(i, "taxes", Number(e.target.value))}
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      checked={line.covered}
                      onChange={(e) => updateEditLine(i, "covered", e.target.checked)}
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
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Lines</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Description</th>
                <th>Qty</th>
                <th>Unit price</th>
                <th>Taxes</th>
                <th>Total</th>
                <th>Covered by contract</th>
              </tr>
            </thead>
            <tbody>
              {quotation.lines.map((line) => (
                <tr key={line.id}>
                  <td>{line.description}</td>
                  <td>{line.quantity}</td>
                  <td>{line.unit_price.toLocaleString()}</td>
                  <td>{line.taxes.toLocaleString()}</td>
                  <td>{line.total.toLocaleString()}</td>
                  <td>{line.covered ? "Yes — contract/vendor-service path" : "No — procurement path"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={confirmReject}
        title="Reject quotation"
        message="This rejects the quotation. Continue?"
        confirmLabel="Reject"
        onConfirm={handleReject}
        onCancel={() => setConfirmReject(false)}
      />
      <ConfirmDialog
        open={confirmCancel}
        title="Cancel quotation"
        message="This cancels the quotation. Continue?"
        confirmLabel="Cancel quotation"
        onConfirm={handleCancel}
        onCancel={() => setConfirmCancel(false)}
      />
      <ConfirmDialog
        open={confirmDelete}
        title="Delete quotation"
        message="This permanently deletes the quotation. Only a DRAFT/SUBMITTED quotation with no indent or purchase order raised against it can be deleted — for anything else, use Cancel instead. Continue?"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
      <LinkedDocuments entityType="quotation" entityId={params.id} />
    </ProtectedShell>
  );
}
