"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { useAuth } from "../../hooks/useAuth";
import { useLookup } from "../../hooks/useLookup";
import { api, ApiError } from "../../lib/api";
import type { Branch, Department, DepartmentMaster, Page } from "../../types";

// Branch-scoping hardening pass — a UNIT_HEAD/TL's branch is fixed by role
// assignment, same lock used on requirements/new, incidents/new, branches/[id].
const BRANCH_LOCKED_ROLES = ["UNIT_HEAD", "TL"];

// This page is the "where a central department is used" half — see
// /departments/master for the "central definition" half. A department name/code
// is never typed here; a branch only ever picks from the existing active masters.
export default function DepartmentsPage() {
  const { user, hasPermission } = useAuth();
  const branchLocked = BRANCH_LOCKED_ROLES.some((r) => user?.roles.includes(r));
  const canWrite = hasPermission("hospital.write");

  const branches = useLookup<Branch>("/api/v1/branches");
  const [branchId, setBranchId] = useState("");

  useEffect(() => {
    if (branchLocked && user?.hospital_scope_id) {
      setBranchId(user.hospital_scope_id);
    }
  }, [branchLocked, user]);

  const [masters, setMasters] = useState<DepartmentMaster[] | null>(null);
  const [assignments, setAssignments] = useState<Department[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyMasterId, setBusyMasterId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!branchId) return;
    setLoadError(null);
    try {
      const [masterResult, assignmentResult] = await Promise.all([
        api.get<Page<DepartmentMaster>>("/api/v1/department-masters?status_filter=ACTIVE&page=1&page_size=200"),
        api.get<Page<Department>>(`/api/v1/departments?branch_id=${branchId}&page=1&page_size=200`),
      ]);
      setMasters(masterResult.items);
      setAssignments(assignmentResult.items);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Failed to load departments.");
    }
  }, [branchId]);

  useEffect(() => {
    load();
  }, [load]);

  function assignmentFor(masterId: string): Department | undefined {
    return assignments?.find((a) => a.department_master_id === masterId);
  }

  async function handleToggle(master: DepartmentMaster) {
    setActionError(null);
    setBusyMasterId(master.id);
    const existing = assignmentFor(master.id);
    try {
      if (existing && existing.status === "ACTIVE") {
        // Remove — hard delete where safe (no history references it yet),
        // otherwise fall back to deactivate, same reference-guard the Department
        // Master and every other master/catalog entity in this app already uses.
        try {
          await api.delete(`/api/v1/departments/${existing.id}`);
        } catch (err) {
          if (err instanceof ApiError && err.status === 409) {
            await api.post(`/api/v1/departments/${existing.id}/deactivate`);
          } else {
            throw err;
          }
        }
      } else if (existing) {
        await api.post(`/api/v1/departments/${existing.id}/activate`);
      } else {
        await api.post("/api/v1/departments", { branch_id: branchId, department_master_id: master.id });
      }
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update assignment.");
    } finally {
      setBusyMasterId(null);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Assign Departments to Branches</h1>
          <p>
            Assign existing departments to a branch. These departments then become available for that branch&apos;s
            operational workflows. To create or edit a department itself, use{" "}
            <Link href="/departments/master">Department Master</Link>.
          </p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="form-field" style={{ maxWidth: 320 }}>
          <label>Select Branch to Manage Departments</label>
          {branchLocked ? (
            <input type="text" value={branches.find((b) => b.id === branchId)?.name ?? "Loading..."} disabled readOnly />
          ) : (
            <select value={branchId} onChange={(e) => setBranchId(e.target.value)}>
              <option value="">Select branch</option>
              {branches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {loadError && <ErrorState message={loadError} />}

      {!loadError && branchId && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>
            Departments Assigned to {branches.find((b) => b.id === branchId)?.name ?? "this branch"}
          </h3>
          {actionError && <ErrorState message={actionError} />}
          {(masters === null || assignments === null) && <LoadingState label="Loading departments..." />}
          {masters !== null && assignments !== null && masters.length === 0 && (
            <p className="field-hint">
              No department masters exist yet.{" "}
              {canWrite ? (
                <>
                  Create one on the <Link href="/departments/master">Department Master</Link> page first.
                </>
              ) : (
                "Ask an administrator to create one."
              )}
            </p>
          )}
          {masters !== null && assignments !== null && masters.length > 0
            && !assignments.some((a) => a.status === "ACTIVE") && (
              <p className="field-hint">No departments are currently assigned to this branch.</p>
          )}
          {masters !== null && assignments !== null && masters.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {masters.map((m) => {
                const existing = assignmentFor(m.id);
                const checked = existing?.status === "ACTIVE";
                return (
                  <label
                    key={m.id}
                    style={{ display: "flex", alignItems: "center", gap: 8, opacity: canWrite ? 1 : 0.7 }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!canWrite || busyMasterId === m.id}
                      onChange={() => handleToggle(m)}
                    />
                    <span>
                      {m.name}
                      {m.code ? ` (${m.code})` : ""}
                    </span>
                  </label>
                );
              })}
            </div>
          )}
        </div>
      )}

      {!loadError && !branchId && !branchLocked && (
        <p className="field-hint">Select a branch to view and manage its assigned departments.</p>
      )}
    </ProtectedShell>
  );
}
