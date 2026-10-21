"use client";

import { useState } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { DataTable } from "../../../components/DataTable";
import { Pagination } from "../../../components/Pagination";
import { StatusBadge } from "../../../components/StatusBadge";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { usePaginatedResource } from "../../../hooks/usePaginatedResource";
import { useLookup } from "../../../hooks/useLookup";
import type { Invoice, Vendor } from "../../../types";

export default function InvoicesPage() {
  const [statusFilter, setStatusFilter] = useState("");
  const { data, page, setPage, loading, error } = usePaginatedResource<Invoice>(
    "/api/v1/procurement/invoices",
    statusFilter ? { verification_status: statusFilter } : {}
  );
  const vendors = useLookup<Vendor>("/api/v1/vendors");

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Invoices</h1>
          <p>Vendor invoices against a Purchase Order. Verification (Inventory Manager) is financial closure, independent of the Incident&apos;s own technical closure.</p>
        </div>
      </div>

      <div className="toolbar">
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="PENDING">Pending</option>
          <option value="VERIFIED">Verified</option>
          <option value="REJECTED">Rejected</option>
        </select>
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<Invoice>
            columns={[
              {
                key: "invoice_number",
                header: "Invoice #",
                render: (i) => <Link href={`/procurement/invoices/${i.id}`}>{i.invoice_number}</Link>,
              },
              { key: "vendor", header: "Vendor", render: (i) => vendors.find((v) => v.id === i.vendor_id)?.name ?? i.vendor_id },
              { key: "amount", header: "Amount", render: (i) => i.amount.toLocaleString() },
              { key: "verification_status", header: "Status", render: (i) => <StatusBadge value={i.verification_status} /> },
              { key: "recorded_at", header: "Recorded", render: (i) => (i.recorded_at ? new Date(i.recorded_at).toLocaleDateString() : "-") },
            ]}
            rows={data.items}
            emptyLabel="No invoices found. Record one from a purchase order."
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}
    </ProtectedShell>
  );
}
