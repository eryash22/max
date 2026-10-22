// Shape of GET /api/v1/dashboard/summary (backend: services/dashboard_service.py).
//
// Every section is optional because the backend OMITS a section the caller has no
// permission for rather than sending zeros — so `summary.incidents === undefined`
// means "not authorized", never "authorized but empty". The page renders what it is
// given instead of re-deciding what to hide, which keeps frontend visibility and
// backend authorization from drifting apart.

export interface DashboardIncidents {
  total: number;
  open: number;
  critical_open: number;
  by_status: Record<string, number>;
  by_severity: Record<string, number>;
  /** The canonical open-status list (F1). Used to build drill-down links so a chart
   * click filters on exactly the definition the count was computed from. */
  open_statuses: string[];
  /** by_status["TO_BE_CONDEMNED"], surfaced separately since it's shown as its own
   * KPI tile (not the asset lifecycle's `condemned`, which only reflects assets that
   * actually finished the real condemnation workflow). */
  to_be_condemned: number;
  /** Echo of the period filter the backend computed `period_total` for. */
  period: "overall" | "year" | "month";
  period_year: number | null;
  period_month: number | null;
  /** Incidents created within the selected period (see `period`/`period_year`/
   * `period_month`), independent of status. Replaces the old fixed financial-year tile. */
  period_total: number;
  /** sum(po_amount) / sum(po_cost_excl_gst) across every incident with PO data. */
  po_spending_incl_gst: number;
  po_spending_excl_gst: number;
}

export interface DashboardRequirements {
  total: number;
  by_status: Record<string, number>;
  pending_approval: number;
  submitted: number;
}

export interface DashboardFulfillments {
  total: number;
  by_status: Record<string, number>;
  pending_allocation: number;
  under_process: number;
  out_for_delivery: number;
}

export interface DashboardMaintenanceBucket {
  due: number;
  overdue: number;
}

export interface DashboardMaintenance {
  /** Present only when the caller holds the matching permission — pm.read and
   * calibration.read are granted independently. */
  pm?: DashboardMaintenanceBucket;
  calibration?: DashboardMaintenanceBucket;
}

export interface DashboardMaintenanceEntry {
  id: string;
  asset_id: string | null;
  source_type: string;
  maintenance_type: string | null;
  service_date: string | null;
}

export interface DashboardAssets {
  total: number;
  by_status: Record<string, number>;
  operational: number;
  under_maintenance: number;
  under_repair: number;
  condemned: number;
  retired: number;
  amc_active: number;
  amc_expired: number;
  cmc_active: number;
  cmc_expired: number;
  warranty_active: number;
  warranty_expired: number;
  upcoming_amc_expiry: number;
  upcoming_cmc_expiry: number;
  recent_maintenance: DashboardMaintenanceEntry[];
}

export interface DashboardReview {
  total: number;
  open: number;
}

/** One row of central warehouse stock: physical Asset rows grouped by DeviceModel,
 * CONDEMNED/RETIRED already excluded server-side. Never a DeviceModel count. */
export interface DashboardCentralStock {
  device_model_id: string;
  name: string;
  manufacturer: string | null;
  model_number: string | null;
  category: string | null;
  available_count: number;
}

export interface DashboardBranchRow {
  id: string;
  name: string;
  status: string;
  is_central_warehouse: boolean;
  devices: number;
  operational: number;
  /** null when the caller lacks the permission for that column. */
  open_incidents: number | null;
  /** Strict single-status counts — open_incidents above is their sum plus
   * to_be_condemned (all three non-terminal statuses), kept for backward
   * compatibility; these let a chart/column show OPEN and WORK_IN_PROGRESS
   * on their own without double-counting against to_be_condemned. */
  open_only: number | null;
  work_in_progress: number | null;
  total_incidents: number | null;
  to_be_condemned: number | null;
  po_spend_incl_gst: number | null;
  pending_requirements: number | null;
}

export interface DashboardCapabilities {
  asset: boolean;
  incident: boolean;
  requirement: boolean;
  fulfillment: boolean;
  inventory: boolean;
  contract: boolean;
  pm: boolean;
  calibration: boolean;
  review: boolean;
  procurement: boolean;
  audit: boolean;
}

export interface DashboardSummary {
  /** The branch the numbers were actually computed for — null means organisation-wide.
   * For a UNIT_HEAD/TL this is always their assigned branch, whatever was requested. */
  branch_id: string | null;
  scope: "BRANCH" | "ORGANIZATION";
  /** False for branch-scoped roles: they must never be offered a branch selector. */
  can_choose_branch: boolean;
  sections: string[];
  capabilities: DashboardCapabilities;
  assets?: DashboardAssets;
  incidents?: DashboardIncidents;
  requirements?: DashboardRequirements;
  fulfillments?: DashboardFulfillments;
  maintenance?: DashboardMaintenance;
  review?: DashboardReview;
  central_device_stock?: DashboardCentralStock[];
  branches?: DashboardBranchRow[];
}

/** The dashboard's semantic vocabulary. Each tone maps to one theme token pair in
 * globals.css and means the same thing everywhere it appears — a status never changes
 * colour between a KPI card, a chart segment and a table badge. */
export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

/** Status -> tone, defined once so every visual agrees. Derived from the real backend
 * vocabularies, not invented. */
export const INCIDENT_STATUS_TONE: Record<string, Tone> = {
  OPEN: "danger",
  WORK_IN_PROGRESS: "warning",
  TO_BE_CONDEMNED: "info",
  OPERATIONAL: "success",
  CANCELLED: "neutral",
};

export const REQUIREMENT_STATUS_TONE: Record<string, Tone> = {
  DRAFT: "neutral",
  SUBMITTED: "info",
  PENDING_MANAGER_APPROVAL: "warning",
  APPROVED: "success",
  REJECTED: "danger",
  CANCELLED: "neutral",
};

export const FULFILLMENT_STATUS_TONE: Record<string, Tone> = {
  PENDING_ALLOCATION: "warning",
  ALLOCATED: "info",
  UNDER_PROCESS: "info",
  PACKED: "info",
  OUT_FOR_DELIVERY: "info",
  DELIVERED: "success",
  COMPLETED: "success",
  CANCELLED: "neutral",
};

export const ASSET_STATUS_TONE: Record<string, Tone> = {
  OPERATIONAL: "success",
  UNDER_MAINTENANCE: "warning",
  UNDER_REPAIR: "warning",
  CONDEMNED: "danger",
  RETIRED: "neutral",
};

export const SEVERITY_TONE: Record<string, Tone> = {
  CRITICAL: "danger",
  HIGH: "danger",
  MEDIUM: "warning",
  LOW: "info",
};

/** Human labels — the UI never shows a raw SCREAMING_SNAKE status. */
export function humanizeStatus(status: string): string {
  return status
    .split("_")
    .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
    .join(" ");
}
