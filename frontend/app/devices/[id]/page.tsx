"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { api, ApiError } from "../../../lib/api";
import type { DeviceCategory, DeviceModel } from "../../../types";
import { DEVICE_CATEGORIES } from "../../../types";

export default function DeviceModelDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("device.write");

  const categories = useLookup<DeviceCategory>("/api/v1/device-categories");
  const categoryName = (m: DeviceModel) => categories.find((c) => c.id === m.category_id)?.name ?? m.category;

  const [model, setModel] = useState<DeviceModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await api.get<DeviceModel>(`/api/v1/device-models/${params.id}`);
      setModel(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load device model.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!model) return;
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    const categoryValue = (formData.get("category") as string) || model.category;
    const matchedCategory = categories.find((c) => c.name === categoryValue);
    try {
      // The backend PATCH replaces the full record, so every field from the
      // currently-loaded model is carried forward and only the ones the form
      // exposes are overridden — otherwise fields like model_information_status
      // or catalog_match_status would silently reset to their defaults.
      await api.patch(`/api/v1/device-models/${params.id}`, {
        ...model,
        name: formData.get("name"),
        manufacturer: formData.get("manufacturer") || null,
        model_number: formData.get("model_number") || null,
        category: categoryValue,
        category_id: matchedCategory ? matchedCategory.id : model.category_id,
        notes: formData.get("notes") || null,
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update device model.");
    } finally {
      setBusy(false);
    }
  }

  async function handleArchiveToggle() {
    if (!model) return;
    setConfirmArchive(false);
    setActionError(null);
    setBusy(true);
    try {
      const action = model.is_active === false ? "activate" : "archive";
      await api.post(`/api/v1/device-models/${model.id}/${action}`);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to change device model status.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!model) return;
    setConfirmDelete(false);
    setActionError(null);
    setBusy(true);
    try {
      await api.delete(`/api/v1/device-models/${model.id}`);
      router.push("/devices");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to delete device model.");
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return (
      <ProtectedShell requiredPermission="device.read">
        <ErrorState message={error} />
      </ProtectedShell>
    );
  }
  if (!model) {
    return (
      <ProtectedShell requiredPermission="device.read">
        <LoadingState />
      </ProtectedShell>
    );
  }

  return (
    <ProtectedShell requiredPermission="device.read">
      <div className="page-header">
        <div>
          <h1>{model.name}</h1>
          <p>
            {categoryName(model)}
            {model.is_active === false && (
              <span className="badge badge--neutral" style={{ marginLeft: 8 }}>
                Archived
              </span>
            )}
            {" · "}
            <Link href="/devices">Back to Device catalog</Link>
          </p>
        </div>
        {canWrite && (
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--ghost" onClick={() => setConfirmArchive(true)} disabled={busy}>
              {model.is_active === false ? "Reactivate" : "Archive"}
            </button>
            {model.is_active !== false && (
              <button className="btn btn--danger" onClick={() => setConfirmDelete(true)} disabled={busy}>
                Delete device model
              </button>
            )}
          </div>
        )}
      </div>

      {actionError && <ErrorState message={actionError} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Catalog information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Name</td>
              <td>{model.name}</td>
            </tr>
            <tr>
              <td>Manufacturer</td>
              <td>{model.manufacturer ?? "Not provided"}</td>
            </tr>
            <tr>
              <td>Model number</td>
              <td>{model.model_number ?? "Not provided"}</td>
            </tr>
            <tr>
              <td>Category</td>
              <td>{categoryName(model)}</td>
            </tr>
            <tr>
              <td>Model info status</td>
              <td>
                <StatusBadge value={model.model_information_status} />
              </td>
            </tr>
            <tr>
              <td>Catalog match</td>
              <td>
                <StatusBadge value={model.catalog_match_status} />
              </td>
            </tr>
            <tr>
              <td>Known/opening quantity</td>
              <td>{model.known_quantity ?? "-"}</td>
            </tr>
            <tr>
              <td>Status</td>
              <td>{model.is_active === false ? <span className="badge badge--neutral">Archived</span> : <span className="badge badge--success">Active</span>}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Edit device model</h3>
          <form onSubmit={handleUpdate} className="form-grid">
            <div className="form-field">
              <label>Device name*</label>
              <input name="name" required defaultValue={model.name} />
            </div>
            <div className="form-field">
              <label>Manufacturer</label>
              <input name="manufacturer" defaultValue={model.manufacturer ?? ""} />
            </div>
            <div className="form-field">
              <label>Model number</label>
              <input name="model_number" defaultValue={model.model_number ?? ""} />
            </div>
            <div className="form-field">
              <label>Category*</label>
              {/* Category is free text at the database level — many existing models
                  (imported/legacy data) carry a value outside the canonical list
                  below. A strict <select> would silently replace an unrecognized
                  value with whatever option renders first on save, so this uses a
                  free-text input with the canonical list only as suggestions. */}
              <input name="category" required list="device-category-options" defaultValue={categoryName(model)} />
              <datalist id="device-category-options">
                {DEVICE_CATEGORIES.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </div>
            <div className="form-field">
              <label>Notes</label>
              <input name="notes" defaultValue={model.notes ?? ""} />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Save changes
              </button>
            </div>
          </form>
        </div>
      )}

      <ConfirmDialog
        open={confirmArchive}
        title={model.is_active === false ? "Reactivate device model" : "Archive device model"}
        message={
          model.is_active === false
            ? "Reactivating makes this model selectable again on new asset/requirement forms. Continue?"
            : "Archiving hides this model from new asset/requirement selections while keeping all existing history. It can be reactivated later. Continue?"
        }
        confirmLabel={model.is_active === false ? "Reactivate" : "Archive"}
        onConfirm={handleArchiveToggle}
        onCancel={() => setConfirmArchive(false)}
      />
      <ConfirmDialog
        open={confirmDelete}
        title="Delete device model"
        message="This permanently deletes the device model. This only succeeds if no asset currently references it — if it's in use, you'll be asked to archive instead. Continue?"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </ProtectedShell>
  );
}
