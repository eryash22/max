"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { ErrorState } from "../../../components/ErrorState";
import { useAuth } from "../../../hooks/useAuth";
import { api, ApiError } from "../../../lib/api";
import type { Vendor } from "../../../types";
import { VENDOR_TYPES } from "../../../types";

export default function NewVendorPage() {
  const router = useRouter();
  const { hasPermission } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!hasPermission("vendor.write")) {
    return (
      <ProtectedShell>
        <ErrorState message="You do not have permission to create vendors." />
      </ProtectedShell>
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const formData = new FormData(event.currentTarget);
    try {
      const vendor = await api.post<Vendor>("/api/v1/vendors", {
        name: formData.get("name"),
        vendor_type: formData.get("vendor_type") || null,
        is_oem: formData.get("is_oem") === "on",
        is_authorized_distributor: formData.get("is_authorized_distributor") === "on",
        is_service_vendor: formData.get("is_service_vendor") === "on",
        city: formData.get("city") || null,
        state: formData.get("state") || null,
        email: formData.get("email") || null,
        phone: formData.get("phone") || null,
        website: formData.get("website") || null,
        support_coverage: formData.get("support_coverage") || null,
        response_sla_hours: formData.get("response_sla_hours") ? Number(formData.get("response_sla_hours")) : null,
        rating: formData.get("rating") ? Number(formData.get("rating")) : null,
        status: formData.get("status") || "ACTIVE",
      });
      router.push(`/vendors/${vendor.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create vendor.");
      setSubmitting(false);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>New vendor</h1>
          <p>Register an OEM, distributor, or service vendor.</p>
        </div>
      </div>

      <form className="card" onSubmit={handleSubmit} style={{ maxWidth: 640 }}>
        <div className="form-grid">
          <div className="form-field">
            <label>Name*</label>
            <input name="name" required />
          </div>
          <div className="form-field">
            <label>Vendor type</label>
            <select name="vendor_type" defaultValue="">
              <option value="">Not set</option>
              {VENDOR_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field form-field--checkbox">
            <label>
              <input name="is_oem" type="checkbox" /> OEM
            </label>
          </div>
          <div className="form-field form-field--checkbox">
            <label>
              <input name="is_authorized_distributor" type="checkbox" /> Authorized distributor
            </label>
          </div>
          <div className="form-field form-field--checkbox">
            <label>
              <input name="is_service_vendor" type="checkbox" /> Service vendor
            </label>
          </div>
          <div className="form-field">
            <label>City</label>
            <input name="city" placeholder="Optional" />
          </div>
          <div className="form-field">
            <label>State</label>
            <input name="state" placeholder="Optional" />
          </div>
          <div className="form-field">
            <label>Email</label>
            <input name="email" type="email" placeholder="Optional" />
          </div>
          <div className="form-field">
            <label>Phone</label>
            <input name="phone" placeholder="Optional" />
          </div>
          <div className="form-field">
            <label>Website</label>
            <input name="website" placeholder="Optional" />
          </div>
          <div className="form-field">
            <label>Support coverage</label>
            <input name="support_coverage" placeholder="e.g. 24x7, business hours" />
          </div>
          <div className="form-field">
            <label>Response SLA (hours)</label>
            <input name="response_sla_hours" type="number" min={1} placeholder="Optional" />
          </div>
          <div className="form-field">
            <label>Rating (1-5)</label>
            <input name="rating" type="number" min={1} max={5} placeholder="Optional" />
          </div>
          <div className="form-field">
            <label>Status</label>
            <select name="status" defaultValue="ACTIVE">
              <option value="ACTIVE">ACTIVE</option>
              <option value="INACTIVE">INACTIVE</option>
            </select>
          </div>
        </div>

        {error && <div className="field-error">{error}</div>}
        <button className="btn btn--primary" type="submit" disabled={submitting}>
          {submitting ? "Saving..." : "Create vendor"}
        </button>
      </form>
    </ProtectedShell>
  );
}
