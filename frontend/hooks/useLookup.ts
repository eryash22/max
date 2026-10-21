"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { Page } from "../types";

export interface LookupResult<T> {
  items: T[];
  loading: boolean;
  error: string | null;
}

// Overloads: existing callers (`useLookup<T>(path)` / `useLookup<T>(path, {fetchAll})`)
// keep getting a plain array back, unchanged — only a caller that explicitly opts in
// with `withStatus: true` gets the richer {items, loading, error} shape. This lets a
// picker distinguish "still loading" / "the API call failed" from a genuine empty
// result (see components/SearchableSelect.tsx) without touching the ~30 existing call
// sites that only ever destructure the array.
export function useLookup<T extends { id: string }>(path: string, options?: { fetchAll?: boolean }): T[];
export function useLookup<T extends { id: string }>(
  path: string,
  options: { fetchAll?: boolean; withStatus: true }
): LookupResult<T>;
export function useLookup<T extends { id: string }>(
  path: string,
  options?: { fetchAll?: boolean; withStatus?: boolean }
): T[] | LookupResult<T> {
  const [items, setItems] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetchAll = options?.fetchAll ?? false;
  const withStatus = options?.withStatus ?? false;

  useEffect(() => {
    let cancelled = false;

    const sep = path.includes("?") ? "&" : "?";

    async function load() {
      setLoading(true);
      setError(null);
      try {
        if (!fetchAll) {
          const result = await api.get<Page<T>>(`${path}${sep}page=1&page_size=200`);
          if (!cancelled) setItems(result.items);
          return;
        }
        // Backend caps page_size at 200 (see PaginationParams), so a full list past
        // that many rows requires paging through until every item has been collected.
        let page = 1;
        let all: T[] = [];
        for (;;) {
          const result = await api.get<Page<T>>(`${path}${sep}page=${page}&page_size=200`);
          all = all.concat(result.items);
          if (result.items.length === 0 || all.length >= result.total) break;
          page += 1;
        }
        if (!cancelled) setItems(all);
      } catch (err) {
        if (!cancelled) {
          setItems([]);
          setError(err instanceof ApiError ? err.message : "Failed to load data.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [path, fetchAll]);

  return withStatus ? { items, loading, error } : items;
}
