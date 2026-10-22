"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { ErrorState } from "../../../components/ErrorState";
import { useLookup } from "../../../hooks/useLookup";
import { useAuth } from "../../../hooks/useAuth";
import { api, ApiError } from "../../../lib/api";
import type { Asset, Branch, DeviceModel, Document, Vendor } from "../../../types";
import { DOCUMENT_ACCESS_LEVELS, DOCUMENT_ENTITY_TYPES, DOCUMENT_TYPES } from "../../../types";

export default function NewDocumentPage() {
  const router = useRouter();
  const { hasPermission } = useAuth();
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const assets = useLookup<Asset>("/api/v1/assets");
  const branches = useLookup<Branch>("/api/v1/branches");
  // status_filter=ACTIVE — a deactivated vendor must not be selectable here.
  const vendors = useLookup<Vendor>("/api/v1/vendors?status_filter=ACTIVE");

  // Most bulk-imported assets have no asset_number, so falling back to the raw
  // UUID (as this dropdown used to) is unreadable — show the device model name
  // and branch instead, same disambiguation the Incidents/Locations pages use.
  const assetLabel = (a: Asset) => {
    const model = models.find((m) => m.id === a.device_model_id);
    const branch = branches.find((b) => b.id === a.branch_id);
    const name = model?.name ?? "Unknown device";
    const ref = a.asset_number ?? a.serial_number ?? null;
    const parts = [name, branch?.name, ref].filter(Boolean);
    return parts.join(" — ");
  };

  const [entityType, setEntityType] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!hasPermission("document.write")) {
    return (
      <ProtectedShell>
        <ErrorState message="You do not have permission to register documents." />
      </ProtectedShell>
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const formData = new FormData(event.currentTarget);
    try {
      const document = await api.post<Document>("/api/v1/documents", {
        title: formData.get("title"),
        document_type: formData.get("document_type"),
        manufacturer: formData.get("manufacturer") || null,
        model_number: formData.get("model_number") || null,
        device_model_id: formData.get("device_model_id") || null,
        asset_id: formData.get("asset_id") || null,
        vendor_id: formData.get("vendor_id") || null,
        effective_date: formData.get("effective_date") || null,
        expiry_date: formData.get("expiry_date") || null,
        access_level: formData.get("access_level") || "INTERNAL",
        is_active: formData.get("is_active") === "on",
      });

      const linkEntityType = formData.get("entity_type") as string;
      const linkEntityId = formData.get("entity_id") as string;
      if (linkEntityType && linkEntityId) {
        await api.post(`/api/v1/documents/${document.id}/links`, {
          entity_type: linkEntityType,
          entity_id: linkEntityId,
        });
      }

      if (file) {
        const versionLabel = (formData.get("version_label") as string) || "v1";
        await api.upload(`/api/v1/documents/${document.id}/versions`, file, { version_label: versionLabel });
      }

      router.push(`/documents/${document.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to register document.");
      setSubmitting(false);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Register document</h1>
          <p>Add a document record, optionally link it to an existing record and upload a file.</p>
        </div>
      </div>

      <form className="card" onSubmit={handleSubmit} style={{ maxWidth: 720 }}>
        <div className="form-grid">
          <div className="form-field">
            <label>Title*</label>
            <input name="title" required placeholder="e.g. Ventilator Service Manual" />
          </div>
          <div className="form-field">
            <label>Document type*</label>
            <select name="document_type" required defaultValue="">
              <option value="" disabled>
                Select type
              </option>
              {DOCUMENT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Manufacturer</label>
            <input name="manufacturer" placeholder="Optional" />
          </div>
          <div className="form-field">
            <label>Model number</label>
            <input name="model_number" placeholder="Optional" />
          </div>
          <div className="form-field">
            <label>Device model</label>
            <select name="device_model_id" defaultValue="">
              <option value="">None</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Asset</label>
            <select name="asset_id" defaultValue="">
              <option value="">None</option>
              {assets.map((a) => (
                <option key={a.id} value={a.id}>
                  {assetLabel(a)}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Vendor</label>
            <select name="vendor_id" defaultValue="">
              <option value="">None</option>
              {vendors.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Access level</label>
            <select name="access_level" defaultValue="INTERNAL">
              {DOCUMENT_ACCESS_LEVELS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Effective date</label>
            <input name="effective_date" type="date" />
          </div>
          <div className="form-field">
            <label>Expiry date</label>
            <input name="expiry_date" type="date" />
          </div>
          <div className="form-field form-field--checkbox">
            <label>
              <input name="is_active" type="checkbox" defaultChecked /> Active
            </label>
          </div>
        </div>

        <h3>Link to a record (optional)</h3>
        <div className="form-grid">
          <div className="form-field">
            <label>Entity type</label>
            <select name="entity_type" value={entityType} onChange={(e) => setEntityType(e.target.value)}>
              <option value="">None</option>
              {DOCUMENT_ENTITY_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>Entity ID</label>
            <input name="entity_id" placeholder="Record ID" disabled={!entityType} />
          </div>
        </div>

        <h3>Upload file (optional)</h3>
        <div className="form-grid">
          <div className="form-field">
            <label>Version label</label>
            <input name="version_label" placeholder="e.g. v1" />
          </div>
          <div className="form-field">
            <label>File</label>
            <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </div>
        </div>

        {error && <div className="field-error">{error}</div>}
        <button className="btn btn--primary" type="submit" disabled={submitting}>
          {submitting ? "Saving..." : "Register document"}
        </button>
      </form>
    </ProtectedShell>
  );
}
