"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { DataTable } from "../../../components/DataTable";
import { Pagination } from "../../../components/Pagination";
import { StatusBadge } from "../../../components/StatusBadge";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { usePaginatedResource } from "../../../hooks/usePaginatedResource";
import { useLookup } from "../../../hooks/useLookup";
import { useAuth } from "../../../hooks/useAuth";
import { api, ApiError } from "../../../lib/api";
import type { Asset, Incident, Quotation, Vendor } from "../../../types";

interface LineRow {
  description: string;
  quantity: string;
  unit_price: string;
  taxes: string;
  covered: boolean;
}

const EMPTY_LINE: LineRow = { description: "", quantity: "1", unit_price: "0", taxes: "0", covered: false };

export default function QuotationsPage() {
  const [statusFilter, setStatusFilter] = useState("");
  const { data, page, setPage, loading, error, reload } = usePaginatedResource<Quotation>(
    "/api/v1/procurement/quotations",
    statusFilter ? { status_filter: statusFilter } : {}
  );
  const incidents = useLookup<Incident>("/api/v1/incidents");
  const assets = useLookup<Asset>("/api/v1/assets");
  const vendors = useLookup<Vendor>("/api/v1/vendors");
  const { hasPermission } = useAuth();
  const canCreate = hasPermission("quotation.create");

  const [showForm, setShowForm] = useState(false);
  const [incidentId, setIncidentId] = useState("");
  const [assetId, setAssetId] = useState("");
  const [vendorId, setVendorId] = useState("");
  const [quoteDate, setQuoteDate] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [coverageNote, setCoverageNote] = useState("");
  const [lines, setLines] = useState<LineRow[]>([{ ...EMPTY_LINE }]);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function updateLine(index: number, patch: Partial<LineRow>) {
    setLines((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  function addLine() {
    setLines((rows) => [...rows, { ...EMPTY_LINE }]);
  }

  function removeLine(index: number) {
    setLines((rows) => rows.filter((_, i) => i !== index));
  }

  function incidentAsset(id: string) {
    return incidents.find((i) => i.id === id)?.asset_id ?? "";
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    if (!incidentId || !assetId || !vendorId) {
      setFormError("Incident, asset, and vendor are required.");
      return;
    }
    if (lines.length === 0 || lines.some((l) => !l.description.trim())) {
      setFormError("At least one line with a description is required.");
      return;
    }
    setBusy(true);
    try {
      const created = await api.post<Quotation>("/api/v1/procurement/quotations", {
        incident_id: incidentId,
        asset_id: assetId,
        vendor_id: vendorId,
        quote_date: quoteDate || null,
        valid_until: validUntil || null,
        coverage_note: coverageNote || null,
        lines: lines.map((l) => ({
          description: l.description,
          quantity: Number(l.quantity) || 1,
          unit_price: Number(l.unit_price) || 0,
          taxes: Number(l.taxes) || 0,
          covered: l.covered,
        })),
      });
      setShowForm(false);
      setIncidentId("");
      setAssetId("");
      setVendorId("");
      setQuoteDate("");
      setValidUntil("");
      setCoverageNote("");
      setLines([{ ...EMPTY_LINE }]);
      await reload();
      window.location.href = `/procurement/quotations/${created.id}`;
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create quotation.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Quotations</h1>
          <p>Vendor quotations for partially/not-covered repairs. Fully covered repairs cannot become a quotation.</p>
        </div>
        {canCreate && (
          <button className="btn btn--primary" onClick={() => setShowForm((v) => !v)}>
            {showForm ? "Cancel" : "New quotation"}
          </button>
        )}
      </div>

      {showForm && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleSubmit}>
          <h3 style={{ marginTop: 0 }}>New quotation</h3>
          {formError && <ErrorState message={formError} />}
          <div className="form-grid" style={{ marginBottom: 12 }}>
            <div className="form-field">
              <label>Incident*</label>
              <select
                value={incidentId}
                onChange={(e) => {
                  setIncidentId(e.target.value);
                  setAssetId(incidentAsset(e.target.value));
                }}
                required
              >
                <option value="">Select incident</option>
                {incidents.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.description.slice(0, 60)}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Asset*</label>
              <select value={assetId} onChange={(e) => setAssetId(e.target.value)} required>
                <option value="">Select asset</option>
                {assets.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.asset_number ?? a.serial_number ?? a.id}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Vendor*</label>
              <select value={vendorId} onChange={(e) => setVendorId(e.target.value)} required>
                <option value="">Select vendor</option>
                {vendors.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Quote date</label>
              <input type="date" value={quoteDate} onChange={(e) => setQuoteDate(e.target.value)} />
            </div>
            <div className="form-field">
              <label>Valid until</label>
              <input type="date" value={validUntil} onChange={(e) => setValidUntil(e.target.value)} />
            </div>
          </div>

          <div className="form-field" style={{ marginBottom: 12 }}>
            <label>Coverage note (required only if some lines are covered under an active AMC/CMC/Warranty)</label>
            <textarea rows={2} value={coverageNote} onChange={(e) => setCoverageNote(e.target.value)} />
          </div>

          <table className="data-table" style={{ marginBottom: 12 }}>
            <thead>
              <tr>
                <th>Description</th>
                <th>Qty</th>
                <th>Unit price</th>
                <th>Taxes</th>
                <th>Covered by contract</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {lines.map((line, i) => (
                <tr key={i}>
                  <td>
                    <input
                      value={line.description}
                      onChange={(e) => updateLine(i, { description: e.target.value })}
                      placeholder="e.g. Replace pump motor"
                    />
                  </td>
                  <td style={{ width: 80 }}>
                    <input
                      type="number"
                      min={1}
                      value={line.quantity}
                      onChange={(e) => updateLine(i, { quantity: e.target.value })}
                    />
                  </td>
                  <td style={{ width: 120 }}>
                    <input
                      type="number"
                      min={0}
                      step="0.01"
                      value={line.unit_price}
                      onChange={(e) => updateLine(i, { unit_price: e.target.value })}
                    />
                  </td>
                  <td style={{ width: 100 }}>
                    <input
                      type="number"
                      min={0}
                      step="0.01"
                      value={line.taxes}
                      onChange={(e) => updateLine(i, { taxes: e.target.value })}
                    />
                  </td>
                  <td style={{ width: 60, textAlign: "center" }}>
                    <input
                      type="checkbox"
                      checked={line.covered}
                      onChange={(e) => updateLine(i, { covered: e.target.checked })}
                    />
                  </td>
                  <td>
                    {lines.length > 1 && (
                      <button type="button" className="btn btn--ghost" onClick={() => removeLine(i)}>
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" className="btn btn--ghost" onClick={addLine} style={{ marginBottom: 12 }}>
            Add line
          </button>
          <div>
            <button className="btn btn--primary" type="submit" disabled={busy}>
              Create quotation
            </button>
          </div>
        </form>
      )}

      <div className="toolbar">
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="DRAFT">Draft</option>
          <option value="SUBMITTED">Submitted</option>
          <option value="APPROVED">Approved</option>
          <option value="REJECTED">Rejected</option>
          <option value="CANCELLED">Cancelled</option>
        </select>
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<Quotation>
            columns={[
              {
                key: "quotation_number",
                header: "Quotation #",
                render: (q) => <Link href={`/procurement/quotations/${q.id}`}>{q.quotation_number}</Link>,
              },
              {
                key: "vendor",
                header: "Vendor",
                render: (q) => vendors.find((v) => v.id === q.vendor_id)?.name ?? q.vendor_id,
              },
              { key: "coverage_status", header: "Coverage", render: (q) => <StatusBadge value={q.coverage_status} /> },
              { key: "total", header: "Total", render: (q) => q.total.toLocaleString() },
              { key: "status", header: "Status", render: (q) => <StatusBadge value={q.status} /> },
              { key: "created_at", header: "Created", render: (q) => new Date(q.created_at).toLocaleDateString() },
            ]}
            rows={data.items}
            emptyLabel="No quotations found."
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}
    </ProtectedShell>
  );
}
