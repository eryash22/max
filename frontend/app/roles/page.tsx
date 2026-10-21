"use client";

import { useEffect, useMemo, useState } from "react";
import { ProtectedShell } from "../../components/ProtectedShell";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type { PermissionRecord, RoleRecord } from "../../types";

// Centralized Role & Permissions UI — groups the flat permission catalog into the
// same modules the sidebar/app is organized into, so Super Admin edits permissions
// the way they actually think about the app ("what can this role do in Inventory?")
// rather than scanning a flat alphabetical list of raw codes. Grouping/labels here
// are presentation-only — every code comes straight from GET /api/v1/permissions
// (backend/app/security/permissions.py's ALL_PERMISSIONS), nothing is invented, and
// any permission the backend returns that isn't in PERMISSION_GROUPS still renders
// (see the "Other" fallback group below) instead of silently disappearing.
const PERMISSION_GROUPS: { title: string; prefixes: string[] }[] = [
  { title: "Organization", prefixes: ["hospital."] },
  { title: "Devices & Assets", prefixes: ["device.", "asset."] },
  { title: "Inventory", prefixes: ["inventory."] },
  { title: "Requirements & Fulfillment", prefixes: ["requirement."] },
  { title: "Incidents", prefixes: ["incident."] },
  { title: "Maintenance & Calibration", prefixes: ["pm.", "calibration."] },
  { title: "Vendors & Contracts", prefixes: ["vendor.", "contract."] },
  { title: "Procurement", prefixes: ["quotation.", "po.", "invoice.", "procurement."] },
  { title: "Documents", prefixes: ["document."] },
  { title: "Notifications", prefixes: ["notification.", "notification_template."] },
  { title: "Business impact & analytics", prefixes: ["business_impact."] },
  { title: "Data governance", prefixes: ["import.", "review.", "audit."] },
  { title: "Administration", prefixes: ["users.", "roles.", "settings.", "dev."] },
];

// Human-readable labels for every permission code the backend currently defines
// (backend/app/security/permissions.py's ALL_PERMISSIONS) — codes are still shown
// underneath each label for admins who want the technical name.
const PERMISSION_LABELS: Record<string, string> = {
  "hospital.read": "View branches, buildings, floors, departments & locations",
  "hospital.write": "Create / edit branches, buildings, floors, departments & locations",
  "device.read": "View device catalog (full admin detail)",
  "device.write": "Create / edit / archive device catalog models",
  "device.read_picker": "Pick a device model by category (requirement raising only)",
  "asset.read": "View physical devices / assets",
  "asset.write": "Create / edit physical devices / assets",
  "inventory.read": "View inventory items & stock levels",
  "inventory.write": "Create / edit inventory items & stock",
  "inventory.picker_read": "Pick an inventory item by name (stock levels hidden)",
  "import.create": "Upload a data import batch",
  "import.approve": "Approve a data import batch",
  "review.read": "View the data review queue",
  "review.resolve": "Resolve / act on review queue items",
  "users.manage": "Create, edit & deactivate users; assign roles",
  "roles.manage": "Manage roles & permissions (this screen)",
  "audit.read": "View audit logs",
  "document.read": "View documents",
  "document.write": "Upload / edit documents",
  "incident.create": "Report an incident / device problem",
  "incident.read": "View incidents",
  "incident.manage": "Assign, update status & close incidents",
  "pm.read": "View preventive maintenance schedules & records",
  "pm.write": "Create / edit preventive maintenance schedules & records",
  "calibration.read": "View calibration schedules & records",
  "calibration.write": "Create / edit calibration schedules & records",
  "vendor.read": "View vendors (full contact detail)",
  "vendor.write": "Create / edit vendors",
  "vendor.read_limited": "View vendors (contact detail hidden)",
  "contract.read": "View AMC / CMC / warranty contracts",
  "contract.write": "Create / edit AMC / CMC / warranty contracts",
  "notification.read": "View notifications",
  "notification.approve": "Approve / configure notification rules",
  "notification_template.delete": "Permanently delete a notification template",
  "business_impact.read": "View business impact & analytics",
  "requirement.create": "Raise / submit / cancel own requirements",
  "requirement.read": "View requirements (broad, read-only)",
  "requirement.approve": "Approve / reject requirements",
  "requirement.delete": "Permanently delete a requirement",
  "requirement.fulfill": "Execute requirement fulfillment & delivery",
  "quotation.create": "Record a vendor quotation",
  "quotation.read": "View quotations",
  "quotation.approve": "Approve a vendor quotation",
  "procurement.manage": "Create & execute Indent / Change Request",
  "po.manage": "Create & execute Purchase Orders",
  "invoice.record": "Record a vendor invoice",
  "invoice.approve": "Verify / reject a recorded invoice",
  "procurement.read": "View procurement status (Indent / PO / Invoice)",
  "settings.manage": "Manage system theme / settings",
  "dev.data_cleanup": "Run DEV/QA data cleanup (Super Admin only)",
};

function groupTitleFor(code: string): string {
  const match = PERMISSION_GROUPS.find((g) => g.prefixes.some((p) => code.startsWith(p)));
  return match?.title ?? "Other";
}

function sameSet(a: Set<string>, b: string[]): boolean {
  if (a.size !== b.length) return false;
  return b.every((code) => a.has(code));
}

export default function RolesPage() {
  const { hasPermission } = useAuth();
  if (!hasPermission("roles.manage")) {
    return (
      <ProtectedShell>
        <ErrorState message="You do not have permission to manage roles." />
      </ProtectedShell>
    );
  }
  return <RolesPageContent />;
}

function RolesPageContent() {
  const [roles, setRoles] = useState<RoleRecord[] | null>(null);
  const [permissions, setPermissions] = useState<PermissionRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [selectedRoleId, setSelectedRoleId] = useState<string | null>(null);
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  async function load(keepSelection = true) {
    try {
      const [roleList, permissionList] = await Promise.all([
        api.get<RoleRecord[]>("/api/v1/roles"),
        api.get<PermissionRecord[]>("/api/v1/permissions"),
      ]);
      setRoles(roleList);
      setPermissions(permissionList);
      const nextSelectedId = keepSelection && selectedRoleId && roleList.some((r) => r.id === selectedRoleId)
        ? selectedRoleId
        : roleList[0]?.id ?? null;
      setSelectedRoleId(nextSelectedId);
      const nextRole = roleList.find((r) => r.id === nextSelectedId);
      setPending(new Set(nextRole?.permissions ?? []));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load roles.");
    }
  }

  useEffect(() => {
    load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedRole = useMemo(() => roles?.find((r) => r.id === selectedRoleId) ?? null, [roles, selectedRoleId]);
  const dirty = selectedRole ? !sameSet(pending, selectedRole.permissions) : false;

  const groupedPermissions = useMemo(() => {
    const groups = new Map<string, PermissionRecord[]>();
    for (const permission of permissions) {
      const title = groupTitleFor(permission.code);
      if (!groups.has(title)) groups.set(title, []);
      groups.get(title)!.push(permission);
    }
    const order = [...PERMISSION_GROUPS.map((g) => g.title), "Other"];
    return order.filter((title) => groups.has(title)).map((title) => ({ title, permissions: groups.get(title)! }));
  }, [permissions]);

  function selectRole(roleId: string) {
    if (dirty && !window.confirm("You have unsaved changes for the current role. Discard them?")) return;
    setSelectedRoleId(roleId);
    const role = roles?.find((r) => r.id === roleId);
    setPending(new Set(role?.permissions ?? []));
    setSaveError(null);
    setSaveMessage(null);
  }

  function toggle(code: string) {
    setSaveMessage(null);
    setPending((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }

  function resetPending() {
    setPending(new Set(selectedRole?.permissions ?? []));
    setSaveError(null);
    setSaveMessage(null);
  }

  async function saveChanges() {
    if (!selectedRole) return;
    setSaving(true);
    setSaveError(null);
    setSaveMessage(null);
    try {
      // Full replacement, not additive — the backend deletes every existing
      // role_permission row for this role and reinserts exactly this list
      // (routers/roles.py:update_role), so an unchecked box is actually removed.
      await api.patch(`/api/v1/roles/${selectedRole.id}`, { permission_codes: Array.from(pending) });
      await load();
      setSaveMessage(`Saved. ${selectedRole.name}'s permissions are now in effect for every user assigned this role.`);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Failed to save changes.");
    } finally {
      setSaving(false);
    }
  }

  if (error) {
    return (
      <ProtectedShell>
        <ErrorState message={error} />
      </ProtectedShell>
    );
  }
  if (!roles) {
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
          <h1>Roles &amp; permissions</h1>
          <p>Select a role, choose its permissions, then click Save changes. Backend enforces these grants on every request.</p>
        </div>
      </div>

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <div className="card" style={{ minWidth: 220, flexShrink: 0, padding: 8 }}>
          {roles.map((role) => (
            <button
              key={role.id}
              type="button"
              onClick={() => selectRole(role.id)}
              className="btn btn--ghost"
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                marginBottom: 4,
                background: role.id === selectedRoleId ? "var(--surface-selected, rgba(99,102,241,0.12))" : undefined,
                fontWeight: role.id === selectedRoleId ? 600 : 400,
              }}
            >
              {role.name}
            </button>
          ))}
        </div>

        {selectedRole && (
          <div className="card" style={{ flex: 1 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
              <div>
                <h3 style={{ margin: 0 }}>{selectedRole.name}</h3>
                {selectedRole.description && <p className="field-hint" style={{ margin: "4px 0 0" }}>{selectedRole.description}</p>}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" className="btn btn--ghost" onClick={resetPending} disabled={!dirty || saving}>
                  Discard changes
                </button>
                <button type="button" className="btn btn--primary" onClick={saveChanges} disabled={!dirty || saving}>
                  {saving ? "Saving..." : "Save changes"}
                </button>
              </div>
            </div>

            {dirty && <div className="state-panel state-panel--warning" style={{ marginBottom: 12 }}>You have unsaved changes.</div>}
            {saveError && <ErrorState message={saveError} />}
            {saveMessage && <div className="state-panel state-panel--success" style={{ marginBottom: 12 }}>{saveMessage}</div>}

            {groupedPermissions.map((group) => (
              <div key={group.title} style={{ marginBottom: 20 }}>
                <h4 style={{ marginBottom: 8 }}>{group.title}</h4>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 8 }}>
                  {group.permissions.map((permission) => (
                    <label
                      key={permission.id}
                      style={{ display: "flex", alignItems: "flex-start", gap: 8, cursor: "pointer" }}
                    >
                      <input
                        type="checkbox"
                        checked={pending.has(permission.code)}
                        onChange={() => toggle(permission.code)}
                        style={{ marginTop: 3 }}
                      />
                      <span>
                        <span style={{ display: "block" }}>{PERMISSION_LABELS[permission.code] ?? permission.code}</span>
                        <span className="field-hint" style={{ display: "block" }}>{permission.code}</span>
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </ProtectedShell>
  );
}
