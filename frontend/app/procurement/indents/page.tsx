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
import type { Indent, Vendor } from "../../../types";

export default function IndentsPage() {
  const [statusFilter, setStatusFilter] = useState("");
  const { data, page, setPage, loading, error } = usePaginatedResource<Indent>(
    "/api/v1/procurement/indents",
    statusFilter ? { status_filter: statusFilter } : {}
  );
  const vendors = useLookup<Vendor>("/api/v1/vendors");

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Indents / CRs</h1>
          <p>Raised by the Procurement Officer against an approved quotation&apos;s uncovered lines. No second approval gate — the Manager already approved at the quotation stage.</p>
        </div>
      </div>

      <div className="toolbar">
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="DRAFT">Draft</option>
          <option value="SUBMITTED">Submitted</option>
          <option value="COMPLETED">Completed</option>
        </select>
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {data && (
        <>
          <DataTable<Indent>
            columns={[
              {
                key: "indent_number",
                header: "Indent #",
                render: (i) => <Link href={`/procurement/indents/${i.id}`}>{i.indent_number}</Link>,
              },
              { key: "vendor", header: "Vendor", render: (i) => vendors.find((v) => v.id === i.vendor_id)?.name ?? i.vendor_id },
              { key: "amount", header: "Amount", render: (i) => i.amount.toLocaleString() },
              { key: "status", header: "Status", render: (i) => <StatusBadge value={i.status} /> },
              { key: "created_at", header: "Created", render: (i) => new Date(i.created_at).toLocaleDateString() },
            ]}
            rows={data.items}
            emptyLabel="No indents found. Create one from an approved quotation."
          />
          <Pagination page={page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
        </>
      )}
    </ProtectedShell>
  );
}
