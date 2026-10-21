"use client";

import { useMemo, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { ErrorState } from "../../../components/ErrorState";
import { useLookup } from "../../../hooks/useLookup";
import { useArchivedDeviceModels } from "../../../hooks/useDeviceVendorInfo";
import { SearchableSelect } from "../../../components/SearchableSelect";
import { useAuth } from "../../../hooks/useAuth";
import { api, ApiError } from "../../../lib/api";
import type { Asset, Contract, DeviceModel, Vendor } from "../../../types";
import { CONTRACT_STATUSES, CONTRACT_TYPES } from "../../../types";

export default function NewContractPage() {
  const router = useRouter();
  const { hasPermission } = useAuth();
  // status_filter=ACTIVE — a deactivated vendor must not be selectable here.
  const vendors = useLookup<Vendor>("/api/v1/vendors?status_filter=ACTIVE");
  const assets = useLookup<Asset>("/api/v1/assets");
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  // `GET /device-models` only takes a binary is_active filter, so an asset whose model
  // was archived would otherwise resolve to its raw UUID here. Same merge the Assets
  // page already does for display — not a second model-resolution system.
  const archivedModels = useArchivedDeviceModels();
  const allModels = useMemo(() => [...models, ...archivedModels], [models, archivedModels]);
  const modelName = (id: string) => allModels.find((m) => m.id === id)?.name ?? id;

  // Covered assets are PHYSICAL Asset records. asset_number is genuinely optional
  // (schemas/device.py), so a bare `${asset_number} — ${model}` renders a leading dash
  // for every asset without one. Model name leads, and a missing number says so.
  const assetLabel = (a: Asset) =>
    `${modelName(a.device_model_id)} — ${a.asset_number || "Asset number not assigned"}`;

  /** Assets that may be NEWLY covered. Mirrors the server-side rule in
   * contract_service.is_asset_contract_eligible: a condemned/retired device is out of
   * service, so new coverage on it is a data-entry mistake. The backend rejects these
   * regardless — this only keeps them out of the picker. */
  const selectableAssets = useMemo(
    () => assets.filter((a) => !["CONDEMNED", "RETIRED"].includes((a.status ?? "").toUpperCase())),
    [assets]
  );

  const assetOptions = useMemo(
    () =>
      selectableAssets.map((a) => ({
        id: a.id,
        label: assetLabel(a),
        // Searchable secondary text — serial number is a real Asset field users search by.
        sublabel: [a.serial_number, a.status].filter(Boolean).join(" · ") || undefined,
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectableAssets, allModels]
  );

  const [contractType, setContractType] = useState("AMC");
  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!hasPermission("contract.write")) {
    return (
      <ProtectedShell>
        <ErrorState message="You do not have permission to create contracts." />
      </ProtectedShell>
    );
  }

  const isWarranty = contractType === "WARRANTY";
  const isAmcCmc = contractType === "AMC" || contractType === "CMC";

  function toggleAsset(id: string, checked: boolean) {
    setSelectedAssetIds((prev) => (checked ? [...prev, id] : prev.filter((a) => a !== id)));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (isWarranty && selectedAssetIds.length !== 1) {
      setError("Warranty contracts must cover exactly one asset.");
      return;
    }

    setSubmitting(true);
    const formData = new FormData(event.currentTarget);
    try {
      const payload: Record<string, unknown> = {
        contract_type: contractType,
        vendor_id: formData.get("vendor_id") || null,
        asset_ids: selectedAssetIds,
        contract_number: formData.get("contract_number") || null,
        start_date: formData.get("start_date") || null,
        end_date: formData.get("end_date") || null,
        status: formData.get("status") || "UNKNOWN",
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

      const contract = await api.post<Contract>("/api/v1/contracts", payload);
      router.push(`/contracts/${contract.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create contract.");
      setSubmitting(false);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>New contract</h1>
          <p>Register an AMC, CMC, or warranty contract and link the assets it covers.</p>
        </div>
      </div>

      <form className="card" onSubmit={handleSubmit} style={{ maxWidth: 720 }}>
        <div className="form-grid">
          <div className="form-field">
            <label>Contract type*</label>
            <select
              name="contract_type"
              value={contractType}
              onChange={(e) => {
                setContractType(e.target.value);
                if (e.target.value === "WARRANTY" && selectedAssetIds.length > 1) {
                  setSelectedAssetIds(selectedAssetIds.slice(0, 1));
                }
              }}
            >
              {CONTRACT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Vendor</label>
            <select name="vendor_id" defaultValue="">
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
            <input name="contract_number" placeholder="Optional" />
          </div>
          <div className="form-field">
            <label>Start date</label>
            <input name="start_date" type="date" />
          </div>
          <div className="form-field">
            <label>End date</label>
            <input name="end_date" type="date" />
          </div>
          <div className="form-field">
            <label>Status</label>
            <select name="status" defaultValue="UNKNOWN">
              {CONTRACT_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="form-field" style={{ marginTop: 16 }}>
          <label>
            {isWarranty ? "Covered asset* (exactly one)" : "Covered assets"}
          </label>
          {isWarranty ? (
            // Exactly one asset — SearchableSelect is a single-select, so it maps directly.
            <SearchableSelect
              options={assetOptions}
              value={selectedAssetIds[0] ?? ""}
              onChange={(id) => setSelectedAssetIds(id ? [id] : [])}
              placeholder="Search device or asset number..."
              emptyMessage="No eligible assets found"
            />
          ) : (
            // AMC/CMC cover many assets. SearchableSelect stays single-select (shared
            // component, unchanged) and acts as an "add" control feeding the list below.
            <>
              <SearchableSelect
                options={assetOptions.filter((o) => !selectedAssetIds.includes(o.id))}
                value=""
                onChange={(id) => {
                  if (id && !selectedAssetIds.includes(id)) setSelectedAssetIds([...selectedAssetIds, id]);
                }}
                placeholder="Search device or asset number to add..."
                emptyMessage="No eligible assets found"
              />
              <div className="covered-assets" style={{ marginTop: 10 }}>
                {selectedAssetIds.length === 0 ? (
                  <p className="field-hint">No assets added yet. Search above to add the devices this contract covers.</p>
                ) : (
                  <ul className="covered-assets__list">
                    {selectedAssetIds.map((id) => {
                      const asset = assets.find((a) => a.id === id);
                      return (
                        <li key={id} className="covered-assets__item">
                          <span>{asset ? assetLabel(asset) : id}</span>
                          <button
                            type="button"
                            className="btn btn--ghost"
                            onClick={() => toggleAsset(id, false)}
                            aria-label={`Remove ${asset ? assetLabel(asset) : id}`}
                          >
                            Remove
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </>
          )}
        </div>

        {isAmcCmc && (
          <>
            <h3 style={{ marginTop: 24, marginBottom: 8 }}>AMC / CMC terms</h3>
            <div className="form-grid">
              <div className="form-field">
                <label>PM frequency</label>
                <input name="pm_frequency" placeholder="e.g. QUARTERLY" />
              </div>
              <div className="form-field">
                <label>Calibration frequency</label>
                <input name="calibration_frequency" placeholder="e.g. YEARLY" />
              </div>
              <div className="form-field">
                <label>Response time (hours)</label>
                <input name="response_time_hours" type="number" min={1} placeholder="Optional" />
              </div>
              <div className="form-field">
                <label>Resolution time (hours)</label>
                <input name="resolution_time_hours" type="number" min={1} placeholder="Optional" />
              </div>
              <div className="form-field">
                <label>Contract value</label>
                <input name="contract_value" type="number" min={0} step="0.01" placeholder="Optional" />
              </div>
              <div className="form-field">
                <label>Renewal date</label>
                <input name="renewal_date" type="date" />
              </div>
              <div className="form-field">
                <label>Contact person</label>
                <input name="contact_person" placeholder="Optional" />
              </div>
              <div className="form-field">
                <label>Contact phone</label>
                <input name="contact_phone" placeholder="Optional" />
              </div>
              <div className="form-field">
                <label>Contact email</label>
                <input name="contact_email" type="email" placeholder="Optional" />
              </div>
            </div>
          </>
        )}

        {isWarranty && (
          <div className="form-field" style={{ marginTop: 16, gridColumn: "1 / -1" }}>
            <label>Coverage notes</label>
            <textarea name="coverage_notes" rows={3} placeholder="Optional" />
          </div>
        )}

        {error && (
          <div className="field-error" style={{ marginTop: 16 }}>
            {error}
          </div>
        )}
        <button className="btn btn--primary" type="submit" disabled={submitting} style={{ marginTop: 16 }}>
          {submitting ? "Saving..." : "Create contract"}
        </button>
      </form>
    </ProtectedShell>
  );
}
