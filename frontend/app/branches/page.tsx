"use client";

import { useState, type FormEvent } from "react";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { SearchBar } from "../../components/SearchBar";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { ExportButtons } from "../../components/ExportButtons";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import Link from "next/link";
import { usePaginatedResource } from "../../hooks/usePaginatedResource";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type { Branch } from "../../types";

export default function BranchesPage() {
  const [search, setSearch] = useState("");
  const { data, page, setPage, loading, error, reload } = usePaginatedResource<Branch>("/api/v1/branches", {
    ...(search ? { search } : {}),
  });
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("hospital.write");
  const canForceDelete = hasPermission("dev.data_cleanup");
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ id: string; message: string } | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeactivateId, setConfirmDeactivateId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  async function handleActivateToggle(branch: Branch) {
    setRowError(null);
    setConflict(null);
    setRowBusy(branch.id);
    try {
      const action = branch.status === "INACTIVE" ? "activate" : "deactivate";
      await api.post(`/api/v1/branches/${branch.id}/${action}`);
      setConfirmDeactivateId(null);
      await reload();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to change branch status.");
    } finally {
      setRowBusy(null);
    }
  }

  async function handleDelete(branchId: string, force = false) {
    setRowError(null);
    setConflict(null);
    setRowBusy(branchId);
    try {
      await api.delete(`/api/v1/branches/${branchId}${force ? "?force=true" : ""}`);
      setConfirmDeleteId(null);
      await reload();
    } catch (err) {
      setConfirmDeleteId(null);
      if (err instanceof ApiError && err.status === 409) {
        // Referenced — surface the reason with a one-click archive fallback
        // instead of leaving the user to hunt for the Deactivate button.
        setConflict({ id: branchId, message: err.message });
      } else {
        setRowError(err instanceof ApiError ? err.message : "Failed to delete branch.");
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
      await api.post("/api/v1/branches", {
        name: formData.get("name"),
        code: formData.get("code") || null,
        region: formData.get("region") || null,
        state: formData.get("state") || null,
        city: formData.get("city") || null,
        address: formData.get("address") || null,
        unit_head: formData.get("unit_head") || null,
        tl: formData.get("tl") || null,
        branch_email: formData.get("branch_email") || null,
        branch_phone: formData.get("branch_phone") || null,
        branch_whatsapp: formData.get("branch_whatsapp") || null,
        status: "ACTIVE",
      });
      setShowForm(false);
      form.reset();
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create branch.");
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Branches</h1>
          <p>Hospital/branch master data. Codes stay NULL until an approved branch-code crosswalk is supplied.</p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Link href="/imports" className="btn btn--ghost">
            Import branches
          </Link>
          {hasPermission("hospital.write") && (
            <button className="btn btn--primary" onClick={() => setShowForm((v) => !v)}>
              {showForm ? "Cancel" : "New branch"}
            </button>
          )}
        </div>
      </div>

      {showForm && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleCreate}>
          <div className="form-grid">
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required />
            </div>
            <div className="form-field">
              <label>Code</label>
              <input name="code" />
            </div>
            <div className="form-field">
              <label>Region</label>
              <input name="region" />
            </div>
            <div className="form-field">
              <label>State</label>
              <input name="state" />
            </div>
            <div className="form-field">
              <label>City</label>
              <input name="city" />
            </div>
            <div className="form-field">
              <label>Address</label>
              <input name="address" />
            </div>
            <div className="form-field">
              <label>Unit Head</label>
              <input name="unit_head" />
            </div>
            <div className="form-field">
              <label>TL</label>
              <input name="tl" />
            </div>
            <div className="form-field">
              <label>Branch email</label>
              <input name="branch_email" type="email" />
            </div>
            <div className="form-field">
              <label>Branch phone</label>
              <input name="branch_phone" />
            </div>
            <div className="form-field">
              <label>Branch WhatsApp</label>
              <input name="branch_whatsapp" />
            </div>
          </div>
          {formError && <div className="field-error">{formError}</div>}
          <button className="btn btn--primary" type="submit">
            Save branch
          </button>
        </form>
      )}

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search by name or code..." />
      </div>

      <ExportButtons
        path="/api/v1/branches/export"
        filters={{ search }}
        recordCount={data?.total ?? 0}
        filenameStem="branches_export"
      />

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
                const branch = data?.items.find((b) => b.id === conflict.id);
                if (branch) handleActivateToggle(branch).then(() => setConflict(null));
              }}
            >
              {rowBusy === conflict.id ? "Working..." : "Deactivate instead"}
            </button>
            {canForceDelete && (
              <button className="btn btn--danger" disabled={rowBusy === conflict.id} onClick={() => handleDelete(conflict.id, true)}>
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
          <DataTable<Branch>
            columns={[
              {
                key: "name",
                header: "Name",
                render: (b) => (
                  <>
                    <Link href={`/branches/${b.id}`}>{b.name}</Link>
                    {b.is_central_warehouse && (
                      <span className="badge" style={{ marginLeft: 8 }}>
                        Central warehouse
                      </span>
                    )}
                  </>
                ),
              },
              { key: "code", header: "Code", render: (b) => b.code ?? "Not provided" },
              { key: "region", header: "Region", render: (b) => b.region ?? "-" },
              { key: "state", header: "State", render: (b) => b.state ?? "-" },
              { key: "status", header: "Status", render: (b) => <StatusBadge value={b.status} /> },
              {
                key: "inventory_mapping_status",
                header: "Inventory mapping",
                render: (b) => <StatusBadge value={b.inventory_mapping_status} />,
              },
              ...(canWrite
                ? [
                    {
                      key: "actions",
                      header: "Actions",
                      render: (b: Branch) => (
                        <div style={{ display: "flex", gap: 6 }}>
                          <Link className="btn btn--ghost" href={`/branches/${b.id}`}>
                            Edit
                          </Link>
                          <button
                            className="btn btn--ghost"
                            disabled={rowBusy === b.id}
                            onClick={() =>
                              b.status === "INACTIVE" ? handleActivateToggle(b) : setConfirmDeactivateId(b.id)
                            }
                          >
                            {b.status === "INACTIVE" ? "Reactivate" : "Deactivate"}
                          </button>
                          <button
                            className="btn btn--ghost"
                            disabled={rowBusy === b.id}
                            onClick={() => setConfirmDeleteId(b.id)}
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
        open={confirmDeactivateId !== null}
        title="Deactivate branch"
        message="Deactivating hides this branch from active selections while keeping all existing history. It can be reactivated later. Continue?"
        confirmLabel="Deactivate"
        onConfirm={() => {
          const branch = data?.items.find((b) => b.id === confirmDeactivateId);
          if (branch) handleActivateToggle(branch);
        }}
        onCancel={() => setConfirmDeactivateId(null)}
      />
      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete branch"
        message={
          canForceDelete
            ? "This permanently deletes the branch. If anything still references it, you'll be offered a force-delete that cascades through everything under this branch — assets and their full history, requirements, incidents, inventory. This cannot be undone. Continue?"
            : "This permanently deletes the branch. This only succeeds if nothing currently references it — if it's in use, you'll be asked to deactivate instead. Continue?"
        }
        confirmLabel="Delete"
        onConfirm={() => confirmDeleteId && handleDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
