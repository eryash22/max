"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { DataTable } from "../../../components/DataTable";
import { Pagination } from "../../../components/Pagination";
import { StatusBadge } from "../../../components/StatusBadge";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { ReferenceConflictBanner } from "../../../components/ReferenceConflictBanner";
import { usePaginatedResource } from "../../../hooks/usePaginatedResource";
import { useAuth } from "../../../hooks/useAuth";
import { api, ApiError } from "../../../lib/api";
import type { DepartmentMaster } from "../../../types";

// Central Department Master + branch assignment — this is the "central definition"
// half (Physiotherapy, ICU, ... created ONCE); /departments is the "where it's used"
// half (which branches have it assigned). Kept as two pages, cross-linked, rather
// than one page trying to show both concepts at once — the exact confusion this
// work exists to remove.
export default function DepartmentMastersPage() {
  const { data, page, setPage, loading, error, reload } = usePaginatedResource<DepartmentMaster>(
    "/api/v1/department-masters"
  );
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("hospital.write");
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [editing, setEditing] = useState<DepartmentMaster | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ id: string; message: string } | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeactivateId, setConfirmDeactivateId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/department-masters", {
        name: formData.get("name"),
        code: formData.get("code") || null,
        status: "ACTIVE",
      });
      setShowForm(false);
      form.reset();
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create department master.");
    }
  }

  async function handleUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    setEditError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.patch(`/api/v1/department-masters/${editing.id}`, {
        name: formData.get("name"),
        code: formData.get("code") || null,
        status: editing.status,
      });
      setEditing(null);
      reload();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : "Failed to update department master.");
    }
  }

  async function handleActivateToggle(master: DepartmentMaster) {
    setRowError(null);
    setConflict(null);
    setRowBusy(master.id);
    try {
      const action = master.status === "INACTIVE" ? "activate" : "deactivate";
      await api.post(`/api/v1/department-masters/${master.id}/${action}`);
      setConfirmDeactivateId(null);
      await reload();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to change status.");
    } finally {
      setRowBusy(null);
    }
  }

  async function handleDelete(masterId: string) {
    setRowError(null);
    setConflict(null);
    setRowBusy(masterId);
    try {
      await api.delete(`/api/v1/department-masters/${masterId}`);
      setConfirmDeleteId(null);
      await reload();
    } catch (err) {
      setConfirmDeleteId(null);
      if (err instanceof ApiError && err.status === 409) {
        setConflict({ id: masterId, message: err.message });
      } else {
        setRowError(err instanceof ApiError ? err.message : "Failed to delete department master.");
      }
    } finally {
      setRowBusy(null);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Department Master</h1>
          <p>
            The central department catalog — create a department once here, then{" "}
            <Link href="/departments">assign it to branches</Link>.
          </p>
        </div>
        {canWrite && (
          <button
            className="btn btn--primary"
            onClick={() => {
              setEditing(null);
              setShowForm((v) => !v);
            }}
          >
            {showForm ? "Cancel" : "New department master"}
          </button>
        )}
      </div>

      {showForm && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleCreate}>
          <div className="form-grid">
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required placeholder="e.g. Physiotherapy" />
            </div>
            <div className="form-field">
              <label>Code</label>
              <input name="code" placeholder="e.g. PHY" />
            </div>
          </div>
          {formError && <div className="field-error">{formError}</div>}
          <button className="btn btn--primary" type="submit">
            Save department master
          </button>
        </form>
      )}

      {editing && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleUpdate}>
          <h3 style={{ marginTop: 0 }}>Edit department master</h3>
          <p className="field-hint">
            Renaming here updates every branch this department is assigned to — branches never hold their own
            independent name.
          </p>
          <div className="form-grid">
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required defaultValue={editing.name} />
            </div>
            <div className="form-field">
              <label>Code</label>
              <input name="code" defaultValue={editing.code ?? ""} />
            </div>
          </div>
          {editError && <div className="field-error">{editError}</div>}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--primary" type="submit">
              Save changes
            </button>
            <button className="btn btn--ghost" type="button" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {conflict && (
        <ReferenceConflictBanner
          message={conflict.message}
          actionLabel="Deactivate instead"
          busy={rowBusy === conflict.id}
          onAction={() => {
            const master = data?.items.find((m) => m.id === conflict.id);
            if (master) {
              handleActivateToggle(master).then(() => setConflict(null));
            }
          }}
        />
      )}
      {rowError && <ErrorState message={rowError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<DepartmentMaster>
            columns={[
              { key: "name", header: "Name", render: (m) => m.name },
              { key: "code", header: "Code", render: (m) => m.code ?? "-" },
              { key: "status", header: "Status", render: (m) => <StatusBadge value={m.status} /> },
              ...(canWrite
                ? [
                    {
                      key: "actions",
                      header: "Actions",
                      render: (m: DepartmentMaster) => (
                        <div style={{ display: "flex", gap: 6 }}>
                          <button
                            className="btn btn--ghost"
                            disabled={rowBusy === m.id}
                            onClick={() => {
                              setShowForm(false);
                              setEditing(m);
                            }}
                          >
                            Edit
                          </button>
                          <button
                            className="btn btn--ghost"
                            disabled={rowBusy === m.id}
                            onClick={() =>
                              m.status === "INACTIVE" ? handleActivateToggle(m) : setConfirmDeactivateId(m.id)
                            }
                          >
                            {m.status === "INACTIVE" ? "Reactivate" : "Deactivate"}
                          </button>
                          {m.status !== "INACTIVE" && (
                            <button
                              className="btn btn--ghost"
                              disabled={rowBusy === m.id}
                              onClick={() => setConfirmDeleteId(m.id)}
                            >
                              Delete
                            </button>
                          )}
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
        title="Deactivate department master"
        message="Deactivating stops this department from being offered for new branch assignments. Branches already assigned it keep working. Continue?"
        confirmLabel="Deactivate"
        onConfirm={() => {
          const master = data?.items.find((m) => m.id === confirmDeactivateId);
          if (master) handleActivateToggle(master);
        }}
        onCancel={() => setConfirmDeactivateId(null)}
      />
      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete department master"
        message="This permanently deletes the department master. This only succeeds if no branch currently has it assigned — if it's in use, you'll be asked to deactivate instead. Continue?"
        confirmLabel="Delete"
        onConfirm={() => confirmDeleteId && handleDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
