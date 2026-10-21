"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { SearchBar } from "../../components/SearchBar";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { usePaginatedResource } from "../../hooks/usePaginatedResource";
import { useLookup } from "../../hooks/useLookup";
import { useAuth } from "../../hooks/useAuth";
import { useAssetVendorMap } from "../../hooks/useDeviceVendorInfo";
import { api, ApiError } from "../../lib/api";
import type { DeviceCategory, DeviceModel, Vendor } from "../../types";
import { DEVICE_CATEGORIES } from "../../types";

export default function DeviceModelsPage() {
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState<"active" | "archived">("active");
  const { data, page, setPage, loading, error, reload } = usePaginatedResource<DeviceModel>(
    "/api/v1/device-models",
    { ...(search ? { search } : {}), is_active: activeTab === "active" ? "true" : "false" }
  );
  const categories = useLookup<DeviceCategory>("/api/v1/device-categories");
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const vendorByModel = useAssetVendorMap(vendors);
  const { hasPermission } = useAuth();
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const canWrite = hasPermission("device.write");
  const canForceDelete = hasPermission("dev.data_cleanup");
  const [rowError, setRowError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ id: string; message: string } | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [confirmArchiveId, setConfirmArchiveId] = useState<string | null>(null);

  async function handleArchiveToggle(model: DeviceModel & { is_active?: boolean }) {
    setRowError(null);
    setConflict(null);
    setRowBusy(model.id);
    try {
      const action = model.is_active === false ? "activate" : "archive";
      await api.post(`/api/v1/device-models/${model.id}/${action}`);
      setConfirmArchiveId(null);
      await reload();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to change device model status.");
    } finally {
      setRowBusy(null);
    }
  }

  async function handleDelete(modelId: string, force = false) {
    setRowError(null);
    setConflict(null);
    setRowBusy(modelId);
    try {
      await api.delete(`/api/v1/device-models/${modelId}${force ? "?force=true" : ""}`);
      setConfirmDeleteId(null);
      await reload();
    } catch (err) {
      setConfirmDeleteId(null);
      if (err instanceof ApiError && err.status === 409) {
        setConflict({ id: modelId, message: err.message });
      } else {
        setRowError(err instanceof ApiError ? err.message : "Failed to delete device model.");
      }
    } finally {
      setRowBusy(null);
    }
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      const categoryName = formData.get("category") as string;
      const matchedCategory = categories.find((c) => c.name === categoryName);
      await api.post("/api/v1/device-models", {
        name: formData.get("name"),
        manufacturer: formData.get("manufacturer") || null,
        model_number: formData.get("model_number") || null,
        category: categoryName,
        category_id: matchedCategory ? matchedCategory.id : null,
      });
      setShowForm(false);
      form.reset();
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create device model.");
    }
  }

  return (
    <ProtectedShell requiredPermission="device.read">
      <div className="page-header">
        <div>
          <h1>Device catalog</h1>
          <p>
            Manufacturer/model records (the catalog side of a device). To add a physical device end-to-end — model +
            asset in one flow — use <strong>Add device</strong>. Individual assets (serial numbers, branch, status)
            live on the <Link href="/assets">Devices</Link> page.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {hasPermission("device.write") && hasPermission("asset.write") && (
            <Link className="btn btn--primary" href="/devices/new">
              Add device
            </Link>
          )}
          {hasPermission("device.write") && (
            <button className="btn btn--ghost" onClick={() => setShowForm((v) => !v)}>
              {showForm ? "Cancel" : "New model only (catalog admin)"}
            </button>
          )}
          {hasPermission("import.create") && (
            <Link className="btn btn--ghost" href="/imports">
              Import device models
            </Link>
          )}
        </div>
      </div>

      {showForm && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleCreate}>
          <div className="form-grid">
            <div className="form-field">
              <label>Device name*</label>
              <input name="name" required />
            </div>
            <div className="form-field">
              <label>Manufacturer</label>
              <input name="manufacturer" />
            </div>
            <div className="form-field">
              <label>Model number</label>
              <input name="model_number" />
            </div>
            <div className="form-field">
              <label>Category*</label>
              <select name="category" required defaultValue="">
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
          {formError && <div className="field-error">{formError}</div>}
          <button className="btn btn--primary" type="submit">
            Save device model
          </button>
        </form>
      )}

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search by name, manufacturer, model..." />
        <div style={{ display: "flex", gap: 4 }}>
          <button
            className={activeTab === "active" ? "btn btn--primary" : "btn btn--ghost"}
            onClick={() => setActiveTab("active")}
          >
            Active
          </button>
          <button
            className={activeTab === "archived" ? "btn btn--primary" : "btn btn--ghost"}
            onClick={() => setActiveTab("archived")}
          >
            Archived
          </button>
        </div>
      </div>

      {conflict && (
        <div
          className="state-panel state-panel--error"
          style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 16 }}
        >
          <span>{conflict.message}</span>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              className="btn btn--primary"
              disabled={rowBusy === conflict.id}
              onClick={() => {
                const model = data?.items.find((m) => m.id === conflict.id);
                if (model) handleArchiveToggle(model).then(() => setConflict(null));
              }}
            >
              {rowBusy === conflict.id ? "Working..." : "Archive instead"}
            </button>
            {canForceDelete && (
              <button
                className="btn btn--danger"
                disabled={rowBusy === conflict.id}
                onClick={() => handleDelete(conflict.id, true)}
              >
                Force delete anyway
              </button>
            )}
          </div>
        </div>
      )}
      {rowError && <ErrorState message={rowError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<DeviceModel>
            columns={[
              {
                key: "name",
                header: "Device name",
                render: (m) => (
                  <>
                    <Link href={`/devices/${m.id}`}>{m.name}</Link>
                    {m.is_active === false && (
                      <span className="badge badge--neutral" style={{ marginLeft: 8 }}>
                        Archived
                      </span>
                    )}
                  </>
                ),
              },
              { key: "manufacturer", header: "Manufacturer", render: (m) => m.manufacturer ?? "Not provided" },
              { key: "model_number", header: "Model number", render: (m) => m.model_number ?? "Not provided" },
              {
                key: "category",
                header: "Category",
                render: (m) => categories.find((c) => c.id === m.category_id)?.name ?? m.category,
              },
              { key: "vendor", header: "Vendor", render: (m) => vendorByModel.get(m.id)?.name ?? "Not linked" },
              {
                key: "units",
                header: "Physical units",
                render: (m) => `${m.total_units} total · ${m.available_units} in central stock`,
              },
              {
                key: "model_information_status",
                header: "Model info status",
                render: (m) => <StatusBadge value={m.model_information_status} />,
              },
              {
                key: "catalog_match_status",
                header: "Catalog match",
                render: (m) => <StatusBadge value={m.catalog_match_status} />,
              },
              ...(canWrite
                ? [
                    {
                      key: "actions",
                      header: "Actions",
                      render: (m: DeviceModel) => (
                        <div style={{ display: "flex", gap: 6 }}>
                          <Link className="btn btn--ghost" href={`/devices/${m.id}`}>
                            Edit
                          </Link>
                          <button
                            className="btn btn--ghost"
                            disabled={rowBusy === m.id}
                            onClick={() => (m.is_active === false ? handleArchiveToggle(m) : setConfirmArchiveId(m.id))}
                          >
                            {m.is_active === false ? "Reactivate" : "Archive"}
                          </button>
                          <button
                            className="btn btn--ghost"
                            disabled={rowBusy === m.id}
                            onClick={() => setConfirmDeleteId(m.id)}
                          >
                            Delete
                          </button>
                        </div>
                      ),
                    },
                  ]
                : []),
            ]}
            rows={data.items}
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={confirmArchiveId !== null}
        title="Archive device model"
        message="Archiving hides this model from new asset/requirement selections while keeping all existing history. It can be reactivated later. Continue?"
        confirmLabel="Archive"
        onConfirm={() => {
          const model = data?.items.find((m) => m.id === confirmArchiveId);
          if (model) handleArchiveToggle(model);
        }}
        onCancel={() => setConfirmArchiveId(null)}
      />
      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete device model"
        message={
          canForceDelete
            ? "This permanently deletes the device model. If it's still referenced by requirements, spare-part links, vendors, branch assignments, documents, or assets, you'll be offered a force-delete — including every asset on this model and its full maintenance/procurement history. This cannot be undone. Continue?"
            : "This permanently deletes the device model. This only succeeds if no record currently references it — if it's in use, you'll be asked to archive instead. Continue?"
        }
        confirmLabel="Delete"
        onConfirm={() => confirmDeleteId && handleDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
