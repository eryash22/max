"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { SearchBar } from "../../components/SearchBar";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { useLookup } from "../../hooks/useLookup";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import { locationLabel } from "../../lib/locationLabel";
import type { Asset, Branch, Department, DeviceModel, Page, PMRecord, PMSchedule } from "../../types";
import { PM_DUE_STATUSES, PM_FREQUENCIES } from "../../types";

const PAGE_SIZE = 25;

interface DueCounts {
  total: number;
  overdue: number;
  due: number;
  upcoming: number;
  completed: number;
}

async function totalOf(path: string): Promise<number> {
  const result = await api.get<Page<unknown>>(`${path}${path.includes("?") ? "&" : "?"}page=1&page_size=1`);
  return result.total;
}

export default function MaintenancePage() {
  const { hasPermission } = useAuth();

  const [counts, setCounts] = useState<DueCounts | null>(null);
  const [countsError, setCountsError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [dueStatus, setDueStatus] = useState("");
  const [branchId, setBranchId] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [assetId, setAssetId] = useState("");
  const [engineerId, setEngineerId] = useState("");
  const [frequency, setFrequency] = useState("");
  const [page, setPage] = useState(1);

  // Hydrates filters from the URL so dashboard/calendar drill-down links (e.g.
  // /maintenance?due_status=OVERDUE) land pre-filtered.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const ds = params.get("due_status");
    const bi = params.get("branch_id");
    if (ds) setDueStatus(ds);
    if (bi) setBranchId(bi);
  }, []);

  const [schedules, setSchedules] = useState<PMSchedule[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [engineerAssetIds, setEngineerAssetIds] = useState<Set<string> | null>(null);

  const assets = useLookup<Asset>("/api/v1/assets");
  const branches = useLookup<Branch>("/api/v1/branches");
  const departments = useLookup<Department>("/api/v1/departments");
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const branchNameFor = (id: string) => branches.find((b) => b.id === id)?.name ?? id;

  const assetById = useMemo(() => new Map(assets.map((a) => [a.id, a])), [assets]);
  const branchName = (id: string) => branches.find((b) => b.id === id)?.name ?? id;
  const modelName = (id: string) => models.find((m) => m.id === id)?.name ?? id;

  useEffect(() => {
    let cancelled = false;
    async function loadCounts() {
      try {
        const [total, overdue, due, upcoming, completed] = await Promise.all([
          totalOf("/api/v1/pm/schedules"),
          totalOf("/api/v1/pm/schedules?due_status=OVERDUE"),
          totalOf("/api/v1/pm/schedules?due_status=DUE"),
          totalOf("/api/v1/pm/schedules?due_status=UPCOMING"),
          totalOf("/api/v1/pm/records"),
        ]);
        if (!cancelled) setCounts({ total, overdue, due, upcoming, completed });
      } catch (err) {
        if (!cancelled) setCountsError(err instanceof ApiError ? err.message : "Failed to load PM summary.");
      }
    }
    loadCounts();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadEngineerAssets() {
      if (!engineerId) {
        setEngineerAssetIds(null);
        return;
      }
      try {
        const result = await api.get<Page<PMRecord>>(
          `/api/v1/pm/records?engineer_id=${encodeURIComponent(engineerId)}&page_size=200`
        );
        if (!cancelled) setEngineerAssetIds(new Set(result.items.map((r) => r.asset_id)));
      } catch {
        if (!cancelled) setEngineerAssetIds(new Set());
      }
    }
    loadEngineerAssets();
    return () => {
      cancelled = true;
    };
  }, [engineerId]);

  useEffect(() => {
    let cancelled = false;
    async function loadSchedules() {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ page: "1", page_size: "200" });
        if (assetId) params.set("asset_id", assetId);
        if (frequency) params.set("frequency", frequency);
        if (dueStatus) params.set("due_status", dueStatus);
        const result = await api.get<Page<PMSchedule>>(`/api/v1/pm/schedules?${params.toString()}`);
        if (!cancelled) setSchedules(result.items);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load PM schedules.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadSchedules();
    return () => {
      cancelled = true;
    };
  }, [assetId, frequency, dueStatus]);

  useEffect(() => {
    setPage(1);
  }, [search, dueStatus, branchId, departmentId, assetId, engineerId, frequency]);

  const filtered = useMemo(() => {
    if (!schedules) return [];
    const term = search.trim().toLowerCase();
    return schedules.filter((s) => {
      const asset = assetById.get(s.asset_id);
      if (branchId && asset?.branch_id !== branchId) return false;
      if (departmentId && asset?.department_id !== departmentId) return false;
      if (engineerId && engineerAssetIds && !engineerAssetIds.has(s.asset_id)) return false;
      if (term) {
        const haystack = [
          asset?.asset_number,
          asset?.serial_number,
          asset ? modelName(asset.device_model_id) : "",
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(term)) return false;
      }
      return true;
    });
  }, [schedules, search, branchId, departmentId, engineerId, engineerAssetIds, assetById, modelName]);

  const pageItems = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Preventive maintenance</h1>
          <p>Scheduled servicing for assets, due-date tracking, and completion history.</p>
        </div>
        {hasPermission("pm.write") && (
          <Link href="/maintenance/new" className="btn btn--primary">
            New PM schedule
          </Link>
        )}
      </div>

      {countsError && <ErrorState message={countsError} />}
      {counts && (
        <div className="stat-grid" style={{ marginBottom: 16 }}>
          <StatTile label="Total schedules" value={counts.total} />
          <StatTile label="Due" value={counts.due} />
          <StatTile label="Upcoming" value={counts.upcoming} />
          <StatTile label="Overdue" value={counts.overdue} />
          <StatTile label="Completed PMs" value={counts.completed} />
        </div>
      )}

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search by asset number, serial, or model..." />
        <select value={dueStatus} onChange={(e) => setDueStatus(e.target.value)}>
          <option value="">All statuses</option>
          {PM_DUE_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <select value={branchId} onChange={(e) => setBranchId(e.target.value)}>
          <option value="">All branches</option>
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
        <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
          <option value="">All departments</option>
          {departments.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} — {branchNameFor(d.branch_id)}
            </option>
          ))}
        </select>
        <select value={assetId} onChange={(e) => setAssetId(e.target.value)}>
          <option value="">All assets</option>
          {assets.map((a) => (
            <option key={a.id} value={a.id}>
              {a.asset_number}
            </option>
          ))}
        </select>
        <select value={frequency} onChange={(e) => setFrequency(e.target.value)}>
          <option value="">All frequencies</option>
          {PM_FREQUENCIES.map((f) => (
            <option key={f} value={f}>
              {f.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <input
          value={engineerId}
          onChange={(e) => setEngineerId(e.target.value)}
          placeholder="Engineer user ID..."
          style={{ minWidth: 160 }}
        />
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {!loading && !error && (
        <>
          <DataTable<PMSchedule>
            columns={[
              {
                key: "asset",
                header: "Asset",
                render: (s) => (
                  <Link href={`/maintenance/${s.id}`}>
                    {assetById.get(s.asset_id)?.asset_number ?? s.asset_id}
                  </Link>
                ),
              },
              {
                key: "model",
                header: "Device model",
                render: (s) => {
                  const asset = assetById.get(s.asset_id);
                  return asset ? modelName(asset.device_model_id) : "-";
                },
              },
              {
                key: "branch",
                header: "Branch / Warehouse",
                render: (s) => locationLabel(assetById.get(s.asset_id)?.branch_id, branches),
              },
              { key: "frequency", header: "Frequency", render: (s) => s.frequency?.replace(/_/g, " ") ?? "-" },
              { key: "next_due_date", header: "Next due", render: (s) => s.next_due_date ?? "Not scheduled" },
              { key: "due_status", header: "Status", render: (s) => <StatusBadge value={s.due_status} /> },
              { key: "active", header: "Active", render: (s) => (s.is_active ? "Yes" : "No") },
            ]}
            rows={pageItems}
            emptyLabel="No PM schedules found."
          />
          <Pagination page={page} pageSize={PAGE_SIZE} total={filtered.length} onPageChange={setPage} />
        </>
      )}
    </ProtectedShell>
  );
}

function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat-tile">
      <div className="stat-tile__label">{label}</div>
      <div className="stat-tile__value">{value}</div>
    </div>
  );
}
