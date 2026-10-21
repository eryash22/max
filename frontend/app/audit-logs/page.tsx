"use client";

import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { usePaginatedResource } from "../../hooks/usePaginatedResource";
import type { AuditLogEntry } from "../../types";

export default function AuditLogsPage() {
  const { data, page, setPage, loading, error } = usePaginatedResource<AuditLogEntry>("/api/v1/audit-logs");

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Audit logs</h1>
          <p>Append-only trail. Nobody, including admins, can delete these entries through the API.</p>
        </div>
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<AuditLogEntry>
            columns={[
              { key: "created_at", header: "Timestamp", render: (l) => l.created_at ?? "-" },
              { key: "action", header: "Action", render: (l) => l.action },
              { key: "entity", header: "Entity", render: (l) => l.entity },
              { key: "entity_id", header: "Entity ID", render: (l) => l.entity_id ?? "-" },
              { key: "user_id", header: "User", render: (l) => l.user_id ?? "system" },
            ]}
            rows={data.items}
            emptyLabel="No audit entries yet."
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}
    </ProtectedShell>
  );
}
