"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { SearchBar } from "../../components/SearchBar";
import { StatusBadge } from "../../components/StatusBadge";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type { EscalationPolicy, Page } from "../../types";
import { ESCALATION_TRIGGER_TYPES, INCIDENT_SEVERITIES } from "../../types";

const PAGE_SIZE = 25;

export default function EscalationPoliciesPage() {
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("incident.manage");

  const [search, setSearch] = useState("");
  const [triggerType, setTriggerType] = useState("");
  const [page, setPage] = useState(1);

  const [policies, setPolicies] = useState<EscalationPolicy[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (search) params.set("search", search);
      if (triggerType) params.set("trigger_type", triggerType);
      const result = await api.get<Page<EscalationPolicy>>(`/api/v1/escalation/policies?${params.toString()}`);
      setPolicies(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load escalation policies.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, triggerType, page]);

  useEffect(() => {
    setPage(1);
  }, [search, triggerType]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post("/api/v1/escalation/policies", {
        name: formData.get("name"),
        trigger_type: formData.get("trigger_type"),
        severity_threshold: formData.get("severity_threshold") || null,
        device_criticality_threshold: formData.get("device_criticality_threshold") || null,
      });
      setShowForm(false);
      form.reset();
      setPage(1);
      await load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create escalation policy.");
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Escalation policies</h1>
          <p>Configurable escalation chains — who gets notified, in what order, when an incident meets a trigger.</p>
        </div>
        {canWrite && (
          <button className="btn btn--primary" onClick={() => setShowForm((v) => !v)}>
            {showForm ? "Cancel" : "New policy"}
          </button>
        )}
      </div>

      {showForm && (
        <form className="card" style={{ marginBottom: 16 }} onSubmit={handleCreate}>
          <div className="form-grid">
            <div className="form-field">
              <label>Policy name*</label>
              <input name="name" required />
            </div>
            <div className="form-field">
              <label>Trigger type*</label>
              <select name="trigger_type" required defaultValue="">
                <option value="" disabled>
                  Select trigger type
                </option>
                {ESCALATION_TRIGGER_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Severity threshold</label>
              <select name="severity_threshold" defaultValue="">
                <option value="">Not set</option>
                {INCIDENT_SEVERITIES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Device criticality threshold</label>
              <input name="device_criticality_threshold" placeholder="Optional" />
            </div>
          </div>
          {formError && <div className="field-error">{formError}</div>}
          <button className="btn btn--primary" type="submit">
            Save policy
          </button>
        </form>
      )}

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search by policy name..." />
        <select value={triggerType} onChange={(e) => setTriggerType(e.target.value)}>
          <option value="">All trigger types</option>
          {ESCALATION_TRIGGER_TYPES.map((t) => (
            <option key={t} value={t}>
              {t.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {!loading && !error && (
        <>
          <DataTable<EscalationPolicy>
            columns={[
              {
                key: "name",
                header: "Policy name",
                render: (p) => <Link href={`/escalation/${p.id}`}>{p.name}</Link>,
              },
              { key: "trigger_type", header: "Trigger type", render: (p) => p.trigger_type.replace(/_/g, " ") },
              { key: "severity_threshold", header: "Severity threshold", render: (p) => p.severity_threshold ?? "-" },
              {
                key: "device_criticality_threshold",
                header: "Device criticality threshold",
                render: (p) => p.device_criticality_threshold ?? "-",
              },
              {
                key: "is_active",
                header: "Status",
                render: (p) => <StatusBadge value={p.is_active ? "ACTIVE" : "INACTIVE"} />,
              },
            ]}
            rows={policies}
            emptyLabel="No escalation policies configured yet."
          />
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
        </>
      )}
    </ProtectedShell>
  );
}
