"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { api, ApiError } from "../../../lib/api";
import type { Asset, Branch, BranchTL, DeviceModel, Page, UserRecord } from "../../../types";

// Restructure consolidation pass, Part B — Branch as the contextual operational hub.
// Every summary tile below reuses an existing filtered list endpoint (branch_id) —
// no new backend endpoint was added. "Assigned/delivered devices" reads the real
// Asset table (the source of truth for delivered devices), never
// BranchDeviceAssignment (import/reconciliation-only infrastructure, Part I).

async function totalOf(path: string): Promise<number> {
  try {
    const result = await api.get<Page<unknown>>(`${path}${path.includes("?") ? "&" : "?"}page=1&page_size=1`);
    return result.total;
  } catch {
    return 0;
  }
}

interface BranchSummary {
  totalAssets: number;
  pendingRequirements: number;
  openIncidents: number;
  activeFulfillments: number;
  pmDue: number;
  calibrationDue: number;
  serviceVisits: number;
  consumables: number;
}

export default function BranchDetailPage() {
  const params = useParams<{ id: string }>();
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("hospital.write");

  const [branch, setBranch] = useState<Branch | null>(null);
  const [summary, setSummary] = useState<BranchSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canManageUsers = hasPermission("users.manage");
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [tls, setTls] = useState<BranchTL[]>([]);
  const [hierarchyError, setHierarchyError] = useState<string | null>(null);

  // Assigned Devices & Assets — reads the same canonical Asset.branch_id
  // relationship the "Assigned / delivered devices" stat tile above already links
  // to (GET /api/v1/assets?branch_id=...), just rendered inline instead of only as
  // a count + click-through. No new endpoint, no BranchDeviceAssignment involved.
  const [assets, setAssets] = useState<Asset[] | null>(null);
  const [assetsError, setAssetsError] = useState<string | null>(null);
  const canViewAssets = hasPermission("asset.read");
  const canAssignAsset = hasPermission("asset.write");
  const deviceModels = useLookup<DeviceModel>("/api/v1/device-models");
  const modelName = (id: string) => deviceModels.find((m) => m.id === id)?.name ?? id;

  const loadAssets = useCallback(async () => {
    if (!canViewAssets) return;
    setAssetsError(null);
    try {
      const result = await api.get<Page<Asset>>(`/api/v1/assets?branch_id=${params.id}&page=1&page_size=200`);
      setAssets(result.items);
    } catch (err) {
      setAssetsError(err instanceof ApiError ? err.message : "Failed to load assigned devices.");
    }
  }, [canViewAssets, params.id]);

  useEffect(() => {
    loadAssets();
  }, [loadAssets]);

  const load = useCallback(async () => {
    try {
      const branchResult = await api.get<Branch>(`/api/v1/branches/${params.id}`);
      setBranch(branchResult);

      // Fetches are unconditional — the backend's require_permission dependencies are
      // the real enforcement point (per AGENTS.md); a 403 here is simply caught by
      // totalOf() and treated as 0, same as "no data" would be. Gating the fetch
      // itself on a client-side hasPermission() check added a class of bug (a stale
      // closure could silently zero out a section) for no real security benefit.
      const [
        totalAssets, pendingRequirements, incidentTotal, incidentResolved, incidentClosed,
        fulfillmentTotal, fulfillmentCompleted, fulfillmentCancelled,
        pmOverdue, pmDueSoon, calibrationOverdue, calibrationDueSoon, serviceVisits, consumables,
      ] =
        await Promise.all([
          totalOf(`/api/v1/assets?branch_id=${params.id}`),
          totalOf(`/api/v1/requirements?branch_id=${params.id}&status_filter=PENDING_MANAGER_APPROVAL`),
          totalOf(`/api/v1/incidents?branch_id=${params.id}`),
          totalOf(`/api/v1/incidents?branch_id=${params.id}&status_filter=RESOLVED`),
          totalOf(`/api/v1/incidents?branch_id=${params.id}&status_filter=CLOSED`),
          totalOf(`/api/v1/fulfillments?branch_id=${params.id}`),
          totalOf(`/api/v1/fulfillments?branch_id=${params.id}&status_filter=COMPLETED`),
          totalOf(`/api/v1/fulfillments?branch_id=${params.id}&status_filter=CANCELLED`),
          totalOf(`/api/v1/pm/schedules?branch_id=${params.id}&due_status=OVERDUE`),
          totalOf(`/api/v1/pm/schedules?branch_id=${params.id}&due_status=DUE`),
          totalOf(`/api/v1/calibration/schedules?branch_id=${params.id}&due_status=OVERDUE`),
          totalOf(`/api/v1/calibration/schedules?branch_id=${params.id}&due_status=DUE`),
          totalOf(`/api/v1/service-visits?branch_id=${params.id}`),
          totalOf(`/api/v1/consumables?branch_id=${params.id}`),
        ]);

      setSummary({
        totalAssets,
        pendingRequirements,
        openIncidents: Math.max(0, incidentTotal - incidentResolved - incidentClosed),
        activeFulfillments: Math.max(0, fulfillmentTotal - fulfillmentCompleted - fulfillmentCancelled),
        pmDue: pmOverdue + pmDueSoon,
        calibrationDue: calibrationOverdue + calibrationDueSoon,
        serviceVisits,
        consumables,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load branch.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  const loadHierarchy = useCallback(async () => {
    if (!canManageUsers) return;
    try {
      const [userList, tlList] = await Promise.all([
        api.get<UserRecord[]>("/api/v1/users"),
        api.get<BranchTL[]>(`/api/v1/branches/${params.id}/tls`),
      ]);
      setUsers(userList);
      setTls(tlList);
    } catch (err) {
      setHierarchyError(err instanceof ApiError ? err.message : "Failed to load branch user hierarchy.");
    }
  }, [canManageUsers, params.id]);

  useEffect(() => {
    loadHierarchy();
  }, [loadHierarchy]);

  function userLabel(userId: string | null): string {
    if (!userId) return "Not assigned";
    const u = users.find((candidate) => candidate.id === userId);
    return u ? `${u.name} (${u.email})` : userId;
  }

  async function handleSetUnitHead(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!branch) return;
    setHierarchyError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    const unitHeadUserId = (formData.get("unit_head_user_id") as string) || null;
    try {
      // Branch's PATCH endpoint is a full-replace (BranchIn), not a partial update —
      // must resend every existing field or they'd silently reset to schema defaults.
      await api.patch(`/api/v1/branches/${params.id}`, {
        network_id: branch.network_id, code: branch.code, name: branch.name,
        source_name: branch.source_name, region: branch.region, state: branch.state,
        city: branch.city, address: branch.address, unit_head: branch.unit_head, tl: branch.tl,
        unit_head_user_id: unitHeadUserId,
        branch_email: branch.branch_email, branch_phone: branch.branch_phone,
        branch_whatsapp: branch.branch_whatsapp, status: branch.status,
        is_central_warehouse: branch.is_central_warehouse,
      });
      await load();
    } catch (err) {
      setHierarchyError(err instanceof ApiError ? err.message : "Failed to set Unit Head.");
    }
  }

  async function handleAddTl(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setHierarchyError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    const userId = formData.get("tl_user_id") as string;
    if (!userId) return;
    try {
      await api.post(`/api/v1/branches/${params.id}/tls`, { user_id: userId });
      form.reset();
      await loadHierarchy();
    } catch (err) {
      setHierarchyError(err instanceof ApiError ? err.message : "Failed to add TL.");
    }
  }

  async function handleRemoveTl(assignmentId: string) {
    setHierarchyError(null);
    try {
      await api.delete(`/api/v1/branches/${params.id}/tls/${assignmentId}`);
      await loadHierarchy();
    } catch (err) {
      setHierarchyError(err instanceof ApiError ? err.message : "Failed to remove TL.");
    }
  }

  async function handleUpdateBranch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.patch(`/api/v1/branches/${params.id}`, {
        name: formData.get("name"),
        code: formData.get("code") || null,
        region: formData.get("region") || null,
        state: formData.get("state") || null,
        city: formData.get("city") || null,
        address: formData.get("address") || null,
        unit_head: formData.get("unit_head") || null,
        tl: formData.get("tl") || null,
        // Not on this form — this PATCH is a full-replace, so the real Unit Head
        // assignment (set from the "Real branch user hierarchy" card below) must be
        // resent unchanged here or it would silently reset to null.
        unit_head_user_id: branch?.unit_head_user_id ?? null,
        branch_email: formData.get("branch_email") || null,
        branch_phone: formData.get("branch_phone") || null,
        branch_whatsapp: formData.get("branch_whatsapp") || null,
        status: formData.get("status"),
        is_central_warehouse: formData.get("is_central_warehouse") === "on",
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update branch.");
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
  if (!branch) {
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
          <h1>{branch.name}</h1>
          <p>
            <StatusBadge value={branch.status} />
            {branch.code ? ` · ${branch.code}` : ""}
            {branch.unit_head ? ` · Unit Head: ${branch.unit_head}` : ""}
            {branch.tl ? ` · TL: ${branch.tl}` : ""}
          </p>
        </div>
      </div>

      {actionError && <ErrorState message={actionError} />}

      <div className="stat-grid" style={{ marginBottom: 16 }}>
        {hasPermission("asset.read") && (
          <StatTile label="Assigned / delivered devices" value={summary?.totalAssets ?? 0} href={`/assets?branch_id=${branch.id}`} />
        )}
        <StatTile label="Pending requirements" value={summary?.pendingRequirements ?? 0} href={`/requirements?branch_id=${branch.id}`} />
        {hasPermission("incident.read") && (
          <StatTile label="Open incidents" value={summary?.openIncidents ?? 0} href={`/incidents?branch_id=${branch.id}`} />
        )}
        <StatTile label="Pending fulfillments" value={summary?.activeFulfillments ?? 0} href={`/fulfillments?branch_id=${branch.id}`} />
        {hasPermission("inventory.read") && (
          <StatTile label="Consumables" value={summary?.consumables ?? 0} href={`/consumables?branch_id=${branch.id}`} />
        )}
        {hasPermission("pm.read") && (
          <StatTile label="PM due" value={summary?.pmDue ?? 0} href={`/maintenance?branch_id=${branch.id}`} />
        )}
        {hasPermission("calibration.read") && (
          <StatTile label="Calibration due" value={summary?.calibrationDue ?? 0} href={`/calibration?branch_id=${branch.id}`} />
        )}
        {hasPermission("asset.read") && (
          <StatTile label="Service visits" value={summary?.serviceVisits ?? 0} href={`/service-visits?branch_id=${branch.id}`} />
        )}
      </div>
      <p style={{ marginTop: -8, marginBottom: 16, color: "var(--color-text-muted)" }}>
        &quot;Assigned / delivered devices&quot; reflects real <code>Asset</code> records for this branch — not the
        import-reconciliation-only Branch Device Assignment list, which never represents delivered inventory.
      </p>

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Branch information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Name</td>
              <td>
                {branch.name}
                {branch.is_central_warehouse && (
                  <span className="badge" style={{ marginLeft: 8 }}>
                    Central warehouse
                  </span>
                )}
              </td>
            </tr>
            <tr>
              <td>Code</td>
              <td>{branch.code ?? "Not provided"}</td>
            </tr>
            <tr>
              <td>Region / State / City</td>
              <td>{[branch.region, branch.state, branch.city].filter(Boolean).join(" / ") || "-"}</td>
            </tr>
            <tr>
              <td>Address</td>
              <td>{branch.address ?? "-"}</td>
            </tr>
            <tr>
              <td>Unit Head</td>
              <td>{branch.unit_head ?? "-"}</td>
            </tr>
            <tr>
              <td>TL</td>
              <td>{branch.tl ?? "-"}</td>
            </tr>
            <tr>
              <td>Branch email</td>
              <td>{branch.branch_email ?? "-"}</td>
            </tr>
            <tr>
              <td>Branch phone</td>
              <td>{branch.branch_phone ?? "-"}</td>
            </tr>
            <tr>
              <td>Branch WhatsApp</td>
              <td>{branch.branch_whatsapp ?? "-"}</td>
            </tr>
            <tr>
              <td>Status</td>
              <td>
                <StatusBadge value={branch.status} />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {canViewAssets && (
        <div className="card" style={{ marginTop: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ marginTop: 0 }}>Assigned Devices &amp; Assets</h3>
            {canAssignAsset && (
              <Link className="btn btn--primary" href={`/assets?branch_id=${branch.id}&new=1`}>
                + Assign Device
              </Link>
            )}
          </div>

          {assetsError && <ErrorState message={assetsError} />}
          {!assetsError && assets === null && <LoadingState label="Loading assigned devices..." />}
          {!assetsError && assets !== null && assets.length === 0 && (
            <p className="field-hint">No devices/assets are currently assigned to this branch.</p>
          )}
          {!assetsError && assets !== null && assets.length > 0 && (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Device / Model</th>
                    <th>Asset Number</th>
                    <th>Serial Number</th>
                    <th>Status</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {assets.map((a) => (
                    <tr key={a.id}>
                      <td>{modelName(a.device_model_id)}</td>
                      <td>{a.asset_number ?? "Not assigned"}</td>
                      <td>{a.serial_number ?? "-"}</td>
                      <td>
                        <StatusBadge value={a.status} />
                      </td>
                      <td>
                        <Link className="btn btn--ghost" href={`/assets/${a.id}`}>
                          View / Manage
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Business-workflow audit: makes the branch reporting line legible — Unit
          Head/TL run this branch day to day; BME, Inventory Manager, and Manager
          oversee it from outside the branch (not subordinate to the TL). Read-only,
          no new data — same unit_head/tl fields shown above, laid out as a chain of
          command instead of a flat table row. */}
      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Operational hierarchy</h3>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 24, alignItems: "flex-start" }}>
          <div>
            <div style={{ fontSize: 12, textTransform: "uppercase", color: "var(--color-text-muted)", marginBottom: 6 }}>
              Branch team
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="badge">{branch.name}</span>
              <span style={{ paddingLeft: 12 }}>&#8627; Unit Head: {branch.unit_head ?? "Not assigned"}</span>
              <span style={{ paddingLeft: 24 }}>&#8627; TL: {branch.tl ?? "Not assigned"}</span>
            </div>
          </div>
          <div>
            <div style={{ fontSize: 12, textTransform: "uppercase", color: "var(--color-text-muted)", marginBottom: 6 }}>
              Oversight (not subordinate to this branch)
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span>BME — technical/incident escalation</span>
              <span>Inventory Manager — fulfillment &amp; delivery</span>
              <span>Manager — requirement approval</span>
            </div>
          </div>
        </div>
      </div>

      {canManageUsers && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0 }}>Real branch user hierarchy</h3>
          <p>
            Assign real users as this branch&apos;s Unit Head and TL(s) — replaces the free-text fields above as the
            source of truth. Give each assigned user the <code>UNIT_HEAD</code> / <code>TL</code> role and a matching
            branch on the <Link href="/users">Users</Link> page so their branch-scoped visibility works.
          </p>
          {hierarchyError && <ErrorState message={hierarchyError} />}

          <form onSubmit={handleSetUnitHead} className="form-grid" style={{ marginBottom: 16 }}>
            <div className="form-field">
              <label>Unit Head</label>
              <select name="unit_head_user_id" defaultValue={branch.unit_head_user_id ?? ""}>
                <option value="">Not assigned</option>
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name} ({u.email})
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit">
                Save Unit Head
              </button>
            </div>
          </form>
          <p>Current Unit Head: {userLabel(branch.unit_head_user_id)}</p>

          <h4>TLs</h4>
          {tls.length === 0 && <p>No TLs assigned yet.</p>}
          {tls.length > 0 && (
            <ul>
              {tls.map((assignment) => (
                <li key={assignment.id}>
                  {userLabel(assignment.user_id)}{" "}
                  <button className="btn btn--ghost" onClick={() => handleRemoveTl(assignment.id)}>
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={handleAddTl} className="form-grid">
            <div className="form-field">
              <label>Add TL</label>
              <select name="tl_user_id" defaultValue="">
                <option value="" disabled>
                  Select user
                </option>
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name} ({u.email})
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--secondary" type="submit">
                Add TL
              </button>
            </div>
          </form>
        </div>
      )}

      {canWrite && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0 }}>Edit branch</h3>
          <form onSubmit={handleUpdateBranch} className="form-grid">
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required defaultValue={branch.name} />
            </div>
            <div className="form-field">
              <label>Code</label>
              <input name="code" defaultValue={branch.code ?? ""} />
            </div>
            <div className="form-field">
              <label>Region</label>
              <input name="region" defaultValue={branch.region ?? ""} />
            </div>
            <div className="form-field">
              <label>State</label>
              <input name="state" defaultValue={branch.state ?? ""} />
            </div>
            <div className="form-field">
              <label>City</label>
              <input name="city" defaultValue={branch.city ?? ""} />
            </div>
            <div className="form-field">
              <label>Address</label>
              <input name="address" defaultValue={branch.address ?? ""} />
            </div>
            <div className="form-field">
              <label>Unit Head</label>
              <input name="unit_head" defaultValue={branch.unit_head ?? ""} />
            </div>
            <div className="form-field">
              <label>TL</label>
              <input name="tl" defaultValue={branch.tl ?? ""} />
            </div>
            <div className="form-field">
              <label>Branch email</label>
              <input name="branch_email" type="email" defaultValue={branch.branch_email ?? ""} />
            </div>
            <div className="form-field">
              <label>Branch phone</label>
              <input name="branch_phone" defaultValue={branch.branch_phone ?? ""} />
            </div>
            <div className="form-field">
              <label>Branch WhatsApp</label>
              <input name="branch_whatsapp" defaultValue={branch.branch_whatsapp ?? ""} />
            </div>
            <div className="form-field">
              <label>Status</label>
              <select name="status" defaultValue={branch.status}>
                <option value="ACTIVE">ACTIVE</option>
                <option value="INACTIVE">INACTIVE</option>
              </select>
            </div>
            <div className="form-field">
              <label>
                <input
                  type="checkbox"
                  name="is_central_warehouse"
                  defaultChecked={branch.is_central_warehouse}
                  style={{ marginRight: 6 }}
                />
                Central biomedical warehouse (e.g. MAX @ HOME, Okhla Phase 2)
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
    </ProtectedShell>
  );
}

function StatTile({ label, value, href }: { label: string; value: number; href?: string }) {
  const tile = (
    <div className="stat-tile">
      <div className="stat-tile__label">{label}</div>
      <div className="stat-tile__value">{value}</div>
    </div>
  );
  if (!href) return tile;
  return (
    <Link href={href} className="stat-tile-link">
      {tile}
    </Link>
  );
}
