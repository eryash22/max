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
import type { Asset, Branch, CalibrationRecord, CalibrationSchedule, DeviceModel, Page } from "../../types";
import { CALIBRATION_DUE_STATUSES, CALIBRATION_FREQUENCIES, CALIBRATION_PASS_FAIL } from "../../types";

const PAGE_SIZE = 25;
const RECORD_PAGE_SIZE = 10;

interface DueCounts {
  total: number;
  overdue: number;
  due: number;
  upcoming: number;
  active: number;
}

async function totalOf(path: string): Promise<number> {
  const result = await api.get<Page<unknown>>(`${path}${path.includes("?") ? "&" : "?"}page=1&page_size=1`);
  return result.total;
}

export default function CalibrationPage() {
  const { hasPermission } = useAuth();

  const [counts, setCounts] = useState<DueCounts | null>(null);
  const [countsError, setCountsError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [dueStatus, setDueStatus] = useState("");
  const [assetId, setAssetId] = useState("");
  const [branchFilter, setBranchFilter] = useState("");
  const [frequency, setFrequency] = useState("");
  const [passFail, setPassFail] = useState("");
  const [performedAfter, setPerformedAfter] = useState("");
  const [performedBefore, setPerformedBefore] = useState("");
  const [page, setPage] = useState(1);
  const [recordPage, setRecordPage] = useState(1);

  // Hydrates filters from the URL so dashboard/calendar drill-down links (e.g.
  // /calibration?due_status=OVERDUE) land pre-filtered.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const ds = params.get("due_status");
    if (ds) setDueStatus(ds);
  }, []);

  const [schedules, setSchedules] = useState<CalibrationSchedule[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [records, setRecords] = useState<CalibrationRecord[]>([]);
  const [recordsTotal, setRecordsTotal] = useState(0);
  const [recordsLoading, setRecordsLoading] = useState(true);
  const [recordsError, setRecordsError] = useState<string | null>(null);

  const assets = useLookup<Asset>("/api/v1/assets");
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const branches = useLookup<Branch>("/api/v1/branches");

  const assetById = useMemo(() => new Map(assets.map((a) => [a.id, a])), [assets]);
  const modelName = (id: string) => models.find((m) => m.id === id)?.name ?? id;

  useEffect(() => {
    let cancelled = false;
    async function loadCounts() {
      try {
        const [total, overdue, due, upcoming, active] = await Promise.all([
          totalOf("/api/v1/calibration/schedules"),
          totalOf("/api/v1/calibration/schedules?due_status=OVERDUE"),
          totalOf("/api/v1/calibration/schedules?due_status=DUE"),
          totalOf("/api/v1/calibration/schedules?due_status=UPCOMING"),
          totalOf("/api/v1/calibration/schedules?is_active=true"),
        ]);
        if (!cancelled) setCounts({ total, overdue, due, upcoming, active });
      } catch (err) {
        if (!cancelled) setCountsError(err instanceof ApiError ? err.message : "Failed to load calibration summary.");
      }
    }
    loadCounts();
    return () => {
      cancelled = true;
    };
  }, []);

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
        const result = await api.get<Page<CalibrationSchedule>>(`/api/v1/calibration/schedules?${params.toString()}`);
        if (!cancelled) setSchedules(result.items);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load calibration schedules.");
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
    let cancelled = false;
    async function loadRecords() {
      setRecordsLoading(true);
      setRecordsError(null);
      try {
        const params = new URLSearchParams({ page: String(recordPage), page_size: String(RECORD_PAGE_SIZE) });
        if (assetId) params.set("asset_id", assetId);
        if (passFail) params.set("pass_fail", passFail);
        if (performedAfter) params.set("performed_after", performedAfter);
        if (performedBefore) params.set("performed_before", performedBefore);
        const result = await api.get<Page<CalibrationRecord>>(`/api/v1/calibration/records?${params.toString()}`);
        if (!cancelled) {
          setRecords(result.items);
          setRecordsTotal(result.total);
        }
      } catch (err) {
        if (!cancelled) setRecordsError(err instanceof ApiError ? err.message : "Failed to load calibration records.");
      } finally {
        if (!cancelled) setRecordsLoading(false);
      }
    }
    loadRecords();
    return () => {
      cancelled = true;
    };
  }, [assetId, passFail, performedAfter, performedBefore, recordPage]);

  useEffect(() => {
    setPage(1);
  }, [search, dueStatus, assetId, frequency, branchFilter]);

  useEffect(() => {
    setRecordPage(1);
  }, [assetId, passFail, performedAfter, performedBefore]);

  const filtered = useMemo(() => {
    if (!schedules) return [];
    const term = search.trim().toLowerCase();
    return schedules.filter((s) => {
      const asset = assetById.get(s.asset_id);
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
      if (branchFilter && asset?.branch_id !== branchFilter) return false;
      return true;
    });
  }, [schedules, search, assetById, modelName, branchFilter]);

  const pageItems = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Calibration</h1>
          <p>Calibration schedules, due-date tracking, and calibration history.</p>
        </div>
        {hasPermission("calibration.write") && (
          <Link href="/calibration/new" className="btn btn--primary">
            New calibration schedule
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
          <StatTile label="Active schedules" value={counts.active} />
        </div>
      )}

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search by asset number, serial, or model..." />
        <select value={assetId} onChange={(e) => setAssetId(e.target.value)}>
          <option value="">All assets</option>
          {assets.map((a) => (
            <option key={a.id} value={a.id}>
              {a.asset_number}
            </option>
          ))}
        </select>
        <select value={branchFilter} onChange={(e) => setBranchFilter(e.target.value)}>
          <option value="">All branches</option>
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.is_central_warehouse ? "CENTRAL WAREHOUSE" : b.name}
            </option>
          ))}
        </select>
        <select value={frequency} onChange={(e) => setFrequency(e.target.value)}>
          <option value="">All frequencies</option>
          {CALIBRATION_FREQUENCIES.map((f) => (
            <option key={f} value={f}>
              {f.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <select value={dueStatus} onChange={(e) => setDueStatus(e.target.value)}>
          <option value="">All statuses</option>
          {CALIBRATION_DUE_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <select value={passFail} onChange={(e) => setPassFail(e.target.value)}>
          <option value="">All results</option>
          {CALIBRATION_PASS_FAIL.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <input
          type="date"
          value={performedAfter}
          onChange={(e) => setPerformedAfter(e.target.value)}
          title="Performed after"
        />
        <input
          type="date"
          value={performedBefore}
          onChange={(e) => setPerformedBefore(e.target.value)}
          title="Performed before"
        />
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {!loading && !error && (
        <>
          <DataTable<CalibrationSchedule>
            columns={[
              {
                key: "asset",
                header: "Asset",
                render: (s) => (
                  <Link href={`/calibration/${s.id}`}>
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
            emptyLabel="No calibration schedules found."
          />
          <Pagination page={page} pageSize={PAGE_SIZE} total={filtered.length} onPageChange={setPage} />
        </>
      )}

      <div className="card" style={{ marginTop: 24 }}>
        <h3 style={{ marginTop: 0 }}>Recent calibration records</h3>
        {recordsError && <ErrorState message={recordsError} />}
        {recordsLoading && <LoadingState />}
        {!recordsLoading && !recordsError && (
          <>
            <DataTable<CalibrationRecord>
              columns={[
                {
                  key: "asset",
                  header: "Asset",
                  render: (r) => assetById.get(r.asset_id)?.asset_number ?? r.asset_id,
                },
                { key: "performed_date", header: "Performed", render: (r) => r.performed_date ?? "-" },
                { key: "pass_fail", header: "Result", render: (r) => (r.pass_fail ? <StatusBadge value={r.pass_fail} /> : "-") },
                { key: "agency", header: "Calibration agency", render: (r) => r.calibration_agency ?? "-" },
                { key: "certificate_number", header: "Certificate #", render: (r) => r.certificate_number ?? "-" },
                { key: "next_due_date", header: "Next due", render: (r) => r.next_due_date ?? "-" },
              ]}
              rows={records}
              emptyLabel="No calibration records found."
            />
            <Pagination page={recordPage} pageSize={RECORD_PAGE_SIZE} total={recordsTotal} onPageChange={setRecordPage} />
          </>
        )}
      </div>
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
