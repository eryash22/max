"use client";

import { useEffect, useState } from "react";
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
import { api, ApiError } from "../../lib/api";
import type { Branch, Incident } from "../../types";
import { INCIDENT_SEVERITIES, INCIDENT_STATUSES } from "../../types";

export default function IncidentsPage() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [severity, setSeverity] = useState("");
  const [branchId, setBranchId] = useState("");

  // Restructure consolidation pass, Part B — hydrate filters from the URL on mount so
  // drill-down links (e.g. from the Branch detail page's "Open incidents" tile, or the
  // Dashboard's "Critical incidents" KPI) apply their filter, matching the same idiom
  // Module 18B already added to 8 other list pages (no useSearchParams()/Suspense
  // restructuring needed for a handful of query params).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const bf = params.get("branch_id");
    const sf = params.get("status_filter");
    const sev = params.get("severity");
    if (bf) setBranchId(bf);
    if (sf) setStatusFilter(sf);
    if (sev) setSeverity(sev);
  }, []);

  const query: Record<string, string> = {};
  if (search) query.search = search;
  if (statusFilter) query.status_filter = statusFilter;
  if (severity) query.severity = severity;
  if (branchId) query.branch_id = branchId;

  const { data, page, setPage, loading, error, reload } = usePaginatedResource<Incident>("/api/v1/incidents", query);
  const branches = useLookup<Branch>("/api/v1/branches");
  const branchName = (id: string) => branches.find((b) => b.id === id)?.name ?? id;
  const { hasPermission } = useAuth();
  const canManage = hasPermission("incident.manage");
  const canForceDelete = hasPermission("dev.data_cleanup");
  const [rowError, setRowError] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  async function handleForceDelete(incidentId: string) {
    setRowError(null);
    setRowBusy(incidentId);
    try {
      await api.delete(`/api/v1/incidents/${incidentId}?force=true`);
      setConfirmDeleteId(null);
      await reload();
    } catch (err) {
      setConfirmDeleteId(null);
      setRowError(err instanceof ApiError ? err.message : "Failed to delete incident.");
    } finally {
      setRowBusy(null);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Incidents</h1>
          <p>Device breakdown / complaint reports. Doctors and staff see only what they reported.</p>
        </div>
        {hasPermission("incident.create") && (
          <Link href="/incidents/new" className="btn btn--primary">
            Report incident
          </Link>
        )}
      </div>

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search description or category..." />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          {INCIDENT_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
          <option value="">All severities</option>
          {INCIDENT_SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select value={branchId} onChange={(e) => setBranchId(e.target.value)}>
          <option value="">All branches</option>
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
      </div>

      {rowError && <ErrorState message={rowError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<Incident>
            columns={[
              {
                key: "description",
                header: "Description",
                render: (i) => <Link href={`/incidents/${i.id}`}>{i.description}</Link>,
              },
              { key: "branch", header: "Branch", render: (i) => branchName(i.branch_id) },
              { key: "severity", header: "Severity", render: (i) => <StatusBadge value={i.severity} /> },
              { key: "status", header: "Status", render: (i) => <StatusBadge value={i.status} /> },
              { key: "category", header: "Category", render: (i) => i.problem_category ?? "-" },
              ...(canManage
                ? [
                    {
                      key: "actions",
                      header: "Actions",
                      render: (i: Incident) => (
                        <div style={{ display: "flex", gap: 6 }}>
                          <Link className="btn btn--ghost" href={`/incidents/${i.id}`}>
                            Edit
                          </Link>
                          {canForceDelete && (
                            <button
                              className="btn btn--ghost"
                              disabled={rowBusy === i.id}
                              onClick={() => setConfirmDeleteId(i.id)}
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
            emptyLabel="No incidents found."
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Force-delete incident"
        message="This permanently deletes the incident and everything tied to it — status history, service visits, and its own quotation/indent/PO/invoice chain. Incidents have no hard delete otherwise — close/cancel is the normal archive path. This cannot be undone. Continue?"
        confirmLabel="Force delete"
        onConfirm={() => confirmDeleteId && handleForceDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
