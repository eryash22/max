"use client";

import { useState } from "react";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { usePaginatedResource } from "../../hooks/usePaginatedResource";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type { ReviewItem } from "../../types";

const ACTIONS: { key: string; label: string }[] = [
  { key: "resolve", label: "Resolve" },
  { key: "accept", label: "Accept" },
  { key: "reject", label: "Reject" },
  { key: "defer", label: "Defer" },
];

export default function ReviewQueuePage() {
  const { data, page, setPage, loading, error, reload } = usePaginatedResource<ReviewItem>(
    "/api/v1/review-queue",
    { status_filter: "OPEN" }
  );
  const { hasPermission } = useAuth();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  async function act(item: ReviewItem, action: string) {
    setActionError(null);
    let notes: string | null = null;
    if (item.reason_code === "LOCATION_ASSET_VERIFICATION_REQUIRED" || action === "reject" || action === "defer") {
      notes = window.prompt(`Notes for "${action}" (required for verification-sensitive items):`, "") ?? null;
      if (notes === null) return; // cancelled
    }
    setBusyId(item.id);
    try {
      await api.post(`/api/v1/review-queue/${item.id}/action`, { action, notes });
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusyId(null);
    }
  }

  // Deletes only the queue entry itself — never the underlying record it points at
  // (e.g. a branch_device_assignment row), see routers/review_queue.py's
  // delete_review_item docstring. Same review.resolve permission as the actions
  // above; no new capability granted for this button.
  async function handleDelete(itemId: string) {
    setActionError(null);
    setBusyId(itemId);
    try {
      await api.delete(`/api/v1/review-queue/${itemId}`);
      setConfirmDeleteId(null);
      reload();
    } catch (err) {
      setConfirmDeleteId(null);
      setActionError(err instanceof ApiError ? err.message : "Failed to delete this item.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Review queue</h1>
          <p>Data-quality items awaiting confirmation. Nothing here is auto-merged or auto-resolved.</p>
        </div>
      </div>

      {actionError && <ErrorState message={actionError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<ReviewItem>
            columns={[
              { key: "source_type", header: "Source", render: (i) => i.source_type },
              { key: "reason_code", header: "Reason", render: (i) => <StatusBadge value={i.reason_code} /> },
              { key: "detail", header: "Detail", render: (i) => i.detail ?? "-" },
              { key: "status", header: "Status", render: (i) => <StatusBadge value={i.status} /> },
              {
                key: "actions",
                header: "Actions",
                render: (i) =>
                  hasPermission("review.resolve") ? (
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      {ACTIONS.map((a) => (
                        <button
                          key={a.key}
                          className="btn btn--ghost"
                          disabled={busyId === i.id}
                          onClick={() => act(i, a.key)}
                        >
                          {a.label}
                        </button>
                      ))}
                      <button
                        className="btn btn--danger"
                        disabled={busyId === i.id}
                        onClick={() => setConfirmDeleteId(i.id)}
                      >
                        Delete
                      </button>
                    </div>
                  ) : (
                    "-"
                  ),
              },
            ]}
            rows={data.items}
            emptyLabel="No open review items."
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete review item"
        message="Remove this item from the review queue? This only removes the queue entry itself — the record it refers to (e.g. a branch device assignment) is never changed or deleted."
        confirmLabel="Delete"
        onConfirm={() => confirmDeleteId && handleDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
