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
import { api, ApiError } from "../../../lib/api";
import type { Asset, CalibrationRecord, CalibrationSchedule, DeviceModel, Page } from "../../../types";
import { CALIBRATION_FREQUENCIES, CALIBRATION_PASS_FAIL } from "../../../types";

export default function CalibrationScheduleDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("calibration.write");

  const assets = useLookup<Asset>("/api/v1/assets");
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const modelName = (id: string) => models.find((m) => m.id === id)?.name ?? id;

  const [schedule, setSchedule] = useState<CalibrationSchedule | null>(null);
  const [records, setRecords] = useState<CalibrationRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Workflow consolidation pass — carried over from /calibration/new?incident_id= when
  // this schedule was created for an Incident's "Create Calibration Record" action;
  // links the CalibrationRecord produced below back to that incident.
  const [incidentId, setIncidentId] = useState<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setIncidentId(params.get("incident_id"));
  }, []);

  const load = useCallback(async () => {
    try {
      const [scheduleResult, recordsResult] = await Promise.all([
        api.get<CalibrationSchedule>(`/api/v1/calibration/schedules/${params.id}`),
        api.get<Page<CalibrationRecord>>(`/api/v1/calibration/records?schedule_id=${params.id}&page_size=100`),
      ]);
      setSchedule(scheduleResult);
      setRecords(recordsResult.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load calibration schedule.");
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
      await api.patch(`/api/v1/calibration/schedules/${params.id}`, {
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
      await api.delete(`/api/v1/calibration/schedules/${params.id}`);
      router.push("/calibration");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to delete schedule.");
      setBusy(false);
    }
  }

  async function handleRecordCalibration(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!schedule) return;
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/calibration/records", {
        schedule_id: schedule.id,
        asset_id: schedule.asset_id,
        incident_id: incidentId,
        performed_date: formData.get("performed_date") || null,
        certificate_number: formData.get("certificate_number") || null,
        calibration_agency: formData.get("calibration_agency") || null,
        reference_standard: formData.get("reference_standard") || null,
        result: formData.get("result") || null,
        pass_fail: formData.get("pass_fail") || null,
        next_due_date: formData.get("next_due_date") || null,
        notes: formData.get("notes") || null,
      });
      form.reset();
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to record calibration.");
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
          <h1>{asset ? modelName(asset.device_model_id) : "Unknown device"}</h1>
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
              <td>
                {asset
                  ? `${modelName(asset.device_model_id)} (${asset.asset_number ?? asset.serial_number ?? "no ref"})`
                  : "Unknown device"}
              </td>
            </tr>
            <tr>
              <td>Serial number</td>
              <td>{asset?.serial_number ?? "Not provided"}</td>
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
              <td>Last calibration date</td>
              <td>{latestRecord?.performed_date ?? "Not yet calibrated"}</td>
            </tr>
            <tr>
              <td>Last result</td>
              <td>{latestRecord?.pass_fail ? <StatusBadge value={latestRecord.pass_fail} /> : "-"}</td>
            </tr>
            <tr>
              <td>Calibration agency</td>
              <td>{latestRecord?.calibration_agency ?? "-"}</td>
            </tr>
            <tr>
              <td>Certificate number</td>
              <td>{latestRecord?.certificate_number ?? "No certificate on file"}</td>
            </tr>
            <tr>
              <td>Reference standard</td>
              <td>{latestRecord?.reference_standard ?? "-"}</td>
            </tr>
            <tr>
              <td>Remarks</td>
              <td>{latestRecord?.notes ?? "-"}</td>
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
                {CALIBRATION_FREQUENCIES.map((f) => (
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
          <h3 style={{ marginTop: 0 }}>Record calibration</h3>
          {incidentId && (
            <p className="field-hint">
              Linked to <a href={`/incidents/${incidentId}`}>incident {incidentId}</a> — recording completion here
              will reference it.
            </p>
          )}
          <form onSubmit={handleRecordCalibration} className="form-grid">
            <div className="form-field">
              <label>Calibration date</label>
              <input name="performed_date" type="date" />
            </div>
            <div className="form-field">
              <label>Result</label>
              <select name="pass_fail" defaultValue="">
                <option value="">Not recorded</option>
                {CALIBRATION_PASS_FAIL.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Calibration agency</label>
              <input name="calibration_agency" placeholder="Optional" />
            </div>
            <div className="form-field">
              <label>Certificate number</label>
              <input name="certificate_number" placeholder="Optional" />
            </div>
            <div className="form-field">
              <label>Reference standard</label>
              <input name="reference_standard" placeholder="Optional" />
            </div>
            <div className="form-field">
              <label>Next due date</label>
              <input name="next_due_date" type="date" />
              <span className="field-hint">Leave blank to auto-project from the schedule's frequency.</span>
            </div>
            <div className="form-field" style={{ gridColumn: "1 / -1" }}>
              <label>Result detail</label>
              <textarea name="result" rows={2} placeholder="Optional free-text result detail" />
            </div>
            <div className="form-field" style={{ gridColumn: "1 / -1" }}>
              <label>Remarks</label>
              <textarea name="notes" rows={3} placeholder="Optional" />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Record calibration
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Calibration history</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Performed</th>
              <th>Result</th>
              <th>Agency</th>
              <th>Certificate #</th>
              <th>Reference standard</th>
              <th>Next due</th>
              <th>Remarks</th>
            </tr>
          </thead>
          <tbody>
            {records.length === 0 && (
              <tr>
                <td colSpan={7}>No calibration records yet.</td>
              </tr>
            )}
            {records.map((r) => (
              <tr key={r.id}>
                <td>{r.performed_date ?? "-"}</td>
                <td>{r.pass_fail ? <StatusBadge value={r.pass_fail} /> : "-"}</td>
                <td>{r.calibration_agency ?? "-"}</td>
                <td>{r.certificate_number ?? "-"}</td>
                <td>{r.reference_standard ?? "-"}</td>
                <td>{r.next_due_date ?? "-"}</td>
                <td>{r.notes ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete calibration schedule"
        message="This permanently removes the schedule. Existing calibration records are kept for history. Continue?"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </ProtectedShell>
  );
}
