"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { ErrorState } from "../../../components/ErrorState";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { useAuth } from "../../../hooks/useAuth";
import { api, ApiError, getToken } from "../../../lib/api";

interface ReplaceAllIssue {
  row_number: number;
  field: string | null;
  severity: string;
  message: string;
}

interface BlockedIncident {
  incident_id: string;
  description: string;
  blocked_by: string;
}

interface ReplaceAllPreview {
  current_incident_count: number;
  incoming_valid_rows: number;
  incoming_invalid_rows: number;
  issues: ReplaceAllIssue[];
  blocking_existing_incidents: BlockedIncident[];
}

// FastAPI treats confirm_replace/expected_current_count as query params on a
// multipart endpoint (no Form() wrapper) — api.upload's `fields` option appends to
// the FormData body instead, so the real confirm call needs the query string built
// directly rather than using that option.
async function uploadReplaceAll(file: File, confirmReplace: boolean, expectedCount: number) {
  const token = getToken();
  const formData = new FormData();
  formData.append("file", file);
  const params = new URLSearchParams({
    confirm_replace: String(confirmReplace),
    expected_current_count: String(expectedCount),
  });
  const base = process.env.NEXT_PUBLIC_API_URL || "";
  const response = await fetch(`${base}/api/v1/incidents/replace-all?${params.toString()}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: formData,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, body?.detail ?? response.statusText);
  }
  return body as { deleted: number; inserted: number };
}

export default function ReplaceAllIncidentsPage() {
  const { hasPermission } = useAuth();
  const canManage = hasPermission("incident.manage");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ReplaceAllPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [result, setResult] = useState<{ deleted: number; inserted: number } | null>(null);

  if (!canManage) {
    return (
      <ProtectedShell>
        <ErrorState message="You don't have permission to replace incident data." />
      </ProtectedShell>
    );
  }

  async function handleFileSelected(file: File) {
    setSelectedFile(file);
    setPreview(null);
    setResult(null);
    setError(null);
    setBusy(true);
    try {
      const body = await api.upload<ReplaceAllPreview>("/api/v1/incidents/replace-all/preview", file);
      setPreview(body);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to preview the workbook.");
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirmReplace() {
    if (!selectedFile || !preview) return;
    setBusy(true);
    setError(null);
    try {
      const body = await uploadReplaceAll(selectedFile, true, preview.current_incident_count);
      setResult(body);
      setPreview(null);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to replace incidents.");
    } finally {
      setBusy(false);
      setConfirmOpen(false);
    }
  }

  const errorIssues = preview?.issues.filter((i) => i.severity === "ERROR") ?? [];
  const warningIssues = preview?.issues.filter((i) => i.severity === "WARNING") ?? [];
  const hasBlockingIncidents = (preview?.blocking_existing_incidents.length ?? 0) > 0;

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Replace all incidents from Excel</h1>
          <p>
            The Excel workbook becomes the only source of truth: every existing incident is deleted and replaced
            by every valid row in the file you upload. This cannot be undone — export the current data first if
            you want a backup. <Link href="/incidents">Back to Incidents</Link>
          </p>
        </div>
      </div>

      {error && <ErrorState message={error} />}
      {result && (
        <div className="card" style={{ marginBottom: 16 }}>
          <p style={{ margin: 0 }}>
            Done — deleted {result.deleted} existing incident{result.deleted === 1 ? "" : "s"} and inserted{" "}
            {result.inserted} new one{result.inserted === 1 ? "" : "s"} from the workbook.
          </p>
          <Link href="/incidents" className="btn btn--primary" style={{ marginTop: 8, display: "inline-block" }}>
            View Incidents
          </Link>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <input
          ref={fileInputRef}
          type="file"
          accept=".xlsx,.xlsm,.csv"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFileSelected(file);
          }}
        />
        {busy && !preview && <p className="dashboard-note">Reading workbook…</p>}
      </div>

      {preview && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Preview — nothing has been changed yet</h3>
          <table className="data-table" style={{ marginBottom: 16 }}>
            <tbody>
              <tr>
                <td>Existing incidents (will be deleted)</td>
                <td>
                  <strong>{preview.current_incident_count}</strong>
                </td>
              </tr>
              <tr>
                <td>Valid rows in this workbook (will be inserted)</td>
                <td>
                  <strong>{preview.incoming_valid_rows}</strong>
                </td>
              </tr>
              <tr>
                <td>Rows with a problem (will be skipped, not inserted)</td>
                <td>{preview.incoming_invalid_rows}</td>
              </tr>
            </tbody>
          </table>

          {hasBlockingIncidents && (
            <div className="card" style={{ background: "var(--color-danger-bg, #fee)", marginBottom: 16 }}>
              <strong>Cannot replace — {preview.blocking_existing_incidents.length} existing incident(s) are still
                referenced by a real procurement record (quotation/indent/PO/invoice).</strong>
              <p>Nothing will be changed until these are resolved (close the procurement record, or leave this
                incident out of the replacement by deleting it individually first).</p>
              <ul>
                {preview.blocking_existing_incidents.map((b) => (
                  <li key={b.incident_id}>
                    {b.description} — blocked by {b.blocked_by}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {errorIssues.length > 0 && (
            <>
              <h4>Rows that will be skipped ({errorIssues.length})</h4>
              <table className="data-table" style={{ marginBottom: 16 }}>
                <thead>
                  <tr>
                    <th>Row</th>
                    <th>Field</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {errorIssues.map((issue, idx) => (
                    <tr key={idx}>
                      <td>{issue.row_number}</td>
                      <td>{issue.field ?? "-"}</td>
                      <td>{issue.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {warningIssues.length > 0 && (
            <>
              <h4>Flagged for review ({warningIssues.length}) — will still be imported using the shown value</h4>
              <table className="data-table" style={{ marginBottom: 16 }}>
                <thead>
                  <tr>
                    <th>Row</th>
                    <th>Field</th>
                    <th>Details</th>
                  </tr>
                </thead>
                <tbody>
                  {warningIssues.map((issue, idx) => (
                    <tr key={idx}>
                      <td>{issue.row_number}</td>
                      <td>{issue.field ?? "-"}</td>
                      <td>{issue.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <button
            className="btn btn--danger"
            disabled={busy || hasBlockingIncidents}
            onClick={() => setConfirmOpen(true)}
          >
            Replace all {preview.current_incident_count} incidents with these {preview.incoming_valid_rows} rows
          </button>
        </div>
      )}

      <ConfirmDialog
        open={confirmOpen}
        title="Replace all incidents"
        message={
          preview
            ? `This permanently deletes all ${preview.current_incident_count} existing incidents and inserts ` +
              `${preview.incoming_valid_rows} new ones from the workbook. This cannot be undone. Continue?`
            : ""
        }
        confirmLabel={busy ? "Replacing…" : "Replace everything"}
        onConfirm={handleConfirmReplace}
        onCancel={() => setConfirmOpen(false)}
      />
    </ProtectedShell>
  );
}
