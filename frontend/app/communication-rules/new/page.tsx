"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { ErrorState } from "../../../components/ErrorState";
import { LoadingState } from "../../../components/LoadingState";
import { useNotificationEvents } from "../../../hooks/useNotificationEvents";
import { api, ApiError } from "../../../lib/api";
import type { NotificationTemplate, NotificationTemplatePreview } from "../../../types";
import { COMMUNICATION_CHANNELS, templateVariablesForEvent } from "../../../types";

export default function NewCommunicationRulePage() {
  const router = useRouter();
  const { events, loading: eventsLoading } = useNotificationEvents();
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
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!eventType && events.length > 0) setEventType(events[0].code);
  }, [events, eventType]);

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

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);

    setSubmitting(true);
    try {
      const rule = await api.post<NotificationTemplate>("/api/v1/notifications/templates", {
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
      router.push(`/communication-rules/${rule.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create communication rule.");
      setSubmitting(false);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>New communication rule</h1>
          <p>Define sender/receiver details and the message template for one workflow event + channel.</p>
        </div>
      </div>

      <form className="card" onSubmit={handleSubmit}>
        <div className="form-grid">
          <div className="form-field">
            <label>Rule code*</label>
            <input name="code" required placeholder="e.g. REQ_SUBMITTED_EMAIL" />
          </div>
          <div className="form-field">
            <label>Event*</label>
            {eventsLoading ? (
              <LoadingState />
            ) : (
              <select name="event" required value={eventType} onChange={(e) => setEventType(e.target.value)}>
                {events.map((ev) => (
                  <option key={ev.code} value={ev.code}>
                    {ev.label}
                  </option>
                ))}
              </select>
            )}
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
            <select name="is_active" defaultValue="on">
              <option value="on">Enabled</option>
              <option value="off">Disabled</option>
            </select>
          </div>
        </div>

        <h3>Sender</h3>
        <div className="form-grid">
          <div className="form-field">
            <label>Sender name</label>
            <input value={senderName} onChange={(e) => setSenderName(e.target.value)} placeholder="Biomedical Department" />
          </div>
          {channel === "EMAIL" ? (
            <div className="form-field">
              <label>Sender email</label>
              <input value={senderEmail} onChange={(e) => setSenderEmail(e.target.value)} placeholder="biomed@hospital.example" />
            </div>
          ) : (
            <div className="form-field">
              <label>Sender WhatsApp number</label>
              <input value={senderWhatsapp} onChange={(e) => setSenderWhatsapp(e.target.value)} placeholder="+91 98765 43210" />
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
            <input value={receiverName} onChange={(e) => setReceiverName(e.target.value)} placeholder="{{receiver_name}}" />
          </div>
          {channel === "EMAIL" ? (
            <div className="form-field">
              <label>Receiver email</label>
              <input value={receiverEmail} onChange={(e) => setReceiverEmail(e.target.value)} placeholder="{{requester_email}}" />
            </div>
          ) : (
            <div className="form-field">
              <label>Receiver WhatsApp number</label>
              <input value={receiverWhatsapp} onChange={(e) => setReceiverWhatsapp(e.target.value)} placeholder="+91 98765 43210" />
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
            <input value={subjectTemplate} onChange={(e) => setSubjectTemplate(e.target.value)} placeholder="Requirement {{requirement_number}} submitted" />
          </div>
        )}
        <div className="form-field" style={{ marginBottom: 12 }}>
          <label>{channel === "EMAIL" ? "Email body template*" : "WhatsApp message template*"}</label>
          <textarea
            required
            rows={5}
            value={bodyTemplate}
            onChange={(e) => setBodyTemplate(e.target.value)}
            placeholder="Requirement {{requirement_number}} for {{hospital}} / {{branch}} is now {{status}}."
          />
        </div>
        <p className="field-hint">
          Available variables:{" "}
          {templateVariablesForEvent(eventType)
            .map((v) => `{{${v}}}`)
            .join(", ")}
        </p>

        <div className="card" style={{ background: "var(--color-surface-elevated)", marginBottom: 16 }}>
          <h4 style={{ marginTop: 0 }}>Preview before saving</h4>
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

        {error && <ErrorState message={error} />}
        <div>
          <button className="btn btn--primary" type="submit" disabled={submitting || !eventType}>
            {submitting ? "Saving..." : "Save rule"}
          </button>
        </div>
      </form>
    </ProtectedShell>
  );
}
