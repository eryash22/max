import type { Branch } from "../types";

/** CENTRAL WAREHOUSE vs branch name vs "Location not assigned" — the one label
 * format for "where is this thing" across the app. Extracted from
 * app/inventory/page.tsx (Batch D, Part 4) so Batch E's Service Visit form
 * (Part 22) can show the exact same label for an asset's branch instead of a
 * second, independently-drifting implementation.
 *
 * `branchId === null/undefined` is a real, meaningful state for a `Consumable`
 * (the central/hub pool) — not a missing reference — so it renders as CENTRAL
 * WAREHOUSE rather than "Location not assigned". `Asset.branch_id` is a required
 * column and should always resolve to a real `Branch`; the "not assigned"
 * fallback only fires if the branch record itself can't be found in the given
 * list (never fabricated as a name). */
export function locationLabel(branchId: string | null | undefined, branches: Branch[]): string {
  if (!branchId) return "CENTRAL WAREHOUSE";
  const branch = branches.find((b) => b.id === branchId);
  if (!branch) return "Location not assigned";
  return branch.is_central_warehouse ? "CENTRAL WAREHOUSE" : branch.name;
}
