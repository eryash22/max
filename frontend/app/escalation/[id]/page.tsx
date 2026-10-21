"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useParams } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { Pagination } from "../../../components/Pagination";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { api, ApiError } from "../../../lib/api";
import type { EscalationEvent, EscalationLevel, EscalationPolicy, Incident, Page } from "../../../types";
import { ESCALATION_TRIGGER_TYPES, INCIDENT_SEVERITIES } from "../../../types";

const EVENTS_PAGE_SIZE = 10;

export default function EscalationPolicyDetailPage() {
  const params = useParams<{ id: string }>();
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("incident.manage");

  const incidents = useLookup<Incident>("/api/v1/incidents");
  const incidentLabel = (id: string) => {
    const incident = incidents.find((i) => i.id === id);
    return incident ? `${incident.description.slice(0, 40)} (${incident.severity})` : id;
  };

  const [policy, setPolicy] = useState<EscalationPolicy | null>(null);
  const [levels, setLevels] = useState<EscalationLevel[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [events, setEvents] = useState<EscalationEvent[]>([]);
  const [eventsTotal, setEventsTotal] = useState(0);
  const [eventsPage, setEventsPage] = useState(1);
  const [eventsError, setEventsError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [policyResult, levelsResult] = await Promise.all([
        api.get<EscalationPolicy>(`/api/v1/escalation/policies/${params.id}`),
        api.get<EscalationLevel[]>(`/api/v1/escalation/policies/${params.id}/levels`),
      ]);
      setPolicy(policyResult);
      setLevels(levelsResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load escalation policy.");
    }
  }, [params.id]);

  const loadEvents = useCallback(async () => {
    setEventsError(null);
    try {
      const query = new URLSearchParams({
        policy_id: params.id,
        page: String(eventsPage),
        page_size: String(EVENTS_PAGE_SIZE),
      });
      const result = await api.get<Page<EscalationEvent>>(`/api/v1/escalation/events?${query.toString()}`);
      setEvents(result.items);
      setEventsTotal(result.total);
    } catch (err) {
      setEventsError(err instanceof ApiError ? err.message : "Failed to load escalation events.");
    }
  }, [params.id, eventsPage]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    loadEvents();
  }, [loadEvents]);

  async function handleUpdatePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!policy) return;
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.patch(`/api/v1/escalation/policies/${params.id}`, {
        name: formData.get("name"),
        trigger_type: formData.get("trigger_type"),
        severity_threshold: formData.get("severity_threshold") || null,
        device_criticality_threshold: formData.get("device_criticality_threshold") || null,
        is_active: formData.get("is_active") === "on",
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update policy.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddLevel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post(`/api/v1/escalation/policies/${params.id}/levels`, {
        level_number: Number(formData.get("level_number")),
        role_code: formData.get("role_code") || null,
        wait_minutes: formData.get("wait_minutes") ? Number(formData.get("wait_minutes")) : null,
      });
      form.reset();
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to add escalation level.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteLevel(levelId: string) {
    setActionError(null);
    setBusy(true);
    try {
      await api.delete(`/api/v1/escalation/policies/${params.id}/levels/${levelId}`);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to delete escalation level.");
    } finally {
      setBusy(false);
    }
  }

  async function handleFireEvent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/escalation/events", {
        incident_id: formData.get("incident_id"),
        policy_id: params.id,
        level_number: Number(formData.get("level_number")),
        notes: formData.get("notes") || null,
      });
      form.reset();
      setEventsPage(1);
      await loadEvents();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to log escalation event.");
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
  if (!policy) {
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
          <h1>{policy.name}</h1>
          <p>
            <StatusBadge value={policy.is_active ? "ACTIVE" : "INACTIVE"} /> · {policy.trigger_type.replace(/_/g, " ")}
          </p>
        </div>
      </div>

      {actionError && <ErrorState message={actionError} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Policy information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Trigger type</td>
              <td>{policy.trigger_type.replace(/_/g, " ")}</td>
            </tr>
            <tr>
              <td>Severity threshold</td>
              <td>{policy.severity_threshold ?? "Not set"}</td>
            </tr>
            <tr>
              <td>Device criticality threshold</td>
              <td>{policy.device_criticality_threshold ?? "Not set"}</td>
            </tr>
            <tr>
              <td>Status</td>
              <td>
                <StatusBadge value={policy.is_active ? "ACTIVE" : "INACTIVE"} />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Edit policy</h3>
          <form onSubmit={handleUpdatePolicy} className="form-grid">
            <div className="form-field">
              <label>Policy name*</label>
              <input name="name" required defaultValue={policy.name} />
            </div>
            <div className="form-field">
              <label>Trigger type*</label>
              <select name="trigger_type" required defaultValue={policy.trigger_type}>
                {ESCALATION_TRIGGER_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Severity threshold</label>
              <select name="severity_threshold" defaultValue={policy.severity_threshold ?? ""}>
                <option value="">Not set</option>
                {INCIDENT_SEVERITIES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Device criticality threshold</label>
              <input name="device_criticality_threshold" defaultValue={policy.device_criticality_threshold ?? ""} />
            </div>
            <div className="form-field form-field--checkbox">
              <label>
                <input name="is_active" type="checkbox" defaultChecked={policy.is_active} /> Active
              </label>
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Save changes
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Escalation levels</h3>
        <p className="field-hint">The ordered chain: level 1 fires first, then level 2 after its wait, and so on.</p>
        <table className="data-table">
          <thead>
            <tr>
              <th>Level</th>
              <th>Role</th>
              <th>Wait (minutes)</th>
              {canWrite && <th />}
            </tr>
          </thead>
          <tbody>
            {levels.length === 0 && (
              <tr>
                <td colSpan={canWrite ? 4 : 3}>No levels configured yet.</td>
              </tr>
            )}
            {levels.map((l) => (
              <tr key={l.id}>
                <td>{l.level_number}</td>
                <td>{l.role_code ?? "-"}</td>
                <td>{l.wait_minutes ?? "-"}</td>
                {canWrite && (
                  <td>
                    <button className="btn btn--ghost" onClick={() => handleDeleteLevel(l.id)} disabled={busy}>
                      Remove
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>

        {canWrite && (
          <form onSubmit={handleAddLevel} className="form-grid" style={{ marginTop: 16 }}>
            <div className="form-field">
              <label>Level number*</label>
              <input name="level_number" type="number" min={1} required />
            </div>
            <div className="form-field">
              <label>Role</label>
              <input name="role_code" placeholder="e.g. BIOMEDICAL_ENGINEER" />
            </div>
            <div className="form-field">
              <label>Wait (minutes)</label>
              <input name="wait_minutes" type="number" min={0} placeholder="Optional" />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Add level
              </button>
            </div>
          </form>
        )}
      </div>

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Log escalation event</h3>
          <p className="field-hint">
            Records that a level of this policy actually fired for a real incident. No background
            scheduler exists in this codebase to fire escalations automatically on a timer — this
            logs a firing the same way a real trigger would, whether initiated by a person or a
            future automated job.
          </p>
          <form onSubmit={handleFireEvent} className="form-grid">
            <div className="form-field">
              <label>Incident*</label>
              <select name="incident_id" required defaultValue="">
                <option value="" disabled>
                  Select incident
                </option>
                {incidents.map((i) => (
                  <option key={i.id} value={i.id}>
                    {incidentLabel(i.id)}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Level number*</label>
              <select name="level_number" required defaultValue="">
                <option value="" disabled>
                  Select level
                </option>
                {levels.map((l) => (
                  <option key={l.id} value={l.level_number}>
                    Level {l.level_number}
                    {l.role_code ? ` (${l.role_code})` : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field" style={{ gridColumn: "1 / -1" }}>
              <label>Notes</label>
              <textarea name="notes" rows={2} placeholder="Optional" />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy || levels.length === 0}>
                Log event
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Escalation event history</h3>
        {eventsError && <ErrorState message={eventsError} />}
        <table className="data-table">
          <thead>
            <tr>
              <th>Incident</th>
              <th>Level</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 && (
              <tr>
                <td colSpan={3}>No escalation events logged yet for this policy.</td>
              </tr>
            )}
            {events.map((e) => (
              <tr key={e.id}>
                <td>{incidentLabel(e.incident_id)}</td>
                <td>{e.level_number}</td>
                <td>{e.notes ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Pagination page={eventsPage} pageSize={EVENTS_PAGE_SIZE} total={eventsTotal} onPageChange={setEventsPage} />
      </div>
    </ProtectedShell>
  );
}
