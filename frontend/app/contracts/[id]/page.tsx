"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { Pagination } from "../../../components/Pagination";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { ReferenceConflictBanner } from "../../../components/ReferenceConflictBanner";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { useArchivedDeviceModels } from "../../../hooks/useDeviceVendorInfo";
import { api, ApiError } from "../../../lib/api";
import type { Asset, Contract, ContractServiceVisit, DeviceModel, Page, Vendor } from "../../../types";
import { CONTRACT_STATUSES } from "../../../types";

const VISIT_PAGE_SIZE = 10;

export default function ContractDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("contract.write");

  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const assets = useLookup<Asset>("/api/v1/assets");
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const assetById = useMemo(() => new Map(assets.map((a) => [a.id, a])), [assets]);
  // Archived models merged for display only — a saved contract may cover an asset whose
  // model was archived since, and it must still show a readable name rather than a raw
  // UUID. Same approach assets/page.tsx already uses.
  const archivedModels = useArchivedDeviceModels();
  const allModels = useMemo(() => [...models, ...archivedModels], [models, archivedModels]);
  const modelName = (id: string) => allModels.find((m) => m.id === id)?.name ?? id;
  // Model name leads, and a missing asset_number is spelled out: asset_number is
  // nullable, so the previous format rendered a bare " (Model)" for those assets.
  const assetLabel = (a: Asset) =>
    `${modelName(a.device_model_id)} — ${a.asset_number || "Asset number not assigned"}`;
  const vendorName = (id: string) => vendors.find((v) => v.id === id)?.name ?? id;

  const [contract, setContract] = useState<Contract | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deleteConflict, setDeleteConflict] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const [visits, setVisits] = useState<ContractServiceVisit[]>([]);
  const [visitsTotal, setVisitsTotal] = useState(0);
  const [visitPage, setVisitPage] = useState(1);
  const [visitsError, setVisitsError] = useState<string | null>(null);

  const isWarranty = contract?.contract_type === "WARRANTY";
  const isAmcCmc = contract?.contract_type === "AMC" || contract?.contract_type === "CMC";

  const load = useCallback(async () => {
    try {
      const result = await api.get<Contract>(`/api/v1/contracts/${params.id}`);
      setContract(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load contract.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    async function loadVisits() {
      setVisitsError(null);
      try {
        const result = await api.get<Page<ContractServiceVisit>>(
          `/api/v1/contracts/${params.id}/service-visits?page=${visitPage}&page_size=${VISIT_PAGE_SIZE}`
        );
        if (!cancelled) {
          setVisits(result.items);
          setVisitsTotal(result.total);
        }
      } catch (err) {
        if (!cancelled) setVisitsError(err instanceof ApiError ? err.message : "Failed to load service visits.");
      }
    }
    loadVisits();
    return () => {
      cancelled = true;
    };
  }, [params.id, visitPage]);

  async function handleUpdateContract(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!contract) return;
    setActionError(null);
    setBusy(true);
    const formData = new FormData(event.currentTarget);
    try {
      const payload: Record<string, unknown> = {
        vendor_id: formData.get("vendor_id") || null,
        contract_number: formData.get("contract_number") || null,
        start_date: formData.get("start_date") || null,
        end_date: formData.get("end_date") || null,
        status: formData.get("status") || null,
      };
      if (isAmcCmc) {
        payload.pm_frequency = formData.get("pm_frequency") || null;
        payload.calibration_frequency = formData.get("calibration_frequency") || null;
        payload.response_time_hours = formData.get("response_time_hours")
          ? Number(formData.get("response_time_hours"))
          : null;
        payload.resolution_time_hours = formData.get("resolution_time_hours")
          ? Number(formData.get("resolution_time_hours"))
          : null;
        payload.contract_value = formData.get("contract_value") ? Number(formData.get("contract_value")) : null;
        payload.renewal_date = formData.get("renewal_date") || null;
        payload.contact_person = formData.get("contact_person") || null;
        payload.contact_phone = formData.get("contact_phone") || null;
        payload.contact_email = formData.get("contact_email") || null;
      } else {
        payload.coverage_notes = formData.get("coverage_notes") || null;
      }
      await api.patch(`/api/v1/contracts/${params.id}`, payload);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update contract.");
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleStatus() {
    if (!contract) return;
    setActionError(null);
    setDeleteConflict(null);
    setBusy(true);
    try {
      const action = contract.status === "ACTIVE" ? "deactivate" : "activate";
      await api.post(`/api/v1/contracts/${params.id}/${action}`);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to change contract status.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!contract) return;
    setConfirmDelete(false);
    setActionError(null);
    setDeleteConflict(null);
    setBusy(true);
    try {
      await api.delete(`/api/v1/contracts/${contract.id}`);
      router.push("/contracts");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Referenced (or an active Warranty) — one-click deactivate fallback.
        setDeleteConflict(err.message);
      } else {
        setActionError(err instanceof ApiError ? err.message : "Failed to delete contract.");
      }
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
  if (!contract) {
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
          <h1>{contract.contract_number ?? contract.id}</h1>
          <p>
            <StatusBadge value={contract.contract_type} />
            {" · "}
            <StatusBadge value={contract.status} />
            {" · "}
            <StatusBadge value={contract.expiry_status} />
          </p>
        </div>
        {canWrite && (
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--ghost" onClick={handleToggleStatus} disabled={busy}>
              {contract.status === "ACTIVE" ? "Deactivate contract" : "Activate contract"}
            </button>
            <button className="btn btn--danger" onClick={() => setConfirmDelete(true)} disabled={busy}>
              Delete contract
            </button>
          </div>
        )}
      </div>

      {deleteConflict && (
        <ReferenceConflictBanner
          message={deleteConflict}
          actionLabel="Deactivate instead"
          busy={busy}
          onAction={() => handleToggleStatus().then(() => setDeleteConflict(null))}
        />
      )}
      {actionError && <ErrorState message={actionError} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Contract information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Contract type</td>
              <td>
                <StatusBadge value={contract.contract_type} />
              </td>
            </tr>
            <tr>
              <td>Contract number</td>
              <td>{contract.contract_number ?? "-"}</td>
            </tr>
            <tr>
              <td>Vendor</td>
              <td>{contract.vendor_id ? vendorName(contract.vendor_id) : "Not set"}</td>
            </tr>
            <tr>
              <td>Covered assets</td>
              <td>
                {contract.asset_ids.length === 0
                  ? "-"
                  : contract.asset_ids
                      .map((id) => {
                        const asset = assetById.get(id);
                        return asset ? assetLabel(asset) : id;
                      })
                      .join(", ")}
              </td>
            </tr>
            <tr>
              <td>Start date</td>
              <td>{contract.start_date ?? "-"}</td>
            </tr>
            <tr>
              <td>End date</td>
              <td>{contract.end_date ?? "-"}</td>
            </tr>
            <tr>
              <td>Expiry status</td>
              <td>
                <StatusBadge value={contract.expiry_status} />
              </td>
            </tr>
            <tr>
              <td>Status</td>
              <td>
                <StatusBadge value={contract.status} />
              </td>
            </tr>
            {isAmcCmc && (
              <>
                <tr>
                  <td>PM frequency</td>
                  <td>{contract.pm_frequency ?? "-"}</td>
                </tr>
                <tr>
                  <td>Calibration frequency</td>
                  <td>{contract.calibration_frequency ?? "-"}</td>
                </tr>
                <tr>
                  <td>Response time</td>
                  <td>{contract.response_time_hours ? `${contract.response_time_hours} hours` : "-"}</td>
                </tr>
                <tr>
                  <td>Resolution time</td>
                  <td>{contract.resolution_time_hours ? `${contract.resolution_time_hours} hours` : "-"}</td>
                </tr>
                <tr>
                  <td>Contract value</td>
                  <td>{contract.contract_value != null ? contract.contract_value : "-"}</td>
                </tr>
                <tr>
                  <td>Renewal date</td>
                  <td>{contract.renewal_date ?? "-"}</td>
                </tr>
                <tr>
                  <td>Contact person</td>
                  <td>{contract.contact_person ?? "-"}</td>
                </tr>
                <tr>
                  <td>Contact phone</td>
                  <td>{contract.contact_phone ?? "-"}</td>
                </tr>
                <tr>
                  <td>Contact email</td>
                  <td>{contract.contact_email ?? "-"}</td>
                </tr>
              </>
            )}
            {isWarranty && (
              <tr>
                <td>Coverage notes</td>
                <td>{contract.coverage_notes ?? "-"}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Edit contract</h3>
          <form onSubmit={handleUpdateContract} className="form-grid">
            <div className="form-field">
              <label>Vendor</label>
              <select name="vendor_id" defaultValue={contract.vendor_id ?? ""}>
                <option value="">Not set</option>
                {vendors.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Contract number</label>
              <input name="contract_number" defaultValue={contract.contract_number ?? ""} />
            </div>
            <div className="form-field">
              <label>Start date</label>
              <input name="start_date" type="date" defaultValue={contract.start_date ?? ""} />
            </div>
            <div className="form-field">
              <label>End date</label>
              <input name="end_date" type="date" defaultValue={contract.end_date ?? ""} />
            </div>
            <div className="form-field">
              <label>Status</label>
              <select name="status" defaultValue={contract.status}>
                {CONTRACT_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>

            {isAmcCmc && (
              <>
                <div className="form-field">
                  <label>PM frequency</label>
                  <input name="pm_frequency" defaultValue={contract.pm_frequency ?? ""} />
                </div>
                <div className="form-field">
                  <label>Calibration frequency</label>
                  <input name="calibration_frequency" defaultValue={contract.calibration_frequency ?? ""} />
                </div>
                <div className="form-field">
                  <label>Response time (hours)</label>
                  <input
                    name="response_time_hours"
                    type="number"
                    min={1}
                    defaultValue={contract.response_time_hours ?? ""}
                  />
                </div>
                <div className="form-field">
                  <label>Resolution time (hours)</label>
                  <input
                    name="resolution_time_hours"
                    type="number"
                    min={1}
                    defaultValue={contract.resolution_time_hours ?? ""}
                  />
                </div>
                <div className="form-field">
                  <label>Contract value</label>
                  <input
                    name="contract_value"
                    type="number"
                    min={0}
                    step="0.01"
                    defaultValue={contract.contract_value ?? ""}
                  />
                </div>
                <div className="form-field">
                  <label>Renewal date</label>
                  <input name="renewal_date" type="date" defaultValue={contract.renewal_date ?? ""} />
                </div>
                <div className="form-field">
                  <label>Contact person</label>
                  <input name="contact_person" defaultValue={contract.contact_person ?? ""} />
                </div>
                <div className="form-field">
                  <label>Contact phone</label>
                  <input name="contact_phone" defaultValue={contract.contact_phone ?? ""} />
                </div>
                <div className="form-field">
                  <label>Contact email</label>
                  <input name="contact_email" type="email" defaultValue={contract.contact_email ?? ""} />
                </div>
              </>
            )}

            {isWarranty && (
              <div className="form-field" style={{ gridColumn: "1 / -1" }}>
                <label>Coverage notes</label>
                <textarea name="coverage_notes" rows={3} defaultValue={contract.coverage_notes ?? ""} />
              </div>
            )}

            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Save changes
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Service visit history</h3>
        {visitsError && <ErrorState message={visitsError} />}
        <table className="data-table">
          <thead>
            <tr>
              <th>Asset</th>
              <th>Visit date</th>
              <th>Type</th>
              <th>Summary</th>
              <th>Parts used</th>
              <th>Cost</th>
            </tr>
          </thead>
          <tbody>
            {visits.length === 0 && (
              <tr>
                <td colSpan={6}>No service visits recorded for this contract's assets.</td>
              </tr>
            )}
            {visits.map((v) => (
              <tr key={v.id}>
                <td>{(() => { const a = assetById.get(v.asset_id); return a ? assetLabel(a) : v.asset_id; })()}</td>
                <td>{v.visit_date ?? "-"}</td>
                <td>{v.visit_type ?? "-"}</td>
                <td>{v.summary ?? "-"}</td>
                <td>{v.parts_used ?? "-"}</td>
                <td>{v.cost != null ? v.cost : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Pagination page={visitPage} pageSize={VISIT_PAGE_SIZE} total={visitsTotal} onPageChange={setVisitPage} />
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete contract"
        message="This permanently deletes the contract. AMC/CMC contracts still linked to assets cannot be deleted — for anything else, use Deactivate instead. Continue?"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </ProtectedShell>
  );
}
