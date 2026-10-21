"use client";

import { useState, type FormEvent } from "react";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { usePaginatedResource } from "../../hooks/usePaginatedResource";
import { useLookup } from "../../hooks/useLookup";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type { Department, Location } from "../../types";

export default function LocationsPage() {
  const { data, page, setPage, loading, error, reload } = usePaginatedResource<Location>("/api/v1/locations");
  const departments = useLookup<Department>("/api/v1/departments");
  const departmentName = (id: string) => departments.find((d) => d.id === id)?.name ?? id;
  const { hasPermission } = useAuth();
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/locations", {
        department_id: formData.get("department_id"),
        name: formData.get("name"),
        code: formData.get("code") || null,
        room: formData.get("room") || null,
        status: "ACTIVE",
      });
      setShowForm(false);
      form.reset();
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create location.");
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Locations</h1>
          <p>Rooms/beds within a department, used for precise asset placement.</p>
        </div>
        {hasPermission("hospital.write") && (
          <button className="btn btn--primary" onClick={() => setShowForm((v) => !v)}>
            {showForm ? "Cancel" : "New location"}
          </button>
        )}
      </div>

      {showForm && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleCreate}>
          <div className="form-grid">
            <div className="form-field">
              <label>Department*</label>
              <select name="department_id" required defaultValue="">
                <option value="" disabled>
                  Select department
                </option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required />
            </div>
            <div className="form-field">
              <label>Code</label>
              <input name="code" />
            </div>
            <div className="form-field">
              <label>Room</label>
              <input name="room" />
            </div>
          </div>
          {formError && <div className="field-error">{formError}</div>}
          <button className="btn btn--primary" type="submit">
            Save location
          </button>
        </form>
      )}

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<Location>
            columns={[
              { key: "name", header: "Name", render: (l) => l.name },
              { key: "department", header: "Department", render: (l) => departmentName(l.department_id) },
              { key: "room", header: "Room", render: (l) => l.room ?? "-" },
              { key: "status", header: "Status", render: (l) => <StatusBadge value={l.status} /> },
            ]}
            rows={data.items}
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}
    </ProtectedShell>
  );
}
