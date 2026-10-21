"use client";

import { useState } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { SearchBar } from "../../components/SearchBar";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { usePaginatedResource } from "../../hooks/usePaginatedResource";
import { useAuth } from "../../hooks/useAuth";
import { useNotificationEvents } from "../../hooks/useNotificationEvents";
import { api, ApiError } from "../../lib/api";
import type { NotificationTemplate } from "../../types";
import { COMMUNICATION_CHANNELS } from "../../types";

interface DeleteResult {
  warning?: string | null;
}

export default function CommunicationRulesPage() {
  const [search, setSearch] = useState("");
  const [event, setEvent] = useState("");
  const [channel, setChannel] = useState("");

  const query: Record<string, string> = {};
  if (search) query.search = search;
  if (event) query.event = event;
  if (channel) query.channel = channel;

  const { data, page, setPage, loading, error, reload } = usePaginatedResource<NotificationTemplate>(
    "/api/v1/notifications/templates",
    query
  );
  const { hasPermission } = useAuth();
  const canManage = hasPermission("notification.approve");
  const canDelete = hasPermission("notification_template.delete");
  const { events, labels: eventLabels } = useNotificationEvents();

  const [rowError, setRowError] = useState<string | null>(null);
  const [rowWarning, setRowWarning] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const receiverSummary = (rule: NotificationTemplate) =>
    rule.channel === "EMAIL" ? rule.receiver_email ?? "-" : rule.receiver_whatsapp_number ?? "-";
  const senderSummary = (rule: NotificationTemplate) =>
    rule.channel === "EMAIL" ? rule.sender_email ?? "-" : rule.sender_whatsapp_number ?? "-";

  async function handleDelete(ruleId: string) {
    setRowError(null);
    setRowWarning(null);
    setRowBusy(ruleId);
    try {
      const result = await api.delete<DeleteResult>(`/api/v1/notifications/templates/${ruleId}`);
      setConfirmDeleteId(null);
      if (result?.warning) setRowWarning(result.warning);
      await reload();
    } catch (err) {
      setConfirmDeleteId(null);
      setRowError(err instanceof ApiError ? err.message : "Failed to delete communication rule.");
    } finally {
      setRowBusy(null);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Communication rules</h1>
          <p>
            Configure who is emailed or WhatsApp&apos;d, and with what message, for each stage of the
            Requirement &rarr; Approval &rarr; Fulfillment &rarr; Delivery workflow.
          </p>
        </div>
        {canManage && (
          <Link href="/communication-rules/new" className="btn btn--primary">
            New rule
          </Link>
        )}
      </div>

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search rule code..." />
        <select value={event} onChange={(e) => setEvent(e.target.value)}>
          <option value="">All events</option>
          {events.map((ev) => (
            <option key={ev.code} value={ev.code}>
              {ev.label}
            </option>
          ))}
        </select>
        <select value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="">All channels</option>
          {COMMUNICATION_CHANNELS.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>

      {rowWarning && <div className="state-panel state-panel--warning">{rowWarning}</div>}
      {rowError && <ErrorState message={rowError} />}
      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<NotificationTemplate>
            columns={[
              {
                key: "code",
                header: "Rule code",
                render: (r) => (canManage ? <Link href={`/communication-rules/${r.id}`}>{r.code}</Link> : r.code),
              },
              { key: "event", header: "Event", render: (r) => eventLabels[r.event] ?? r.event },
              { key: "channel", header: "Channel", render: (r) => <StatusBadge value={r.channel} /> },
              { key: "sender", header: "Sender", render: (r) => senderSummary(r) },
              { key: "receiver", header: "Receiver", render: (r) => receiverSummary(r) },
              { key: "enabled", header: "Enabled", render: (r) => <StatusBadge value={r.is_active ? "ACTIVE" : "DISABLED"} /> },
              {
                key: "delivery",
                header: "Delivery",
                // A Super Admin scanning this list needs to know at a glance which rules
                // send on their own — that is the difference between "someone will review
                // this" and "this already went out".
                render: (r) => <StatusBadge value={r.auto_send ? "AUTOMATIC" : "NEEDS APPROVAL"} />,
              },
              ...(canManage || canDelete
                ? [
                    {
                      key: "actions",
                      header: "Actions",
                      render: (r: NotificationTemplate) => (
                        <div style={{ display: "flex", gap: 6 }}>
                          {canManage && (
                            <Link className="btn btn--ghost" href={`/communication-rules/${r.id}`}>
                              Edit
                            </Link>
                          )}
                          {canDelete && (
                            <button
                              className="btn btn--ghost"
                              disabled={rowBusy === r.id}
                              onClick={() => setConfirmDeleteId(r.id)}
                            >
                              Delete
                            </button>
                          )}
                        </div>
                      ),
                    },
                  ]
                : []),
            ]}
            rows={data.items}
            emptyLabel="No communication rules configured yet."
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete communication rule"
        message="This permanently deletes the rule. If it's the last active rule covering its event/channel, that event becomes uncovered — you'll get a warning after deleting, not a block. Continue?"
        confirmLabel="Delete"
        onConfirm={() => confirmDeleteId && handleDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </ProtectedShell>
  );
}
