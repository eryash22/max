"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { Page } from "../types";

export function usePaginatedResource<T>(basePath: string, query: Record<string, string> = {}) {
  const [data, setData] = useState<Page<T> | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const queryKey = JSON.stringify(query);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: "25", ...query });
      const result = await api.get<Page<T>>(`${basePath}?${params.toString()}`);
      setData(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load data.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [basePath, page, queryKey]);

  useEffect(() => {
    load();
  }, [load]);

  return { data, page, setPage, loading, error, reload: load };
}
