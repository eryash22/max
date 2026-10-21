"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import Link from "next/link";
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
import { api, ApiError } from "../../../lib/api";
import type {
  Asset,
  Contract,
  DeviceModel,
  DeviceModelVendor,
  Invoice,
  Page,
  PurchaseOrder,
  Quotation,
  Vendor,
  VendorContact,
  VendorServiceVisit,
} from "../../../types";
import { VENDOR_TYPES } from "../../../types";

const VISIT_PAGE_SIZE = 10;

export default function VendorDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("vendor.write");

  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const assets = useLookup<Asset>("/api/v1/assets");
  const assetById = useMemo(() => new Map(assets.map((a) => [a.id, a])), [assets]);
  const modelName = (id: string) => models.find((m) => m.id === id)?.name ?? id;

  const [vendor, setVendor] = useState<Vendor | null>(null);
  const [contacts, setContacts] = useState<VendorContact[]>([]);
  const [deviceModels, setDeviceModels] = useState<DeviceModelVendor[]>([]);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deleteConflict, setDeleteConflict] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const [visits, setVisits] = useState<VendorServiceVisit[]>([]);
  const [visitsTotal, setVisitsTotal] = useState(0);
  const [visitPage, setVisitPage] = useState(1);
  const [visitsError, setVisitsError] = useState<string | null>(null);

  // Part 6 — linked assets (via device_model_id -> this vendor's linked device
  // models) and procurement records (Quotations/POs/Invoices all support a
  // vendor_id filter on their list endpoints — see backend/app/routers/procurement.py;
  // Invoice.vendor_id is a direct column so no join was needed to add the filter).
  const [quotations, setQuotations] = useState<Quotation[]>([]);
  const [purchaseOrders, setPurchaseOrders] = useState<PurchaseOrder[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [procurementError, setProcurementError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [vendorResult, contactsResult, deviceModelsResult, contractsResult] = await Promise.all([
        api.get<Vendor>(`/api/v1/vendors/${params.id}`),
        api.get<VendorContact[]>(`/api/v1/vendors/${params.id}/contacts`),
        api.get<DeviceModelVendor[]>(`/api/v1/vendors/${params.id}/device-models`),
        api.get<Page<Contract>>(`/api/v1/contracts?vendor_id=${params.id}&page_size=100`),
      ]);
      setVendor(vendorResult);
      setContacts(contactsResult);
      setDeviceModels(deviceModelsResult);
      setContracts(contractsResult.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load vendor.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    async function loadProcurement() {
      setProcurementError(null);
      try {
        const [q, po, inv] = await Promise.all([
          api.get<Page<Quotation>>(`/api/v1/procurement/quotations?vendor_id=${params.id}&page_size=25`),
          api.get<Page<PurchaseOrder>>(`/api/v1/procurement/purchase-orders?vendor_id=${params.id}&page_size=25`),
          api.get<Page<Invoice>>(`/api/v1/procurement/invoices?vendor_id=${params.id}&page_size=25`),
        ]);
        if (!cancelled) {
          setQuotations(q.items);
          setPurchaseOrders(po.items);
          setInvoices(inv.items);
        }
      } catch (err) {
        if (!cancelled) setProcurementError(err instanceof ApiError ? err.message : "Failed to load procurement records.");
      }
    }
    loadProcurement();
    return () => {
      cancelled = true;
    };
  }, [params.id]);

  useEffect(() => {
    let cancelled = false;
    async function loadVisits() {
      setVisitsError(null);
      try {
        const result = await api.get<Page<VendorServiceVisit>>(
          `/api/v1/vendors/${params.id}/service-visits?page=${visitPage}&page_size=${VISIT_PAGE_SIZE}`
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

  async function handleUpdateVendor(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.patch(`/api/v1/vendors/${params.id}`, {
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
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update vendor.");
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleStatus() {
    if (!vendor) return;
    setActionError(null);
    setDeleteConflict(null);
    setBusy(true);
    try {
      const action = vendor.status === "ACTIVE" ? "deactivate" : "activate";
      await api.post(`/api/v1/vendors/${params.id}/${action}`);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to change vendor status.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!vendor) return;
    setConfirmDelete(false);
    setActionError(null);
    setDeleteConflict(null);
    setBusy(true);
    try {
      await api.delete(`/api/v1/vendors/${vendor.id}`);
      router.push("/vendors");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Referenced — surface the reason with a one-click deactivate fallback.
        setDeleteConflict(err.message);
      } else {
        setActionError(err instanceof ApiError ? err.message : "Failed to delete vendor.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleAddContact(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post(`/api/v1/vendors/${params.id}/contacts`, {
        name: formData.get("name"),
        role: formData.get("role") || null,
        email: formData.get("email") || null,
        phone: formData.get("phone") || null,
        is_primary: formData.get("is_primary") === "on",
      });
      form.reset();
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to add contact.");
    } finally {
      setBusy(false);
    }
  }

  async function handleLinkDeviceModel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post(`/api/v1/vendors/${params.id}/device-models`, {
        device_model_id: formData.get("device_model_id"),
        rank: formData.get("rank") ? Number(formData.get("rank")) : 0,
        notes: formData.get("notes") || null,
      });
      form.reset();
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to link device model.");
    } finally {
      setBusy(false);
    }
  }

  const linkedModelIds = useMemo(() => new Set(deviceModels.map((d) => d.device_model_id)), [deviceModels]);
  const linkedAssets = useMemo(() => assets.filter((a) => linkedModelIds.has(a.device_model_id)), [assets, linkedModelIds]);

  if (error) {
    return (
      <ProtectedShell>
        <ErrorState message={error} />
      </ProtectedShell>
    );
  }
  if (!vendor) {
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
          <h1>{vendor.name}</h1>
          <p>
            <StatusBadge value={vendor.status} />
            {vendor.vendor_type ? ` · ${vendor.vendor_type}` : ""}
            {vendor.rating ? ` · Rating ${vendor.rating}/5` : ""}
          </p>
        </div>
        {canWrite && (
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--ghost" onClick={handleToggleStatus} disabled={busy}>
              {vendor.status === "ACTIVE" ? "Deactivate vendor" : "Activate vendor"}
            </button>
            <button className="btn btn--danger" onClick={() => setConfirmDelete(true)} disabled={busy}>
              Delete vendor
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
        <h3 style={{ marginTop: 0 }}>Vendor information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Name</td>
              <td>{vendor.name}</td>
            </tr>
            <tr>
              <td>Type</td>
              <td>{vendor.vendor_type ?? "Not set"}</td>
            </tr>
            <tr>
              <td>Roles</td>
              <td>
                {[
                  vendor.is_oem ? "OEM" : null,
                  vendor.is_authorized_distributor ? "Authorized distributor" : null,
                  vendor.is_service_vendor ? "Service vendor" : null,
                ]
                  .filter(Boolean)
                  .join(", ") || "-"}
              </td>
            </tr>
            <tr>
              <td>Location</td>
              <td>{[vendor.city, vendor.state].filter(Boolean).join(", ") || "-"}</td>
            </tr>
            <tr>
              <td>Email</td>
              <td>{vendor.email ?? "-"}</td>
            </tr>
            <tr>
              <td>Phone</td>
              <td>{vendor.phone ?? "-"}</td>
            </tr>
            <tr>
              <td>Website</td>
              <td>{vendor.website ?? "-"}</td>
            </tr>
            <tr>
              <td>Support coverage</td>
              <td>{vendor.support_coverage ?? "-"}</td>
            </tr>
            <tr>
              <td>Response SLA</td>
              <td>{vendor.response_sla_hours ? `${vendor.response_sla_hours} hours` : "-"}</td>
            </tr>
            <tr>
              <td>Rating</td>
              <td>{vendor.rating ? `${vendor.rating} / 5` : "-"}</td>
            </tr>
            <tr>
              <td>Status</td>
              <td>
                <StatusBadge value={vendor.status} />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Edit vendor</h3>
          <form onSubmit={handleUpdateVendor} className="form-grid">
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required defaultValue={vendor.name} />
            </div>
            <div className="form-field">
              <label>Vendor type</label>
              <select name="vendor_type" defaultValue={vendor.vendor_type ?? ""}>
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
                <input name="is_oem" type="checkbox" defaultChecked={vendor.is_oem} /> OEM
              </label>
            </div>
            <div className="form-field form-field--checkbox">
              <label>
                <input name="is_authorized_distributor" type="checkbox" defaultChecked={vendor.is_authorized_distributor} />{" "}
                Authorized distributor
              </label>
            </div>
            <div className="form-field form-field--checkbox">
              <label>
                <input name="is_service_vendor" type="checkbox" defaultChecked={vendor.is_service_vendor} /> Service vendor
              </label>
            </div>
            <div className="form-field">
              <label>City</label>
              <input name="city" defaultValue={vendor.city ?? ""} />
            </div>
            <div className="form-field">
              <label>State</label>
              <input name="state" defaultValue={vendor.state ?? ""} />
            </div>
            <div className="form-field">
              <label>Email</label>
              <input name="email" type="email" defaultValue={vendor.email ?? ""} />
            </div>
            <div className="form-field">
              <label>Phone</label>
              <input name="phone" defaultValue={vendor.phone ?? ""} />
            </div>
            <div className="form-field">
              <label>Website</label>
              <input name="website" defaultValue={vendor.website ?? ""} />
            </div>
            <div className="form-field">
              <label>Support coverage</label>
              <input name="support_coverage" defaultValue={vendor.support_coverage ?? ""} />
            </div>
            <div className="form-field">
              <label>Response SLA (hours)</label>
              <input name="response_sla_hours" type="number" min={1} defaultValue={vendor.response_sla_hours ?? ""} />
            </div>
            <div className="form-field">
              <label>Rating (1-5)</label>
              <input name="rating" type="number" min={1} max={5} defaultValue={vendor.rating ?? ""} />
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
        <h3 style={{ marginTop: 0 }}>Contacts</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Role</th>
              <th>Email</th>
              <th>Phone</th>
              <th>Primary</th>
            </tr>
          </thead>
          <tbody>
            {contacts.length === 0 && (
              <tr>
                <td colSpan={5}>No contacts yet.</td>
              </tr>
            )}
            {contacts.map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td>{c.role ?? "-"}</td>
                <td>{c.email ?? "-"}</td>
                <td>{c.phone ?? "-"}</td>
                <td>{c.is_primary ? "Yes" : "No"}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {canWrite && (
          <form onSubmit={handleAddContact} className="form-grid" style={{ marginTop: 16 }}>
            <div className="form-field">
              <label>Name*</label>
              <input name="name" required />
            </div>
            <div className="form-field">
              <label>Role</label>
              <input name="role" placeholder="Optional" />
            </div>
            <div className="form-field">
              <label>Email</label>
              <input name="email" type="email" placeholder="Optional" />
            </div>
            <div className="form-field">
              <label>Phone</label>
              <input name="phone" placeholder="Optional" />
            </div>
            <div className="form-field form-field--checkbox">
              <label>
                <input name="is_primary" type="checkbox" /> Primary contact
              </label>
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Add contact
              </button>
            </div>
          </form>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Supported device models</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Device model</th>
              <th>Rank</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {deviceModels.length === 0 && (
              <tr>
                <td colSpan={3}>No device models linked yet.</td>
              </tr>
            )}
            {deviceModels.map((d) => (
              <tr key={d.id}>
                <td>{modelName(d.device_model_id)}</td>
                <td>{d.rank === 0 ? "Primary" : `Alternative ${d.rank}`}</td>
                <td>{d.notes ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {canWrite && (
          <form onSubmit={handleLinkDeviceModel} className="form-grid" style={{ marginTop: 16 }}>
            <div className="form-field">
              <label>Device model*</label>
              <select name="device_model_id" required defaultValue="">
                <option value="" disabled>
                  Select device model
                </option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Rank (0 = primary)</label>
              <input name="rank" type="number" min={0} max={3} defaultValue={0} />
            </div>
            <div className="form-field">
              <label>Notes</label>
              <input name="notes" placeholder="Optional" />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Link device model
              </button>
            </div>
          </form>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Linked assets</h3>
        <p className="dashboard-note" style={{ marginTop: 0 }}>
          Every asset whose device model is linked to this vendor above (Part 3&apos;s device model → vendor join,
          same rule the Devices list page uses to resolve a vendor per asset).
        </p>
        <table className="data-table">
          <thead>
            <tr>
              <th>Asset number</th>
              <th>Serial number</th>
              <th>Device model</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {linkedAssets.length === 0 && (
              <tr>
                <td colSpan={4}>No assets linked to this vendor&apos;s device models yet.</td>
              </tr>
            )}
            {linkedAssets.map((a) => (
              <tr key={a.id}>
                <td>
                  <Link href={`/assets/${a.id}`}>{a.asset_number ?? "Not assigned"}</Link>
                </td>
                <td>{a.serial_number ?? "-"}</td>
                <td>{modelName(a.device_model_id)}</td>
                <td>
                  <StatusBadge value={a.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Procurement records</h3>
        {procurementError && <ErrorState message={procurementError} />}
        <p className="dashboard-note" style={{ marginTop: 0 }}>
          Quotations, purchase orders, and invoices filtered to this vendor.
        </p>
        <table className="data-table" style={{ marginBottom: 16 }}>
          <thead>
            <tr>
              <th>Quotation #</th>
              <th>Status</th>
              <th>Total</th>
              <th>Quote date</th>
            </tr>
          </thead>
          <tbody>
            {quotations.length === 0 && (
              <tr>
                <td colSpan={4}>No quotations from this vendor.</td>
              </tr>
            )}
            {quotations.map((q) => (
              <tr key={q.id}>
                <td>
                  <Link href={`/procurement/quotations/${q.id}`}>{q.quotation_number}</Link>
                </td>
                <td>
                  <StatusBadge value={q.status} />
                </td>
                <td>{q.total}</td>
                <td>{q.quote_date ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <table className="data-table">
          <thead>
            <tr>
              <th>PO #</th>
              <th>Status</th>
              <th>Total</th>
              <th>Issue date</th>
            </tr>
          </thead>
          <tbody>
            {purchaseOrders.length === 0 && (
              <tr>
                <td colSpan={4}>No purchase orders with this vendor.</td>
              </tr>
            )}
            {purchaseOrders.map((po) => (
              <tr key={po.id}>
                <td>
                  <Link href={`/procurement/purchase-orders/${po.id}`}>{po.po_number}</Link>
                </td>
                <td>
                  <StatusBadge value={po.status} />
                </td>
                <td>{po.total}</td>
                <td>{po.issue_date ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <table className="data-table" style={{ marginTop: 16 }}>
          <thead>
            <tr>
              <th>Invoice #</th>
              <th>Status</th>
              <th>Amount</th>
              <th>Invoice date</th>
            </tr>
          </thead>
          <tbody>
            {invoices.length === 0 && (
              <tr>
                <td colSpan={4}>No invoices from this vendor.</td>
              </tr>
            )}
            {invoices.map((inv) => (
              <tr key={inv.id}>
                <td>
                  <Link href={`/procurement/invoices/${inv.id}`}>{inv.invoice_number}</Link>
                </td>
                <td>
                  <StatusBadge value={inv.verification_status} />
                </td>
                <td>{inv.amount}</td>
                <td>{inv.invoice_date ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>AMC / CMC / Warranty contracts</h3>
        <p style={{ marginTop: 0, color: "var(--color-text-muted)" }}>
          Reuses the existing Contracts module (<code>GET /api/v1/contracts?vendor_id=</code>) — no contract data is
          duplicated onto Vendor.
        </p>
        <table className="data-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Contract number</th>
              <th>Start</th>
              <th>End</th>
              <th>Status</th>
              <th>Expiry</th>
            </tr>
          </thead>
          <tbody>
            {contracts.length === 0 && (
              <tr>
                <td colSpan={6}>No AMC/CMC/Warranty contracts linked to this vendor yet.</td>
              </tr>
            )}
            {contracts.map((c) => (
              <tr key={c.id}>
                <td>{c.contract_type}</td>
                <td>{c.contract_number ?? "-"}</td>
                <td>{c.start_date ?? "-"}</td>
                <td>{c.end_date ?? "-"}</td>
                <td>
                  <StatusBadge value={c.status} />
                </td>
                <td>
                  <StatusBadge value={c.expiry_status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

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
              <th>Next action</th>
            </tr>
          </thead>
          <tbody>
            {visits.length === 0 && (
              <tr>
                <td colSpan={7}>No service visits recorded for this vendor.</td>
              </tr>
            )}
            {visits.map((v) => (
              <tr key={v.id}>
                <td>{assetById.get(v.asset_id)?.asset_number ?? v.asset_id}</td>
                <td>{v.visit_date ?? "-"}</td>
                <td>{v.visit_type ?? "-"}</td>
                <td>{v.summary ?? "-"}</td>
                <td>{v.parts_used ?? "-"}</td>
                <td>{v.cost != null ? v.cost : "-"}</td>
                <td>{v.next_action ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Pagination page={visitPage} pageSize={VISIT_PAGE_SIZE} total={visitsTotal} onPageChange={setVisitPage} />
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete vendor"
        message="This permanently deletes the vendor. Only vendors with no linked contacts, device models, contracts, procurement records, or service visits can be deleted — for anything else, use Deactivate instead. Continue?"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </ProtectedShell>
  );
}
