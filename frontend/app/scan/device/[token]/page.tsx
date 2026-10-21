"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError } from "../../../../lib/api";
import type { QrPublicPage } from "../../../../types";

const SECTION_LABELS: Record<string, string> = {
  identity: "Device",
  location: "Location",
  lifecycle: "Lifecycle",
  maintenance: "Maintenance",
  coverage: "Coverage",
};

type LoadState = "loading" | "ready" | "disabled" | "not_found" | "error";

/**
 * Public, unauthenticated scan landing page — intentionally does NOT use
 * ProtectedShell (same pattern as app/login/page.tsx, the only other
 * unauthenticated page in this app). A staff member reaches this by scanning the
 * printed QR label with an ordinary phone camera; the API call behind it
 * (GET /api/v1/public/qr/{token}) has no auth dependency and returns only the
 * allowlisted fields a Super Admin has chosen to show (see routers/qr_public.py).
 */
export default function PublicDeviceScanPage() {
  const params = useParams<{ token: string }>();
  const [state, setState] = useState<LoadState>("loading");
  const [page, setPage] = useState<QrPublicPage | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const result = await api.get<QrPublicPage>(`/api/v1/public/qr/${params.token}`);
        if (cancelled) return;
        setPage(result);
        setState(result.enabled ? "ready" : "disabled");
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setState("not_found");
        } else {
          setState("error");
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [params.token]);

  const grouped = page
    ? page.fields.reduce<Record<string, typeof page.fields>>((acc, f) => {
        (acc[f.section] = acc[f.section] || []).push(f);
        return acc;
      }, {})
    : {};

  return (
    <div className="scan-page">
      <div className="scan-page__card">
        <div className="scan-page__brand">MAX Biomedical AI</div>

        {state === "loading" && <p className="scan-page__status">Loading device information...</p>}

        {state === "not_found" && (
          <div className="state-panel state-panel--error">
            This QR code could not be found. It may be invalid, revoked, or no longer active.
          </div>
        )}

        {state === "error" && (
          <div className="state-panel state-panel--error">
            Could not load this device's information right now. Please try again in a moment.
          </div>
        )}

        {state === "disabled" && (
          <div className="state-panel state-panel--warning">
            Device information is temporarily unavailable. Please contact the biomedical engineering team.
          </div>
        )}

        {state === "ready" && page && (
          <>
            {Object.entries(grouped).map(([section, fields]) => (
              <div className="scan-page__section" key={section}>
                <h2>{SECTION_LABELS[section] ?? section}</h2>
                <dl className="scan-page__fields">
                  {fields.map((field) => (
                    <div className="scan-page__field" key={field.key}>
                      <dt>{field.label}</dt>
                      <dd>{field.value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))}
            {page.fields.length === 0 && (
              <p className="scan-page__status">No information is configured to display for this device.</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
