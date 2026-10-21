"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { ErrorState } from "../../../components/ErrorState";
import { RequirementItemsTable, buildItemsPayload, makeEmptyRow, validateItemRows, type ItemRow } from "../../../components/RequirementItemsTable";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { api, ApiError } from "../../../lib/api";
import type { Asset, Branch, Consumable, Department, DeviceModel, InventoryItem, Requirement } from "../../../types";
import { REQUIREMENT_PRIORITIES } from "../../../types";

// Branch-scoping hardening pass — a UNIT_HEAD/TL's branch is fixed by their role
// assignment (User.hospital_scope_id); giving them an editable dropdown let them raise
// a requirement for another branch, which the backend then rejected on the very next
// GET (the "Requirement not found" bug — see routers/requirements.py:create_requirement's
// comment). Every other role keeps the full branch dropdown as before.
const BRANCH_LOCKED_ROLES = ["UNIT_HEAD", "TL"];

export default function NewRequirementPage() {
  const router = useRouter();
  const { user } = useAuth();
  const branchLocked = BRANCH_LOCKED_ROLES.some((r) => user?.roles.includes(r));
  // status_filter=ACTIVE — a deactivated branch/department must not be selectable
  // when raising a new requirement (see backend/app/routers/hospitals.py).
  const branches = useLookup<Branch>("/api/v1/branches?status_filter=ACTIVE");
  const departments = useLookup<Department>("/api/v1/departments?status_filter=ACTIVE");
  // Correction pass — normal Device requirement is now a single searchable dropdown
  // over the full catalog (no Category step), so the complete list is loaded once up
  // front (fetchAll, same pattern as inventory/new/page.tsx's consumable picker)
  // rather than paged, and `withStatus` lets the picker show "loading"/"failed to
  // load" instead of a misleading empty list — see RequirementItemsTable.tsx.
  const { items: deviceModels, loading: deviceModelsLoading, error: deviceModelsError } = useLookup<DeviceModel>(
    "/api/v1/device-models",
    { fetchAll: true, withStatus: true }
  );
  const inventoryItems = useLookup<InventoryItem>("/api/v1/inventory/items");
  // Business-rule correction: a consumable requirement is always fulfilled from
  // CENTRAL STOCK to the requesting branch, never branch-to-branch — the requester
  // picks WHAT they need, never WHICH branch's stock row. central_only=true
  // returns exactly one row per catalog item (the central/hub pool), so no branch
  // is ever shown or selectable here. See backend/app/routers/inventory.py's
  // list_consumables and services/requirement_service.py's matching server-side
  // enforcement.
  const consumables = useLookup<Consumable>("/api/v1/consumables?central_only=true");
  const assets = useLookup<Asset>("/api/v1/assets");

  const [branchId, setBranchId] = useState("");
  const [rows, setRows] = useState<ItemRow[]>([makeEmptyRow()]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (branchLocked && user?.hospital_scope_id) {
      setBranchId(user.hospital_scope_id);
    }
  }, [branchLocked, user]);

  // Restructure consolidation pass, Part G — the unified "Raise Request / Incident"
  // hub (/raise) links here with ?item_type=... for New Device/Consumable/Spare Part;
  // this only preselects the first row's type, same underlying Requirement domain.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const itemType = params.get("item_type");
    if (itemType) {
      setRows((prev) => (prev.length === 1 && !prev[0].reference_id ? [{ ...prev[0], item_type: itemType }] : prev));
    }
  }, []);

  const departmentsForBranch = useMemo(
    () => departments.filter((d) => !branchId || d.branch_id === branchId),
    [departments, branchId]
  );

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);

    const rowError = validateItemRows(rows);
    if (rowError) {
      setError(rowError);
      return;
    }

    setSubmitting(true);
    try {
      const requirement = await api.post<Requirement>("/api/v1/requirements", {
        branch_id: formData.get("branch_id"),
        department_id: formData.get("department_id") || null,
        priority: formData.get("priority"),
        required_by_date: formData.get("required_by_date") || null,
        reason: formData.get("reason"),
        items: buildItemsPayload(rows),
      });
      router.push(`/requirements/${requirement.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create requirement.");
      setSubmitting(false);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>New requirement</h1>
          <p>Raise a hospital requirement request. It is saved as a draft until you submit it.</p>
        </div>
      </div>

      <form className="card" onSubmit={handleSubmit}>
        <div className="form-grid">
          <div className="form-field">
            <label>Hospital / Branch*</label>
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
            <label>Department / Unit</label>
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
            <label>Priority*</label>
            <select name="priority" defaultValue="MEDIUM">
              {REQUIREMENT_PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Required by date</label>
            <input name="required_by_date" type="date" />
          </div>
        </div>

        <div className="form-field" style={{ marginBottom: 12 }}>
          <label>Reason / Description*</label>
          <textarea name="reason" required rows={4} placeholder="Why is this requirement needed?" />
        </div>

        <h3>Requested items</h3>
        <p className="field-hint">Add every device model, consumable, spare part, or other item needed for this request.</p>

        <RequirementItemsTable
          rows={rows}
          onChange={setRows}
          deviceModels={deviceModels}
          deviceModelsLoading={deviceModelsLoading}
          deviceModelsError={deviceModelsError}
          inventoryItems={inventoryItems}
          consumables={consumables}
          assets={assets}
          branches={branches}
          branchId={branchId}
        />

        {error && <ErrorState message={error} />}
        <div>
          <button className="btn btn--primary" type="submit" disabled={submitting}>
            {submitting ? "Saving..." : "Save draft"}
          </button>
        </div>
      </form>
    </ProtectedShell>
  );
}
