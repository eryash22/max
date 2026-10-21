"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { useUserDirectory } from "../../../hooks/useUserDirectory";
import { api, ApiError } from "../../../lib/api";
import { locationLabel } from "../../../lib/locationLabel";
import type { Asset, Branch, DeviceModel, Page, PMRecord, PMSchedule } from "../../../types";
import { PM_FREQUENCIES, PM_RESULT_OPTIONS } from "../../../types";

function parseChecklist(text: string): Record<string, string> | null {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) return null;
  const checklist: Record<string, string> = {};
  lines.forEach((line, index) => {
    const separatorIndex = line.indexOf(":");
    if (separatorIndex === -1) {
      checklist[`item_${index + 1}`] = line;
    } else {
      checklist[line.slice(0, separatorIndex).trim()] = line.slice(separatorIndex + 1).trim();
    }
  });
  return checklist;
}

export default function PMScheduleDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("pm.write");

  const assets = useLookup<Asset>("/api/v1/assets");
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const branches = useLookup<Branch>("/api/v1/branches");
  const users = useUserDirectory();
  const modelName = (id: string) => models.find((m) => m.id === id)?.name ?? id;
  const engineerName = (id: string | null | undefined) => (id ? users.find((u) => u.id === id)?.name ?? id : "Unassigned");

  const [schedule, setSchedule] = useState<PMSchedule | null>(null);
  const [records, setRecords] = useState<PMRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Workflow consolidation pass — carried over from /maintenance/new?incident_id= when
  // this schedule was created for an Incident's "Create PM Record" action; links the
  // PMRecord produced by "Execute PM" below back to that incident.
  const [incidentId, setIncidentId] = useState<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setIncidentId(params.get("incident_id"));
  }, []);

  const load = useCallback(async () => {
    try {
      const [scheduleResult, recordsResult] = await Promise.all([
        api.get<PMSchedule>(`/api/v1/pm/schedules/${params.id}`),
        api.get<Page<PMRecord>>(`/api/v1/pm/records?schedule_id=${params.id}&page_size=100`),
      ]);
      setSchedule(scheduleResult);
      setRecords(recordsResult.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load PM schedule.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  const asset = useMemo(() => assets.find((a) => a.id === schedule?.asset_id) ?? null, [assets, schedule]);
  const latestRecord = records[0] ?? null;

  async function handleUpdateSchedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    const frequency = (formData.get("frequency") as string) || null;
    try {
      await api.patch(`/api/v1/pm/schedules/${params.id}`, {
        frequency,
        custom_interval_days:
          frequency === "CUSTOM" && formData.get("custom_interval_days")
            ? Number(formData.get("custom_interval_days"))
            : null,
        next_due_date: formData.get("next_due_date") || null,
        is_active: formData.get("is_active") === "on",
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update schedule.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    setConfirmDelete(false);
    setBusy(true);
    try {
      await api.delete(`/api/v1/pm/schedules/${params.id}`);
      router.push("/maintenance");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to delete schedule.");
      setBusy(false);
    }
  }

  async function handleExecute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!schedule) return;
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/pm/records", {
        schedule_id: schedule.id,
        asset_id: schedule.asset_id,
        incident_id: incidentId,
        engineer_id: formData.get("engineer_id") || null,
        performed_date: formData.get("performed_date") || null,
        checklist: parseChecklist((formData.get("checklist") as string) || ""),
        parts_replaced: formData.get("parts_replaced") || null,
        electrical_safety_result: formData.get("electrical_safety_result") || null,
        functional_check_result: formData.get("functional_check_result") || null,
        calibration_status: formData.get("calibration_status") || null,
        next_due_date: formData.get("next_due_date") || null,
        notes: formData.get("notes") || null,
      });
      form.reset();
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to record PM completion.");
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
  if (!schedule) {
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
          <h1>{asset?.asset_number ?? schedule.asset_id}</h1>
          <p>
            <StatusBadge value={schedule.due_status} />
            {schedule.frequency ? ` · ${schedule.frequency.replace(/_/g, " ")}` : " · No frequency set"}
            {!schedule.is_active ? " · Inactive" : ""}
          </p>
        </div>
        {canWrite && (
          <button className="btn btn--ghost" onClick={() => setConfirmDelete(true)} disabled={busy}>
            Delete schedule
          </button>
        )}
      </div>

      {actionError && <ErrorState message={actionError} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Schedule &amp; asset information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Asset</td>
              <td>{asset ? `${asset.asset_number} (${modelName(asset.device_model_id)})` : schedule.asset_id}</td>
            </tr>
            <tr>
              <td>Serial number</td>
              <td>{asset?.serial_number ?? "Not provided"}</td>
            </tr>
            <tr>
              <td>Branch / Warehouse</td>
              <td>{locationLabel(asset?.branch_id, branches)}</td>
            </tr>
            <tr>
              <td>Asset status</td>
              <td>{asset ? <StatusBadge value={asset.status} /> : "-"}</td>
            </tr>
            <tr>
              <td>Frequency</td>
              <td>{schedule.frequency?.replace(/_/g, " ") ?? "Not set"}</td>
            </tr>
            <tr>
              <td>Custom interval</td>
              <td>{schedule.custom_interval_days ? `${schedule.custom_interval_days} days` : "-"}</td>
            </tr>
            <tr>
              <td>Next due date</td>
              <td>{schedule.next_due_date ?? "Not scheduled"}</td>
            </tr>
            <tr>
              <td>Due status</td>
              <td>
                <StatusBadge value={schedule.due_status} />
              </td>
            </tr>
            <tr>
              <td>Responsible BME (last PM)</td>
              <td>{engineerName(latestRecord?.engineer_id)}</td>
            </tr>
            <tr>
              <td>Last checklist</td>
              <td>
                {latestRecord?.checklist ? (
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {Object.entries(latestRecord.checklist).map(([key, value]) => (
                      <li key={key}>
                        {key}: {String(value)}
                      </li>
                    ))}
                  </ul>
                ) : (
                  "No checklist recorded yet"
                )}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Edit schedule</h3>
          <form onSubmit={handleUpdateSchedule} className="form-grid">
            <div className="form-field">
              <label>Frequency</label>
              <select name="frequency" defaultValue={schedule.frequency ?? ""}>
                <option value="">Not set</option>
                {PM_FREQUENCIES.map((f) => (
                  <option key={f} value={f}>
                    {f.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Custom interval (days)</label>
              <input
                name="custom_interval_days"
                type="number"
                min={1}
                defaultValue={schedule.custom_interval_days ?? ""}
              />
            </div>
            <div className="form-field">
              <label>Next due date</label>
              <input name="next_due_date" type="date" defaultValue={schedule.next_due_date ?? ""} />
            </div>
            <div className="form-field form-field--checkbox">
              <label>
                <input name="is_active" type="checkbox" defaultChecked={schedule.is_active} /> Active
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

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Execute PM</h3>
          {incidentId && (
            <p className="field-hint">
              Linked to <a href={`/incidents/${incidentId}`}>incident {incidentId}</a> — recording completion here
              will reference it.
            </p>
          )}
          <form onSubmit={handleExecute} className="form-grid">
            <div className="form-field">
              <label>Engineer user ID</label>
              <input name="engineer_id" placeholder="Optional" />
            </div>
            <div className="form-field">
              <label>Completion date</label>
              <input name="performed_date" type="date" />
            </div>
            <div className="form-field">
              <label>Electrical safety result</label>
              <select name="electrical_safety_result" defaultValue="">
                <option value="">Not recorded</option>
                {PM_RESULT_OPTIONS.map((r) => (
                  <option key={r} value={r}>
                    {r.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Functional check result</label>
              <select name="functional_check_result" defaultValue="">
                <option value="">Not recorded</option>
                {PM_RESULT_OPTIONS.map((r) => (
                  <option key={r} value={r}>
                    {r.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Calibration status</label>
              <select name="calibration_status" defaultValue="">
                <option value="">Not recorded</option>
                {PM_RESULT_OPTIONS.map((r) => (
                  <option key={r} value={r}>
                    {r.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Parts used</label>
              <input name="parts_replaced" placeholder="Optional" />
            </div>
            <div className="form-field">
              <label>Next due date</label>
              <input name="next_due_date" type="date" />
              <span className="field-hint">Leave blank to auto-project from the schedule's frequency.</span>
            </div>
            <div className="form-field" style={{ gridColumn: "1 / -1" }}>
              <label>Checklist</label>
              <textarea name="checklist" rows={4} placeholder={"One item per line, e.g.\nVisual inspection: OK\nEarth leakage test: PASS"} />
            </div>
            <div className="form-field" style={{ gridColumn: "1 / -1" }}>
              <label>Observations / remarks</label>
              <textarea name="notes" rows={3} placeholder="Optional" />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Record completion
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>PM history</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Performed</th>
              <th>Engineer</th>
              <th>Electrical</th>
              <th>Functional</th>
              <th>Calibration</th>
              <th>Parts used</th>
              <th>Next due</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {records.length === 0 && (
              <tr>
                <td colSpan={8}>No completed PM records yet.</td>
              </tr>
            )}
            {records.map((r) => (
              <tr key={r.id}>
                <td>{r.performed_date ?? "-"}</td>
                <td>{engineerName(r.engineer_id)}</td>
                <td>{r.electrical_safety_result ? <StatusBadge value={r.electrical_safety_result} /> : "-"}</td>
                <td>{r.functional_check_result ? <StatusBadge value={r.functional_check_result} /> : "-"}</td>
                <td>{r.calibration_status ? <StatusBadge value={r.calibration_status} /> : "-"}</td>
                <td>{r.parts_replaced ?? "-"}</td>
                <td>{r.next_due_date ?? "-"}</td>
                <td>{r.notes ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete PM schedule"
        message="This permanently removes the schedule. Existing PM records are kept for history. Continue?"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </ProtectedShell>
  );
}
