"use client";

import { Fragment, useEffect, useState, type FormEvent } from "react";
import { ProtectedShell } from "../../components/ProtectedShell";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { StatusBadge } from "../../components/StatusBadge";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type { Branch, Department, Page, RoleRecord, UserRecord } from "../../types";
import { NOTIFICATION_PREFERENCES } from "../../types";

export default function UsersPage() {
  return (
    <ProtectedShell requiredPermission="users.manage">
      <UsersPageContent />
    </ProtectedShell>
  );
}

function UsersPageContent() {
  const { user: currentUser, hasPermission } = useAuth();
  const canForceDelete = hasPermission("dev.data_cleanup");

  const [users, setUsers] = useState<UserRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [roles, setRoles] = useState<RoleRecord[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [passwordEditId, setPasswordEditId] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccessId, setPasswordSuccessId] = useState<string | null>(null);

  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deleteConflict, setDeleteConflict] = useState<{ id: string; message: string } | null>(null);
  const [deleteBusyId, setDeleteBusyId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  async function load() {
    try {
      const [userList, roleList, branchPage, departmentPage] = await Promise.all([
        api.get<UserRecord[]>("/api/v1/users"),
        api.get<RoleRecord[]>("/api/v1/roles"),
        api.get<Page<Branch>>("/api/v1/branches?page_size=200"),
        api.get<Page<Department>>("/api/v1/departments?page_size=200"),
      ]);
      setUsers(userList);
      setRoles(roleList);
      setBranches(branchPage.items);
      setDepartments(departmentPage.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load users.");
    }
  }

  function branchName(branchId: string | null): string {
    if (!branchId) return "-";
    return branches.find((b) => b.id === branchId)?.name ?? branchId;
  }

  function departmentName(departmentId: string | null): string {
    if (!departmentId) return "-";
    return departments.find((d) => d.id === departmentId)?.name ?? departmentId;
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/users", {
        email: formData.get("email"),
        name: formData.get("name"),
        password: formData.get("password"),
        role_codes: [formData.get("role_code")],
        hospital_scope_id: formData.get("hospital_scope_id") || null,
      });
      setShowForm(false);
      form.reset();
      load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create user.");
    }
  }

  async function handleEditSubmit(userId: string, event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setEditError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    const isSelf = currentUser?.id === userId;
    try {
      await api.patch(`/api/v1/users/${userId}`, {
        name: formData.get("name") || null,
        email: formData.get("email") || null,
        phone: formData.get("phone") || null,
        whatsapp_number: formData.get("whatsapp_number") || null,
        department_id: formData.get("department_id") || null,
        notification_preference: formData.get("notification_preference") || null,
        // Self-protection: the "Active" checkbox is disabled in the form for the
        // currently authenticated user (see the field below), so this always sends
        // true for self — the backend also hard-blocks self-deactivation regardless.
        is_active: isSelf ? true : formData.get("is_active") === "on",
        role_codes: [formData.get("role_code")],
        hospital_scope_id: formData.get("hospital_scope_id") || null,
      });
      setEditingId(null);
      load();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : "Failed to update user.");
    }
  }

  async function handlePasswordSubmit(userId: string, event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPasswordError(null);
    setPasswordSuccessId(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post(`/api/v1/users/${userId}/password`, {
        new_password: formData.get("new_password"),
        confirm_password: formData.get("confirm_password"),
      });
      setPasswordSuccessId(userId);
      form.reset();
      setTimeout(() => setPasswordEditId(null), 1200);
    } catch (err) {
      setPasswordError(err instanceof ApiError ? err.message : "Failed to change password.");
    }
  }

  async function handleDelete(userId: string, force = false) {
    setRowError(null);
    setDeleteConflict(null);
    setDeleteBusyId(userId);
    try {
      await api.delete(`/api/v1/users/${userId}${force ? "?force=true" : ""}`);
      setConfirmDeleteId(null);
      await load();
    } catch (err) {
      setConfirmDeleteId(null);
      if (err instanceof ApiError && err.status === 409 && canForceDelete) {
        setDeleteConflict({ id: userId, message: err.message });
      } else {
        setRowError(err instanceof ApiError ? err.message : "Failed to delete user.");
      }
    } finally {
      setDeleteBusyId(null);
    }
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Users</h1>
          <p>Every user must have at least one role; permissions come only from role bundles.</p>
        </div>
        <button className="btn btn--primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Cancel" : "New user"}
        </button>
      </div>

      {showForm && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleCreate}>
          <div className="form-grid">
            <div className="form-field">
              <label>Email*</label>
              <input name="email" type="email" required />
            </div>
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required />
            </div>
            <div className="form-field">
              <label>Temporary password*</label>
              <input name="password" type="password" required minLength={12} />
            </div>
            <div className="form-field">
              <label>Role*</label>
              <select name="role_code" required defaultValue="">
                <option value="" disabled>
                  Select role
                </option>
                {roles.map((r) => (
                  <option key={r.id} value={r.code}>
                    {r.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Branch (required for Unit Head / TL)</label>
              <select name="hospital_scope_id" defaultValue="">
                <option value="">No branch assignment</option>
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {formError && <div className="field-error">{formError}</div>}
          <button className="btn btn--primary" type="submit">
            Create user
          </button>
        </form>
      )}

      {rowError && <ErrorState message={rowError} />}
      {error && <ErrorState message={error} />}
      {!error && !users && <LoadingState />}
      {users && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Name</th>
                <th>Roles</th>
                <th>Branch</th>
                <th>Department</th>
                <th>Phone</th>
                <th>WhatsApp number</th>
                <th>Notification preference</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const isSelf = currentUser?.id === u.id;
                return (
                  <Fragment key={u.id}>
                    <tr>
                      <td>
                        {u.email}
                        {isSelf && (
                          <span className="badge" style={{ marginLeft: 8 }}>
                            You
                          </span>
                        )}
                      </td>
                      <td>{u.name}</td>
                      <td>{u.roles.join(", ") || "None"}</td>
                      <td>{branchName(u.hospital_scope_id)}</td>
                      <td>{departmentName(u.department_id)}</td>
                      <td>{u.phone || "-"}</td>
                      <td>{u.whatsapp_number || "-"}</td>
                      <td>{u.notification_preference}</td>
                      <td>
                        <StatusBadge value={u.is_active ? "ACTIVE" : "INACTIVE"} />
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: 6 }}>
                          <button
                            className="btn btn--secondary"
                            onClick={() => {
                              setEditingId(editingId === u.id ? null : u.id);
                              setPasswordEditId(null);
                            }}
                          >
                            {editingId === u.id ? "Cancel" : "Edit"}
                          </button>
                          <button
                            className="btn btn--ghost"
                            onClick={() => {
                              setPasswordEditId(passwordEditId === u.id ? null : u.id);
                              setEditingId(null);
                              setPasswordError(null);
                            }}
                          >
                            {passwordEditId === u.id ? "Cancel" : "Password"}
                          </button>
                          {!isSelf && (
                            <button
                              className="btn btn--ghost"
                              disabled={deleteBusyId === u.id}
                              onClick={() => setConfirmDeleteId(u.id)}
                            >
                              Delete
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>

                    {deleteConflict?.id === u.id && (
                      <tr>
                        <td colSpan={9}>
                          <div
                            className="state-panel state-panel--error"
                            style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}
                          >
                            <span>{deleteConflict.message}</span>
                            <button className="btn btn--danger" disabled={deleteBusyId === u.id} onClick={() => handleDelete(u.id, true)}>
                              Force delete anyway
                            </button>
                          </div>
                        </td>
                      </tr>
                    )}

                    {editingId === u.id && (
                      <tr>
                        <td colSpan={9}>
                          <form className="form-grid" onSubmit={(e) => handleEditSubmit(u.id, e)}>
                            <div className="form-field">
                              <label>Full name*</label>
                              <input name="name" required defaultValue={u.name} />
                            </div>
                            <div className="form-field">
                              <label>Email*</label>
                              <input name="email" type="email" required defaultValue={u.email} />
                            </div>
                            <div className="form-field">
                              <label>Role*</label>
                              <select name="role_code" required defaultValue={u.roles[0] ?? ""}>
                                <option value="" disabled>
                                  Select role
                                </option>
                                {roles.map((r) => (
                                  <option key={r.id} value={r.code}>
                                    {r.name}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className="form-field">
                              <label>Branch (required for Unit Head / TL)</label>
                              <select name="hospital_scope_id" defaultValue={u.hospital_scope_id ?? ""}>
                                <option value="">No branch assignment</option>
                                {branches.map((b) => (
                                  <option key={b.id} value={b.id}>
                                    {b.name}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className="form-field">
                              <label>Department</label>
                              <select name="department_id" defaultValue={u.department_id ?? ""}>
                                <option value="">No department</option>
                                {departments.map((d) => (
                                  <option key={d.id} value={d.id}>
                                    {d.name} — {branchName(d.branch_id)}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className="form-field">
                              <label>Phone</label>
                              <input name="phone" defaultValue={u.phone ?? ""} placeholder="+91 98765 43210" />
                              <span className="field-hint">General contact number.</span>
                            </div>
                            <div className="form-field">
                              <label>WhatsApp number</label>
                              <input name="whatsapp_number" defaultValue={u.whatsapp_number ?? ""} placeholder="+91 98765 43210" />
                              <span className="field-hint">
                                Used for WhatsApp delivery — kept separate from Phone on purpose.
                              </span>
                            </div>
                            <div className="form-field">
                              <label>Notification preference</label>
                              <select name="notification_preference" defaultValue={u.notification_preference}>
                                {NOTIFICATION_PREFERENCES.map((p) => (
                                  <option key={p} value={p}>
                                    {p}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className="form-field">
                              <label>
                                <input type="checkbox" name="is_active" defaultChecked={u.is_active} disabled={isSelf} /> Active
                                {isSelf && (
                                  <span className="field-hint" style={{ marginLeft: 6 }}>
                                    (you cannot deactivate your own account)
                                  </span>
                                )}
                              </label>
                            </div>
                            {editError && <div className="field-error">{editError}</div>}
                            <div className="form-field" style={{ justifyContent: "flex-end" }}>
                              <button className="btn btn--primary" type="submit">
                                Save
                              </button>
                            </div>
                          </form>
                        </td>
                      </tr>
                    )}

                    {passwordEditId === u.id && (
                      <tr>
                        <td colSpan={9}>
                          <form className="form-grid" onSubmit={(e) => handlePasswordSubmit(u.id, e)}>
                            <div className="form-field">
                              <label>New password*</label>
                              <input name="new_password" type="password" required minLength={8} />
                            </div>
                            <div className="form-field">
                              <label>Confirm new password*</label>
                              <input name="confirm_password" type="password" required minLength={8} />
                            </div>
                            {passwordError && <div className="field-error">{passwordError}</div>}
                            {passwordSuccessId === u.id && (
                              <div className="state-panel state-panel--success">Password changed.</div>
                            )}
                            <div className="form-field" style={{ justifyContent: "flex-end" }}>
                              <button className="btn btn--primary" type="submit">
                                Set new password
                              </button>
                            </div>
                          </form>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete user"
        message={
          canForceDelete
            ? "This permanently deletes the user. If they're still referenced by requirements, incidents, PM records, or similar history, you'll be offered a force-delete that cascades through those — otherwise deactivate the user instead. Continue?"
            : "This permanently deletes the user. Only succeeds if nothing currently references them — if they're referenced, deactivate the user instead. Continue?"
        }
        confirmLabel="Delete"
        onConfirm={() => confirmDeleteId && handleDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </>
  );
}
