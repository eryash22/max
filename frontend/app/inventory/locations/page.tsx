"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { DataTable } from "../../../components/DataTable";
import { Pagination } from "../../../components/Pagination";
import { StatusBadge } from "../../../components/StatusBadge";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { api, ApiError } from "../../../lib/api";
import type { Branch, InventoryLocation, Page } from "../../../types";
import { INVENTORY_LOCATION_KINDS } from "../../../types";

const PAGE_SIZE = 25;

export default function InventoryLocationsPage() {
  const { hasPermission } = useAuth();
  const canRead = hasPermission("inventory.read");
  const canWrite = hasPermission("inventory.write");

  const branches = useLookup<Branch>("/api/v1/branches");
  const branchName = (id: string) => branches.find((b) => b.id === id)?.name ?? id;

  const [branchFilter, setBranchFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const [locations, setLocations] = useState<InventoryLocation[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<InventoryLocation | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (branchFilter) params.set("branch_id", branchFilter);
      if (kindFilter) params.set("kind", kindFilter);
      if (search) params.set("search", search);
      const result = await api.get<Page<InventoryLocation>>(`/api/v1/inventory/locations?${params.toString()}`);
      setLocations(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load storage locations.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [branchFilter, kindFilter, search, page]);

  useEffect(() => {
    setPage(1);
  }, [branchFilter, kindFilter, search]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/inventory/locations", {
        branch_id: formData.get("branch_id"),
        name: formData.get("name"),
        code: formData.get("code") || null,
        kind: formData.get("kind") || "WAREHOUSE",
      });
      setShowForm(false);
      form.reset();
      setPage(1);
      await load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create storage location.");
    }
  }

  async function handleUpdate(event: FormEvent<HTMLFormElement>, id: string) {
    event.preventDefault();
    setEditError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.patch(`/api/v1/inventory/locations/${id}`, {
        name: formData.get("name"),
        code: formData.get("code") || null,
        kind: formData.get("kind"),
      });
      setEditingId(null);
      await load();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : "Failed to update storage location.");
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setActionError(null);
    try {
      await api.delete(`/api/v1/inventory/locations/${deleteTarget.id}`);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to delete storage location.");
      setDeleteTarget(null);
    }
  }

  if (!canRead) {
    return (
      <ProtectedShell>
        <ErrorState message="You do not have permission to view storage locations." />
      </ProtectedShell>
    );
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Storage locations</h1>
          <p>
            Warehouses, bins, and stores that inventory stock transactions can be recorded
            against — distinct from hospital wards/rooms.
          </p>
        </div>
        {canWrite && (
          <button className="btn btn--primary" onClick={() => setShowForm((v) => !v)}>
            {showForm ? "Cancel" : "New location"}
          </button>
        )}
      </div>

      {showForm && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleCreate}>
          <div className="form-grid">
            <div className="form-field">
              <label>Branch*</label>
              <select name="branch_id" required defaultValue="">
                <option value="" disabled>
                  Select a branch
                </option>
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required placeholder="e.g. Central Warehouse" />
            </div>
            <div className="form-field">
              <label>Code</label>
              <input name="code" placeholder="Optional, e.g. WH-1" />
            </div>
            <div className="form-field">
              <label>Kind</label>
              <select name="kind" defaultValue="WAREHOUSE">
                {INVENTORY_LOCATION_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {formError && <div className="field-error">{formError}</div>}
          <button className="btn btn--primary" type="submit">
            Save location
          </button>
        </form>
      )}

      <div className="toolbar">
        <input placeholder="Search name or code..." value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={branchFilter} onChange={(e) => setBranchFilter(e.target.value)}>
          <option value="">All branches</option>
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
        <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
          <option value="">All kinds</option>
          {INVENTORY_LOCATION_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </div>

      {actionError && <ErrorState message={actionError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {!loading && !error && (
        <>
          <DataTable<InventoryLocation>
            columns={[
              { key: "name", header: "Name", render: (l) => l.name },
              { key: "code", header: "Code", render: (l) => l.code ?? "—" },
              { key: "kind", header: "Kind", render: (l) => <StatusBadge value={l.kind} /> },
              { key: "branch", header: "Branch", render: (l) => branchName(l.branch_id) },
              {
                key: "actions",
                header: "Actions",
                render: (l) =>
                  canWrite ? (
                    <div style={{ display: "flex", gap: 8 }}>
                      <button className="btn btn--ghost" onClick={() => setEditingId(editingId === l.id ? null : l.id)}>
                        {editingId === l.id ? "Close" : "Edit"}
                      </button>
                      <button className="btn btn--ghost" onClick={() => setDeleteTarget(l)}>
                        Delete
                      </button>
                    </div>
                  ) : (
                    "—"
                  ),
              },
            ]}
            rows={locations}
            emptyLabel="No storage locations configured yet."
          />
          {editingId && (
            <form className="card" style={{ marginTop: 16 }} onSubmit={(e) => handleUpdate(e, editingId)}>
              {(() => {
                const location = locations.find((l) => l.id === editingId);
                if (!location) return null;
                return (
                  <div className="form-grid">
                    <div className="form-field">
                      <label>Name*</label>
                      <input name="name" required defaultValue={location.name} />
                    </div>
                    <div className="form-field">
                      <label>Code</label>
                      <input name="code" defaultValue={location.code ?? ""} />
                    </div>
                    <div className="form-field">
                      <label>Kind</label>
                      <select name="kind" defaultValue={location.kind}>
                        {INVENTORY_LOCATION_KINDS.map((k) => (
                          <option key={k} value={k}>
                            {k}
                          </option>
                        ))}
                      </select>
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
        title="Delete storage location"
        message={`Delete "${deleteTarget?.name}"? This is blocked if any inventory transaction still references it.`}
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </ProtectedShell>
  );
}
