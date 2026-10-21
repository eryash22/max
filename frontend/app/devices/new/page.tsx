"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { ErrorState } from "../../../components/ErrorState";
import { useLookup } from "../../../hooks/useLookup";
import { api, ApiError } from "../../../lib/api";
import type { Branch, DeviceCategory, DeviceCreateResult, DeviceModel, Department, Location } from "../../../types";
import { DEVICE_CATEGORIES, LIFECYCLE_STATUSES } from "../../../types";

// Restructure consolidation pass, Part D — one combined "Add Device" flow instead of
// the old two-step Device Model -> Asset journey. Calls the new composite
// POST /api/v1/devices endpoint (devices.py), which reuses an existing DeviceModel or
// creates one inline, then always creates the Asset in the same request. AMC/CMC/
// Warranty are intentionally NOT collected here — they're derived live from the
// existing Contract module (Part F) and set up from the Asset detail page after
// creation, to avoid duplicating contract data onto Asset.
export default function NewDevicePage() {
  const router = useRouter();
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const categories = useLookup<DeviceCategory>("/api/v1/device-categories");
  // status_filter=ACTIVE — a deactivated branch/department/location must not be
  // selectable when creating a new device/asset (see backend/app/routers/hospitals.py).
  const branches = useLookup<Branch>("/api/v1/branches?status_filter=ACTIVE");
  const departments = useLookup<Department>("/api/v1/departments?status_filter=ACTIVE");
  const locations = useLookup<Location>("/api/v1/locations?status_filter=ACTIVE");

  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [selectedModelId, setSelectedModelId] = useState("");
  const [branchId, setBranchId] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const filteredDepartments = departments.filter((d) => !branchId || d.branch_id === branchId);
  const filteredLocations = locations.filter((l) => !departmentId || l.department_id === departmentId);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);

    const payload: Record<string, unknown> = {
      branch_id: formData.get("branch_id"),
      department_id: formData.get("department_id") || null,
      location_id: formData.get("location_id") || null,
      serial_number: formData.get("serial_number") || null,
      asset_number: formData.get("asset_number") || null,
      status: formData.get("status") || "Pending",
      operational_since: formData.get("operational_since") || null,
      installation_date: formData.get("installation_date") || null,
      criticality: formData.get("criticality") || null,
    };

    if (mode === "existing") {
      payload.device_model_id = formData.get("device_model_id");
    } else {
      const categoryName = formData.get("category_name") as string;
      payload.model_name = formData.get("model_name");
      payload.manufacturer = formData.get("manufacturer") || null;
      payload.model_number = formData.get("model_number") || null;
      payload.category = categoryName;
      const matchedCategory = categories.find((c) => c.name === categoryName);
      payload.category_id = matchedCategory ? matchedCategory.id : null;
    }

    try {
      const result = await api.post<DeviceCreateResult>("/api/v1/devices", payload);
      router.push(`/assets/${result.asset.id}`);
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create device.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ProtectedShell requiredPermission="device.read">
      <div className="page-header">
        <div>
          <h1>Register physical device</h1>
          <p>
            Pick an existing device model (or add a new one inline), then register the physical machine itself —
            its own Asset Number, serial number, and current location. Already have this machine and just need to
            move it to a different branch? Use{" "}
            <a href="/inventory/allocate">Allocate Device to Branch</a> instead — this page always creates a new
            physical asset.
          </p>
        </div>
      </div>

      {formError && <ErrorState message={formError} />}

      <form onSubmit={handleSubmit} className="card">
        <h3 style={{ marginTop: 0 }}>Device information</h3>

        <div className="form-field form-field--checkbox" style={{ marginBottom: 12 }}>
          <label>
            <input
              type="radio"
              name="mode"
              checked={mode === "existing"}
              onChange={() => setMode("existing")}
            />{" "}
            Use an existing device model
          </label>
          <label style={{ marginLeft: 16 }}>
            <input type="radio" name="mode" checked={mode === "new"} onChange={() => setMode("new")} /> Add a new
            device model
          </label>
        </div>

        {mode === "existing" ? (
          <div className="form-grid">
            <div className="form-field">
              <label>Device model*</label>
              <select
                name="device_model_id"
                required
                value={selectedModelId}
                onChange={(e) => setSelectedModelId(e.target.value)}
              >
                <option value="" disabled>
                  Select device model
                </option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name} {m.manufacturer ? `(${m.manufacturer})` : ""}
                  </option>
                ))}
              </select>
            </div>
          </div>
        ) : (
          <div className="form-grid">
            <div className="form-field">
              <label>Device name*</label>
              <input name="model_name" required />
            </div>
            <div className="form-field">
              <label>Manufacturer / Brand</label>
              <input name="manufacturer" />
            </div>
            <div className="form-field">
              <label>Model number</label>
              <input name="model_number" />
            </div>
            <div className="form-field">
              <label>Device category*</label>
              <select name="category_name" required defaultValue="">
                <option value="" disabled>
                  Select category
                </option>
                {DEVICE_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}

        <h3>Physical asset</h3>
        <div className="form-grid">
          <div className="form-field">
            <label>Serial number</label>
            <input name="serial_number" />
          </div>
          <div className="form-field">
            <label>Asset number (optional)</label>
            <input name="asset_number" placeholder="Optional — can be added later" />
          </div>
          <div className="form-field">
            <label>Branch / warehouse (current location)*</label>
            <select
              name="branch_id"
              required
              value={branchId}
              onChange={(e) => {
                setBranchId(e.target.value);
                setDepartmentId("");
              }}
            >
              <option value="" disabled>
                Select branch
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
            <select name="department_id" value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
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
            <select name="location_id">
              <option value="">Not set</option>
              {filteredLocations.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Current status</label>
            <input name="status" list="lifecycle-statuses" defaultValue="Pending" />
            <datalist id="lifecycle-statuses">
              {LIFECYCLE_STATUSES.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </div>
          <div className="form-field">
            <label>Operational since</label>
            <input name="operational_since" type="date" />
          </div>
          <div className="form-field">
            <label>Installation date</label>
            <input name="installation_date" type="date" />
          </div>
          <div className="form-field">
            <label>Criticality</label>
            <input name="criticality" />
          </div>
        </div>

        <div className="card" style={{ background: "var(--color-surface-elevated)", marginTop: 12 }}>
          <strong>Vendor / AMC / CMC / Warranty</strong>
          <p style={{ marginBottom: 0 }}>
            Coverage isn&apos;t set here — it&apos;s always derived live from the Vendor and Contract modules (never
            duplicated onto the device) so it can&apos;t drift. After saving, you&apos;ll land on the asset&apos;s
            detail page, which shows current vendor/AMC/CMC/warranty status and lets you attach a contract.
          </p>
        </div>

        <div className="form-field" style={{ justifyContent: "flex-end", marginTop: 16 }}>
          <button className="btn btn--primary" type="submit" disabled={busy}>
            {busy ? "Saving..." : "Save device"}
          </button>
        </div>
      </form>
    </ProtectedShell>
  );
}
