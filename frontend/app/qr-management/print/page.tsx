"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { api, ApiError } from "../../../lib/api";
import type { Page as ApiPage, QrAssetStatus } from "../../../types";

type LabelData = QrAssetStatus & { imageUrl: string };

export default function QrPrintPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <QrPrintPageInner />
    </Suspense>
  );
}

function QrPrintPageInner() {
  const searchParams = useSearchParams();
  const ids = (searchParams.get("ids") ?? "").split(",").filter(Boolean);
  const [labels, setLabels] = useState<LabelData[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let urls: string[] = [];
    async function load() {
      if (ids.length === 0) {
        setLoading(false);
        return;
      }
      try {
        const result = await api.get<ApiPage<QrAssetStatus>>(
          `/api/v1/qr/assets?asset_ids=${encodeURIComponent(ids.join(","))}&page_size=${ids.length}`
        );
        const withImages: LabelData[] = [];
        for (const asset of result.items) {
          if (!asset.has_qr) continue;
          const { blob } = await api.download(`/api/v1/qr/assets/${asset.asset_id}/image`);
          const url = URL.createObjectURL(blob);
          urls.push(url);
          withImages.push({ ...asset, imageUrl: url });
        }
        setLabels(withImages);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Failed to load QR labels.");
      } finally {
        setLoading(false);
      }
    }
    load();
    return () => {
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids.join(",")]);

  return (
    <ProtectedShell requiredPermission="qr.manage">
      <div className="page-header no-print">
        <div>
          <h1>Print QR labels</h1>
          <p>{labels.length} label(s) ready — use your browser's Print (or Save as PDF) on an A4 sheet.</p>
        </div>
        <button className="btn btn--primary" onClick={() => window.print()} disabled={labels.length === 0}>
          Print
        </button>
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}

      <div className="qr-label-grid">
        {labels.map((label) => (
          <div className="qr-label" key={label.asset_id}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={label.imageUrl} alt={`QR code for ${label.device_name}`} className="qr-label__image" />
            <div className="qr-label__text">
              <strong>{label.device_name}</strong>
              <span>{label.asset_number ?? "Asset number not assigned"}</span>
              {label.branch_name && <span>{label.branch_name}</span>}
            </div>
          </div>
        ))}
      </div>
    </ProtectedShell>
  );
}
