"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { UserRecord } from "../types";

/**
 * GET /api/v1/users returns a plain list[UserOut] (no pagination envelope), unlike
 * every other lookup endpoint. `useLookup` assumes a `{items, total, ...}` envelope,
 * so pointing it at /api/v1/users would set its `items` state to `undefined` and crash
 * any `.length`/`.map` read on it. This hook mirrors `useLookup`'s shape (an array,
 * defaulting to `[]`, silently swallowing errors — e.g. the 403 a non-users.manage role
 * gets) but calls the endpoint directly, matching the same pattern already used in
 * app/users/page.tsx.
 */
export function useUserDirectory() {
  const [users, setUsers] = useState<UserRecord[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .get<UserRecord[]>("/api/v1/users")
      .then((result) => {
        if (!cancelled) setUsers(result);
      })
      .catch(() => {
        if (!cancelled) setUsers([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return users;
}
