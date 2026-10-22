"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { SearchBar } from "../../components/SearchBar";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { api, ApiError } from "../../lib/api";
import { usePaginatedResource } from "../../hooks/usePaginatedResource";
import { useLookup } from "../../hooks/useLookup";
import { useUserDirectory } from "../../hooks/useUserDirectory";
import { useAuth } from "../../hooks/useAuth";
import type { Branch, Department, HospitalNetwork, Requirement } from "../../types";
import { REQUIREMENT_PRIORITIES, REQUIREMENT_STATUSES, REQUIREMENT_STATUS_LABELS } from "../../types";

export default function RequirementsPage() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [hospitalId, setHospitalId] = useState("");
  const [branchId, setBranchId] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [priority, setPriority] = useState("");
  const [requesterId, setRequesterId] = useState("");
  const [managerId, setManagerId] = useState("");
  const [createdAfter, setCreatedAfter] = useState("");
  const [createdBefore, setCreatedBefore] = useState("");

  // Hydrates filters from the URL so dashboard drill-down links (e.g.
  // /requirements?status_filter=PENDING_MANAGER_APPROVAL&branch_id=X) land pre-filtered.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sf = params.get("status_filter");
    const bi = params.get("branch_id");
    if (sf) setStatusFilter(sf);
    if (bi) setBranchId(bi);
  }, []);

  const query: Record<string, string> = {};
  if (search) query.search = search;
  if (statusFilter) query.status_filter = statusFilter;
  if (branchId) query.branch_id = branchId;
  if (departmentId) query.department_id = departmentId;
  if (priority) query.priority = priority;
  if (requesterId) query.requester_id = requesterId;
  if (managerId) query.manager_id = managerId;
  if (createdAfter) query.created_after = createdAfter;
  if (createdBefore) query.created_before = createdBefore;

  const { data, page, setPage, loading, error, reload } = usePaginatedResource<Requirement>("/api/v1/requirements", query);
  const hospitals = useLookup<HospitalNetwork>("/api/v1/hospital-networks");
  const branches = useLookup<Branch>("/api/v1/branches");
  const departments = useLookup<Department>("/api/v1/departments");
  const users = useUserDirectory();
  const { hasPermission, user } = useAuth();

  const branchName = (id: string) => branches.find((b) => b.id === id)?.name ?? id;
  const departmentName = (id: string | null) => (id ? departments.find((d) => d.id === id)?.name ?? id : "-");
  const userName = (id: string | null) => {
    if (!id) return "-";
    if (user && id === user.id) return `${user.name} (You)`;
    return users.find((u) => u.id === id)?.name ?? id;
  };

  // Hospital is not a filterable field on the requirement itself (only branch_id is) —
  // selecting a hospital narrows the Branch dropdown to that hospital's branches, and the
  // actual server-side filter query still uses branch_id, matching the real 16A API shape.
  const branchesForHospital = useMemo(() => {
    if (!hospitalId) return branches;
    return branches.filter((b) => b.network_id === hospitalId);
  }, [branches, hospitalId]);

  function handleHospitalChange(value: string) {
    setHospitalId(value);
    setBranchId("");
  }

  const canCreate = hasPermission("requirement.create");
  const canForceDelete = hasPermission("dev.data_cleanup");
  const [rowError, setRowError] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ id: string; message: string } | null>(null);

  async function handleDelete(requirementId: string, force = false) {
    setRowError(null);
    setConflict(null);
    setRowBusy(requirementId);
    try {
      await api.delete(`/api/v1/requirements/${requirementId}${force ? "?force=true" : ""}`);
      setConfirmDeleteId(null);
      await reload();
    } catch (err) {
      setConfirmDeleteId(null);
      if (err instanceof ApiError && err.status === 409 && canForceDelete) {
        setConflict({ id: requirementId, message: err.message });
      } else {
        setRowError(err instanceof ApiError ? err.message : "Failed to delete requirement.");
      }
    } finally {
      setRowBusy(null);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Requirements</h1>
          <p>Hospital requirement requests and manager approval workflow.</p>
        </div>
        {canCreate && (
          <Link href="/requirements/new" className="btn btn--primary">
            New requirement
          </Link>
        )}
      </div>

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search reason or request number..." />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          {REQUIREMENT_STATUSES.map((s) => (
            <option key={s} value={s}>
              {REQUIREMENT_STATUS_LABELS[s] ?? s}
            </option>
          ))}
        </select>
        <select value={hospitalId} onChange={(e) => handleHospitalChange(e.target.value)}>
          <option value="">All hospitals</option>
          {hospitals.map((h) => (
            <option key={h.id} value={h.id}>
              {h.name}
            </option>
          ))}
        </select>
        <select value={branchId} onChange={(e) => setBranchId(e.target.value)}>
          <option value="">All branches</option>
          {branchesForHospital.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
        <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
          <option value="">All departments</option>
          {departments.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} — {branchName(d.branch_id)}
            </option>
          ))}
        </select>
        <select value={priority} onChange={(e) => setPriority(e.target.value)}>
          <option value="">All priorities</option>
          {REQUIREMENT_PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        {users.length > 0 && (
          <>
            <select value={requesterId} onChange={(e) => setRequesterId(e.target.value)}>
              <option value="">All requesters</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                </option>
              ))}
            </select>
            <select value={managerId} onChange={(e) => setManagerId(e.target.value)}>
              <option value="">All managers</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                </option>
              ))}
            </select>
          </>
        )}
        <input
          type="date"
          value={createdAfter}
          onChange={(e) => setCreatedAfter(e.target.value)}
          title="Created after"
        />
        <input
          type="date"
          value={createdBefore}
          onChange={(e) => setCreatedBefore(e.target.value)}
          title="Created before"
        />
      </div>

      {conflict && (
        <div
          className="state-panel state-panel--error"
          style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 16 }}
        >
          <span>{conflict.message}</span>
          {canForceDelete && (
            <button className="btn btn--danger" disabled={rowBusy === conflict.id} onClick={() => handleDelete(conflict.id, true)}>
              Force delete anyway
            </button>
          )}
        </div>
      )}
      {rowError && <ErrorState message={rowError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<Requirement>
            columns={[
              {
                key: "request_number",
                header: "Request Number",
                render: (r) => <Link href={`/requirements/${r.id}`}>{r.request_number}</Link>,
              },
              {
                key: "hospital",
                header: "Hospital",
                render: (r) => {
                  const branch = branches.find((b) => b.id === r.branch_id);
                  const hospital = branch ? hospitals.find((h) => h.id === branch.network_id) : undefined;
                  return hospital?.name ?? "-";
                },
              },
              { key: "branch", header: "Branch", render: (r) => branchName(r.branch_id) },
              { key: "department", header: "Department/Unit", render: (r) => departmentName(r.department_id) },
              { key: "requester", header: "Requester", render: (r) => userName(r.requester_id) },
              { key: "priority", header: "Priority", render: (r) => <StatusBadge value={r.priority} /> },
              { key: "required_by_date", header: "Required By", render: (r) => r.required_by_date ?? "-" },
              { key: "status", header: "Status", render: (r) => <StatusBadge value={r.status} /> },
              {
                key: "created_at",
                header: "Created Date",
                render: (r) => new Date(r.created_at).toLocaleDateString(),
              },
              { key: "manager", header: "Manager", render: (r) => userName(r.manager_id) },
              {
                key: "actions",
                header: "Actions",
                render: (r) => (
                  <div style={{ display: "flex", gap: 6 }}>
                    <Link className="btn btn--ghost" href={`/requirements/${r.id}`}>
                      View
                    </Link>
                    {hasPermission("requirement.delete") && (
                      <button
                        className="btn btn--ghost"
                        disabled={rowBusy === r.id}
                        onClick={() => setConfirmDeleteId(r.id)}
                      >
                        Delete
                      </button>
                    )}
                  </div>
                ),
              },
            ]}
            rows={data.items}
            emptyLabel="No requirements found."
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete requirement"
        message={
          canForceDelete
            ? "This permanently deletes the requirement. Only DRAFT requirements with no linked fulfillment can be deleted this way — otherwise you'll be offered a force-delete that cascades through its items, fulfillment, and history. Continue?"
            : "This permanently deletes the requirement. Only DRAFT requirements with no linked fulfillment can be deleted — for anything else, use Cancel on the requirement's own page instead. Continue?"
        }
        confirmLabel="Delete"
        onConfirm={() => confirmDeleteId && handleDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
