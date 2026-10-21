"use client";

import { useEffect } from "react";

// Root-layout error boundary — catches an exception thrown by RootLayout itself
// (e.g. AuthProvider/ThemeProvider init), which app/error.tsx cannot catch since it
// renders inside the layout. Must render its own <html>/<body> since the real root
// layout is what failed; kept dependency-free (no globals.css classes) for the same
// reason. Same last-resort-safety-net role as app/error.tsx.
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // eslint-disable-next-line no-console
    console.error("[app] unhandled root layout error:", error);
  }, [error]);

  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: "system-ui, sans-serif", display: "flex", minHeight: "100vh", alignItems: "center", justifyContent: "center", background: "#f5f5f7" }}>
        <div style={{ maxWidth: 420, textAlign: "center", padding: 24, background: "#fff", borderRadius: 8, boxShadow: "0 1px 4px rgba(0,0,0,0.1)" }}>
          <h2 style={{ marginTop: 0 }}>Something went wrong</h2>
          <p style={{ color: "#555" }}>The application failed to load. Please try again.</p>
          <button
            type="button"
            onClick={() => reset()}
            style={{ padding: "8px 16px", borderRadius: 6, border: "none", background: "#4f46e5", color: "#fff", cursor: "pointer" }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
