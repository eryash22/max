"use client";

import { useState, type FormEvent } from "react";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { SearchBar } from "../../components/SearchBar";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { usePaginatedResource } from "../../hooks/usePaginatedResource";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type { HospitalNetwork } from "../../types";

export default function HospitalNetworksPage() {
  const [search, setSearch] = useState("");
  const { data, page, setPage, loading, error, reload } = usePaginatedResource<HospitalNetwork>(
    "/api/v1/hospital-networks",
    search ? { search } : {}
  );
  const { hasPermission } = useAuth();
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/hospital-networks", { name: formData.get("name"), code: formData.get("code") || null });
      setShowForm(false);
      form.reset();
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create network.");
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Hospital networks</h1>
          <p>Top-level organization grouping above individual branches.</p>
        </div>
        {hasPermission("hospital.write") && (
          <button className="btn btn--primary" onClick={() => setShowForm((v) => !v)}>
            {showForm ? "Cancel" : "New network"}
          </button>
        )}
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
          </div>
          {formError && <div className="field-error">{formError}</div>}
          <button className="btn btn--primary" type="submit">
            Save network
          </button>
        </form>
      )}

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search networks..." />
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<HospitalNetwork>
            columns={[
              { key: "name", header: "Name", render: (n) => n.name },
              { key: "code", header: "Code", render: (n) => n.code ?? "Not provided" },
            ]}
            rows={data.items}
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}
    </ProtectedShell>
  );
}
