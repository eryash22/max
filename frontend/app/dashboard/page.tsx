"use client";

// ============================================================================
// Operations dashboard — role-aware, interactive, one request.
//
// Data:   a single GET /api/v1/dashboard/summary. The previous version issued
//         ~37 + 8-per-branch requests (one per KPI, via `?page_size=1` count hacks).
//         Every number below comes from that one aggregated, branch-scoped,
//         permission-filtered response.
//
// Authorization: the backend OMITS sections the caller may not see, so this page
//         renders what it is given. `summary.incidents === undefined` means "not
//         authorized" — the page never decides on its own what to hide, which is what
//         keeps frontend visibility and backend authorization in step.
//
// Role:   permissions decide WHAT appears; role decides the ORDER and emphasis, because
//         an Inventory Manager and a Unit Head need different things first. See
//         `sectionOrderFor()`.
//
// Interaction: clicking a chart segment navigates to the existing page that already
//         supports that filter — only params those pages actually read are ever used
//         (verified against each page's `params.get(...)` calls). Clicking a branch
//         re-requests the summary with `branch_id`, a real server-side cross-filter;
//         a branch-scoped role is pinned server-side and never gets the control.
// ============================================================================

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { ErrorState } from "../../components/ErrorState";
import { BarChart, DonutChart, Panel, PipelineChart, type Slice } from "../../components/dashboard/Charts";
import { KpiCard, KpiSkeleton } from "../../components/dashboard/KpiCard";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import {
  ASSET_STATUS_TONE,
  FULFILLMENT_STATUS_TONE,
  INCIDENT_STATUS_TONE,
  REQUIREMENT_STATUS_TONE,
  SEVERITY_TONE,
  humanizeStatus,
  type DashboardSummary,
  type Tone,
} from "../../types/dashboard";

/** Ordered workflow vocabularies — these mirror the backend's real status sequences
 * (schemas/requirement.py, schemas/fulfillment.py). Order is meaningful: both render
 * as a funnel, so a wrong order would misrepresent the workflow. */
const REQUIREMENT_FLOW = ["DRAFT", "SUBMITTED", "PENDING_MANAGER_APPROVAL", "APPROVED"];
const FULFILLMENT_FLOW = [
  "PENDING_ALLOCATION",
  "ALLOCATED",
  "UNDER_PROCESS",
  "PACKED",
  "OUT_FOR_DELIVERY",
  "DELIVERED",
  "COMPLETED",
];

type SectionKey =
  | "attention"
  | "operations"
  | "assets"
  | "workflow"
  | "warehouse"
  | "maintenance"
  | "branches";

/** Role-specific emphasis. Permissions still gate every section — this only decides
 * what a role sees FIRST, which is a real business distinction: an Inventory Manager
 * opens the page to answer "what stock and fulfillment needs me?", a Unit Head to
 * answer "what is happening in my branch?". */
function sectionOrderFor(roles: string[]): SectionKey[] {
  if (roles.includes("INVENTORY_MANAGER")) {
    return ["attention", "operations", "warehouse", "workflow", "assets", "maintenance", "branches"];
  }
  if (roles.includes("UNIT_HEAD") || roles.includes("TL")) {
    return ["attention", "operations", "assets", "workflow", "maintenance", "warehouse", "branches"];
  }
  if (roles.includes("PROCUREMENT_OFFICER")) {
    return ["attention", "operations", "workflow", "warehouse", "assets", "maintenance", "branches"];
  }
  // Super Admin, Biomedical Admin/Engineer and every other role: organisation overview.
  return ["attention", "operations", "assets", "workflow", "maintenance", "warehouse", "branches"];
}

function roleHeadline(roles: string[], scope: "BRANCH" | "ORGANIZATION"): string {
  if (roles.includes("INVENTORY_MANAGER")) return "Stock and fulfillment that needs you";
  if (roles.includes("UNIT_HEAD")) return "Your branch at a glance";
  if (roles.includes("TL")) return "Branch device operations";
  if (roles.includes("PROCUREMENT_OFFICER")) return "Procurement and supply overview";
  return scope === "BRANCH" ? "Branch operations" : "Organisation operations";
}

function toSlices(
  counts: Record<string, number> | undefined,
  toneMap: Record<string, Tone>,
  order?: string[]
): Slice[] {
  if (!counts) return [];
  const keys = order ? order.filter((k) => k in counts) : Object.keys(counts);
  return keys.map((key) => ({
    key,
    label: humanizeStatus(key),
    value: counts[key] ?? 0,
    tone: toneMap[key] ?? "neutral",
  }));
}

interface Alert {
  key: string;
  label: string;
  count: number;
  tone: "danger" | "warning";
  href: string;
}

export default function DashboardPage() {
  const { user } = useAuth();
  const roles = useMemo(() => user?.roles ?? [], [user]);

  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  /** The active branch cross-filter. Only ever set by an organisation-scope user —
   * the server pins branch-scoped roles regardless of what is sent. */
  const [branchFilter, setBranchFilter] = useState<string | null>(null);

  // Replaces the old fixed "financial year" incidents tile — Overall by default,
  // or a specific calendar year / year+month the user picks.
  const now = useMemo(() => new Date(), []);
  const [periodMode, setPeriodMode] = useState<"overall" | "year" | "month">("overall");
  const [periodYear, setPeriodYear] = useState<number>(now.getFullYear());
  const [periodMonth, setPeriodMonth] = useState<number>(now.getMonth() + 1);

  const load = useCallback(
    async (branchId: string | null, mode: "overall" | "year" | "month", year: number, month: number) => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams();
        if (branchId) params.set("branch_id", branchId);
        params.set("period", mode);
        if (mode !== "overall") params.set("period_year", String(year));
        if (mode === "month") params.set("period_month", String(month));
        const query = params.toString();
        const result = await api.get<DashboardSummary>(`/api/v1/dashboard/summary?${query}`);
        setSummary(result);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not load the dashboard.");
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    load(branchFilter, periodMode, periodYear, periodMonth);
  }, [load, branchFilter, periodMode, periodYear, periodMonth]);

  const scopeBranchId = summary?.branch_id ?? null;
  /** Appended to drill-down links so a filtered page opens in the same context the
   * dashboard was showing. Omitted when organisation-wide. */
  const branchQ = scopeBranchId ? `branch_id=${encodeURIComponent(scopeBranchId)}` : "";
  const withBranch = (base: string) => {
    if (!branchQ) return base;
    return base.includes("?") ? `${base}&${branchQ}` : `${base}?${branchQ}`;
  };

  const alerts = useMemo<Alert[]>(() => {
    if (!summary) return [];
    const items: Alert[] = [];
    const { incidents, maintenance, assets, requirements, fulfillments, review } = summary;

    if (incidents && incidents.critical_open > 0) {
      items.push({
        key: "critical",
        label: "Critical incidents open",
        count: incidents.critical_open,
        tone: "danger",
        href: withBranch("/incidents?severity=CRITICAL"),
      });
    }
    if (maintenance?.pm && maintenance.pm.overdue > 0) {
      items.push({
        key: "pm-overdue",
        label: "Preventive maintenance overdue",
        count: maintenance.pm.overdue,
        tone: "danger",
        href: withBranch("/maintenance?due_status=OVERDUE"),
      });
    }
    if (maintenance?.calibration && maintenance.calibration.overdue > 0) {
      items.push({
        key: "cal-overdue",
        label: "Calibration overdue",
        count: maintenance.calibration.overdue,
        tone: "danger",
        // The calibration page reads due_status only — no branch_id param exists there.
        href: "/calibration?due_status=OVERDUE",
      });
    }
    if (assets) {
      const expired = assets.amc_expired + assets.cmc_expired + assets.warranty_expired;
      if (expired > 0) {
        items.push({
          key: "coverage",
          label: "Contracts expired (AMC / CMC / warranty)",
          count: expired,
          tone: "warning",
          href: "/contracts?expiry_status=EXPIRED",
        });
      }
    }
    if (requirements && requirements.pending_approval > 0) {
      items.push({
        key: "approvals",
        label: "Requirements awaiting approval",
        count: requirements.pending_approval,
        tone: "warning",
        href: withBranch("/requirements?status_filter=PENDING_MANAGER_APPROVAL"),
      });
    }
    if (fulfillments && fulfillments.pending_allocation > 0) {
      items.push({
        key: "allocation",
        label: "Fulfillments awaiting allocation",
        count: fulfillments.pending_allocation,
        tone: "warning",
        href: withBranch("/fulfillments?status_filter=PENDING_ALLOCATION"),
      });
    }
    if (review && review.open > 0) {
      items.push({
        key: "review",
        label: "Review queue items open",
        count: review.open,
        tone: "warning",
        href: "/review-queue",
      });
    }
    return items.sort((a, b) => (a.tone === b.tone ? b.count - a.count : a.tone === "danger" ? -1 : 1));
  }, [summary, branchQ]); // eslint-disable-line react-hooks/exhaustive-deps

  const activeBranchName = useMemo(() => {
    if (!summary?.branch_id) return null;
    return summary.branches?.find((b) => b.id === summary.branch_id)?.name ?? null;
  }, [summary]);

  // ---- Render ----

  if (error && !summary) {
    return (
      <ProtectedShell>
        <div className="page-header">
          <h1>Operations</h1>
        </div>
        <ErrorState message={error} />
        <button type="button" className="btn btn--primary" onClick={() => load(branchFilter, periodMode, periodYear, periodMonth)}>
          Try again
        </button>
      </ProtectedShell>
    );
  }

  const order = sectionOrderFor(roles);
  const caps = summary?.capabilities;

  return (
    <ProtectedShell>
      <div className="dash-header">
        <div>
          <h1>{roleHeadline(roles, summary?.scope ?? "ORGANIZATION")}</h1>
          <p className="dash-header__scope">
            {summary?.scope === "BRANCH" ? (
              <>
                <span className="dash-chip dash-chip--locked">
                  {activeBranchName ?? "Your branch"}
                </span>
                <span>Everything below is limited to your assigned branch.</span>
              </>
            ) : branchFilter ? (
              <>
                <span className="dash-chip">{activeBranchName ?? "Filtered"}</span>
                <button type="button" className="dash-linkbtn" onClick={() => setBranchFilter(null)}>
                  Clear branch filter
                </button>
              </>
            ) : (
              <span>All branches</span>
            )}
          </p>
        </div>
        <div className="dash-header__actions">
          <button
            type="button"
            className="btn"
            onClick={() => load(branchFilter, periodMode, periodYear, periodMonth)}
            disabled={loading}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          <Link href="/calendar" className="btn btn--primary">
            Calendar
          </Link>
        </div>
      </div>

      {error && summary && (
        <div className="dash-inline-error">
          <ErrorState message={error} />
        </div>
      )}

      {!loading && summary && summary.sections.length === 0 && (
        <p className="dash-empty dash-empty--page">
          Your role doesn&apos;t include any dashboard data yet. Use the menu to reach the areas you
          have access to.
        </p>
      )}

      <div className="dash-grid">
        {order.map((section) => {
          switch (section) {
            // ---- What needs attention, first for every role ----
            case "attention":
              if (!summary && loading) {
                return (
                  <Panel key="attention" title="Needs attention" span="full">
                    <div className="dash-kpi-row">
                      <KpiSkeleton count={4} />
                    </div>
                  </Panel>
                );
              }
              if (!summary || alerts.length === 0) {
                return (
                  <Panel key="attention" title="Needs attention" span="full">
                    <p className="dash-empty">
                      Nothing overdue, critical, or awaiting approval right now.
                    </p>
                  </Panel>
                );
              }
              return (
                <Panel
                  key="attention"
                  title="Needs attention"
                  hint="Most urgent first. Each row opens the matching filtered list."
                  span="full"
                >
                  <div className="dash-alerts">
                    {alerts.map((alert) => (
                      <Link key={alert.key} href={alert.href} className={`dash-alert dash-alert--${alert.tone}`}>
                        <span className="dash-alert__count">{alert.count}</span>
                        <span className="dash-alert__label">{alert.label}</span>
                        <span className="dash-alert__go" aria-hidden="true">→</span>
                      </Link>
                    ))}
                  </div>
                </Panel>
              );

            // ---- The role's headline numbers ----
            case "operations": {
              if (!summary) {
                return (
                  <Panel key="operations" title="Key numbers" span="full">
                    <div className="dash-kpi-row">
                      <KpiSkeleton count={4} />
                    </div>
                  </Panel>
                );
              }
              const cards = buildKpis(summary, roles, withBranch);
              if (cards.length === 0) return null;
              return (
                <Panel
                  key="operations"
                  title="Key numbers"
                  span="full"
                  action={
                    summary.incidents ? (
                      <div className="dash-period-filter">
                        <select
                          className="dash-period-filter__select"
                          value={periodMode}
                          onChange={(e) => setPeriodMode(e.target.value as "overall" | "year" | "month")}
                          aria-label="Incident period"
                        >
                          <option value="overall">Overall</option>
                          <option value="year">By year</option>
                          <option value="month">By month</option>
                        </select>
                        {periodMode !== "overall" && (
                          <select
                            className="dash-period-filter__select"
                            value={periodYear}
                            onChange={(e) => setPeriodYear(Number(e.target.value))}
                            aria-label="Year"
                          >
                            {Array.from({ length: 6 }, (_, i) => now.getFullYear() - i).map((y) => (
                              <option key={y} value={y}>
                                {y}
                              </option>
                            ))}
                          </select>
                        )}
                        {periodMode === "month" && (
                          <select
                            className="dash-period-filter__select"
                            value={periodMonth}
                            onChange={(e) => setPeriodMonth(Number(e.target.value))}
                            aria-label="Month"
                          >
                            {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                              <option key={m} value={m}>
                                {new Date(2000, m - 1, 1).toLocaleString("en-GB", { month: "long" })}
                              </option>
                            ))}
                          </select>
                        )}
                      </div>
                    ) : undefined
                  }
                >
                  <div className="dash-kpi-row">{cards}</div>
                </Panel>
              );
            }

            // ---- Device fleet ----
            case "assets": {
              if (!summary?.assets || !caps?.asset) return null;
              const slices = toSlices(summary.assets.by_status, ASSET_STATUS_TONE);
              return (
                <Panel
                  key="assets"
                  title="Device fleet"
                  hint="Physical assets by lifecycle status. Select a status to open that list."
                >
                  {summary.assets.total === 0 ? (
                    <p className="dash-empty">No devices recorded for this scope yet.</p>
                  ) : (
                    <DonutChart
                      slices={slices}
                      total={summary.assets.total}
                      centerLabel="devices"
                      onSelect={(key) => {
                        window.location.href = withBranch(`/assets?status_filter=${encodeURIComponent(key)}`);
                      }}
                    />
                  )}
                </Panel>
              );
            }

            // ---- Requirement -> fulfillment workflow, plus incidents ----
            case "workflow": {
              if (!summary) return null;
              const panels = [];

              if (summary.incidents && caps?.incident) {
                const severitySlices = toSlices(summary.incidents.by_severity, SEVERITY_TONE, [
                  "CRITICAL",
                  "HIGH",
                  "MEDIUM",
                  "LOW",
                ]);
                // Status breakdown, open statuses only — the closed/cancelled tail would
                // dominate the chart without being actionable. Each bar drills into
                // /incidents?status_filter=<status>, which is an exact backend filter
                // (unlike "open", which spans nine statuses and has no single param).
                const statusSlices = toSlices(
                  summary.incidents.by_status,
                  INCIDENT_STATUS_TONE,
                  summary.incidents.open_statuses
                ).filter((s) => s.value > 0);

                panels.push(
                  <Panel
                    key="incidents"
                    title="Incidents"
                    hint={`${summary.incidents.open} of ${summary.incidents.total} still open — resolved, closed and cancelled are excluded.`}
                  >
                    {summary.incidents.total === 0 ? (
                      <p className="dash-empty">No incidents reported for this scope.</p>
                    ) : (
                      <>
                        <h3 className="dash-subhead">By severity</h3>
                        <BarChart
                          slices={severitySlices}
                          valueLabel="Incidents by severity"
                          onSelect={(key) => {
                            window.location.href = withBranch(`/incidents?severity=${encodeURIComponent(key)}`);
                          }}
                        />
                        {statusSlices.length > 0 && (
                          <>
                            <h3 className="dash-subhead">Open by status</h3>
                            <BarChart
                              slices={statusSlices}
                              valueLabel="Open incidents by status"
                              onSelect={(key) => {
                                window.location.href = withBranch(
                                  `/incidents?status_filter=${encodeURIComponent(key)}`
                                );
                              }}
                            />
                          </>
                        )}
                      </>
                    )}
                  </Panel>
                );
              }

              if (summary.requirements && caps?.requirement) {
                panels.push(
                  <Panel
                    key="requirements"
                    title="Requirements"
                    hint="Draft through approval. Select a stage to open it."
                  >
                    {summary.requirements.total === 0 ? (
                      <p className="dash-empty">No requirements raised for this scope yet.</p>
                    ) : (
                      <PipelineChart
                        stages={toSlices(
                          summary.requirements.by_status,
                          REQUIREMENT_STATUS_TONE,
                          REQUIREMENT_FLOW
                        )}
                        onSelect={(key) => {
                          window.location.href = withBranch(
                            `/requirements?status_filter=${encodeURIComponent(key)}`
                          );
                        }}
                      />
                    )}
                  </Panel>
                );
              }

              if (summary.fulfillments && caps?.fulfillment) {
                panels.push(
                  <Panel
                    key="fulfillments"
                    title="Fulfillment pipeline"
                    hint="Allocation through delivery. Select a stage to open it."
                    span="full"
                  >
                    {summary.fulfillments.total === 0 ? (
                      <p className="dash-empty">Nothing in the fulfillment pipeline for this scope.</p>
                    ) : (
                      <PipelineChart
                        stages={toSlices(
                          summary.fulfillments.by_status,
                          FULFILLMENT_STATUS_TONE,
                          FULFILLMENT_FLOW
                        )}
                        onSelect={(key) => {
                          window.location.href = withBranch(
                            `/fulfillments?status_filter=${encodeURIComponent(key)}`
                          );
                        }}
                      />
                    )}
                  </Panel>
                );
              }
              return panels.length ? <Fragment key="workflow">{panels}</Fragment> : null;
            }

            // ---- Central warehouse ----
            case "warehouse": {
              if (!summary?.central_device_stock || !caps?.inventory) return null;
              const stock = summary.central_device_stock;
              return (
                <Panel
                  key="warehouse"
                  title="Central warehouse devices"
                  hint="Physical assets available to allocate, by model. Condemned and retired are excluded."
                  span="full"
                >
                  {stock.length === 0 ? (
                    <p className="dash-empty">
                      No devices are currently available at the central warehouse.
                    </p>
                  ) : (
                    <div className="dash-scroller">
                      <table className="data-table dash-table">
                        <thead>
                          <tr>
                            <th scope="col">Device model</th>
                            <th scope="col">Manufacturer</th>
                            <th scope="col">Category</th>
                            <th scope="col" className="dash-num">Available</th>
                          </tr>
                        </thead>
                        <tbody>
                          {stock.map((row) => (
                            <tr key={row.device_model_id}>
                              <td>{row.name}</td>
                              <td>{row.manufacturer ?? "—"}</td>
                              <td>{row.category ?? "—"}</td>
                              <td className="dash-num">
                                <strong>{row.available_count}</strong>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </Panel>
              );
            }

            // ---- Maintenance ----
            case "maintenance": {
              if (!summary?.maintenance) return null;
              const m = summary.maintenance;
              if (!m.pm && !m.calibration) return null;
              return (
                <Panel key="maintenance" title="Maintenance due" hint="Scheduled work coming up or already late.">
                  <div className="dash-kpi-row">
                    {m.pm && (
                      <>
                        <KpiCard
                          label="PM due soon"
                          value={m.pm.due}
                          tone={m.pm.due > 0 ? "warning" : "success"}
                          state={m.pm.due > 0 ? "Scheduled" : "Clear"}
                          href={withBranch("/maintenance?due_status=DUE")}
                        />
                        <KpiCard
                          label="PM overdue"
                          value={m.pm.overdue}
                          tone={m.pm.overdue > 0 ? "danger" : "success"}
                          state={m.pm.overdue > 0 ? "Late" : "Clear"}
                          href={withBranch("/maintenance?due_status=OVERDUE")}
                        />
                      </>
                    )}
                    {m.calibration && (
                      <>
                        <KpiCard
                          label="Calibration due soon"
                          value={m.calibration.due}
                          tone={m.calibration.due > 0 ? "warning" : "success"}
                          state={m.calibration.due > 0 ? "Scheduled" : "Clear"}
                          href="/calibration?due_status=DUE"
                        />
                        <KpiCard
                          label="Calibration overdue"
                          value={m.calibration.overdue}
                          tone={m.calibration.overdue > 0 ? "danger" : "success"}
                          state={m.calibration.overdue > 0 ? "Late" : "Clear"}
                          href="/calibration?due_status=OVERDUE"
                        />
                      </>
                    )}
                  </div>
                </Panel>
              );
            }

            // ---- Branch comparison / cross-filter ----
            case "branches": {
              if (!summary?.branches || summary.branches.length === 0) return null;
              // A branch-scoped role gets one row; presenting that as a "comparison"
              // would be nonsense, so it is framed as branch context instead.
              const isSingle = summary.scope === "BRANCH";
              const rows = summary.branches;
              return (
                <Panel
                  key="branches"
                  title={isSingle ? "Your branch" : "Branches"}
                  hint={
                    isSingle
                      ? undefined
                      : "Select a branch to filter this dashboard to it."
                  }
                  span="full"
                  action={
                    branchFilter && !isSingle ? (
                      <button type="button" className="dash-linkbtn" onClick={() => setBranchFilter(null)}>
                        Show all branches
                      </button>
                    ) : undefined
                  }
                >
                  {!isSingle && (
                    <BarChart
                      slices={rows.map((row) => ({
                        key: row.id,
                        label: row.name,
                        value: row.devices,
                        tone: row.is_central_warehouse ? "info" : "neutral",
                      }))}
                      selectedKey={branchFilter}
                      valueLabel="Devices by branch"
                      onSelect={(key) => setBranchFilter(key === branchFilter ? null : key)}
                    />
                  )}
                  <div className="dash-scroller">
                    <table className="data-table dash-table">
                      <thead>
                        <tr>
                          <th scope="col">Branch</th>
                          <th scope="col" className="dash-num">Devices</th>
                          <th scope="col" className="dash-num">Operational</th>
                          {caps?.incident && <th scope="col" className="dash-num">Open incidents</th>}
                          {caps?.requirement && <th scope="col" className="dash-num">Pending requirements</th>}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row) => (
                          <tr key={row.id}>
                            <td>
                              <Link href={`/branches/${row.id}`}>{row.name}</Link>
                              {row.is_central_warehouse && (
                                <span className="dash-tag">Central warehouse</span>
                              )}
                            </td>
                            <td className="dash-num">{row.devices}</td>
                            <td className="dash-num">{row.operational}</td>
                            {caps?.incident && <td className="dash-num">{row.open_incidents ?? "—"}</td>}
                            {caps?.requirement && (
                              <td className="dash-num">{row.pending_requirements ?? "—"}</td>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Panel>
              );
            }

            default:
              return null;
          }
        })}
      </div>
    </ProtectedShell>
  );
}

/** The role's headline figures. Each card links only where a filtered view genuinely
 * exists — no card pretends to be clickable. */
function buildKpis(
  summary: DashboardSummary,
  roles: string[],
  withBranch: (base: string) => string
) {
  const cards = [];
  const inventoryFocused = roles.includes("INVENTORY_MANAGER");

  if (inventoryFocused && summary.fulfillments) {
    cards.push(
      <KpiCard
        key="alloc"
        label="Awaiting allocation"
        value={summary.fulfillments.pending_allocation}
        tone={summary.fulfillments.pending_allocation > 0 ? "warning" : "success"}
        state="Fulfillment"
        emphasis="lead"
        href={withBranch("/fulfillments?status_filter=PENDING_ALLOCATION")}
      />,
      <KpiCard
        key="transit"
        label="Out for delivery"
        value={summary.fulfillments.out_for_delivery}
        tone="info"
        state="In transit"
        emphasis="lead"
        href={withBranch("/fulfillments?status_filter=OUT_FOR_DELIVERY")}
      />
    );
  }

  if (summary.assets) {
    cards.push(
      <KpiCard
        key="devices"
        label="Devices"
        value={summary.assets.total}
        tone="neutral"
        state={`${summary.assets.operational} operational`}
        emphasis={inventoryFocused ? undefined : "lead"}
        href={withBranch("/assets")}
      />
    );
  }
  if (summary.incidents) {
    const byStatus = summary.incidents.by_status;
    cards.push(
      <KpiCard
        key="incidents-open"
        label="Open incidents"
        value={byStatus.OPEN ?? 0}
        tone={(byStatus.OPEN ?? 0) > 0 ? "danger" : "success"}
        state={
          summary.incidents.critical_open > 0
            ? `${summary.incidents.critical_open} critical`
            : "None critical"
        }
        emphasis={inventoryFocused ? undefined : "lead"}
        href={withBranch("/incidents?status_filter=OPEN")}
      />,
      <KpiCard
        key="incidents-wip"
        label="Work in progress"
        value={byStatus.WORK_IN_PROGRESS ?? 0}
        tone="warning"
        state="Incidents"
        href={withBranch("/incidents?status_filter=WORK_IN_PROGRESS")}
      />,
      <KpiCard
        key="incidents-operational"
        label="Operational"
        value={byStatus.OPERATIONAL ?? 0}
        tone="success"
        state="Resolved incidents"
        href={withBranch("/incidents?status_filter=OPERATIONAL")}
      />
    );
  }
  if (summary.incidents) {
    cards.push(
      <KpiCard
        key="condemned"
        label="Total condemned"
        value={summary.incidents.to_be_condemned}
        tone={summary.incidents.to_be_condemned > 0 ? "danger" : "success"}
        state="To be condemned"
        href={withBranch("/incidents?status_filter=TO_BE_CONDEMNED")}
      />
    );
  }
  if (summary.incidents) {
    const periodState =
      summary.incidents.period === "overall"
        ? "All time"
        : summary.incidents.period === "year"
          ? String(summary.incidents.period_year ?? "")
          : `${new Date(2000, (summary.incidents.period_month ?? 1) - 1, 1).toLocaleString("en-GB", {
              month: "short",
            })} ${summary.incidents.period_year ?? ""}`;
    cards.push(
      <KpiCard
        key="incidents-period"
        label="Incidents reported"
        value={summary.incidents.period_total}
        tone="info"
        state={periodState}
        href={withBranch("/incidents")}
      />
    );
  }
  if (summary.incidents) {
    const fmtInr = (n: number) =>
      `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
    cards.push(
      <KpiCard
        key="po-spend-incl"
        label="Total PO spending (incl. GST)"
        value={fmtInr(summary.incidents.po_spending_incl_gst)}
        tone="neutral"
        state="All incidents"
        href={withBranch("/incidents")}
      />,
      <KpiCard
        key="po-spend-excl"
        label="Total PO spending (excl. GST)"
        value={fmtInr(summary.incidents.po_spending_excl_gst)}
        tone="neutral"
        state="All incidents"
        href={withBranch("/incidents")}
      />
    );
  }
  if (summary.requirements) {
    cards.push(
      <KpiCard
        key="reqs"
        label="Awaiting approval"
        value={summary.requirements.pending_approval}
        tone={summary.requirements.pending_approval > 0 ? "warning" : "success"}
        state="Requirements"
        href={withBranch("/requirements?status_filter=PENDING_MANAGER_APPROVAL")}
      />
    );
  }
  if (summary.review) {
    cards.push(
      <KpiCard
        key="review"
        label="Review queue"
        value={summary.review.open}
        tone={summary.review.open > 0 ? "warning" : "success"}
        state="Open items"
        href="/review-queue"
      />
    );
  }
  return cards;
}
