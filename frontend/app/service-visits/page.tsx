"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useAuth } from "../../hooks/useAuth";
import { useLookup } from "../../hooks/useLookup";
import { coverageForAsset } from "../../hooks/useDeviceVendorInfo";
import { api, ApiError } from "../../lib/api";
import { locationLabel } from "../../lib/locationLabel";
import type { Asset, Branch, Contract, DeviceModel, Incident, Page, ServiceVisit, ServiceVisitType, Vendor } from "../../types";

const PAGE_SIZE = 25;

export default function ServiceVisitsPage() {
  const { hasPermission } = useAuth();
  const canRead = hasPermission("asset.read");
  const canWrite = hasPermission("asset.write");

  const assets = useLookup<Asset>("/api/v1/assets");
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const modelName = (id: string) => models.find((m) => m.id === id)?.name ?? id;
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const incidents = useLookup<Incident>("/api/v1/incidents");
  // Batch E, Part 20 — admin-managed visit types (replaces the old hardcoded
  // SERVICE_VISIT_TYPES constant in the <select>s below).
  const visitTypes = useLookup<ServiceVisitType>("/api/v1/service-visits/types");
  // Batch E, Part 21/22 — AMC/CMC/Warranty coverage and Branch/CENTRAL WAREHOUSE
  // location, both derived live (never duplicated onto Asset), same data already
  // fetched by assets/page.tsx and inventory/page.tsx for the identical purpose.
  const contracts = useLookup<Contract>("/api/v1/contracts");
  const branches = useLookup<Branch>("/api/v1/branches");
  const assetNumber = (id: string) => assets.find((a) => a.id === id)?.asset_number ?? id;
  const visitBranchLabel = (assetId: string) => locationLabel(assets.find((a) => a.id === assetId)?.branch_id, branches);
  const vendorName = (id: string | null) => (id ? vendors.find((v) => v.id === id)?.name ?? id : "—");

  const [assetFilter, setAssetFilter] = useState("");
  const [branchFilter, setBranchFilter] = useState("");
  const [visitTypeFilter, setVisitTypeFilter] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  // Hydrates filters from the URL so calendar/branch-hub drill-down links (e.g.
  // /service-visits?asset_id=X or /service-visits?branch_id=X) land pre-filtered.
  // Workflow consolidation pass — also hydrates incidentPrefill so an Incident's
  // "Create Service Visit" action (/service-visits?asset_id=&incident_id=) opens the
  // create form pre-linked to it.
  const [incidentPrefill, setIncidentPrefill] = useState("");
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const af = params.get("asset_id");
    if (af) setAssetFilter(af);
    const bi = params.get("branch_id");
    if (bi) setBranchFilter(bi);
    const inc = params.get("incident_id");
    if (inc) {
      setIncidentPrefill(inc);
      setShowForm(true);
    }
  }, []);

  const [visits, setVisits] = useState<ServiceVisit[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  // Batch E, Part 21/22 — tracks the create form's own Asset selection separately
  // from `assetFilter` (the list filter above): changing which asset the NEW visit
  // is against must not also re-filter the table. Seeded from assetFilter/the URL
  // prefill so a drill-down link still shows coverage/location immediately.
  const [formAssetId, setFormAssetId] = useState(assetFilter);
  useEffect(() => {
    if (assetFilter) setFormAssetId(assetFilter);
  }, [assetFilter]);
  const formAsset = assets.find((a) => a.id === formAssetId) ?? null;
  const formCoverage = formAsset ? coverageForAsset(formAsset, contracts) : null;
  const formCovered = formCoverage !== null && formCoverage.label !== "Not covered";
  const formLocation = formAsset ? locationLabel(formAsset.branch_id, branches) : null;

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<ServiceVisit | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (assetFilter) params.set("asset_id", assetFilter);
      if (branchFilter) params.set("branch_id", branchFilter);
      if (visitTypeFilter) params.set("visit_type", visitTypeFilter);
      if (search) params.set("search", search);
      const result = await api.get<Page<ServiceVisit>>(`/api/v1/service-visits?${params.toString()}`);
      setVisits(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load service visits.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assetFilter, branchFilter, visitTypeFilter, search, page]);

  useEffect(() => {
    setPage(1);
  }, [assetFilter, branchFilter, visitTypeFilter, search]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/service-visits", {
        asset_id: formData.get("asset_id"),
        incident_id: formData.get("incident_id") || null,
        vendor_id: formData.get("vendor_id") || null,
        visit_type: formData.get("visit_type") || null,
        visit_date: formData.get("visit_date") || null,
        summary: formData.get("summary") || null,
        parts_used: formData.get("parts_used") || null,
        cost: formData.get("cost") ? Number(formData.get("cost")) : null,
        next_action: formData.get("next_action") || null,
        issue: formData.get("issue") || null,
        downtime_hours: formData.get("downtime_hours") ? Number(formData.get("downtime_hours")) : null,
      });
      setShowForm(false);
      form.reset();
      setPage(1);
      await load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create service visit.");
    }
  }

  async function handleUpdate(event: FormEvent<HTMLFormElement>, id: string) {
    event.preventDefault();
    setEditError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.patch(`/api/v1/service-visits/${id}`, {
        vendor_id: formData.get("vendor_id") || null,
        visit_type: formData.get("visit_type") || null,
        visit_date: formData.get("visit_date") || null,
        summary: formData.get("summary") || null,
        parts_used: formData.get("parts_used") || null,
        cost: formData.get("cost") ? Number(formData.get("cost")) : null,
        next_action: formData.get("next_action") || null,
        issue: formData.get("issue") || null,
        downtime_hours: formData.get("downtime_hours") ? Number(formData.get("downtime_hours")) : null,
      });
      setEditingId(null);
      await load();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : "Failed to update service visit.");
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setActionError(null);
    try {
      await api.delete(`/api/v1/service-visits/${deleteTarget.id}`);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to delete service visit.");
      setDeleteTarget(null);
    }
  }

  if (!canRead) {
    return (
      <ProtectedShell>
        <ErrorState message="You do not have permission to view service visits." />
      </ProtectedShell>
    );
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Service visits</h1>
          <p>
            Engineer and vendor visits/actions against an asset — PM, calibration, breakdown,
            installation, or other work — optionally linked to a formal incident.
          </p>
        </div>
        {canWrite && (
          <button className="btn btn--primary" onClick={() => setShowForm((v) => !v)}>
            {showForm ? "Cancel" : "Log service visit"}
          </button>
        )}
      </div>

      {showForm && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleCreate}>
          <div className="form-grid">
            <div className="form-field">
              <label>Asset*</label>
              <select
                name="asset_id"
                required
                defaultValue={assetFilter}
                key={`asset-${assetFilter}`}
                onChange={(e) => setFormAssetId(e.target.value)}
              >
                <option value="" disabled>
                  Select an asset
                </option>
                {assets.map((a) => (
                  <option key={a.id} value={a.id}>
                    {modelName(a.device_model_id)} — {a.asset_number}
                  </option>
                ))}
              </select>
            </div>
            {formAsset && (
              // Batch E, Part 21/22 — read-only Device/Asset Number/Branch/Coverage
              // display, all computed live (never duplicated onto Asset), reusing
              // coverageForAsset() (hooks/useDeviceVendorInfo.ts) and locationLabel()
              // (lib/locationLabel.ts) exactly as assets/inventory pages already do.
              <div className="form-field" style={{ gridColumn: "1 / -1" }}>
                <p className="dashboard-note" style={{ margin: 0 }}>
                  Device: <strong>{modelName(formAsset.device_model_id)}</strong> &nbsp;|&nbsp; Asset Number:{" "}
                  <strong>{formAsset.asset_number}</strong> &nbsp;|&nbsp; Branch/Warehouse:{" "}
                  <strong>{formLocation}</strong> &nbsp;|&nbsp; Coverage: <strong>{formCoverage?.label}</strong>
                </p>
              </div>
            )}
            <div className="form-field">
              <label>Visit type</label>
              <select name="visit_type" defaultValue="">
                <option value="">Unspecified</option>
                {visitTypes.map((t) => (
                  <option key={t.id} value={t.name}>
                    {t.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Incident (optional)</label>
              <select name="incident_id" defaultValue={incidentPrefill} key={`incident-${incidentPrefill}`}>
                <option value="">None</option>
                {incidents.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.description.slice(0, 50)}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Vendor (optional)</label>
              <select name="vendor_id" defaultValue="">
                <option value="">None (internal engineer)</option>
                {vendors.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Visit date</label>
              <input type="date" name="visit_date" />
            </div>
            <div className="form-field">
              <label>Cost{formCovered && " (defaulted to 0 — covered by " + formCoverage?.label + ", edit if needed)"}</label>
              <input
                type="number"
                name="cost"
                min="0"
                step="0.01"
                defaultValue={formCovered ? "0" : ""}
                key={`cost-${formAssetId}-${formCovered}`}
              />
            </div>
            <div className="form-field">
              <label>Downtime (hours)</label>
              <input type="number" name="downtime_hours" min="0" step="0.1" />
            </div>
            <div className="form-field" style={{ gridColumn: "1 / -1" }}>
              <label>Issue</label>
              <input name="issue" placeholder="What was reported/observed" />
            </div>
            <div className="form-field" style={{ gridColumn: "1 / -1" }}>
              <label>Summary / action taken</label>
              <textarea name="summary" rows={2} placeholder="What was done" />
            </div>
            <div className="form-field">
              <label>Parts used</label>
              <input name="parts_used" />
            </div>
            <div className="form-field">
              <label>Next action</label>
              <input name="next_action" />
            </div>
          </div>
          {formError && <div className="field-error">{formError}</div>}
          <button className="btn btn--primary" type="submit">
            Save visit
          </button>
        </form>
      )}

      <div className="toolbar">
        <input
          placeholder="Search summary..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={assetFilter} onChange={(e) => setAssetFilter(e.target.value)}>
          <option value="">All assets</option>
          {assets.map((a) => (
            <option key={a.id} value={a.id}>
              {a.asset_number}
            </option>
          ))}
        </select>
        <select value={visitTypeFilter} onChange={(e) => setVisitTypeFilter(e.target.value)}>
          <option value="">All visit types</option>
          {visitTypes.map((t) => (
            <option key={t.id} value={t.name}>
              {t.name}
            </option>
          ))}
        </select>
        <select value={branchFilter} onChange={(e) => setBranchFilter(e.target.value)}>
          <option value="">All branches</option>
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.is_central_warehouse ? "CENTRAL WAREHOUSE" : b.name}
            </option>
          ))}
        </select>
      </div>

      {actionError && <ErrorState message={actionError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {!loading && !error && (
        <>
          <DataTable<ServiceVisit>
            columns={[
              { key: "asset", header: "Asset", render: (v) => assetNumber(v.asset_id) },
              { key: "branch", header: "Branch / Warehouse", render: (v) => visitBranchLabel(v.asset_id) },
              {
                key: "visit_type",
                header: "Type",
                render: (v) => (v.visit_type ? <StatusBadge value={v.visit_type} /> : "—"),
              },
              { key: "visit_date", header: "Date", render: (v) => v.visit_date ?? "—" },
              { key: "vendor", header: "Vendor", render: (v) => vendorName(v.vendor_id) },
              { key: "issue", header: "Issue", render: (v) => v.issue ?? "—" },
              { key: "summary", header: "Summary", render: (v) => v.summary ?? "—" },
              { key: "downtime", header: "Downtime (hrs)", render: (v) => v.downtime_hours ?? "—" },
              { key: "cost", header: "Cost", render: (v) => (v.cost != null ? v.cost.toFixed(2) : "—") },
              {
                key: "actions",
                header: "Actions",
                render: (v) =>
                  canWrite ? (
                    <div style={{ display: "flex", gap: 8 }}>
                      <button className="btn btn--ghost" onClick={() => setEditingId(editingId === v.id ? null : v.id)}>
                        {editingId === v.id ? "Close" : "Edit"}
                      </button>
                      <button className="btn btn--ghost" onClick={() => setDeleteTarget(v)}>
                        Delete
                      </button>
                    </div>
                  ) : (
                    "—"
                  ),
              },
            ]}
            rows={visits}
            emptyLabel="No service visits logged yet."
          />
          {editingId && (
            <form
              className="card"
              style={{ marginTop: 16 }}
              onSubmit={(e) => handleUpdate(e, editingId)}
            >
              {(() => {
                const visit = visits.find((v) => v.id === editingId);
                if (!visit) return null;
                return (
                  <div className="form-grid">
                    <div className="form-field">
                      <label>Visit type</label>
                      <select name="visit_type" defaultValue={visit.visit_type ?? ""}>
                        <option value="">Unspecified</option>
                        {visitTypes.map((t) => (
                          <option key={t.id} value={t.name}>
                            {t.name}
                          </option>
                        ))}
                        {/* Preserves an existing value the active-types list no longer has —
                            an inactive/legacy type or free-text value predating this table —
                            so re-saving without touching this field doesn't silently blank it. */}
                        {visit.visit_type && !visitTypes.some((t) => t.name === visit.visit_type) && (
                          <option value={visit.visit_type}>{visit.visit_type} (inactive)</option>
                        )}
                      </select>
                    </div>
                    <div className="form-field">
                      <label>Vendor</label>
                      <select name="vendor_id" defaultValue={visit.vendor_id ?? ""}>
                        <option value="">None (internal engineer)</option>
                        {vendors.map((v) => (
                          <option key={v.id} value={v.id}>
                            {v.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="form-field">
                      <label>Visit date</label>
                      <input type="date" name="visit_date" defaultValue={visit.visit_date ?? ""} />
                    </div>
                    <div className="form-field">
                      <label>Cost</label>
                      <input type="number" name="cost" min="0" step="0.01" defaultValue={visit.cost ?? ""} />
                    </div>
                    <div className="form-field">
                      <label>Downtime (hours)</label>
                      <input type="number" name="downtime_hours" min="0" step="0.1" defaultValue={visit.downtime_hours ?? ""} />
                    </div>
                    <div className="form-field" style={{ gridColumn: "1 / -1" }}>
                      <label>Issue</label>
                      <input name="issue" defaultValue={visit.issue ?? ""} />
                    </div>
                    <div className="form-field" style={{ gridColumn: "1 / -1" }}>
                      <label>Summary / action taken</label>
                      <textarea name="summary" rows={2} defaultValue={visit.summary ?? ""} />
                    </div>
                    <div className="form-field">
                      <label>Parts used</label>
                      <input name="parts_used" defaultValue={visit.parts_used ?? ""} />
                    </div>
                    <div className="form-field">
                      <label>Next action</label>
                      <input name="next_action" defaultValue={visit.next_action ?? ""} />
                    </div>
                  </div>
                );
              })()}
              {editError && <div className="field-error">{editError}</div>}
              <button className="btn btn--primary" type="submit">
                Save changes
              </button>
            </form>
          )}
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete service visit"
        message={`Delete this service visit record${deleteTarget?.summary ? ` ("${deleteTarget.summary}")` : ""}? This cannot be undone.`}
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </ProtectedShell>
  );
}
