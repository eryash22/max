"use client";

import { useState, type FormEvent } from "react";
import { useAuth } from "../../hooks/useAuth";
import { ApiError } from "../../lib/api";

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
    } catch (err) {
      if (err instanceof ApiError) {
        // Covers both a real backend rejection (wrong credentials, inactive
        // account — err.message is the backend's own safe detail string) and a
        // NetworkError (lib/api.ts) when the request never reached the server at
        // all — both carry a message safe to show as-is.
        setError(err.message);
      } else {
        if (process.env.NODE_ENV !== "production") {
          // eslint-disable-next-line no-console
          console.error("[login] unexpected error", err);
        }
        setError("Unable to sign in.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <p className="dev-banner">DEVELOPMENT / DEMO</p>
        <h1>MAX Biomedical AI</h1>
        <p>Sign in to the Phase 1 admin console.</p>
        <form onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="form-field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error && <div className="field-error">{error}</div>}
          <button className="btn btn--primary" type="submit" disabled={submitting}>
            {submitting ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
