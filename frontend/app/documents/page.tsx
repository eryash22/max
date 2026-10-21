"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { DataTable } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { SearchBar } from "../../components/SearchBar";
import { StatusBadge } from "../../components/StatusBadge";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { useAuth } from "../../hooks/useAuth";
import { api, ApiError } from "../../lib/api";
import type { Document, Page } from "../../types";
import { DOCUMENT_ENTITY_TYPES, DOCUMENT_TYPES } from "../../types";

const PAGE_SIZE = 25;

export default function DocumentsPage() {
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("document.write");
  const canForceDelete = hasPermission("dev.data_cleanup");

  const [search, setSearch] = useState("");
  const [documentType, setDocumentType] = useState("");
  const [entityType, setEntityType] = useState("");
  const [entityId, setEntityId] = useState("");
  const [expiryAfter, setExpiryAfter] = useState("");
  const [expiryBefore, setExpiryBefore] = useState("");
  const [page, setPage] = useState(1);

  const [items, setItems] = useState<Document[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<Document | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deleteConflict, setDeleteConflict] = useState<string | null>(null);

  useEffect(() => {
    setPage(1);
  }, [search, documentType, entityType, entityId, expiryAfter, expiryBefore]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
        if (search) params.set("search", search);
        if (documentType) params.set("document_type", documentType);
        if (entityType && entityId) {
          params.set("entity_type", entityType);
          params.set("entity_id", entityId);
        }
        if (expiryAfter) params.set("expiry_after", expiryAfter);
        if (expiryBefore) params.set("expiry_before", expiryBefore);
        const result = await api.get<Page<Document>>(`/api/v1/documents?${params.toString()}`);
        if (!cancelled) {
          setItems(result.items);
          setTotal(result.total);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load documents.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [page, search, documentType, entityType, entityId, expiryAfter, expiryBefore]);

  async function handleDelete(force = false) {
    if (!deleteTarget) return;
    setActionError(null);
    setDeleteConflict(null);
    try {
      await api.delete(`/api/v1/documents/${deleteTarget.id}${force ? "?force=true" : ""}`);
      setItems((prev) => prev.filter((d) => d.id !== deleteTarget.id));
      setTotal((t) => t - 1);
      setDeleteTarget(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && canForceDelete) {
        setDeleteConflict(err.message);
      } else {
        setActionError(err instanceof ApiError ? err.message : "Failed to delete document.");
        setDeleteTarget(null);
      }
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Documents</h1>
          <p>Manuals, SOPs, certificates, contracts, and other records linked to assets, vendors, and service events.</p>
        </div>
        {canWrite && (
          <Link href="/documents/new" className="btn btn--primary">
            Register document
          </Link>
        )}
      </div>

      {actionError && <ErrorState message={actionError} />}

      <div className="toolbar">
        <SearchBar value={search} onChange={setSearch} placeholder="Search by title, manufacturer, or model..." />
        <select value={documentType} onChange={(e) => setDocumentType(e.target.value)}>
          <option value="">All document types</option>
          {DOCUMENT_TYPES.map((t) => (
            <option key={t} value={t}>
              {t.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <select value={entityType} onChange={(e) => setEntityType(e.target.value)}>
          <option value="">All linked entity types</option>
          {DOCUMENT_ENTITY_TYPES.map((t) => (
            <option key={t} value={t}>
              {t.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <input
          value={entityId}
          onChange={(e) => setEntityId(e.target.value)}
          placeholder="Linked entity ID"
          disabled={!entityType}
          style={{ minWidth: 160 }}
        />
        <input type="date" value={expiryAfter} onChange={(e) => setExpiryAfter(e.target.value)} title="Expiry after" />
        <input type="date" value={expiryBefore} onChange={(e) => setExpiryBefore(e.target.value)} title="Expiry before" />
      </div>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}
      {!loading && !error && (
        <>
          <DataTable<Document>
            columns={[
              {
                key: "title",
                header: "Title",
                render: (d) => <Link href={`/documents/${d.id}`}>{d.title}</Link>,
              },
              { key: "document_type", header: "Type", render: (d) => d.document_type.replace(/_/g, " ") },
              { key: "manufacturer", header: "Manufacturer", render: (d) => d.manufacturer ?? "-" },
              { key: "access_level", header: "Access", render: (d) => <StatusBadge value={d.access_level} /> },
              { key: "approval_status", header: "Approval", render: (d) => <StatusBadge value={d.approval_status} /> },
              { key: "versions", header: "Versions", render: (d) => d.version_count },
              { key: "active", header: "Active", render: (d) => (d.is_active ? "Yes" : "No") },
              {
                key: "actions",
                header: "",
                render: (d) => (
                  <div style={{ display: "flex", gap: 8 }}>
                    <Link href={`/documents/${d.id}`} className="btn btn--ghost">
                      View
                    </Link>
                    {canWrite && (
                      <button className="btn btn--ghost" onClick={() => setDeleteTarget(d)}>
                        Delete
                      </button>
                    )}
                  </div>
                ),
              },
            ]}
            rows={items}
            emptyLabel="No documents found."
          />
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
        </>
      )}

      {deleteConflict && (
        <div
          className="state-panel state-panel--error"
          style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}
        >
          <span>{deleteConflict}</span>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--danger" onClick={() => handleDelete(true)}>
              Force delete anyway
            </button>
            <button className="btn btn--ghost" onClick={() => { setDeleteConflict(null); setDeleteTarget(null); }}>
              Cancel
            </button>
          </div>
        </div>
      )}
      <ConfirmDialog
        open={deleteTarget !== null && !deleteConflict}
        title="Delete document"
        message={
          canForceDelete
            ? `Delete "${deleteTarget?.title}"? If it's still linked to a record, you'll be offered a force-delete that removes those links too. This cannot be undone.`
            : `Delete "${deleteTarget?.title}"? This cannot be undone. Documents still linked to a record cannot be deleted.`
        }
        confirmLabel="Delete"
        onConfirm={() => handleDelete()}
        onCancel={() => setDeleteTarget(null)}
      />
    </ProtectedShell>
  );
}
