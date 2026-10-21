"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { useAuth } from "../../../hooks/useAuth";
import { useNotificationEvents } from "../../../hooks/useNotificationEvents";
import { api, ApiError } from "../../../lib/api";
import type { NotificationTemplate, NotificationTemplateDeleteResult, NotificationTemplatePreview } from "../../../types";
import { COMMUNICATION_CHANNELS, templateVariablesForEvent } from "../../../types";

export default function CommunicationRuleDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { hasPermission } = useAuth();
  const canManage = hasPermission("notification.approve");
  // Batch F — Item 7: Super Admin-only, distinct from notification.approve (create/edit).
  const canDelete = hasPermission("notification_template.delete");
  const { events, labels: eventLabels } = useNotificationEvents();

  const [rule, setRule] = useState<NotificationTemplate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionWarning, setActionWarning] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleted, setDeleted] = useState(false);

  const [eventType, setEventType] = useState("");
  const [channel, setChannel] = useState("EMAIL");
  const [autoSend, setAutoSend] = useState(false);
  const [subjectTemplate, setSubjectTemplate] = useState("");
  const [bodyTemplate, setBodyTemplate] = useState("");
  const [senderName, setSenderName] = useState("");
  const [senderEmail, setSenderEmail] = useState("");
  const [senderWhatsapp, setSenderWhatsapp] = useState("");
  const [receiverName, setReceiverName] = useState("");
  const [receiverEmail, setReceiverEmail] = useState("");
  const [receiverWhatsapp, setReceiverWhatsapp] = useState("");

  const [previewRequirementId, setPreviewRequirementId] = useState("");
  const [preview, setPreview] = useState<NotificationTemplatePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await api.get<NotificationTemplate>(`/api/v1/notifications/templates/${params.id}`);
      setRule(result);
      setEventType(result.event);
      setChannel(result.channel);
      setAutoSend(result.auto_send ?? false);
      setSubjectTemplate(result.subject_template ?? "");
      setBodyTemplate(result.body_template);
      setSenderName(result.sender_name ?? "");
      setSenderEmail(result.sender_email ?? "");
      setSenderWhatsapp(result.sender_whatsapp_number ?? "");
      setReceiverName(result.receiver_name ?? "");
      setReceiverEmail(result.receiver_email ?? "");
      setReceiverWhatsapp(result.receiver_whatsapp_number ?? "");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load communication rule.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function handlePreview() {
    setPreviewError(null);
    setPreviewing(true);
    try {
      const result = await api.post<NotificationTemplatePreview>("/api/v1/notifications/templates/preview", {
        channel,
        event: eventType,
        subject_template: subjectTemplate || null,
        body_template: bodyTemplate,
        sender_name: senderName || null,
        sender_email: senderEmail || null,
        sender_whatsapp_number: senderWhatsapp || null,
        receiver_name: receiverName || null,
        receiver_email: receiverEmail || null,
        receiver_whatsapp_number: receiverWhatsapp || null,
        requirement_id: previewRequirementId || null,
      });
      setPreview(result);
    } catch (err) {
      setPreviewError(err instanceof ApiError ? err.message : "Failed to render preview.");
    } finally {
      setPreviewing(false);
    }
  }

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!rule) return;
    setActionError(null);
    setActionMessage(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    setBusy(true);
    try {
      await api.patch<NotificationTemplate>(`/api/v1/notifications/templates/${rule.id}`, {
        code: formData.get("code"),
        channel,
        event: eventType,
        subject_template: subjectTemplate || null,
        body_template: bodyTemplate,
        is_active: formData.get("is_active") === "on",
        sender_name: senderName || null,
        sender_email: senderEmail || null,
        sender_whatsapp_number: senderWhatsapp || null,
        receiver_name: receiverName || null,
        receiver_email: receiverEmail || null,
        receiver_whatsapp_number: receiverWhatsapp || null,
        auto_send: autoSend,
      });
      setActionMessage("Communication rule updated.");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update communication rule.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!rule) return;
    setConfirmDelete(false);
    setActionError(null);
    setActionMessage(null);
    setActionWarning(null);
    setBusy(true);
    try {
      const result = await api.delete<NotificationTemplateDeleteResult>(`/api/v1/notifications/templates/${rule.id}`);
      if (result.warning) {
        // Stay put so the warning is actually seen before leaving — an immediate
        // redirect would drop it, same as any other transient success message.
        setActionWarning(result.warning);
        setActionMessage("Communication rule deleted.");
        setDeleted(true);
      } else {
        router.push("/communication-rules");
      }
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to delete communication rule.");
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return (
      <ProtectedShell>
        <ErrorState message={error} />
      </ProtectedShell>
    );
  }
  if (deleted) {
    return (
      <ProtectedShell>
        {actionMessage && <div className="state-panel state-panel--success">{actionMessage}</div>}
        {actionWarning && <div className="state-panel state-panel--warning">{actionWarning}</div>}
        <p>
          <a href="/communication-rules">Back to communication rules</a>
        </p>
      </ProtectedShell>
    );
  }
  if (!rule) {
    return (
      <ProtectedShell>
        <LoadingState />
      </ProtectedShell>
    );
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>{rule.code}</h1>
          <p>
            <StatusBadge value={eventLabels[rule.event] ?? rule.event} /> ·{" "}
            <StatusBadge value={rule.channel} /> ·{" "}
            <StatusBadge value={rule.is_active ? "ACTIVE" : "DISABLED"} />
          </p>
        </div>
        {canDelete && (
          <button className="btn btn--danger" onClick={() => setConfirmDelete(true)} disabled={busy}>
            Delete rule
          </button>
        )}
      </div>

      {actionMessage && <div className="state-panel state-panel--success">{actionMessage}</div>}
      {actionWarning && <div className="state-panel state-panel--warning">{actionWarning}</div>}
      {actionError && <ErrorState message={actionError} />}

      <form className="card" onSubmit={handleSave}>
        <fieldset disabled={!canManage} style={{ border: "none", padding: 0, margin: 0 }}>
          <div className="form-grid">
            <div className="form-field">
              <label>Rule code*</label>
              <input name="code" required defaultValue={rule.code} />
            </div>
            <div className="form-field">
              <label>Event*</label>
              <select name="event" required value={eventType} onChange={(e) => setEventType(e.target.value)}>
                {events.map((ev) => (
                  <option key={ev.code} value={ev.code}>
                    {ev.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Channel*</label>
              <select name="channel" value={channel} onChange={(e) => setChannel(e.target.value)}>
                {COMMUNICATION_CHANNELS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Enabled</label>
              <select name="is_active" defaultValue={rule.is_active ? "on" : "off"}>
                <option value="on">Enabled</option>
                <option value="off">Disabled</option>
              </select>
            </div>
          </div>

          <h3>Sender</h3>
          <div className="form-grid">
            <div className="form-field">
              <label>Sender name</label>
              <input value={senderName} onChange={(e) => setSenderName(e.target.value)} />
            </div>
            {channel === "EMAIL" ? (
              <div className="form-field">
                <label>Sender email</label>
                <input value={senderEmail} onChange={(e) => setSenderEmail(e.target.value)} />
              </div>
            ) : (
              <div className="form-field">
                <label>Sender WhatsApp number</label>
                <input value={senderWhatsapp} onChange={(e) => setSenderWhatsapp(e.target.value)} />
              </div>
            )}
          </div>

          <h3>Receiver</h3>
          <p className="field-hint">
            Use a fixed address, or a variable like <code>{"{{requester_email}}"}</code> to route dynamically.
          </p>
          <div className="form-grid">
            <div className="form-field">
              <label>Receiver name</label>
              <input value={receiverName} onChange={(e) => setReceiverName(e.target.value)} />
            </div>
            {channel === "EMAIL" ? (
              <div className="form-field">
                <label>Receiver email</label>
                <input value={receiverEmail} onChange={(e) => setReceiverEmail(e.target.value)} />
              </div>
            ) : (
              <div className="form-field">
                <label>Receiver WhatsApp number</label>
                <input value={receiverWhatsapp} onChange={(e) => setReceiverWhatsapp(e.target.value)} />
              </div>
            )}
          </div>

          <div className="form-field" style={{ marginTop: 12 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input type="checkbox" checked={autoSend} onChange={(e) => setAutoSend(e.target.checked)} />
              Send automatically
            </label>
            <span className="field-hint">
              {autoSend
                ? channel === "WHATSAPP"
                  ? "Delivered immediately through the linked WhatsApp Web session — no manual approval step. Delivery still depends on that session being connected; a failure is recorded on the notification and never affects the underlying record."
                  : "Delivered immediately through the configured mailbox — no manual approval step. A send failure is recorded on the notification and never affects the underlying record."
                : "Messages from this rule wait for manual approval before they are sent."}
            </span>
          </div>

          <h3>Message</h3>
          {channel === "EMAIL" && (
            <div className="form-field" style={{ marginBottom: 12 }}>
              <label>Email subject template</label>
              <input value={subjectTemplate} onChange={(e) => setSubjectTemplate(e.target.value)} />
            </div>
          )}
          <div className="form-field" style={{ marginBottom: 12 }}>
            <label>{channel === "EMAIL" ? "Email body template*" : "WhatsApp message template*"}</label>
            <textarea required rows={5} value={bodyTemplate} onChange={(e) => setBodyTemplate(e.target.value)} />
          </div>
          <p className="field-hint">
            Available variables:{" "}
            {templateVariablesForEvent(eventType)
              .map((v) => `{{${v}}}`)
              .join(", ")}
          </p>

          {canManage && (
            <div>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                {busy ? "Saving..." : "Save changes"}
              </button>
            </div>
          )}
        </fieldset>
      </form>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Preview</h3>
        <div className="form-field" style={{ maxWidth: 360, marginBottom: 8 }}>
          <label>Preview against a real requirement ID (optional)</label>
          <input value={previewRequirementId} onChange={(e) => setPreviewRequirementId(e.target.value)} placeholder="Leave blank to use sample data" />
        </div>
        <button type="button" className="btn btn--ghost" onClick={handlePreview} disabled={previewing || !bodyTemplate}>
          {previewing ? "Rendering..." : "Preview"}
        </button>
        {previewError && <ErrorState message={previewError} />}
        {preview && (
          <div style={{ marginTop: 12 }}>
            {preview.used_sample_data && <p className="field-hint">Rendered against sample data.</p>}
            {preview.rendered_subject && (
              <p>
                <strong>Subject:</strong> {preview.rendered_subject}
              </p>
            )}
            <p>
              <strong>Message:</strong>
            </p>
            <pre style={{ whiteSpace: "pre-wrap" }}>{preview.rendered_body}</pre>
            <p>
              <strong>Sender:</strong> {preview.rendered_sender_name ?? "-"} &lt;{preview.rendered_sender_email ?? preview.rendered_sender_whatsapp_number ?? "-"}&gt;
            </p>
            <p>
              <strong>Receiver:</strong> {preview.rendered_receiver_name ?? "-"} &lt;{preview.rendered_receiver_email ?? preview.rendered_receiver_whatsapp_number ?? "-"}&gt;
            </p>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete communication rule"
        message="This permanently deletes the rule. If it's the last active template covering this event, the event will be left without a configured notification — you'll see a warning after deleting, but the delete cannot be undone. Continue?"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </ProtectedShell>
  );
}
