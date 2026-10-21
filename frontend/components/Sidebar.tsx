"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "../hooks/useAuth";

// Restructure consolidation pass, Part J — regrouped so the sidebar reads as one
// coherent hospital-operations system instead of a flat list of internal entities.
// No page was removed: "Hospital networks" is de-emphasized into Administration
// (MAX Healthcare is now the fixed org context — see Part A — so it no longer needs
// primary-nav prominence, but the page/API/table are untouched and still reachable).
// Branch-scoping hardening pass — UNIT_HEAD/TL keep `hospital.read` (they still need
// to resolve their own branch's name/details elsewhere in the app; the backend scopes
// GET /branches to just that one branch for them, see routers/hospitals.py), but a
// permission-only gate can't distinguish "browse every branch" from "read my own" —
// there is no such thing as a Unit Head/TL who is *supposed* to see this
// administrative multi-branch page, so it is excluded by role here rather than left
// reachable. Real authorization is still enforced server-side regardless of this list.
const HIDE_BRANCHES_NAV_FOR_ROLES = ["UNIT_HEAD", "TL"];

const NAV_SECTIONS: { title: string; items: { href: string; label: string; permission?: string; anyPermission?: string[]; hideForRoles?: string[] }[] }[] = [
  {
    title: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard" },
      {
        href: "/raise",
        label: "Raise Request / Incident",
        anyPermission: ["requirement.create", "requirement.read", "requirement.approve", "incident.create", "incident.read"],
      },
      { href: "/calendar", label: "Calendar", permission: "asset.read" },
    ],
  },
  {
    title: "Organization",
    items: [
      { href: "/branches", label: "Branches", permission: "hospital.read", hideForRoles: HIDE_BRANCHES_NAV_FOR_ROLES },
      { href: "/departments", label: "Departments", permission: "hospital.read" },
      { href: "/departments/master", label: "Department Master", permission: "hospital.write" },
      { href: "/locations", label: "Locations", permission: "hospital.read" },
      { href: "/users", label: "Users", permission: "users.manage" },
    ],
  },
  {
    title: "Devices & assets",
    items: [
      { href: "/devices", label: "Device catalog", permission: "device.read" },
      { href: "/vendors", label: "Vendors", permission: "vendor.read" },
      { href: "/contracts", label: "AMC/CMC/Warranty", permission: "contract.read" },
    ],
  },
  {
    // Inventory/Asset unification pass — grouped around distinct physical-device
    // tasks (register, allocate, label) instead of one undifferentiated list.
    // "Physical Assets" is the former "Devices" link under Devices & assets
    // (same /assets route, not a duplicate page); "Device QR codes" moved here
    // from Devices & assets for the same reason — QR belongs beside the physical
    // records staff already manage here, not a second, separately-found screen.
    title: "Inventory",
    items: [
      { href: "/inventory", label: "Inventory overview", permission: "inventory.read" },
      { href: "/assets", label: "Physical Assets", permission: "asset.read" },
      { href: "/consumables", label: "Consumables", permission: "inventory.read" },
      { href: "/inventory/locations", label: "Storage locations", permission: "inventory.read" },
      { href: "/inventory/allocate", label: "Allocate Device to Branch", permission: "asset.write" },
      { href: "/qr-management", label: "QR Labels", permission: "qr.manage" },
    ],
  },
  {
    title: "Workflow",
    items: [
      {
        href: "/requirements",
        label: "Requirements",
        anyPermission: ["requirement.create", "requirement.read", "requirement.approve"],
      },
      {
        href: "/fulfillments",
        label: "Fulfillment & Delivery",
        anyPermission: ["requirement.create", "requirement.read", "requirement.approve", "requirement.fulfill"],
      },
      { href: "/incidents", label: "Incidents", permission: "incident.read" },
      {
        href: "/procurement",
        label: "Procurement",
        anyPermission: ["quotation.create", "quotation.read", "quotation.approve", "procurement.read", "procurement.manage", "po.manage", "invoice.record", "invoice.approve"],
      },
      { href: "/escalation", label: "Escalation policies", permission: "incident.read" },
      { href: "/maintenance", label: "Preventive maintenance", permission: "pm.read" },
      { href: "/calibration", label: "Calibration", permission: "calibration.read" },
      { href: "/service-visits", label: "Service visits", permission: "asset.read" },
    ],
  },
  {
    title: "Documents & communication",
    items: [
      { href: "/documents", label: "Documents", permission: "document.read" },
      { href: "/notifications", label: "Notifications", permission: "notification.read" },
      { href: "/communication-rules", label: "Communication rules", permission: "notification.approve" },
    ],
  },
  {
    title: "Business impact & analytics",
    items: [
      { href: "/business-impact", label: "Business impact & analytics", permission: "business_impact.read" },
    ],
  },
  {
    title: "Data governance",
    items: [
      { href: "/imports", label: "Data import center", permission: "import.create" },
      { href: "/review-queue", label: "Review queue", permission: "review.read" },
      { href: "/audit-logs", label: "Audit logs", permission: "audit.read" },
    ],
  },
  {
    title: "Administration",
    items: [
      { href: "/hospitals", label: "Hospital networks (MAX Healthcare)", permission: "hospital.read" },
      { href: "/roles", label: "Roles & permissions", permission: "roles.manage" },
      { href: "/settings", label: "Settings" },
      { href: "/settings/theme", label: "Theme", permission: "settings.manage" },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const { hasPermission, user } = useAuth();

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__env">DEVELOPMENT / DEMO</span>
        <strong>MAX Biomedical AI</strong>
      </div>
      <nav>
        {NAV_SECTIONS.map((section) => {
          const items = section.items.filter(
            (item) =>
              (!item.permission || hasPermission(item.permission)) &&
              (!item.anyPermission || item.anyPermission.some((p) => hasPermission(p))) &&
              (!item.hideForRoles || !item.hideForRoles.some((r) => user?.roles.includes(r)))
          );
          if (items.length === 0) return null;
          return (
            <div className="sidebar__section" key={section.title}>
              <div className="sidebar__section-title">{section.title}</div>
              {items.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`sidebar__link ${pathname?.startsWith(item.href) ? "sidebar__link--active" : ""}`}
                >
                  {item.label}
                </Link>
              ))}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
