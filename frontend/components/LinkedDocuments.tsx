"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "../lib/api";
import type { Document, Page } from "../types";

/**
 * Read-only "Linked documents" panel for a workflow entity's detail page — final
 * hardening pass, document-linking extension. Documents are still attached/linked
 * from the Document's own detail page (`/documents/[id]`, entity_type + entity_id),
 * this only surfaces what's already linked so a reader doesn't have to go hunt for
 * it. `entityType` must be one of schemas.documents.ENTITY_TYPES (backend-validated).
 */
export function LinkedDocuments({ entityType, entityId }: { entityType: string; entityId: string }) {
  const [documents, setDocuments] = useState<Document[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const params = new URLSearchParams({ entity_type: entityType, entity_id: entityId, page_size: "50" });
        const result = await api.get<Page<Document>>(`/api/v1/documents?${params.toString()}`);
        if (!cancelled) setDocuments(result.items);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load linked documents.");
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [entityType, entityId]);

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3>Linked documents</h3>
      {error && <div className="field-error">{error}</div>}
      {!error && documents === null && <p>Loading…</p>}
      {documents && documents.length === 0 && <p>No documents linked yet.</p>}
      {documents && documents.length > 0 && (
        <ul>
          {documents.map((d) => (
            <li key={d.id}>
              <Link href={`/documents/${d.id}`}>{d.title}</Link> — {d.document_type.replace(/_/g, " ")}
            </li>
          ))}
        </ul>
      )}
      <p style={{ marginTop: 8 }}>
        <Link href="/documents/new">Attach a new document</Link> and link it to this record from its detail page
        (entity type <code>{entityType}</code>, entity ID <code>{entityId}</code>).
      </p>
    </div>
  );
}
