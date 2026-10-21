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
import type { PurchaseOrder, Vendor } from "../../../types";

export default function PurchaseOrdersPage() {
  const [statusFilter, setStatusFilter] = useState("");
  const { data, page, setPage, loading, error } = usePaginatedResource<PurchaseOrder>(
    "/api/v1/procurement/purchase-orders",
    statusFilter ? { status_filter: statusFilter } : {}
  );
  const vendors = useLookup<Vendor>("/api/v1/vendors");

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Purchase Orders</h1>
          <p>PO -&gt; Indent -&gt; Quotation -&gt; Incident -&gt; Asset -&gt; Vendor. Six states: Draft, Issued, Acknowledged, In Progress, Delivered, Completed.</p>
        </div>
      </div>

      <div className="toolbar">
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="DRAFT">Draft</option>
          <option value="ISSUED">Issued</option>
          <option value="ACKNOWLEDGED">Acknowledged</option>
          <option value="IN_PROGRESS">In Progress</option>
          <option value="DELIVERED">Delivered</option>
          <option value="COMPLETED">Completed</option>
        </select>
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<PurchaseOrder>
            columns={[
              {
                key: "po_number",
                header: "PO #",
                render: (p) => <Link href={`/procurement/purchase-orders/${p.id}`}>{p.po_number}</Link>,
              },
              { key: "vendor", header: "Vendor", render: (p) => vendors.find((v) => v.id === p.vendor_id)?.name ?? p.vendor_id },
              { key: "total", header: "Total", render: (p) => p.total.toLocaleString() },
              { key: "status", header: "Status", render: (p) => <StatusBadge value={p.status} /> },
              { key: "created_at", header: "Created", render: (p) => new Date(p.created_at).toLocaleDateString() },
            ]}
            rows={data.items}
            emptyLabel="No purchase orders found. Create one from a submitted indent."
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}
    </ProtectedShell>
  );
}
