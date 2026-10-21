"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { NotificationEvent } from "../types";

/** Every event a communication rule can be configured against, fetched from the
 * backend's single source of truth (GET /api/v1/notifications/events) instead of
 * a second, hard-coded list living in the frontend — keeps the admin UI's event
 * selector in sync with whatever events the backend actually supports/dispatches. */
export function useNotificationEvents() {
  const [events, setEvents] = useState<NotificationEvent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .get<NotificationEvent[]>("/api/v1/notifications/events")
      .then((result) => {
        if (!cancelled) setEvents(result);
      })
      .catch(() => {
        if (!cancelled) setEvents([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const labels: Record<string, string> = {};
  for (const e of events) labels[e.code] = e.label;

  return { events, labels, loading };
}
