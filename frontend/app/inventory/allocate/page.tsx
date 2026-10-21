"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { ErrorState } from "../../../components/ErrorState";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { useLookup } from "../../../hooks/useLookup";
import { useAuth } from "../../../hooks/useAuth";
import { useAssetVendorMap } from "../../../hooks/useDeviceVendorInfo";
import { api, ApiError } from "../../../lib/api";
import type { Asset, Branch, Department, DeviceModel, Location, Page, Vendor } from "../../../types";

/**
 * Device Catalog / Allocate-to-Branch unification pass.
 *
 * Two clearly separate workflows, never mixed in one dropdown:
 *
 * A. "Allocate existing stock" — pick an already-registered physical Asset
 *    currently sitting in central warehouse stock (central_only=true) and move it
 *    to a branch via POST /api/v1/assets/{id}/allocate. UPDATEs the same Asset row
 *    in place — same Asset Number, serial number and QR identity throughout.
 *
 * B. "Register physical unit(s) & allocate" — pick an existing, active catalog
 *    DeviceModel (including ones with zero physical stock today, shown with live
 *    total/available/allocated counts from GET /device-models) and register one or
 *    more brand-new physical units under it directly into a destination branch,
 *    via POST /api/v1/assets (device_model_id + branch_id together — creation and
 *    allocation in the same request/transaction, QR identity auto-provisioned,
 *    never a second DeviceModel). Each row is an independent request with its own
 *    result, so one failing Asset Number never blocks or half-creates the others.
 */
type Mode = "existing" | "register";

interface UnitRow {
  key: number;
  assetNumber: string;
  serialNumber: string;
  result: "idle" | "creating" | "done" | "error";
  error: string | null;
  createdAssetId: string | null;
}

let rowKeySeq = 0;
function newRow(): UnitRow {
  return { key: ++rowKeySeq, assetNumber: "", serialNumber: "", result: "idle", error: null, createdAssetId: null };
}

export default function AllocateDevicePage() {
  const router = useRouter();
  const { hasPermission } = useAuth();
  const canForceDelete = hasPermission("dev.data_cleanup");
  const [mode, setMode] = useState<Mode>("existing");

  const branches = useLookup<Branch>("/api/v1/branches?status_filter=ACTIVE");
  const departments = useLookup<Department>("/api/v1/departments?status_filter=ACTIVE");
  const locations = useLookup<Location>("/api/v1/locations?status_filter=ACTIVE");
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const vendorByModel = useAssetVendorMap(vendors);

  const branchName = (id: string) => branches.find((b) => b.id === id)?.name ?? id;

  // ---- Section A: allocate existing central-stock asset ----
  const [search, setSearch] = useState("");
  const [candidates, setCandidates] = useState<Asset[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Asset | null>(null);
  const [branchId, setBranchId] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [rowBusyId, setRowBusyId] = useState<string | null>(null);
  const [rowActionError, setRowActionError] = useState<string | null>(null);
  const [confirmArchiveId, setConfirmArchiveId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  // Candidate asset model names resolved via the same catalog lookup Section B uses
  // (active models) plus whatever's already in the current candidate page, so a
  // candidate referencing an archived model still shows a readable name.
  const [modelsById, setModelsById] = useState<Map<string, DeviceModel>>(new Map());

  async function reloadCandidates() {
    setSearching(true);
    setSearchError(null);
    try {
      const params = new URLSearchParams({
        available_for_allocation: "true",
        central_only: "true",
        page: "1",
        page_size: "25",
      });
      if (search) params.set("search", search);
      const result = await api.get<Page<Asset>>(`/api/v1/assets?${params.toString()}`);
      setCandidates(result.items);
      const missingIds = Array.from(new Set(result.items.map((a) => a.device_model_id))).filter(
        (id) => !modelsById.has(id)
      );
      if (missingIds.length > 0) {
        const fetched = await Promise.all(
          missingIds.map((id) =>
            api.get<DeviceModel>(`/api/v1/device-models/${id}`).catch(() => null)
          )
        );
        setModelsById((prev) => {
          const next = new Map(prev);
          fetched.forEach((m) => {
            if (m) next.set(m.id, m);
          });
          return next;
        });
      }
    } catch (err) {
      setSearchError(err instanceof ApiError ? err.message : "Failed to search assets.");
    } finally {
      setSearching(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (mode !== "existing") return;
      await reloadCandidates();
    }
    load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, mode]);

  const modelName = (id: string) => modelsById.get(id)?.name ?? id;
  const filteredDepartments = departments.filter((d) => !branchId || d.branch_id === branchId);
  const filteredLocations = locations.filter((l) => !departmentId || l.department_id === departmentId);

  function selectAsset(asset: Asset) {
    setSelected(asset);
    setSubmitError(null);
    setBranchId("");
    setDepartmentId("");
    setLocationId("");
  }

  async function handleConfirm() {
    if (!selected || !branchId) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await api.post(`/api/v1/assets/${selected.id}/allocate`, {
        branch_id: branchId,
        department_id: departmentId || null,
        location_id: locationId || null,
      });
      router.push(`/assets/${selected.id}`);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : "Failed to allocate this device.");
    } finally {
      setSubmitting(false);
      setConfirmOpen(false);
    }
  }

  // "Deallocate" an existing candidate back to central stock is just the same
  // allocate endpoint pointed at a central-warehouse branch — never a separate
  // code path, so it can't drift from the one real allocation rule. Archive/
  // Force-delete reuse the exact same endpoints the Physical Assets list uses
  // (app/assets/page.tsx) rather than duplicating that logic here.
  async function handleArchive(asset: Asset) {
    setRowActionError(null);
    setRowBusyId(asset.id);
    try {
      await api.post(`/api/v1/assets/${asset.id}/archive`);
      setConfirmArchiveId(null);
      await reloadCandidates();
    } catch (err) {
      setRowActionError(err instanceof ApiError ? err.message : "Failed to retire this asset.");
    } finally {
      setRowBusyId(null);
    }
  }

  async function handleForceDelete(assetId: string) {
    setRowActionError(null);
    setRowBusyId(assetId);
    try {
      await api.delete(`/api/v1/assets/${assetId}?force=true`);
      setConfirmDeleteId(null);
      await reloadCandidates();
    } catch (err) {
      setConfirmDeleteId(null);
      setRowActionError(err instanceof ApiError ? err.message : "Failed to delete this asset.");
    } finally {
      setRowBusyId(null);
    }
  }

  // ---- Section B: register new unit(s) from an existing catalog model ----
  const [catalogSearch, setCatalogSearch] = useState("");
  const [catalogModels, setCatalogModels] = useState<DeviceModel[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [registerModel, setRegisterModel] = useState<DeviceModel | null>(null);
  const [registerBranchId, setRegisterBranchId] = useState("");
  const [registerDepartmentId, setRegisterDepartmentId] = useState("");
  const [registerLocationId, setRegisterLocationId] = useState("");
  const [rows, setRows] = useState<UnitRow[]>([newRow()]);
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== "register") return;
    let cancelled = false;
    setCatalogLoading(true);
    setCatalogError(null);
    const params = new URLSearchParams({ page: "1", page_size: "50" });
    if (catalogSearch) params.set("search", catalogSearch);
    api
      .get<Page<DeviceModel>>(`/api/v1/device-models?${params.toString()}`)
      .then((result) => {
        if (!cancelled) setCatalogModels(result.items);
      })
      .catch((err) => {
        if (!cancelled) setCatalogError(err instanceof ApiError ? err.message : "Failed to load device catalog.");
      })
      .finally(() => {
        if (!cancelled) setCatalogLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [catalogSearch, mode]);

  const registerFilteredDepartments = departments.filter((d) => !registerBranchId || d.branch_id === registerBranchId);
  const registerFilteredLocations = locations.filter(
    (l) => !registerDepartmentId || l.department_id === registerDepartmentId
  );

  function startRegistering(model: DeviceModel) {
    setRegisterModel(model);
    setRegisterBranchId("");
    setRegisterDepartmentId("");
    setRegisterLocationId("");
    setRows([newRow()]);
    setRegisterError(null);
  }

  function updateRow(key: number, patch: Partial<UnitRow>) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  function addRow() {
    setRows((prev) => [...prev, newRow()]);
  }

  function removeRow(key: number) {
    setRows((prev) => (prev.length > 1 ? prev.filter((r) => r.key !== key) : prev));
  }

  const canSubmitRegister =
    registerModel && registerBranchId && rows.some((r) => r.result !== "done") && !registering;

  async function handleRegisterSubmit() {
    if (!registerModel || !registerBranchId) return;
    setRegistering(true);
    setRegisterError(null);

    // Each row is its own independent POST /api/v1/assets request/transaction — a
    // row that fails (e.g. duplicate Asset Number) never rolls back or blocks the
    // others, and never leaves a half-created Asset: the backend either commits the
    // whole row (device_model_id + branch_id + QR provisioning) or none of it.
    for (const row of rows) {
      if (row.result === "done") continue;
      updateRow(row.key, { result: "creating", error: null });
      try {
        const created = await api.post<Asset>("/api/v1/assets", {
          device_model_id: registerModel.id,
          branch_id: registerBranchId,
          department_id: registerDepartmentId || null,
          location_id: registerLocationId || null,
          asset_number: row.assetNumber || null,
          serial_number: row.serialNumber || null,
          status: "OPERATIONAL",
        });
        updateRow(row.key, { result: "done", createdAssetId: created.id, error: null });
      } catch (err) {
        updateRow(row.key, {
          result: "error",
          error: err instanceof ApiError ? err.message : "Failed to register this unit.",
        });
      }
    }
    setRegistering(false);
  }

  const allRowsDone = rows.length > 0 && rows.every((r) => r.result === "done");

  return (
    <ProtectedShell requiredPermission="asset.write">
      <div className="page-header">
        <div>
          <h1>Allocate device to branch</h1>
          <p>
            Either move an already-registered physical device from central stock to a branch, or register one or
            more brand-new physical units of an existing catalog model directly into a branch. These are two
            separate actions — picking one never creates or moves the wrong thing.
          </p>
        </div>
      </div>

      <div className="form-field form-field--checkbox" style={{ marginBottom: 16 }}>
        <label>
          <input type="radio" checked={mode === "existing"} onChange={() => setMode("existing")} /> Allocate
          existing central stock
        </label>
        <label style={{ marginLeft: 16 }}>
          <input type="radio" checked={mode === "register"} onChange={() => setMode("register")} /> Register new
          unit(s) from Device Catalog &amp; allocate
        </label>
      </div>

      {mode === "existing" && (
        <>
          {!selected && (
            <div className="card" style={{ marginBottom: 16 }}>
              <h3 style={{ marginTop: 0 }}>1. Select an existing physical device (central warehouse stock)</h3>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by Asset Number, model name or serial number..."
                style={{ marginBottom: 12, width: "100%", maxWidth: 480 }}
              />
              {searching && <p className="dashboard-note">Searching...</p>}
              {searchError && <ErrorState message={searchError} />}
              {rowActionError && <ErrorState message={rowActionError} />}
              {!searching && candidates.length === 0 && (
                <div className="state-panel">
                  No eligible central-stock devices found. Condemned, retired and already-allocated devices are
                  excluded.
                </div>
              )}
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Asset Number</th>
                      <th>Device model</th>
                      <th>Current location</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {candidates.map((a) => (
                      <tr key={a.id}>
                        <td>{a.asset_number ?? "Asset number not assigned"}</td>
                        <td>{modelName(a.device_model_id)}</td>
                        <td>{branchName(a.branch_id)}</td>
                        <td>
                          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                            <button className="btn btn--primary" onClick={() => selectAsset(a)}>
                              Select &amp; allocate
                            </button>
                            <button
                              className="btn btn--ghost"
                              disabled={rowBusyId === a.id}
                              onClick={() => setConfirmArchiveId(a.id)}
                            >
                              Retire
                            </button>
                            {canForceDelete && (
                              <button
                                className="btn btn--danger"
                                disabled={rowBusyId === a.id}
                                onClick={() => setConfirmDeleteId(a.id)}
                              >
                                Delete
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {selected && (
            <div className="card">
              <h3 style={{ marginTop: 0 }}>2. Allocate</h3>
              <p>
                <strong>
                  {modelName(selected.device_model_id)} — {selected.asset_number ?? "Asset number not assigned"}
                </strong>
                {selected.serial_number ? ` (S/N ${selected.serial_number})` : ""} — currently at{" "}
                {branchName(selected.branch_id)}
              </p>
              <button className="btn btn--ghost" onClick={() => setSelected(null)} style={{ marginBottom: 16 }}>
                Remove this selection — choose a different device
              </button>

              <div className="form-grid">
                <div className="form-field">
                  <label>Destination branch*</label>
                  <select
                    value={branchId}
                    onChange={(e) => {
                      setBranchId(e.target.value);
                      setDepartmentId("");
                      setLocationId("");
                    }}
                    required
                  >
                    <option value="" disabled>
                      Select destination branch
                    </option>
                    {branches.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-field">
                  <label>Department</label>
                  <select
                    value={departmentId}
                    onChange={(e) => {
                      setDepartmentId(e.target.value);
                      setLocationId("");
                    }}
                  >
                    <option value="">Not set</option>
                    {filteredDepartments.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-field">
                  <label>Location</label>
                  <select value={locationId} onChange={(e) => setLocationId(e.target.value)}>
                    <option value="">Not set</option>
                    {filteredLocations.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {submitError && <div className="field-error">{submitError}</div>}
              <button
                className="btn btn--primary"
                disabled={!branchId || submitting}
                onClick={() => setConfirmOpen(true)}
                style={{ marginTop: 12 }}
              >
                Review &amp; allocate
              </button>
            </div>
          )}
        </>
      )}

      {mode === "register" && (
        <>
          {!registerModel && (
            <div className="card" style={{ marginBottom: 16 }}>
              <h3 style={{ marginTop: 0 }}>1. Pick a Device Catalog model</h3>
              <p className="dashboard-note">
                Every active catalog model is shown, including ones with no physical units yet — registering here
                creates a brand-new physical Asset under the model you pick, it never creates a second catalog
                entry.
              </p>
              <input
                value={catalogSearch}
                onChange={(e) => setCatalogSearch(e.target.value)}
                placeholder="Search by model name, manufacturer or model number..."
                style={{ marginBottom: 12, width: "100%", maxWidth: 480 }}
              />
              {catalogLoading && <p className="dashboard-note">Loading catalog...</p>}
              {catalogError && <ErrorState message={catalogError} />}
              {!catalogLoading && catalogModels.length === 0 && (
                <div className="state-panel">No active catalog models match this search.</div>
              )}
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th>Manufacturer</th>
                      <th>Vendor</th>
                      <th>Total units</th>
                      <th>Available (central)</th>
                      <th>Allocated</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {catalogModels.map((m) => (
                      <tr key={m.id}>
                        <td>{m.name}</td>
                        <td>{m.manufacturer ?? "-"}</td>
                        <td>{vendorByModel.get(m.id)?.name ?? "-"}</td>
                        <td>{m.total_units}</td>
                        <td>{m.available_units}</td>
                        <td>{m.allocated_units}</td>
                        <td>
                          <button className="btn btn--primary" onClick={() => startRegistering(m)}>
                            Register physical unit &amp; allocate
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {registerModel && (
            <div className="card">
              <h3 style={{ marginTop: 0 }}>2. Register physical unit(s) &amp; allocate</h3>
              <p>
                <strong>{registerModel.name}</strong>
                {registerModel.manufacturer ? ` (${registerModel.manufacturer})` : ""} — currently{" "}
                {registerModel.total_units} physical unit(s) registered, {registerModel.available_units} available
                in central stock.
              </p>
              <button
                className="btn btn--ghost"
                onClick={() => setRegisterModel(null)}
                style={{ marginBottom: 16 }}
                disabled={registering}
              >
                Remove this selection — choose a different model
              </button>

              <div className="form-grid">
                <div className="form-field">
                  <label>Destination branch*</label>
                  <select
                    value={registerBranchId}
                    onChange={(e) => {
                      setRegisterBranchId(e.target.value);
                      setRegisterDepartmentId("");
                      setRegisterLocationId("");
                    }}
                    disabled={registering}
                    required
                  >
                    <option value="" disabled>
                      Select destination branch
                    </option>
                    {branches.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-field">
                  <label>Department</label>
                  <select
                    value={registerDepartmentId}
                    onChange={(e) => {
                      setRegisterDepartmentId(e.target.value);
                      setRegisterLocationId("");
                    }}
                    disabled={registering}
                  >
                    <option value="">Not set</option>
                    {registerFilteredDepartments.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-field">
                  <label>Location</label>
                  <select
                    value={registerLocationId}
                    onChange={(e) => setRegisterLocationId(e.target.value)}
                    disabled={registering}
                  >
                    <option value="">Not set</option>
                    {registerFilteredLocations.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <h4>Physical units</h4>
              <p className="dashboard-note">
                Add one row per physical machine — each gets its own Asset Number and its own QR identity. Asset
                Number is optional per row but, if given, must be unique across the whole organization.
              </p>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Asset Number</th>
                      <th>Serial number</th>
                      <th>Result</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.key}>
                        <td>
                          <input
                            value={row.assetNumber}
                            onChange={(e) => updateRow(row.key, { assetNumber: e.target.value })}
                            placeholder="Optional — can be added later"
                            disabled={row.result === "done" || registering}
                          />
                        </td>
                        <td>
                          <input
                            value={row.serialNumber}
                            onChange={(e) => updateRow(row.key, { serialNumber: e.target.value })}
                            disabled={row.result === "done" || registering}
                          />
                        </td>
                        <td>
                          {row.result === "idle" && "-"}
                          {row.result === "creating" && "Creating..."}
                          {row.result === "done" && (
                            <span style={{ color: "var(--color-success, green)" }}>
                              Created
                              {row.createdAssetId && (
                                <>
                                  {" "}
                                  — <a href={`/assets/${row.createdAssetId}`}>view</a>
                                </>
                              )}
                            </span>
                          )}
                          {row.result === "error" && <span className="field-error">{row.error}</span>}
                        </td>
                        <td>
                          <button
                            className="btn btn--ghost"
                            disabled={rows.length === 1 || registering || row.result === "done"}
                            onClick={() => removeRow(row.key)}
                            title="Remove this row from the current selection — never deletes a created Asset"
                          >
                            Remove row
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <button className="btn btn--ghost" onClick={addRow} disabled={registering} style={{ marginTop: 8 }}>
                + Add another unit
              </button>

              {registerError && <div className="field-error">{registerError}</div>}
              <div style={{ marginTop: 16 }}>
                {allRowsDone ? (
                  <button className="btn btn--primary" onClick={() => setRegisterModel(null)}>
                    Done — register more units
                  </button>
                ) : (
                  <button className="btn btn--primary" disabled={!canSubmitRegister} onClick={handleRegisterSubmit}>
                    {registering ? "Registering..." : "Register & allocate"}
                  </button>
                )}
              </div>
            </div>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmOpen}
        title="Confirm allocation"
        message={
          selected
            ? `Allocate ${modelName(selected.device_model_id)} — ${
                selected.asset_number ?? "Asset number not assigned"
              } to ${branchName(branchId)}? This updates the same asset record and is recorded in its history.`
            : ""
        }
        confirmLabel="Allocate"
        onConfirm={handleConfirm}
        onCancel={() => setConfirmOpen(false)}
      />

      <ConfirmDialog
        open={confirmArchiveId !== null}
        title="Retire this asset"
        message="Retire this physical asset? This never deletes it, its history, or its QR identity — it only marks it RETIRED and removes it from allocation candidates. Reverse this from the Physical Assets page at any time."
        confirmLabel="Retire"
        onConfirm={() => {
          const asset = candidates.find((a) => a.id === confirmArchiveId);
          if (asset) handleArchive(asset);
        }}
        onCancel={() => setConfirmArchiveId(null)}
      />

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Permanently delete this asset"
        message="This permanently deletes the asset and every maintenance/compliance/procurement record linked to it. This cannot be undone and should only be used for a genuinely unused, mistakenly-created record — never to remove a real asset from this list."
        confirmLabel="Delete permanently"
        onConfirm={() => confirmDeleteId && handleForceDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
