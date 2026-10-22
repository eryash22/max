"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { api, ApiError } from "../../../lib/api";
import type { Asset, Branch, Department, DeviceModel, Incident } from "../../../types";
import { INCIDENT_SEVERITIES } from "../../../types";

// Branch-scoping hardening pass — same rationale as requirements/new/page.tsx:
// UNIT_HEAD/TL's branch is fixed by role assignment, not a free choice.
const BRANCH_LOCKED_ROLES = ["UNIT_HEAD", "TL"];

export default function NewIncidentPage() {
  const router = useRouter();
  const { user } = useAuth();
  const branchLocked = BRANCH_LOCKED_ROLES.some((r) => user?.roles.includes(r));
  // status_filter=ACTIVE — a deactivated branch/department must not be selectable
  // when reporting a new incident (see backend/app/routers/hospitals.py).
  const branches = useLookup<Branch>("/api/v1/branches?status_filter=ACTIVE");
  const departments = useLookup<Department>("/api/v1/departments?status_filter=ACTIVE");
  const assets = useLookup<Asset>("/api/v1/assets");
  const deviceModels = useLookup<DeviceModel>("/api/v1/device-models");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [problemCategory, setProblemCategory] = useState("");
  const [branchId, setBranchId] = useState("");
  const [assetId, setAssetId] = useState("");

  useEffect(() => {
    if (branchLocked && user?.hospital_scope_id) {
      setBranchId(user.hospital_scope_id);
    }
  }, [branchLocked, user]);

  // Same-branch-only device picker — asset_id must belong to whichever branch is
  // currently selected (backend cross-checks this too, see
  // routers/incidents.py:report_incident). For UNIT_HEAD/TL, GET /assets is already
  // server-scoped to their own branch regardless of this filter (branch_scope_id), so
  // this filter mainly matters for other roles who freely pick a branch.
  const assetsForBranch = useMemo(
    () => (branchId ? assets.filter((a) => a.branch_id === branchId) : assets),
    [assets, branchId]
  );

  // Department names aren't globally unique — the same master (e.g.
  // "Physiotherapy") is assigned once per branch, so the full unfiltered list
  // renders as repeated identical-looking options. Scope it to the selected
  // branch, same as assetsForBranch above.
  const departmentsForBranch = useMemo(
    () => (branchId ? departments.filter((d) => d.branch_id === branchId) : departments),
    [departments, branchId]
  );

  useEffect(() => {
    if (assetId && !assetsForBranch.some((a) => a.id === assetId)) {
      setAssetId("");
    }
  }, [assetsForBranch, assetId]);

  // Restructure consolidation pass, Part G — the unified "Raise Request / Incident"
  // hub (/raise) links here with ?problem_category=... for Device Issue/Maintenance/
  // Calibration/Service Visit/Other Biomedical Issue; still the same Incident domain,
  // no PM/Calibration/Service Visit logic duplicated here.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const category = params.get("problem_category");
    if (category) setProblemCategory(category);
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const formData = new FormData(event.currentTarget);
    try {
      const incident = await api.post<Incident>("/api/v1/incidents", {
        branch_id: formData.get("branch_id"),
        department_id: formData.get("department_id") || null,
        asset_id: formData.get("asset_id") || null,
        description: formData.get("description"),
        severity: formData.get("severity"),
        problem_category: formData.get("problem_category") || null,
        clinical_impact: formData.get("clinical_impact") || null,
        patient_care_impact: formData.get("patient_care_impact") || null,
        device_status_at_report: formData.get("device_status_at_report") || null,
      });
      router.push(`/incidents/${incident.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to report incident.");
      setSubmitting(false);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Report device problem</h1>
          <p>Describe what's wrong. A biomedical engineer will be notified.</p>
        </div>
      </div>

      <form className="card" onSubmit={handleSubmit} style={{ maxWidth: 640 }}>
        <div className="form-grid">
          <div className="form-field">
            <label>Branch*</label>
            {branchLocked ? (
              <>
                <input
                  type="text"
                  value={branches.find((b) => b.id === branchId)?.name ?? "Loading..."}
                  disabled
                  readOnly
                />
                <input type="hidden" name="branch_id" value={branchId} />
              </>
            ) : (
              <select name="branch_id" required value={branchId} onChange={(e) => setBranchId(e.target.value)}>
                <option value="" disabled>
                  Select branch
                </option>
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            )}
          </div>
          <div className="form-field">
            <label>Department</label>
            <select name="department_id" defaultValue="">
              <option value="">Not specified</option>
              {departmentsForBranch.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Device / Asset</label>
            <select name="asset_id" value={assetId} onChange={(e) => setAssetId(e.target.value)}>
              <option value="">Not linked to a specific device</option>
              {assetsForBranch.map((a) => {
                const modelName = deviceModels.find((m) => m.id === a.device_model_id)?.name;
                const tag = a.asset_number ?? a.serial_number ?? a.id;
                return (
                  <option key={a.id} value={a.id}>
                    {modelName ? `${modelName} — ${tag}` : tag}
                  </option>
                );
              })}
            </select>
          </div>
          <div className="form-field">
            <label>Severity*</label>
            <select name="severity" defaultValue="MEDIUM">
              {INCIDENT_SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Problem category</label>
            <input
              name="problem_category"
              placeholder="e.g. Not powering on"
              value={problemCategory}
              onChange={(e) => setProblemCategory(e.target.value)}
            />
          </div>
        </div>

        <div className="form-field" style={{ marginBottom: 12 }}>
          <label>Description*</label>
          <textarea name="description" required rows={4} />
        </div>

        <div className="form-grid">
          <div className="form-field">
            <label>Clinical impact</label>
            <input name="clinical_impact" placeholder="Optional" />
          </div>
          <div className="form-field">
            <label>Patient-care impact</label>
            <input name="patient_care_impact" placeholder="Optional" />
          </div>
          <div className="form-field">
            <label>Device status when reported</label>
            <input name="device_status_at_report" placeholder="Optional" />
          </div>
        </div>

        {error && <div className="field-error">{error}</div>}
        <button className="btn btn--primary" type="submit" disabled={submitting}>
          {submitting ? "Submitting..." : "Submit report"}
        </button>
      </form>
    </ProtectedShell>
  );
}
