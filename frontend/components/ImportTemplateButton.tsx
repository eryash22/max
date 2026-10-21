"use client";

import { useState } from "react";
import { api, ApiError, triggerBrowserDownload } from "../lib/api";

/** "Download Template" for one Import Center sheet — hits the same
 * GET /api/v1/imports/templates/{sheetKey} endpoint regardless of caller, so every
 * module's import entry point (Devices, Consumables, Branches, ...) gets template
 * downloads without a bespoke fetch per page. */
export function ImportTemplateButton({ sheetKey, label }: { sheetKey: string; label: string }) {
  const [busyFormat, setBusyFormat] = useState<"xlsx" | "csv" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleDownload(format: "xlsx" | "csv") {
    setBusyFormat(format);
    setError(null);
    try {
      const { blob, fileName } = await api.download(
        `/api/v1/imports/templates/${encodeURIComponent(sheetKey)}?format=${format}`
      );
      triggerBrowserDownload(blob, fileName ?? `${sheetKey.replace(/ /g, "_")}_template.${format}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Failed to download ${format.toUpperCase()} template.`);
    } finally {
      setBusyFormat(null);
    }
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <span className="field-hint">{label}</span>
      <button className="btn btn--ghost" onClick={() => handleDownload("xlsx")} disabled={busyFormat !== null}>
        {busyFormat === "xlsx" ? "Downloading…" : "Template (XLSX)"}
      </button>
      <button className="btn btn--ghost" onClick={() => handleDownload("csv")} disabled={busyFormat !== null}>
        {busyFormat === "csv" ? "Downloading…" : "Template (CSV)"}
      </button>
      {error && <span className="field-error">{error}</span>}
    </div>
  );
}
