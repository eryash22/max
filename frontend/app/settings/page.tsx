"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ProtectedShell } from "../../components/ProtectedShell";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { StatusBadge } from "../../components/StatusBadge";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type {
  CommunicationProviderConfig,
  Page,
  ServiceVisitType,
  SystemHealth,
  WhatsAppAutomationStatus,
  WhatsAppTestMessageResult,
} from "../../types";
import { NOTIFICATION_PREFERENCES } from "../../types";

export default function SettingsPage() {
  const { user, hasPermission, refreshUser } = useAuth();
  const canManageProvider = hasPermission("notification.approve");
  // Batch E, Part 20 — Super Admin (SETTINGS_MANAGE) gate, same permission
  // theme.py's PUT /theme uses, for the Service Visit Types control below.
  const canManageSettings = hasPermission("settings.manage");
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [visitTypes, setVisitTypes] = useState<ServiceVisitType[]>([]);
  const [visitTypesError, setVisitTypesError] = useState<string | null>(null);
  const [newVisitTypeName, setNewVisitTypeName] = useState("");
  const [visitTypeBusy, setVisitTypeBusy] = useState(false);

  function loadVisitTypes() {
    api
      .get<Page<ServiceVisitType>>("/api/v1/service-visits/types?include_inactive=true&page_size=200")
      .then((result) => setVisitTypes(result.items))
      .catch((err) => setVisitTypesError(err instanceof ApiError ? err.message : "Failed to load service visit types."));
  }

  useEffect(() => {
    if (!canManageSettings) return;
    loadVisitTypes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canManageSettings]);

  async function handleCreateVisitType(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setVisitTypesError(null);
    const name = newVisitTypeName.trim();
    if (!name) return;
    setVisitTypeBusy(true);
    try {
      await api.post("/api/v1/service-visits/types", { name });
      setNewVisitTypeName("");
      loadVisitTypes();
    } catch (err) {
      setVisitTypesError(err instanceof ApiError ? err.message : "Failed to add service visit type.");
    } finally {
      setVisitTypeBusy(false);
    }
  }

  async function handleToggleVisitType(vt: ServiceVisitType) {
    setVisitTypesError(null);
    setVisitTypeBusy(true);
    try {
      await api.post(`/api/v1/service-visits/types/${vt.id}/${vt.is_active ? "deactivate" : "activate"}`, {});
      loadVisitTypes();
    } catch (err) {
      setVisitTypesError(err instanceof ApiError ? err.message : "Failed to update service visit type.");
    } finally {
      setVisitTypeBusy(false);
    }
  }

  const [providerConfig, setProviderConfig] = useState<CommunicationProviderConfig | null>(null);
  const [providerError, setProviderError] = useState<string | null>(null);
  const [providerSaving, setProviderSaving] = useState(false);

  const [contactSaving, setContactSaving] = useState(false);
  const [contactError, setContactError] = useState<string | null>(null);
  const [contactSaved, setContactSaved] = useState(false);

  const [automationStatus, setAutomationStatus] = useState<WhatsAppAutomationStatus | null>(null);
  const [automationError, setAutomationError] = useState<string | null>(null);
  const [automationBusy, setAutomationBusy] = useState(false);
  const [testRecipient, setTestRecipient] = useState("");
  const [testMessage, setTestMessage] = useState("");
  const [testResult, setTestResult] = useState<WhatsAppTestMessageResult | null>(null);

  useEffect(() => {
    api
      .get<SystemHealth>("/api/v1/system/health")
      .then(setHealth)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to load system health."));
  }, []);

  useEffect(() => {
    if (!canManageProvider) return;
    api
      .get<CommunicationProviderConfig>("/api/v1/notifications/provider-config")
      .then(setProviderConfig)
      .catch((err) => setProviderError(err instanceof ApiError ? err.message : "Failed to load provider configuration."));
  }, [canManageProvider]);

  useEffect(() => {
    if (!canManageProvider) return;
    let cancelled = false;
    function poll() {
      api
        .get<WhatsAppAutomationStatus>("/api/v1/notifications/whatsapp-automation/status")
        .then((s) => {
          if (!cancelled) setAutomationStatus(s);
        })
        .catch((err) => {
          if (!cancelled) setAutomationError(err instanceof ApiError ? err.message : "Failed to load automation status.");
        });
    }
    poll();
    const interval = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [canManageProvider]);

  async function handleAutomationConnect() {
    setAutomationError(null);
    setAutomationBusy(true);
    try {
      const status = await api.post<WhatsAppAutomationStatus>("/api/v1/notifications/whatsapp-automation/connect", {});
      setAutomationStatus(status);
    } catch (err) {
      setAutomationError(err instanceof ApiError ? err.message : "Failed to connect the WhatsApp Web session.");
    } finally {
      setAutomationBusy(false);
    }
  }

  async function handleAutomationDisconnect() {
    setAutomationError(null);
    setAutomationBusy(true);
    try {
      const status = await api.post<WhatsAppAutomationStatus>("/api/v1/notifications/whatsapp-automation/disconnect", {});
      setAutomationStatus(status);
    } catch (err) {
      setAutomationError(err instanceof ApiError ? err.message : "Failed to disconnect the WhatsApp Web session.");
    } finally {
      setAutomationBusy(false);
    }
  }

  async function handleTestMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAutomationError(null);
    setTestResult(null);
    setAutomationBusy(true);
    try {
      const result = await api.post<WhatsAppTestMessageResult>("/api/v1/notifications/whatsapp-automation/test-message", {
        recipient: testRecipient,
        message: testMessage,
      });
      setTestResult(result);
    } catch (err) {
      setAutomationError(err instanceof ApiError ? err.message : "Failed to send the test message.");
    } finally {
      setAutomationBusy(false);
    }
  }

  async function handleProviderChange(activeProvider: string) {
    setProviderError(null);
    setProviderSaving(true);
    try {
      const updated = await api.patch<CommunicationProviderConfig>("/api/v1/notifications/provider-config", {
        active_provider: activeProvider,
      });
      setProviderConfig(updated);
    } catch (err) {
      setProviderError(err instanceof ApiError ? err.message : "Failed to update provider configuration.");
    } finally {
      setProviderSaving(false);
    }
  }

  async function handleContactSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setContactError(null);
    setContactSaved(false);
    const form = event.currentTarget;
    const formData = new FormData(form);
    setContactSaving(true);
    try {
      await api.patch("/api/v1/auth/me", {
        whatsapp_number: formData.get("whatsapp_number") || null,
        notification_preference: formData.get("notification_preference") || null,
      });
      await refreshUser();
      setContactSaved(true);
    } catch (err) {
      setContactError(err instanceof ApiError ? err.message : "Failed to update your contact preferences.");
    } finally {
      setContactSaving(false);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Settings</h1>
          <p>Environment and system health. Phase 2 AI features are intentionally not configured here.</p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Signed in as</h3>
        <p>
          {user?.name} ({user?.email}) — {user?.roles.join(", ")}
        </p>
      </div>

      {canManageSettings && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Service visit types</h3>
          <p className="dashboard-note" style={{ marginTop: 0 }}>
            Controlled list backing the Visit type field on the Service Visits page (Super Admin only). A type
            already used by a past service visit is never deleted — only deactivated, so it drops out of the
            picker for new visits while history keeps displaying it correctly.
          </p>
          {visitTypesError && <ErrorState message={visitTypesError} />}
          <table className="data-table" style={{ marginBottom: 12 }}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {visitTypes.map((vt) => (
                <tr key={vt.id}>
                  <td>{vt.name}</td>
                  <td>
                    <StatusBadge value={vt.is_active ? "ACTIVE" : "INACTIVE"} />
                  </td>
                  <td>
                    <button className="btn btn--ghost" disabled={visitTypeBusy} onClick={() => handleToggleVisitType(vt)}>
                      {vt.is_active ? "Deactivate" : "Activate"}
                    </button>
                  </td>
                </tr>
              ))}
              {visitTypes.length === 0 && (
                <tr>
                  <td colSpan={3}>No service visit types yet.</td>
                </tr>
              )}
            </tbody>
          </table>
          <form onSubmit={handleCreateVisitType} style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
            <div className="form-field" style={{ flex: 1 }}>
              <label>New visit type name</label>
              <input
                value={newVisitTypeName}
                onChange={(e) => setNewVisitTypeName(e.target.value)}
                placeholder="e.g. UPGRADE"
              />
            </div>
            <button className="btn btn--primary" type="submit" disabled={visitTypeBusy || !newVisitTypeName.trim()}>
              Add
            </button>
          </form>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>My communication preferences</h3>
        <p className="dashboard-note" style={{ marginTop: 0 }}>
          Your own WhatsApp number and notification preference — you can only edit your own, never another
          user&apos;s (an admin can, from the Users page).
        </p>
        <form onSubmit={handleContactSubmit} className="form-grid">
          <div className="form-field">
            <label>WhatsApp number</label>
            <input name="whatsapp_number" defaultValue={user?.whatsapp_number ?? ""} placeholder="+91 98765 43210" />
          </div>
          <div className="form-field">
            <label>Notification preference</label>
            <select name="notification_preference" defaultValue={user?.notification_preference ?? "EMAIL"}>
              {NOTIFICATION_PREFERENCES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          {contactError && <div className="field-error">{contactError}</div>}
          <div className="form-field" style={{ justifyContent: "flex-end" }}>
            <button className="btn btn--primary" type="submit" disabled={contactSaving}>
              {contactSaving ? "Saving..." : "Save"}
            </button>
          </div>
        </form>
        {contactSaved && <p className="dashboard-note">Saved.</p>}
      </div>

      {canManageProvider && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>WhatsApp provider</h3>
          <p className="dashboard-note" style={{ marginTop: 0 }}>
            BROWSER_LINK is the original working mode — a human opens a pre-filled WhatsApp Web link and sends it
            manually. BROWSER_AUTOMATION drives a real, admin-connected WhatsApp Web session automatically (see
            below) — only reports configured once connected. BUSINESS_API is a structural placeholder for a
            future official WhatsApp Business API integration; no credentials are configured, so it cannot send yet.
          </p>
          {providerError && <ErrorState message={providerError} />}
          {!providerConfig && !providerError && <LoadingState />}
          {providerConfig && (
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <select
                value={providerConfig.active_provider}
                disabled={providerSaving}
                onChange={(e) => handleProviderChange(e.target.value)}
              >
                <option value="BROWSER_LINK">BROWSER_LINK</option>
                <option value="BROWSER_AUTOMATION">BROWSER_AUTOMATION</option>
                <option value="BUSINESS_API">BUSINESS_API</option>
              </select>
              <StatusBadge value={providerConfig.is_configured ? "CONFIGURED" : "NOT CONFIGURED"} />
            </div>
          )}
        </div>
      )}

      {canManageProvider && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>WhatsApp Web automation session</h3>
          <p className="dashboard-note" style={{ marginTop: 0 }}>
            A real Playwright-driven browser holds a WhatsApp Web login here, same as scanning a QR code on a
            phone to link a device. It only ever reports SENT when it actually detects the outgoing message in
            the chat — otherwise a notification is left for a human to confirm. No session data is ever exposed
            to this page beyond connection state.
          </p>
          {automationError && <ErrorState message={automationError} />}
          {automationStatus && (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                <StatusBadge value={automationStatus.state} />
                {automationStatus.connected_at && <span className="dashboard-note">since {automationStatus.connected_at}</span>}
                <button className="btn btn--secondary" disabled={automationBusy} onClick={handleAutomationConnect}>
                  Connect
                </button>
                <button className="btn btn--secondary" disabled={automationBusy} onClick={handleAutomationDisconnect}>
                  Disconnect
                </button>
              </div>
              {automationStatus.last_error && (
                <p className="field-error">Last error: {automationStatus.last_error}</p>
              )}
              {automationStatus.state === "AWAITING_QR_SCAN" && automationStatus.qr_code_base64 && (
                <div style={{ marginBottom: 12 }}>
                  <p className="dashboard-note">Scan this code with WhatsApp on your phone (Linked Devices):</p>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`data:image/png;base64,${automationStatus.qr_code_base64}`}
                    alt="WhatsApp Web QR code"
                    style={{ width: 220, height: 220 }}
                  />
                </div>
              )}
              {automationStatus.state === "CONNECTED" && (
                <form onSubmit={handleTestMessage} className="form-grid">
                  <div className="form-field">
                    <label>Test recipient (WhatsApp number)</label>
                    <input value={testRecipient} onChange={(e) => setTestRecipient(e.target.value)} placeholder="+91 98765 43210" />
                  </div>
                  <div className="form-field">
                    <label>Test message</label>
                    <input value={testMessage} onChange={(e) => setTestMessage(e.target.value)} placeholder="Test message from settings" />
                  </div>
                  <div className="form-field" style={{ justifyContent: "flex-end" }}>
                    <button className="btn btn--primary" type="submit" disabled={automationBusy}>
                      Send test message
                    </button>
                  </div>
                </form>
              )}
              {testResult && (
                <p className="dashboard-note">
                  Result: <StatusBadge value={testResult.state} /> — {testResult.detail}
                </p>
              )}
            </>
          )}
        </div>
      )}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>System health</h3>
        {error && <ErrorState message={error} />}
        {!error && !health && <LoadingState />}
        {health && (
          <table className="data-table">
            <tbody>
              <tr>
                <td>API</td>
                <td>
                  <StatusBadge value={health.api} />
                </td>
              </tr>
              <tr>
                <td>Database connection</td>
                <td>
                  <StatusBadge value={health.database} />
                </td>
              </tr>
              <tr>
                <td>Migration status</td>
                <td>
                  <StatusBadge value={health.migration_status} />
                </td>
              </tr>
              {health.missing_tables.length > 0 && (
                <tr>
                  <td>Missing tables</td>
                  <td>{health.missing_tables.join(", ")}</td>
                </tr>
              )}
              <tr>
                <td>AI provider</td>
                <td>
                  <StatusBadge value={health.ai} />
                </td>
              </tr>
              <tr>
                <td>Docker required</td>
                <td>{health.docker_required ? "Yes" : "No"}</td>
              </tr>
            </tbody>
          </table>
        )}
      </div>
    </ProtectedShell>
  );
}
