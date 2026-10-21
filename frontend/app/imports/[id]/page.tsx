"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { DataTable } from "../../../components/DataTable";
import { StatusBadge } from "../../../components/StatusBadge";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { useAuth } from "../../../hooks/useAuth";
import { api, ApiError } from "../../../lib/api";
import type { ImportPreview } from "../../../types";

export default function ImportDetailPage() {
  const params = useParams<{ id: string }>();
  const { hasPermission } = useAuth();
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<"approve" | "rollback" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await api.get<ImportPreview>(`/api/v1/imports/${params.id}/preview`);
      setPreview(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load import job.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function runAction() {
    if (!confirmAction) return;
    setBusy(true);
    setActionError(null);
    try {
      await api.post(`/api/v1/imports/${params.id}/${confirmAction}`, { decision: "APPROVED" });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : `Failed to ${confirmAction} import.`);
    } finally {
      setBusy(false);
      setConfirmAction(null);
    }
  }

  if (error) {
    return (
      <ProtectedShell>
        <ErrorState message={error} />
      </ProtectedShell>
    );
  }
  if (!preview) {
    return (
      <ProtectedShell>
        <LoadingState />
      </ProtectedShell>
    );
  }

  const { job, rows, errors } = preview;

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>{job.file_name}</h1>
          <p>
            Status: <StatusBadge value={job.status} /> · Uploaded {job.created_at ?? "-"}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {hasPermission("import.approve") && job.status === "READY_FOR_REVIEW" && (
            <button className="btn btn--primary" onClick={() => setConfirmAction("approve")}>
              Approve &amp; import
            </button>
          )}
          {hasPermission("import.approve") && job.status === "COMPLETED" && (
            <button className="btn btn--ghost" onClick={() => setConfirmAction("rollback")}>
              Rollback
            </button>
          )}
        </div>
      </div>

      {actionError && <ErrorState message={actionError} />}

      {job.summary && (
        <div className="stat-grid">
          {Object.entries(job.summary)
            .filter(([, v]) => typeof v === "number")
            .map(([key, value]) => (
              <div className="stat-tile" key={key}>
                <div className="stat-tile__label">{key.replace(/_/g, " ")}</div>
                <div className="stat-tile__value">{value as number}</div>
              </div>
            ))}
        </div>
      )}

      <h2 style={{ fontSize: 15, marginTop: 24 }}>Validation issues</h2>
      <DataTable
        columns={[
          { key: "sheet", header: "Sheet", render: (e) => e.sheet_name ?? "-" },
          { key: "row", header: "Row", render: (e) => e.row_number ?? "-" },
          { key: "field", header: "Field", render: (e) => e.field ?? "-" },
          { key: "severity", header: "Severity", render: (e) => <StatusBadge value={e.severity} /> },
          { key: "message", header: "Message", render: (e) => e.message },
        ]}
        rows={errors}
        emptyLabel="No validation issues found."
      />

      <h2 style={{ fontSize: 15, marginTop: 24 }}>Staged rows</h2>
      <DataTable
        columns={[
          { key: "sheet", header: "Sheet", render: (r) => r.sheet_name },
          { key: "row", header: "Row", render: (r) => r.row_number },
          { key: "entity_type", header: "Entity", render: (r) => r.entity_type ?? "-" },
          { key: "business_key", header: "Business key", render: (r) => r.business_key ?? "Not resolved" },
          { key: "outcome", header: "Outcome", render: (r) => <StatusBadge value={r.outcome} /> },
          {
            key: "duplicate",
            header: "Duplicate?",
            render: (r) => (r.is_duplicate_candidate ? <StatusBadge value="DUPLICATE_CANDIDATE" /> : "-"),
          },
        ]}
        rows={rows}
        emptyLabel="No rows staged for this import."
      />

      <ConfirmDialog
        open={confirmAction !== null}
        title={confirmAction === "approve" ? "Approve this import?" : "Roll back this import?"}
        message={
          confirmAction === "approve"
            ? "This writes all ready/duplicate-resolved rows to the production tables inside a single transaction and creates an audit record."
            : "This deletes only the records this job inserted, and only where nothing else now depends on them."
        }
        confirmLabel={busy ? "Working..." : "Confirm"}
        onConfirm={runAction}
        onCancel={() => setConfirmAction(null)}
      />
    </ProtectedShell>
  );
}
