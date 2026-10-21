"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import { LinkedDocuments } from "../../../components/LinkedDocuments";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import {
  RequirementItemsTable,
  buildItemsPayload,
  rowsFromItems,
  validateItemRows,
  type ItemRow,
} from "../../../components/RequirementItemsTable";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { useUserDirectory } from "../../../hooks/useUserDirectory";
import { api, ApiError } from "../../../lib/api";
import type {
  Asset,
  Branch,
  Consumable,
  Department,
  DeviceModel,
  Fulfillment,
  HospitalNetwork,
  InventoryItem,
  Page,
  Requirement,
  RequirementStatusHistoryEntry,
} from "../../../types";
import { REQUIREMENT_PRIORITIES, REQUIREMENT_STATUS_LABELS } from "../../../types";

const CANCELLABLE_STATUSES = ["DRAFT", "SUBMITTED", "PENDING_MANAGER_APPROVAL"];
// Branch-scoping hardening pass — same lock as requirements/new/page.tsx: a
// UNIT_HEAD/TL editing their own DRAFT requirement must not be able to move it to
// another branch either (the backend rejects the mismatch — see
// routers/requirements.py:update_requirement).
const BRANCH_LOCKED_ROLES = ["UNIT_HEAD", "TL"];

export default function RequirementDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { hasPermission, user } = useAuth();
  const branchLocked = BRANCH_LOCKED_ROLES.some((r) => user?.roles.includes(r));

  const branches = useLookup<Branch>("/api/v1/branches");
  const hospitals = useLookup<HospitalNetwork>("/api/v1/hospital-networks");
  const departments = useLookup<Department>("/api/v1/departments");
  const users = useUserDirectory();
  // Correction pass — same fetchAll+withStatus switch as requirements/new/page.tsx,
  // so editing a DRAFT requirement's items gets the same searchable Device dropdown
  // (RequirementItemsTable is shared between both pages) with correct loading/error
  // reporting instead of a silently-empty list.
  const { items: deviceModels, loading: deviceModelsLoading, error: deviceModelsError } = useLookup<DeviceModel>(
    "/api/v1/device-models",
    { fetchAll: true, withStatus: true }
  );
  const inventoryItems = useLookup<InventoryItem>("/api/v1/inventory/items");
  // Business-rule correction: same central-only picker as requirements/new — see
  // that page's comment for why.
  const consumables = useLookup<Consumable>("/api/v1/consumables?central_only=true");
  const assets = useLookup<Asset>("/api/v1/assets");

  const [requirement, setRequirement] = useState<Requirement | null>(null);
  const [history, setHistory] = useState<RequirementStatusHistoryEntry[]>([]);
  const [fulfillment, setFulfillment] = useState<Fulfillment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [editing, setEditing] = useState(false);
  const [editRows, setEditRows] = useState<ItemRow[]>([]);
  const [editBranchId, setEditBranchId] = useState("");

  const [confirmCancel, setConfirmCancel] = useState(false);
  const [confirmApprove, setConfirmApprove] = useState(false);
  const [confirmReject, setConfirmReject] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteConflict, setDeleteConflict] = useState<string | null>(null);
  const [decisionRemarks, setDecisionRemarks] = useState("");

  const load = useCallback(async () => {
    try {
      const [requirementResult, historyResult] = await Promise.all([
        api.get<Requirement>(`/api/v1/requirements/${params.id}`),
        api.get<RequirementStatusHistoryEntry[]>(`/api/v1/requirements/${params.id}/history`),
      ]);
      setRequirement(requirementResult);
      setHistory(historyResult);
      if (requirementResult.status === "APPROVED") {
        try {
          const fulfillments = await api.get<Page<Fulfillment>>(
            `/api/v1/fulfillments?search=${encodeURIComponent(requirementResult.request_number)}`
          );
          setFulfillment(fulfillments.items.find((f) => f.requirement_id === requirementResult.id) ?? null);
        } catch {
          setFulfillment(null);
        }
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load requirement.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  const branchName = (id: string | null) => (id ? branches.find((b) => b.id === id)?.name ?? id : "-");
  const departmentName = (id: string | null) => (id ? departments.find((d) => d.id === id)?.name ?? id : "-");
  const hospitalName = (branchId: string | null) => {
    const branch = branchId ? branches.find((b) => b.id === branchId) : undefined;
    const hospital = branch ? hospitals.find((h) => h.id === branch.network_id) : undefined;
    return hospital?.name ?? "-";
  };
  const userName = (id: string | null) => {
    if (!id) return "-";
    if (user && id === user.id) return `${user.name} (You)`;
    return users.find((u) => u.id === id)?.name ?? id;
  };
  const itemReferenceLabel = (item: Requirement["items"][number]) => {
    if (item.item_type === "OTHER") return item.description ?? "-";
    if (item.device_model_id) {
      const m = deviceModels.find((d) => d.id === item.device_model_id);
      return m ? `${m.name}${m.model_number ? ` (${m.model_number})` : ""}` : item.device_model_id;
    }
    if (item.consumable_id) {
      const c = consumables.find((x) => x.id === item.consumable_id);
      return c ? c.name : item.consumable_id;
    }
    if (item.inventory_item_id) {
      const i = inventoryItems.find((x) => x.id === item.inventory_item_id);
      return i ? i.name : item.inventory_item_id;
    }
    if (item.asset_id) {
      const a = assets.find((x) => x.id === item.asset_id);
      if (!a) return item.asset_id;
      const tag = a.asset_number ?? a.serial_number ?? a.id;
      return `${tag} (currently: ${a.status})`;
    }
    return "-";
  };

  const isOwner = !!requirement && !!user && requirement.requester_id === user.id;
  const canApprovePermission = hasPermission("requirement.approve");
  const canCreatePermission = hasPermission("requirement.create");
  const canEditActions = !!requirement && (canApprovePermission || (canCreatePermission && isOwner));

  const canEditDraft = canEditActions && requirement?.status === "DRAFT";
  const canSubmit = canEditDraft;
  const canCancel = canEditActions && !!requirement && CANCELLABLE_STATUSES.includes(requirement.status);
  const canDecide =
    canApprovePermission && requirement?.status === "PENDING_MANAGER_APPROVAL" && !isOwner;
  const canStartFulfillment =
    hasPermission("requirement.fulfill") && requirement?.status === "APPROVED" && !fulfillment;
  // Part 7 — Super Admin requirement delete. Backend only allows the non-force path
  // for DRAFT status with no linked fulfillment (409 otherwise, directing to Cancel or
  // to force-delete). A Super Admin who also holds dev.data_cleanup gets the Delete
  // button regardless of status — the force-delete escape hatch it can fall back to
  // bypasses both restrictions server-side.
  const canForceDelete = hasPermission("dev.data_cleanup");
  const canDelete = hasPermission("requirement.delete") && (requirement?.status === "DRAFT" || canForceDelete);

  const departmentsForEditBranch = useMemo(
    () => departments.filter((d) => !editBranchId || d.branch_id === editBranchId),
    [departments, editBranchId]
  );

  function startEdit() {
    if (!requirement) return;
    setEditRows(rowsFromItems(requirement.items));
    setEditBranchId(requirement.branch_id);
    setActionError(null);
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
  }

  async function handleSaveEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!requirement) return;
    setActionError(null);
    setActionMessage(null);

    const rowError = validateItemRows(editRows);
    if (rowError) {
      setActionError(rowError);
      return;
    }

    const form = event.currentTarget;
    const formData = new FormData(form);
    setBusy(true);
    try {
      await api.patch<Requirement>(`/api/v1/requirements/${requirement.id}`, {
        branch_id: formData.get("branch_id"),
        department_id: formData.get("department_id") || null,
        priority: formData.get("priority"),
        required_by_date: formData.get("required_by_date") || null,
        reason: formData.get("reason"),
        items: buildItemsPayload(editRows),
      });
      setEditing(false);
      setActionMessage("Requirement updated.");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update requirement.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSubmitRequirement() {
    if (!requirement) return;
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      const updated = await api.post<Requirement>(`/api/v1/requirements/${requirement.id}/submit`);
      setActionMessage(`Requirement submitted. Status is now ${REQUIREMENT_STATUS_LABELS[updated.status] ?? updated.status}.`);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to submit requirement.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (!requirement) return;
    setConfirmCancel(false);
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/requirements/${requirement.id}/cancel`, {});
      setActionMessage("Requirement cancelled.");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to cancel requirement.");
    } finally {
      setBusy(false);
    }
  }

  async function handleApprove() {
    if (!requirement) return;
    setConfirmApprove(false);
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/requirements/${requirement.id}/approve`, { remarks: decisionRemarks || null });
      setActionMessage("Requirement approved.");
      setDecisionRemarks("");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to approve requirement.");
    } finally {
      setBusy(false);
    }
  }

  async function handleReject() {
    if (!requirement) return;
    setConfirmReject(false);
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/requirements/${requirement.id}/reject`, { remarks: decisionRemarks || null });
      setActionMessage("Requirement rejected.");
      setDecisionRemarks("");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to reject requirement.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(force = false) {
    if (!requirement) return;
    setConfirmDelete(false);
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await api.delete(`/api/v1/requirements/${requirement.id}${force ? "?force=true" : ""}`);
      router.push("/requirements");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && canForceDelete) {
        setDeleteConflict(err.message);
      } else if (err instanceof ApiError && err.status === 409) {
        setActionError(`${err.message} Use Cancel instead to safely archive this requirement.`);
      } else {
        setActionError(err instanceof ApiError ? err.message : "Failed to delete requirement.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleStartFulfillment() {
    if (!requirement) return;
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      const created = await api.post<Fulfillment>("/api/v1/fulfillments", { requirement_id: requirement.id });
      router.push(`/fulfillments/${created.id}`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to start fulfillment.");
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
  if (!requirement) {
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
          <h1>{requirement.request_number}</h1>
          <p>
            <StatusBadge value={requirement.status} /> · <StatusBadge value={requirement.priority} />
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {canEditDraft && !editing && (
            <button className="btn btn--ghost" onClick={startEdit} disabled={busy}>
              Edit draft
            </button>
          )}
          {canSubmit && !editing && (
            <button className="btn btn--primary" onClick={handleSubmitRequirement} disabled={busy}>
              Submit requirement
            </button>
          )}
          {canCancel && !editing && (
            <button className="btn btn--ghost" onClick={() => setConfirmCancel(true)} disabled={busy}>
              Cancel requirement
            </button>
          )}
          {canStartFulfillment && (
            <button className="btn btn--primary" onClick={handleStartFulfillment} disabled={busy}>
              Start fulfillment
            </button>
          )}
          {fulfillment && (
            <a className="btn btn--ghost" href={`/fulfillments/${fulfillment.id}`}>
              View fulfillment ({fulfillment.fulfillment_number})
            </a>
          )}
          {canDelete && !editing && (
            <button className="btn btn--danger" onClick={() => setConfirmDelete(true)} disabled={busy}>
              Delete requirement
            </button>
          )}
        </div>
      </div>

      {deleteConflict && (
        <div
          className="state-panel state-panel--error"
          style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}
        >
          <span>{deleteConflict}</span>
          <button className="btn btn--danger" onClick={() => handleDelete(true)} disabled={busy}>
            Force delete anyway
          </button>
        </div>
      )}
      {actionMessage && <div className="state-panel state-panel--success">{actionMessage}</div>}
      {actionError && <ErrorState message={actionError} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Requirement information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Hospital</td>
              <td>{hospitalName(requirement.branch_id)}</td>
            </tr>
            <tr>
              <td>Branch</td>
              <td>{branchName(requirement.branch_id)}</td>
            </tr>
            <tr>
              <td>Department / Unit</td>
              <td>{departmentName(requirement.department_id)}</td>
            </tr>
            <tr>
              <td>Requester</td>
              <td>
                {userName(requirement.requester_id)}
                {requirement.requester_role ? ` (${requirement.requester_role.replace(/_/g, " ")})` : ""}
              </td>
            </tr>
            <tr>
              <td>Priority</td>
              <td>
                <StatusBadge value={requirement.priority} />
              </td>
            </tr>
            <tr>
              <td>Required by</td>
              <td>{requirement.required_by_date ?? "-"}</td>
            </tr>
            <tr>
              <td>Reason / Description</td>
              <td>{requirement.reason}</td>
            </tr>
            <tr>
              <td>Current status</td>
              <td>
                <StatusBadge value={requirement.status} />
              </td>
            </tr>
            <tr>
              <td>Created</td>
              <td>{new Date(requirement.created_at).toLocaleString()}</td>
            </tr>
            <tr>
              <td>Last updated</td>
              <td>{new Date(requirement.updated_at).toLocaleString()}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Approval information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Manager</td>
              <td>{userName(requirement.manager_id)}</td>
            </tr>
            <tr>
              <td>Decision</td>
              <td>{requirement.manager_decision ? <StatusBadge value={requirement.manager_decision} /> : "Pending"}</td>
            </tr>
            <tr>
              <td>Decision at</td>
              <td>{requirement.manager_decision_at ? new Date(requirement.manager_decision_at).toLocaleString() : "-"}</td>
            </tr>
            <tr>
              <td>Manager remarks</td>
              <td>{requirement.manager_remarks ?? "-"}</td>
            </tr>
          </tbody>
        </table>

        {canDecide && !editing && (
          <div style={{ marginTop: 16 }}>
            <div className="form-field" style={{ maxWidth: 480, marginBottom: 12 }}>
              <label>Remarks (optional)</label>
              <textarea
                value={decisionRemarks}
                onChange={(e) => setDecisionRemarks(e.target.value)}
                rows={2}
                placeholder="Notes for this decision"
              />
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn btn--primary" onClick={() => setConfirmApprove(true)} disabled={busy}>
                Approve
              </button>
              <button className="btn btn--ghost" onClick={() => setConfirmReject(true)} disabled={busy}>
                Reject
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Requested items</h3>
        {editing ? (
          <form onSubmit={handleSaveEdit}>
            <div className="form-grid" style={{ marginBottom: 12 }}>
              <div className="form-field">
                <label>Branch*</label>
                {branchLocked ? (
                  <>
                    <input
                      type="text"
                      value={branches.find((b) => b.id === editBranchId)?.name ?? "Loading..."}
                      disabled
                      readOnly
                    />
                    <input type="hidden" name="branch_id" value={editBranchId} />
                  </>
                ) : (
                  <select
                    name="branch_id"
                    required
                    value={editBranchId}
                    onChange={(e) => setEditBranchId(e.target.value)}
                  >
                    {branches.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.name}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              <div className="form-field">
                <label>Department / Unit</label>
                <select name="department_id" defaultValue={requirement.department_id ?? ""}>
                  <option value="">Not specified</option>
                  {departmentsForEditBranch.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-field">
                <label>Priority*</label>
                <select name="priority" defaultValue={requirement.priority}>
                  {REQUIREMENT_PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-field">
                <label>Required by date</label>
                <input name="required_by_date" type="date" defaultValue={requirement.required_by_date ?? ""} />
              </div>
            </div>
            <div className="form-field" style={{ marginBottom: 12 }}>
              <label>Reason / Description*</label>
              <textarea name="reason" required rows={3} defaultValue={requirement.reason} />
            </div>

            <RequirementItemsTable
              rows={editRows}
              onChange={setEditRows}
              deviceModels={deviceModels}
              deviceModelsLoading={deviceModelsLoading}
              deviceModelsError={deviceModelsError}
              inventoryItems={inventoryItems}
              consumables={consumables}
              assets={assets}
              branches={branches}
              branchId={editBranchId}
            />

            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Save changes
              </button>
              <button className="btn btn--ghost" type="button" onClick={cancelEdit} disabled={busy}>
                Cancel edit
              </button>
            </div>
          </form>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Item Category</th>
                <th>Item Reference / Description</th>
                <th>Quantity</th>
                <th>Unit</th>
                <th>Remarks</th>
              </tr>
            </thead>
            <tbody>
              {requirement.items.length === 0 && (
                <tr>
                  <td colSpan={5}>No items on this requirement.</td>
                </tr>
              )}
              {requirement.items.map((item) => (
                <tr key={item.id}>
                  <td>{item.item_type.replace(/_/g, " ")}</td>
                  <td>{itemReferenceLabel(item)}</td>
                  <td>{item.requested_quantity}</td>
                  <td>{item.unit ?? "-"}</td>
                  <td>{item.remarks ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Status history</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>From</th>
              <th>To</th>
              <th>Actor</th>
              <th>Timestamp</th>
              <th>Remarks</th>
            </tr>
          </thead>
          <tbody>
            {history.length === 0 && (
              <tr>
                <td colSpan={5}>No history recorded yet.</td>
              </tr>
            )}
            {history.map((h) => (
              <tr key={h.id}>
                <td>{h.from_status ? <StatusBadge value={h.from_status} /> : "-"}</td>
                <td>
                  <StatusBadge value={h.to_status} />
                </td>
                <td>{userName(h.changed_by)}</td>
                <td>{new Date(h.created_at).toLocaleString()}</td>
                <td>{h.notes ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={confirmCancel}
        title="Cancel requirement"
        message="This cancels the requirement. Cancelled requirements cannot be resubmitted — a new requirement would need to be raised. Continue?"
        confirmLabel="Cancel requirement"
        onConfirm={handleCancel}
        onCancel={() => setConfirmCancel(false)}
      />
      <ConfirmDialog
        open={confirmApprove}
        title="Approve requirement"
        message="This approves the requirement. Approval is final for this workflow. Continue?"
        confirmLabel="Approve"
        onConfirm={handleApprove}
        onCancel={() => setConfirmApprove(false)}
      />
      <ConfirmDialog
        open={confirmReject}
        title="Reject requirement"
        message="This rejects the requirement. Rejected requirements cannot be resubmitted — a new requirement would need to be raised. Continue?"
        confirmLabel="Reject"
        onConfirm={handleReject}
        onCancel={() => setConfirmReject(false)}
      />
      <ConfirmDialog
        open={confirmDelete}
        title="Delete requirement"
        message={
          canForceDelete
            ? "This permanently deletes the requirement. Only DRAFT requirements with no linked fulfillment can be deleted this way — otherwise you'll be offered a force-delete that cascades through its items, fulfillment, and history. Continue?"
            : "This permanently deletes the requirement. Only DRAFT requirements with no linked fulfillment can be deleted — for anything else, use Cancel instead. Continue?"
        }
        confirmLabel="Delete"
        onConfirm={() => handleDelete()}
        onCancel={() => setConfirmDelete(false)}
      />
      <LinkedDocuments entityType="requirement" entityId={params.id} />
    </ProtectedShell>
  );
}
