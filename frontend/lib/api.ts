// PEHLI LINE KO BADAL KAR YAHI KAR DEIN:
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const TOKEN_KEY = "max_biomedical_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// The request never reached the server at all — connection refused, DNS failure, CORS
// block, offline, etc. `fetch()` throws a plain TypeError for all of these with a
// browser-specific, not-very-actionable message ("Failed to fetch" / "Load failed").
// Distinct from ApiError (status 0, no server ever responded) so callers — the login
// page especially — can tell "the server rejected this" apart from "the server was
// never reachable" instead of collapsing both into one generic "unable to sign in."
export class NetworkError extends ApiError {
  constructor(cause: unknown) {
    super(0, "Could not reach the server. Check your connection and that the backend is running.");
    if (process.env.NODE_ENV !== "production") {
      // eslint-disable-next-line no-console
      console.error(`[api] network error calling ${API_BASE}:`, cause);
    }
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { ...(options.headers as Record<string, string>) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch (cause) {
    throw new NetworkError(cause);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const isJson = response.headers.get("content-type")?.includes("application/json");
  const body = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message = isJson && body?.detail ? body.detail : response.statusText;
    throw new ApiError(response.status, typeof message === "string" ? message : JSON.stringify(message));
  }

  return body as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: "GET" }),
  post: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: "POST", body: data !== undefined ? JSON.stringify(data) : undefined }),
  patch: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: "PATCH", body: data !== undefined ? JSON.stringify(data) : undefined }),
  put: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: "PUT", body: data !== undefined ? JSON.stringify(data) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, file: File, fields?: Record<string, string>) => {
    const formData = new FormData();
    formData.append("file", file);
    if (fields) {
      for (const [key, value] of Object.entries(fields)) formData.append(key, value);
    }
    return request<T>(path, { method: "POST", body: formData });
  },
  download: async (path: string): Promise<{ blob: Blob; fileName: string | null }> => {
    const token = getToken();
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const response = await fetch(`${API_BASE}${path}`, { headers });
    if (!response.ok) {
      const isJson = response.headers.get("content-type")?.includes("application/json");
      const body = isJson ? await response.json() : null;
      throw new ApiError(response.status, body?.detail ?? response.statusText);
    }
    const disposition = response.headers.get("content-disposition");
    const match = disposition?.match(/filename="?([^"]+)"?/);
    return { blob: await response.blob(), fileName: match?.[1] ?? null };
  },
};

/** Saves a downloaded blob (from `api.download`) to disk via a throwaway anchor —
 * shared by every "download a file" flow (Documents, and every module's Export
 * button) so this isn't reimplemented per page. */
export function triggerBrowserDownload(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const anchor = window.document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
}
