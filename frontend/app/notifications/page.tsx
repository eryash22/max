"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { useAuth } from "../../hooks/useAuth";
import { useNotificationEvents } from "../../hooks/useNotificationEvents";
import { api, ApiError } from "../../lib/api";
import type { NotificationRecord, NotificationTemplate, Page } from "../../types";
import { NOTIFICATION_CHANNELS, NOTIFICATION_STATUSES } from "../../types";

const PAGE_SIZE = 25;

export default function NotificationsPage() {
  const { hasPermission } = useAuth();
  const canRead = hasPermission("notification.read");
  const canApprove = hasPermission("notification.approve");
  const { labels: eventLabels } = useNotificationEvents();

  const [statusFilter, setStatusFilter] = useState("");
  const [channelFilter, setChannelFilter] = useState("");
  const [page, setPage] = useState(1);

  const [notifications, setNotifications] = useState<NotificationRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [templates, setTemplates] = useState<NotificationTemplate[]>([]);
  const [showTemplateForm, setShowTemplateForm] = useState(false);
  const [templateFormError, setTemplateFormError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (statusFilter) params.set("status_filter", statusFilter);
      if (channelFilter) params.set("channel", channelFilter);
      const result = await api.get<Page<NotificationRecord>>(`/api/v1/notifications?${params.toString()}`);
      setNotifications(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load notifications.");
    } finally {
      setLoading(false);
    }
  }

  async function loadTemplates() {
    try {
      const result = await api.get<Page<NotificationTemplate>>("/api/v1/notifications/templates?page=1&page_size=100");
      setTemplates(result.items);
    } catch {
      setTemplates([]);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, channelFilter, page]);

  useEffect(() => {
    loadTemplates();
  }, []);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, channelFilter]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/notifications", {
        template_id: formData.get("template_id") || null,
        recipient: formData.get("recipient"),
        channel: formData.get("channel"),
        event: formData.get("event"),
      });
      setShowForm(false);
      form.reset();
      setPage(1);
      await load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create notification.");
    }
  }

  async function handleCreateTemplate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTemplateFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/notifications/templates", {
        code: formData.get("code"),
        channel: formData.get("channel"),
        event: formData.get("event"),
        subject_template: formData.get("subject_template") || null,
        body_template: formData.get("body_template"),
      });
      setShowTemplateForm(false);
      form.reset();
      await loadTemplates();
    } catch (err) {
      setTemplateFormError(err instanceof ApiError ? err.message : "Failed to create template.");
    }
  }

  async function handleSubmit(id: string) {
    setActionError(null);
    setBusyId(id);
    try {
      await api.post(`/api/v1/notifications/${id}/submit`);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to submit notification.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleApprove(id: string) {
    setActionError(null);
    setBusyId(id);
    try {
      await api.post(`/api/v1/notifications/${id}/approve`);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to approve notification.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(id: string) {
    const reason = window.prompt("Reason for rejecting this notification:");
    if (!reason) return;
    setActionError(null);
    setBusyId(id);
    try {
      await api.post(`/api/v1/notifications/${id}/reject`, { failure_reason: reason });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to reject notification.");
    } finally {
      setBusyId(null);
    }
  }

  function handleOpenWhatsApp(n: NotificationRecord) {
    window.open(n.whatsapp_url ?? "", "_blank", "noopener,noreferrer");
    // Best-effort telemetry only — never blocks the state machine, see backend docstring.
    api.post(`/api/v1/notifications/${n.id}/whatsapp/opened`).then(load).catch(() => {});
  }

  async function handleWhatsAppConfirmSent(id: string) {
    setActionError(null);
    setBusyId(id);
    try {
      await api.post(`/api/v1/notifications/${id}/whatsapp/confirm-sent`);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to confirm the WhatsApp message was sent.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleWhatsAppMarkFailed(id: string) {
    const reason = window.prompt("Why did this WhatsApp message fail to send?");
    if (!reason) return;
    setActionError(null);
    setBusyId(id);
    try {
      await api.post(`/api/v1/notifications/${id}/whatsapp/mark-failed`, { failure_reason: reason });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to record the WhatsApp failure.");
    } finally {
      setBusyId(null);
    }
  }

  if (!canRead) {
    return (
      <ProtectedShell>
        <ErrorState message="You do not have permission to view notifications." />
      </ProtectedShell>
    );
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Notifications</h1>
          <p>
            Communication history for every channel, including the automated Requirement/Fulfillment
            workflow events. Email is sent via the configured SMTP provider on approval. WhatsApp has no
            send API — approving queues a pre-filled WhatsApp Web link for a human to open and send, and
            stays PENDING_APPROVAL until someone explicitly confirms it sent (or marks it failed); this
            project never marks a WhatsApp message SENT without that explicit confirmation. Manually-created
            notifications with no rule behind them keep the original behavior — approval marks them sent
            within this system without a real external dispatch. Configure the automated rules on the{" "}
            <Link href="/communication-rules">Communication rules</Link> page, and the active WhatsApp
            provider on the <Link href="/settings">Settings</Link> page.
          </p>
        </div>
        <button className="btn btn--primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Cancel" : "New notification"}
        </button>
      </div>

      {showForm && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleCreate}>
          <div className="form-grid">
            <div className="form-field">
              <label>Recipient*</label>
              <input name="recipient" required placeholder="Email, phone, or user identifier" />
            </div>
            <div className="form-field">
              <label>Channel*</label>
              <select name="channel" required defaultValue="IN_APP">
                {NOTIFICATION_CHANNELS.map((c) => (
                  <option key={c} value={c}>
                    {c.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Event*</label>
              <input name="event" required placeholder="e.g. PM_DUE, INCIDENT_CREATED" />
            </div>
            <div className="form-field">
              <label>Template</label>
              <select name="template_id" defaultValue="">
                <option value="">None</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.code}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {formError && <div className="field-error">{formError}</div>}
          <button className="btn btn--primary" type="submit">
            Save notification
          </button>
        </form>
      )}

      <div className="toolbar">
        <select value={channelFilter} onChange={(e) => setChannelFilter(e.target.value)}>
          <option value="">All channels</option>
          {NOTIFICATION_CHANNELS.map((c) => (
            <option key={c} value={c}>
              {c.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          {NOTIFICATION_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      </div>

      {actionError && <ErrorState message={actionError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {!loading && !error && (
        <>
          <DataTable<NotificationRecord>
            columns={[
              { key: "recipient", header: "Recipient", render: (n) => n.recipient || <em>none resolved</em> },
              { key: "channel", header: "Channel", render: (n) => n.channel.replace(/_/g, " ") },
              { key: "event", header: "Event", render: (n) => eventLabels[n.event] ?? n.event },
              {
                key: "related_to",
                header: "Related to",
                render: (n) => (
                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    {n.related_requirement_id && (
                      <Link href={`/requirements/${n.related_requirement_id}`}>Requirement</Link>
                    )}
                    {n.related_fulfillment_id && (
                      <Link href={`/fulfillments/${n.related_fulfillment_id}`}>Fulfillment</Link>
                    )}
                    {!n.related_requirement_id && !n.related_fulfillment_id && "-"}
                  </div>
                ),
              },
              {
                key: "message",
                header: "Message",
                render: (n) =>
                  n.rendered_body ? (
                    <span title={n.rendered_body}>
                      {n.rendered_body.length > 60 ? `${n.rendered_body.slice(0, 60)}...` : n.rendered_body}
                    </span>
                  ) : (
                    "-"
                  ),
              },
              {
                key: "status",
                header: "Status",
                render: (n) => (
                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    <StatusBadge value={n.status} />
                    {n.channel === "WHATSAPP" && n.whatsapp_status && (
                      <span className="field-hint">WhatsApp: {n.whatsapp_status.replace(/_/g, " ")}</span>
                    )}
                  </div>
                ),
              },
              {
                key: "approval_required",
                header: "Approval required",
                render: (n) => (n.approval_required ? "Yes" : "No"),
              },
              {
                key: "actions",
                header: "Actions",
                render: (n) => (
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {n.status === "DRAFT" && (
                      <button className="btn btn--ghost" disabled={busyId === n.id} onClick={() => handleSubmit(n.id)}>
                        Submit
                      </button>
                    )}
                    {n.status === "PENDING_APPROVAL" && canApprove && (
                      <>
                        <button className="btn btn--primary" disabled={busyId === n.id} onClick={() => handleApprove(n.id)}>
                          Approve
                        </button>
                        <button className="btn btn--ghost" disabled={busyId === n.id} onClick={() => handleReject(n.id)}>
                          Reject
                        </button>
                      </>
                    )}
                    {n.whatsapp_url && (
                      <button className="btn btn--ghost" disabled={busyId === n.id} onClick={() => handleOpenWhatsApp(n)}>
                        Open WhatsApp
                      </button>
                    )}
                    {n.channel === "WHATSAPP" && n.whatsapp_status &&
                      ["QUEUED", "OPENED", "REQUIRES_USER_CONFIRMATION"].includes(n.whatsapp_status) &&
                      canApprove && (
                        <>
                          <button
                            className="btn btn--primary"
                            disabled={busyId === n.id}
                            onClick={() => handleWhatsAppConfirmSent(n.id)}
                          >
                            Confirm sent
                          </button>
                          <button
                            className="btn btn--ghost"
                            disabled={busyId === n.id}
                            onClick={() => handleWhatsAppMarkFailed(n.id)}
                          >
                            Mark failed
                          </button>
                        </>
                      )}
                    {n.channel === "WHATSAPP" && n.whatsapp_status === "REQUIRES_USER_CONFIRMATION" && (
                      <span className="field-hint">
                        The configured WhatsApp provider could not send automatically — send manually and confirm.
                      </span>
                    )}
                    {n.status === "FAILED" && n.failure_reason && (
                      <span className="field-hint">{n.failure_reason}</span>
                    )}
                  </div>
                ),
              },
            ]}
            rows={notifications}
            emptyLabel="No notifications yet."
          />
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
        </>
      )}

      <div className="card" style={{ marginTop: 24 }}>
        <div className="page-header" style={{ marginBottom: 0 }}>
          <h3 style={{ marginTop: 0 }}>Notification templates</h3>
          <div style={{ display: "flex", gap: 8 }}>
            <Link href="/communication-rules" className="btn btn--ghost">
              Communication rules editor
            </Link>
            {canApprove && (
              <button className="btn btn--ghost" onClick={() => setShowTemplateForm((v) => !v)}>
                {showTemplateForm ? "Cancel" : "New template"}
              </button>
            )}
          </div>
        </div>
        {showTemplateForm && (
          <form onSubmit={handleCreateTemplate} className="form-grid" style={{ marginTop: 16 }}>
            <div className="form-field">
              <label>Code*</label>
              <input name="code" required placeholder="e.g. PM_DUE_EMAIL" />
            </div>
            <div className="form-field">
              <label>Channel*</label>
              <select name="channel" required defaultValue="EMAIL">
                {NOTIFICATION_CHANNELS.map((c) => (
                  <option key={c} value={c}>
                    {c.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Event*</label>
              <input name="event" required placeholder="e.g. PM_DUE" />
            </div>
            <div className="form-field">
              <label>Subject template</label>
              <input name="subject_template" placeholder="Optional" />
            </div>
            <div className="form-field" style={{ gridColumn: "1 / -1" }}>
              <label>Body template*</label>
              <textarea name="body_template" required rows={3} />
            </div>
            {templateFormError && <div className="field-error">{templateFormError}</div>}
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit">
                Save template
              </button>
            </div>
          </form>
        )}
        <table className="data-table" style={{ marginTop: 16 }}>
          <thead>
            <tr>
              <th>Code</th>
              <th>Channel</th>
              <th>Event</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {templates.length === 0 && (
              <tr>
                <td colSpan={4}>No templates configured yet.</td>
              </tr>
            )}
            {templates.map((t) => (
              <tr key={t.id}>
                <td>{t.code}</td>
                <td>{t.channel.replace(/_/g, " ")}</td>
                <td>{t.event}</td>
                <td>
                  <StatusBadge value={t.is_active ? "ACTIVE" : "INACTIVE"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ProtectedShell>
  );
}
