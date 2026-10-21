"use client";

import { useState } from "react";
import { api, ApiError, triggerBrowserDownload } from "../lib/api";

/** Export XLSX/CSV buttons reused by every module's list page — wired against a
 * `GET {path}/export` endpoint sharing the exact same filter/search query params as
 * that module's list endpoint (and the same RBAC gate, enforced server-side). Each
 * click is independent and non-blocking: the button that was clicked shows "Exporting…"
 * while its own request is in flight, the other stays usable, and the rest of the page
 * is never blocked. */
export function ExportButtons({
  path,
  filters,
  recordCount,
  filenameStem = "export",
}: {
  path: string;
  filters: Record<string, string | number | boolean | undefined | null>;
  recordCount: number;
  filenameStem?: string;
}) {
  const [busyFormat, setBusyFormat] = useState<"xlsx" | "csv" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successFormat, setSuccessFormat] = useState<"xlsx" | "csv" | null>(null);

  async function handleExport(format: "xlsx" | "csv") {
    setBusyFormat(format);
    setError(null);
    setSuccessFormat(null);
    try {
      const params = new URLSearchParams({ format });
      for (const [key, value] of Object.entries(filters)) {
        if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
      }
      const { blob, fileName } = await api.download(`${path}?${params.toString()}`);
      triggerBrowserDownload(blob, fileName ?? `${filenameStem}.${format}`);
      setSuccessFormat(format);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Failed to export ${format.toUpperCase()}.`);
    } finally {
      setBusyFormat(null);
    }
  }

  return (
    <div className="export-buttons" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <span className="field-hint">{recordCount} record{recordCount === 1 ? "" : "s"} match current filters</span>
      <button className="btn btn--ghost" onClick={() => handleExport("xlsx")} disabled={busyFormat !== null}>
        {busyFormat === "xlsx" ? "Exporting…" : "Export XLSX"}
      </button>
      <button className="btn btn--ghost" onClick={() => handleExport("csv")} disabled={busyFormat !== null}>
        {busyFormat === "csv" ? "Exporting…" : "Export CSV"}
      </button>
      {successFormat && <span className="field-hint">{successFormat.toUpperCase()} export ready.</span>}
      {error && <span className="field-error">{error}</span>}
    </div>
  );
}
