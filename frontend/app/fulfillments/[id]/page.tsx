"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
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
  FulfillmentAllocateInput,
  FulfillmentStatusHistoryEntry,
  HospitalNetwork,
  InventoryItem,
  InventoryLocation,
  Requirement,
} from "../../../types";
import { FULFILLMENT_CANCELLABLE_STATUSES, FULFILLMENT_SERIALIZED_ITEM_TYPES, FULFILLMENT_STATUS_LABELS } from "../../../types";

interface AllocationRow {
  requirement_item_id: string;
  quantity: string;
  asset_ids: string[];
}

export default function FulfillmentDetailPage() {
  const params = useParams<{ id: string }>();
  const { hasPermission, user } = useAuth();
  const canFulfill = hasPermission("requirement.fulfill");

  const branches = useLookup<Branch>("/api/v1/branches");
  const hospitals = useLookup<HospitalNetwork>("/api/v1/hospital-networks");
  const departments = useLookup<Department>("/api/v1/departments");
  const users = useUserDirectory();
  const deviceModels = useLookup<DeviceModel>("/api/v1/device-models");
  const inventoryItems = useLookup<InventoryItem>("/api/v1/inventory/items");
  const consumables = useLookup<Consumable>("/api/v1/consumables");
  const locations = useLookup<InventoryLocation>("/api/v1/inventory/locations");
  const assets = useLookup<Asset>("/api/v1/assets");

  const [fulfillment, setFulfillment] = useState<Fulfillment | null>(null);
  const [requirement, setRequirement] = useState<Requirement | null>(null);
  const [history, setHistory] = useState<FulfillmentStatusHistoryEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [rows, setRows] = useState<AllocationRow[]>([]);
  const [sourceLocationId, setSourceLocationId] = useState("");
  const [expectedDeliveryDate, setExpectedDeliveryDate] = useState("");
  const [actualDeliveryDate, setActualDeliveryDate] = useState("");
  const [deliveryRemarks, setDeliveryRemarks] = useState("");
  const [cancelRemarks, setCancelRemarks] = useState("");
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [confirmComplete, setConfirmComplete] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await api.get<Fulfillment>(`/api/v1/fulfillments/${params.id}`);
      setFulfillment(result);
      const [requirementResult, historyResult] = await Promise.all([
        api.get<Requirement>(`/api/v1/requirements/${result.requirement_id}`),
        api.get<FulfillmentStatusHistoryEntry[]>(`/api/v1/fulfillments/${params.id}/history`),
      ]);
      setRequirement(requirementResult);
      setHistory(historyResult);
      setSourceLocationId(result.source_location_id ?? "");
      setExpectedDeliveryDate(result.expected_delivery_date ?? "");
      setRows(
        result.items.map((item) => ({
          requirement_item_id: item.requirement_item_id,
          quantity: String(item.allocated_quantity),
          asset_ids: item.assets.map((a) => a.asset_id),
        }))
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load fulfillment.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  const branchName = (id: string | null | undefined) => (id ? branches.find((b) => b.id === id)?.name ?? id : "-");
  const departmentName = (id: string | null | undefined) => (id ? departments.find((d) => d.id === id)?.name ?? id : "-");
  const hospitalName = (branchId: string | null | undefined) => {
    const branch = branchId ? branches.find((b) => b.id === branchId) : undefined;
    const hospital = branch ? hospitals.find((h) => h.id === branch.network_id) : undefined;
    return hospital?.name ?? "-";
  };
  const locationName = (id: string | null) => (id ? locations.find((l) => l.id === id)?.name ?? id : "-");
  const userName = (id: string | null) => {
    if (!id) return "-";
    if (user && id === user.id) return `${user.name} (You)`;
    return users.find((u) => u.id === id)?.name ?? id;
  };
  const itemReferenceLabel = (item: Fulfillment["items"][number]) => {
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
    return "-";
  };
  const matchingAssets = (deviceModelId: string | null) =>
    deviceModelId ? assets.filter((a) => a.device_model_id === deviceModelId) : [];

  const isOwner = !!requirement && !!user && requirement.requester_id === user.id;
  const canEditAllocation =
    canFulfill && !!fulfillment && ["PENDING_ALLOCATION", "ALLOCATED"].includes(fulfillment.status);
  const canProcess = canFulfill && fulfillment?.status === "ALLOCATED";
  const canPack = canFulfill && fulfillment?.status === "UNDER_PROCESS";
  const canDispatch = canFulfill && fulfillment?.status === "PACKED";
  const canDeliver = canFulfill && fulfillment?.status === "OUT_FOR_DELIVERY";
  const canComplete = canFulfill && fulfillment?.status === "DELIVERED";
  const canCancel = canFulfill && !!fulfillment && FULFILLMENT_CANCELLABLE_STATUSES.includes(fulfillment.status);

  function updateRow(requirementItemId: string, patch: Partial<AllocationRow>) {
    setRows((prev) => prev.map((r) => (r.requirement_item_id === requirementItemId ? { ...r, ...patch } : r)));
  }

  function toggleAsset(requirementItemId: string, assetId: string, checked: boolean) {
    setRows((prev) =>
      prev.map((r) => {
        if (r.requirement_item_id !== requirementItemId) return r;
        const next = checked ? [...r.asset_ids, assetId] : r.asset_ids.filter((id) => id !== assetId);
        return { ...r, asset_ids: next };
      })
    );
  }

  async function handleSaveAllocation() {
    if (!fulfillment) return;
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      const payload: FulfillmentAllocateInput = {
        source_location_id: sourceLocationId || null,
        expected_delivery_date: expectedDeliveryDate || null,
        items: rows.map((row) => {
          const item = fulfillment.items.find((i) => i.requirement_item_id === row.requirement_item_id)!;
          if (FULFILLMENT_SERIALIZED_ITEM_TYPES.includes(item.item_type)) {
            return { requirement_item_id: row.requirement_item_id, asset_ids: row.asset_ids };
          }
          return { requirement_item_id: row.requirement_item_id, quantity: Number(row.quantity || 0) };
        }),
      };
      await api.post<Fulfillment>(`/api/v1/fulfillments/${fulfillment.id}/allocate`, payload);
      setActionMessage("Allocation saved.");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to save allocation.");
    } finally {
      setBusy(false);
    }
  }

  async function runAction(action: () => Promise<unknown>, successMessage: string) {
    setActionError(null);
    setActionMessage(null);
    setBusy(true);
    try {
      await action();
      setActionMessage(successMessage);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDeliver() {
    if (!fulfillment) return;
    await runAction(
      () =>
        api.post(`/api/v1/fulfillments/${fulfillment.id}/deliver`, {
          actual_delivery_date: actualDeliveryDate || null,
          delivery_remarks: deliveryRemarks || null,
        }),
      "Marked as delivered."
    );
  }

  async function handleComplete() {
    if (!fulfillment) return;
    setConfirmComplete(false);
    await runAction(() => api.post(`/api/v1/fulfillments/${fulfillment.id}/complete`), "Fulfillment completed — inventory updated.");
  }

  async function handleCancel() {
    if (!fulfillment) return;
    setConfirmCancel(false);
    await runAction(
      () => api.post(`/api/v1/fulfillments/${fulfillment.id}/cancel`, { remarks: cancelRemarks || null }),
      "Fulfillment cancelled."
    );
  }

  const totals = useMemo(() => {
    if (!fulfillment) return { requested: 0, allocated: 0, pending: 0 };
    return fulfillment.items.reduce(
      (acc, item) => ({
        requested: acc.requested + item.requested_quantity,
        allocated: acc.allocated + item.allocated_quantity,
        pending: acc.pending + item.pending_quantity,
      }),
      { requested: 0, allocated: 0, pending: 0 }
    );
  }, [fulfillment]);

  if (error) {
    return (
      <ProtectedShell>
        <ErrorState message={error} />
      </ProtectedShell>
    );
  }
  if (!fulfillment || !requirement) {
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
          <h1>{fulfillment.fulfillment_number}</h1>
          <p>
            <StatusBadge value={fulfillment.status} /> · Requirement{" "}
            <a href={`/requirements/${requirement.id}`}>{requirement.request_number}</a>
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {canProcess && (
            <button className="btn btn--primary" disabled={busy} onClick={() => runAction(() => api.post(`/api/v1/fulfillments/${fulfillment.id}/process`), "Moved to Under Process.")}>
              Start processing
            </button>
          )}
          {canPack && (
            <button className="btn btn--primary" disabled={busy} onClick={() => runAction(() => api.post(`/api/v1/fulfillments/${fulfillment.id}/pack`), "Marked packed.")}>
              Mark packed
            </button>
          )}
          {canDispatch && (
            <button className="btn btn--primary" disabled={busy} onClick={() => runAction(() => api.post(`/api/v1/fulfillments/${fulfillment.id}/out-for-delivery`), "Marked out for delivery.")}>
              Out for delivery
            </button>
          )}
          {canComplete && (
            <button className="btn btn--primary" disabled={busy} onClick={() => setConfirmComplete(true)}>
              Complete (update inventory)
            </button>
          )}
          {canCancel && (
            <button className="btn btn--ghost" disabled={busy} onClick={() => setConfirmCancel(true)}>
              Cancel fulfillment
            </button>
          )}
        </div>
      </div>

      {actionMessage && <div className="state-panel state-panel--success">{actionMessage}</div>}
      {actionError && <ErrorState message={actionError} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Requirement details</h3>
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
              <td>{userName(requirement.requester_id)}</td>
            </tr>
            <tr>
              <td>Priority</td>
              <td>
                <StatusBadge value={requirement.priority} />
              </td>
            </tr>
            <tr>
              <td>Reason</td>
              <td>{requirement.reason}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Delivery details</h3>
        <div className="form-grid" style={{ marginBottom: 12 }}>
          <div className="form-field">
            <label>Source inventory location</label>
            <select value={sourceLocationId} onChange={(e) => setSourceLocationId(e.target.value)} disabled={!canEditAllocation}>
              <option value="">Not specified</option>
              {locations.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Expected delivery date</label>
            <input type="date" value={expectedDeliveryDate} onChange={(e) => setExpectedDeliveryDate(e.target.value)} disabled={!canEditAllocation} />
          </div>
        </div>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Inventory manager</td>
              <td>{userName(fulfillment.inventory_manager_id)}</td>
            </tr>
            <tr>
              <td>Source location</td>
              <td>{locationName(fulfillment.source_location_id)}</td>
            </tr>
            <tr>
              <td>Actual delivery date</td>
              <td>{fulfillment.actual_delivery_date ?? "-"}</td>
            </tr>
            <tr>
              <td>Delivered by</td>
              <td>{userName(fulfillment.delivered_by_id)}</td>
            </tr>
            <tr>
              <td>Delivery remarks</td>
              <td>{fulfillment.delivery_remarks ?? "-"}</td>
            </tr>
            {fulfillment.status === "CANCELLED" && (
              <tr>
                <td>Cancellation remarks</td>
                <td>{fulfillment.cancel_remarks ?? "-"}</td>
              </tr>
            )}
          </tbody>
        </table>

        {canDeliver && (
          <div style={{ marginTop: 16 }}>
            <div className="form-grid" style={{ marginBottom: 12 }}>
              <div className="form-field">
                <label>Actual delivery date</label>
                <input type="date" value={actualDeliveryDate} onChange={(e) => setActualDeliveryDate(e.target.value)} />
              </div>
              <div className="form-field">
                <label>Delivery remarks</label>
                <input value={deliveryRemarks} onChange={(e) => setDeliveryRemarks(e.target.value)} placeholder="Handed to / received by" />
              </div>
            </div>
            <button className="btn btn--primary" disabled={busy} onClick={handleDeliver}>
              Mark delivered
            </button>
          </div>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>
          Items — {totals.allocated}/{totals.requested} allocated, {totals.pending} pending
        </h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Item</th>
              <th>Requested</th>
              <th>Available</th>
              <th>Allocated</th>
              <th>Pending</th>
              <th>Delivered</th>
              <th>Assets</th>
              {canEditAllocation && <th>Allocate</th>}
            </tr>
          </thead>
          <tbody>
            {fulfillment.items.map((item) => {
              const row = rows.find((r) => r.requirement_item_id === item.requirement_item_id);
              const serialized = FULFILLMENT_SERIALIZED_ITEM_TYPES.includes(item.item_type);
              return (
                <tr key={item.id}>
                  <td>
                    {item.item_type.replace(/_/g, " ")}: {itemReferenceLabel(item)}
                  </td>
                  <td>{item.requested_quantity}</td>
                  <td>{item.available_quantity ?? "N/A"}</td>
                  <td>{item.allocated_quantity}</td>
                  <td>{item.pending_quantity}</td>
                  <td>{item.delivered_quantity}</td>
                  <td>
                    {item.assets.length === 0
                      ? "-"
                      : item.assets.map((a, idx) => {
                          const asset = assets.find((x) => x.id === a.asset_id);
                          return (
                            <span key={a.asset_id}>
                              {idx > 0 ? ", " : ""}
                              <Link href={`/assets/${a.asset_id}`}>{asset?.asset_number ?? a.asset_id}</Link>
                            </span>
                          );
                        })}
                  </td>
                  {canEditAllocation && (
                    <td>
                      {serialized ? (
                        <div style={{ maxHeight: 120, overflowY: "auto" }}>
                          {matchingAssets(item.device_model_id).length === 0 && <span>No matching assets.</span>}
                          {matchingAssets(item.device_model_id).map((asset) => (
                            <label key={asset.id} style={{ display: "block", fontWeight: "normal" }}>
                              <input
                                type="checkbox"
                                checked={row?.asset_ids.includes(asset.id) ?? false}
                                onChange={(e) => toggleAsset(item.requirement_item_id, asset.id, e.target.checked)}
                              />{" "}
                              {asset.asset_number}
                              {asset.serial_number ? ` (${asset.serial_number})` : ""}
                            </label>
                          ))}
                        </div>
                      ) : (
                        <input
                          type="number"
                          min={0}
                          max={item.requested_quantity}
                          style={{ width: 80 }}
                          value={row?.quantity ?? "0"}
                          onChange={(e) => updateRow(item.requirement_item_id, { quantity: e.target.value })}
                        />
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
        {canEditAllocation && (
          <button className="btn btn--primary" style={{ marginTop: 12 }} disabled={busy} onClick={handleSaveAllocation}>
            Save allocation
          </button>
        )}
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Status timeline</h3>
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
        title="Cancel fulfillment"
        message="This cancels the fulfillment and releases any reserved stock/assets. Continue?"
        confirmLabel="Cancel fulfillment"
        onConfirm={handleCancel}
        onCancel={() => setConfirmCancel(false)}
      />
      <ConfirmDialog
        open={confirmComplete}
        title="Complete fulfillment"
        message="This deducts stock, updates consumable quantities and relocates serialized devices to the requesting branch/department. This cannot be undone. Continue?"
        confirmLabel="Complete"
        onConfirm={handleComplete}
        onCancel={() => setConfirmComplete(false)}
      />
    </ProtectedShell>
  );
}
