"use client";

import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { useAuth } from "../../hooks/useAuth";

// Module 21 — Procurement hub. BME incident -> technical inspection -> AMC/CMC/
// Warranty check -> [fully covered: vendor service, no quotation] / [partially or not
// covered: Quotation -> Manager approval -> Indent/CR -> Purchase Order -> vendor
// visit -> Invoice -> Inventory Manager verification -> financial closure]. Each tile
// below routes into its own list page; the workflow itself lives in the detail pages.
const TILES = [
  {
    href: "/procurement/quotations",
    label: "Quotations",
    description: "Vendor quotations for partially/not-covered repairs, and Manager approval.",
    anyPermission: ["quotation.create", "quotation.read", "quotation.approve"],
  },
  {
    href: "/procurement/indents",
    label: "Indents / CRs",
    description: "Indent/Change Request raised against an approved quotation.",
    anyPermission: ["procurement.manage", "procurement.read"],
  },
  {
    href: "/procurement/purchase-orders",
    label: "Purchase Orders",
    description: "PO issued to the vendor, tracked through to delivery and completion.",
    anyPermission: ["po.manage", "procurement.read"],
  },
  {
    href: "/procurement/invoices",
    label: "Invoices",
    description: "Vendor invoices and Inventory Manager verification (financial closure).",
    anyPermission: ["invoice.record", "invoice.approve", "procurement.read"],
  },
];

export default function ProcurementHubPage() {
  const { hasPermission } = useAuth();
  const visibleTiles = TILES.filter((t) => t.anyPermission.some((p) => hasPermission(p)));

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Procurement</h1>
          <p>
            Device Incident → Technical Inspection → AMC/CMC/Warranty check. Fully covered repairs go straight to
            vendor service under the existing contract — no quotation. Partially or not covered repairs go
            Quotation → Manager approval → Indent/CR → Purchase Order → Vendor visit → Invoice → Inventory Manager
            verification.
          </p>
        </div>
      </div>

      {visibleTiles.length === 0 && (
        <div className="card">
          <p style={{ margin: 0 }}>You don&apos;t have permission to view any procurement area.</p>
        </div>
      )}

      <div className="stat-grid">
        {visibleTiles.map((tile) => (
          <Link key={tile.href} className="stat-tile-link" href={tile.href}>
            <div className="stat-tile">
              <div className="stat-tile__label">{tile.label}</div>
              <p style={{ margin: "4px 0 0", fontSize: "0.85em" }}>{tile.description}</p>
            </div>
          </Link>
        ))}
      </div>
    </ProtectedShell>
  );
}
