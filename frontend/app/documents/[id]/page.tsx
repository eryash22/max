"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { StatusBadge } from "../../../components/StatusBadge";
import { ConfirmDialog } from "../../../components/ConfirmDialog";
import { useAuth } from "../../../hooks/useAuth";
import { api, ApiError, triggerBrowserDownload } from "../../../lib/api";
import type { Document, DocumentLink, DocumentVersion } from "../../../types";
import { DOCUMENT_ACCESS_LEVELS, DOCUMENT_APPROVAL_STATUSES, DOCUMENT_ENTITY_TYPES, DOCUMENT_TYPES } from "../../../types";

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { hasPermission } = useAuth();
  const canWrite = hasPermission("document.write");

  const [document, setDocument] = useState<Document | null>(null);
  const [versions, setVersions] = useState<DocumentVersion[]>([]);
  const [links, setLinks] = useState<DocumentLink[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [entityType, setEntityType] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  const load = useCallback(async () => {
    try {
      const [documentResult, versionsResult, linksResult] = await Promise.all([
        api.get<Document>(`/api/v1/documents/${params.id}`),
        api.get<DocumentVersion[]>(`/api/v1/documents/${params.id}/versions`),
        api.get<DocumentLink[]>(`/api/v1/documents/${params.id}/links`),
      ]);
      setDocument(documentResult);
      setVersions(versionsResult);
      setLinks(linksResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load document.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.patch(`/api/v1/documents/${params.id}`, {
        title: formData.get("title"),
        document_type: formData.get("document_type"),
        manufacturer: formData.get("manufacturer") || null,
        model_number: formData.get("model_number") || null,
        effective_date: formData.get("effective_date") || null,
        expiry_date: formData.get("expiry_date") || null,
        access_level: formData.get("access_level"),
        approval_status: formData.get("approval_status"),
        is_active: formData.get("is_active") === "on",
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to update document.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    setConfirmDelete(false);
    setBusy(true);
    try {
      await api.delete(`/api/v1/documents/${params.id}`);
      router.push("/documents");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to delete document.");
      setBusy(false);
    }
  }

  async function handleDownload(versionId?: string) {
    setActionError(null);
    try {
      const path = versionId
        ? `/api/v1/documents/${params.id}/versions/${versionId}/download`
        : `/api/v1/documents/${params.id}/download`;
      const { blob, fileName } = await api.download(path);
      triggerBrowserDownload(blob, fileName ?? "document");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to download file.");
    }
  }

  async function handleUploadVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!uploadFile) {
      setActionError("Select a file to upload.");
      return;
    }
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      const versionLabel = (formData.get("version_label") as string) || "v1";
      await api.upload(`/api/v1/documents/${params.id}/versions`, uploadFile, { version_label: versionLabel });
      form.reset();
      setUploadFile(null);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to upload file.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddLink(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    setBusy(true);
    const form = event.currentTarget;
    const formData = new FormData(form);
    try {
      await api.post(`/api/v1/documents/${params.id}/links`, {
        entity_type: formData.get("entity_type"),
        entity_id: formData.get("entity_id"),
      });
      form.reset();
      setEntityType("");
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to link document.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemoveLink(linkId: string) {
    setActionError(null);
    setBusy(true);
    try {
      await api.delete(`/api/v1/documents/${params.id}/links/${linkId}`);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to remove link.");
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return (
      <ProtectedShell>
        <ErrorState message={error} />
      </ProtectedShell>
    );
  }
  if (!document) {
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
          <h1>{document.title}</h1>
          <p>
            <StatusBadge value={document.document_type} />
            {" · "}
            <StatusBadge value={document.approval_status} />
            {!document.is_active ? " · Inactive" : ""}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {document.active_version_id && (
            <button className="btn btn--ghost" onClick={() => handleDownload()} disabled={busy}>
              Download latest
            </button>
          )}
          {canWrite && (
            <button className="btn btn--ghost" onClick={() => setConfirmDelete(true)} disabled={busy}>
              Delete document
            </button>
          )}
        </div>
      </div>

      {actionError && <ErrorState message={actionError} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Document information</h3>
        <table className="data-table">
          <tbody>
            <tr>
              <td>Document type</td>
              <td>{document.document_type.replace(/_/g, " ")}</td>
            </tr>
            <tr>
              <td>Manufacturer</td>
              <td>{document.manufacturer ?? "-"}</td>
            </tr>
            <tr>
              <td>Model number</td>
              <td>{document.model_number ?? "-"}</td>
            </tr>
            <tr>
              <td>Effective date</td>
              <td>{document.effective_date ?? "-"}</td>
            </tr>
            <tr>
              <td>Expiry date</td>
              <td>{document.expiry_date ?? "-"}</td>
            </tr>
            <tr>
              <td>Access level</td>
              <td>
                <StatusBadge value={document.access_level} />
              </td>
            </tr>
            <tr>
              <td>Approval status</td>
              <td>
                <StatusBadge value={document.approval_status} />
              </td>
            </tr>
            <tr>
              <td>Approved by</td>
              <td>{document.approved_by ?? "-"}</td>
            </tr>
            <tr>
              <td>Uploaded by</td>
              <td>{document.uploaded_by ?? "-"}</td>
            </tr>
            <tr>
              <td>Versions on file</td>
              <td>{document.version_count}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {canWrite && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Edit document</h3>
          <form onSubmit={handleUpdate} className="form-grid">
            <div className="form-field">
              <label>Title</label>
              <input name="title" defaultValue={document.title} required />
            </div>
            <div className="form-field">
              <label>Document type</label>
              <select name="document_type" defaultValue={document.document_type}>
                {DOCUMENT_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Manufacturer</label>
              <input name="manufacturer" defaultValue={document.manufacturer ?? ""} />
            </div>
            <div className="form-field">
              <label>Model number</label>
              <input name="model_number" defaultValue={document.model_number ?? ""} />
            </div>
            <div className="form-field">
              <label>Effective date</label>
              <input name="effective_date" type="date" defaultValue={document.effective_date ?? ""} />
            </div>
            <div className="form-field">
              <label>Expiry date</label>
              <input name="expiry_date" type="date" defaultValue={document.expiry_date ?? ""} />
            </div>
            <div className="form-field">
              <label>Access level</label>
              <select name="access_level" defaultValue={document.access_level}>
                {DOCUMENT_ACCESS_LEVELS.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Approval status</label>
              <select name="approval_status" defaultValue={document.approval_status}>
                {DOCUMENT_APPROVAL_STATUSES.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field form-field--checkbox">
              <label>
                <input name="is_active" type="checkbox" defaultChecked={document.is_active} /> Active
              </label>
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Save changes
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>File versions</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Version</th>
              <th>File</th>
              <th>Effective date</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {versions.length === 0 && (
              <tr>
                <td colSpan={5}>No files uploaded yet.</td>
              </tr>
            )}
            {versions.map((v) => (
              <tr key={v.id}>
                <td>{v.version_label}</td>
                <td>{v.file_name}</td>
                <td>{v.effective_date ?? "-"}</td>
                <td>
                  <StatusBadge value={v.processing_status} />
                </td>
                <td>
                  <button className="btn btn--ghost" onClick={() => handleDownload(v.id)}>
                    Download
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {canWrite && (
          <form onSubmit={handleUploadVersion} className="form-grid" style={{ marginTop: 16 }}>
            <div className="form-field">
              <label>Version label</label>
              <input name="version_label" placeholder="e.g. v2" />
            </div>
            <div className="form-field">
              <label>File</label>
              <input type="file" onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)} required />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Upload version
              </button>
            </div>
          </form>
        )}
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Linked records</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Entity type</th>
              <th>Entity ID</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {links.length === 0 && (
              <tr>
                <td colSpan={3}>Not linked to any record.</td>
              </tr>
            )}
            {links.map((l) => (
              <tr key={l.id}>
                <td>{l.entity_type.replace(/_/g, " ")}</td>
                <td>{l.entity_id}</td>
                <td>
                  {canWrite && (
                    <button className="btn btn--ghost" onClick={() => handleRemoveLink(l.id)} disabled={busy}>
                      Unlink
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {canWrite && (
          <form onSubmit={handleAddLink} className="form-grid" style={{ marginTop: 16 }}>
            <div className="form-field">
              <label>Entity type</label>
              <select name="entity_type" value={entityType} onChange={(e) => setEntityType(e.target.value)} required>
                <option value="" disabled>
                  Select type
                </option>
                {DOCUMENT_ENTITY_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Entity ID</label>
              <input name="entity_id" required />
            </div>
            <div className="form-field" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--primary" type="submit" disabled={busy}>
                Link record
              </button>
            </div>
          </form>
        )}
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete document"
        message="This permanently removes the document and its file versions. Documents still linked to a record cannot be deleted. Continue?"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </ProtectedShell>
  );
}
