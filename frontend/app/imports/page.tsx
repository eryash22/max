"use client";

import { useRef, useState, type ChangeEvent } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { ImportTemplateButton } from "../../components/ImportTemplateButton";
import { usePaginatedResource } from "../../hooks/usePaginatedResource";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type { ImportJob } from "../../types";

const TEMPLATE_SHEETS: { key: string; label: string }[] = [
  { key: "hospital master", label: "Branches" },
  { key: "device model master", label: "Device models" },
  { key: "device master", label: "Physical devices (assets)" },
  { key: "consumables", label: "Consumables" },
];

export default function ImportsPage() {
  const { data, page, setPage, loading, error, reload } = usePaginatedResource<ImportJob>("/api/v1/imports");
  const { hasPermission } = useAuth();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  async function handleFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      await api.upload<ImportJob>("/api/v1/imports", file);
      reload();
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Data import center</h1>
          <p>Upload → validate → preview → approve. Writes only happen after an authorized approval.</p>
        </div>
        {hasPermission("import.create") && (
          <div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xlsm,.csv"
              style={{ display: "none" }}
              onChange={handleFileSelected}
            />
            <button className="btn btn--primary" disabled={uploading} onClick={() => fileInputRef.current?.click()}>
              {uploading ? "Uploading..." : "Upload workbook"}
            </button>
          </div>
        )}
      </div>

      {hasPermission("import.create") && (
        <div className="card" style={{ marginBottom: 16, display: "flex", flexDirection: "column", gap: 8 }}>
          <strong>Download a blank template</strong>
          <p className="dashboard-note" style={{ marginTop: 0 }}>
            Headers only, generated from the fields this Import Center actually accepts — no fabricated example
            values. Physical devices (assets) is a separate sheet from Device models: uploading only a Device model
            template never creates asset rows.
          </p>
          {TEMPLATE_SHEETS.map((s) => (
            <ImportTemplateButton key={s.key} sheetKey={s.key} label={s.label} />
          ))}
        </div>
      )}

      {uploadError && <ErrorState message={uploadError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<ImportJob>
            columns={[
              {
                key: "file_name",
                header: "File",
                render: (job) => <Link href={`/imports/${job.id}`}>{job.file_name}</Link>,
              },
              { key: "status", header: "Status", render: (job) => <StatusBadge value={job.status} /> },
              {
                key: "summary",
                header: "Summary",
                render: (job) =>
                  job.summary
                    ? Object.entries(job.summary)
                        .filter(([, v]) => typeof v === "number")
                        .map(([k, v]) => `${k}: ${v}`)
                        .join(" · ")
                    : "-",
              },
              { key: "created_at", header: "Uploaded", render: (job) => job.created_at ?? "-" },
            ]}
            rows={data.items}
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}
    </ProtectedShell>
  );
}
