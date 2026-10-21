"use client";

import { useEffect } from "react";

// Next.js App Router error boundary — catches any unhandled render/runtime exception
// in a page or layout below the root, so one broken page (e.g. an unexpected shape in
// an API response) shows a recoverable screen instead of a blank page or a dev-server
// crash. Every page's own data fetching already has try/catch around API calls (see
// lib/api.ts's ApiError), so this is a last-resort safety net, not the primary error
// handling path.
export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // eslint-disable-next-line no-console
    console.error("[app] unhandled render error:", error);
  }, [error]);

  return (
    <div style={{ display: "flex", minHeight: "100vh", alignItems: "center", justifyContent: "center", padding: 24 }}>
      <div className="card" style={{ maxWidth: 420, textAlign: "center" }}>
        <h2 style={{ marginTop: 0 }}>Something went wrong</h2>
        <p className="field-hint">
          An unexpected error occurred while loading this page. Your work elsewhere in the app is unaffected.
        </p>
        <button className="btn btn--primary" type="button" onClick={() => reset()}>
          Try again
        </button>
      </div>
    </div>
  );
}
