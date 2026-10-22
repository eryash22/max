"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ProtectedShell } from "../../components/ProtectedShell";
import { LoadingState } from "../../components/LoadingState";
import { ErrorState } from "../../components/ErrorState";
import { useLookup } from "../../hooks/useLookup";
import { useAssetLabel } from "../../hooks/useAssetLabel";
import { api, ApiError } from "../../lib/api";
import type { CalendarEvent } from "../../types";

const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const EVENT_TYPES = [
  { value: "PM_DUE", label: "PM due", category: "maintenance" },
  { value: "CALIBRATION_DUE", label: "Calibration due", category: "calibration" },
  { value: "AMC_EXPIRY", label: "AMC expiry", category: "contract" },
  { value: "CMC_EXPIRY", label: "CMC expiry", category: "contract" },
  { value: "WARRANTY_EXPIRY", label: "Warranty expiry", category: "contract" },
  { value: "SERVICE_VISIT", label: "Service visit", category: "service" },
  { value: "EXPECTED_DELIVERY", label: "Expected delivery", category: "fulfillment" },
] as const;

type Category = (typeof EVENT_TYPES)[number]["category"];

function categoryOf(eventType: string): Category {
  return EVENT_TYPES.find((t) => t.value === eventType)?.category ?? "service";
}

function toIsoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function addDays(d: Date, days: number): Date {
  const copy = new Date(d);
  copy.setDate(copy.getDate() + days);
  return copy;
}

function addMonths(d: Date, months: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + months, 1);
}

/** Route for the real record behind a calendar event, per Module 18A's
 * compute_calendar_events() reference_id mapping (PMSchedule/CalibrationSchedule/
 * AmcContract/CmcContract/Warranty/ServiceVisit ids) — service visits have no
 * detail page in this app, so they route to the filtered service-visits list. */
function eventHref(event: CalendarEvent): string {
  switch (event.event_type) {
    case "PM_DUE":
      return `/maintenance/${event.reference_id}`;
    case "CALIBRATION_DUE":
      return `/calibration/${event.reference_id}`;
    case "AMC_EXPIRY":
    case "CMC_EXPIRY":
    case "WARRANTY_EXPIRY":
      return `/contracts/${event.reference_id}`;
    case "SERVICE_VISIT":
      return event.asset_id ? `/service-visits?asset_id=${encodeURIComponent(event.asset_id)}` : "/service-visits";
    case "EXPECTED_DELIVERY":
      return `/fulfillments/${event.reference_id}`;
    default:
      return event.asset_id ? `/assets/${event.asset_id}` : "#";
  }
}

export default function CalendarPage() {
  const [monthStart, setMonthStart] = useState(() => startOfMonth(new Date()));
  const [typeFilter, setTypeFilter] = useState("");
  const [events, setEvents] = useState<CalendarEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ date: string; events: CalendarEvent[] } | null>(null);

  const { labelForAssetId } = useAssetLabel();

  const gridStart = useMemo(() => addDays(monthStart, -monthStart.getDay()), [monthStart]);
  const gridEnd = useMemo(() => addDays(gridStart, 41), [gridStart]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setError(null);
      setEvents(null);
      try {
        const params = new URLSearchParams({ start_date: toIsoDate(gridStart), end_date: toIsoDate(gridEnd) });
        const result = await api.get<CalendarEvent[]>(`/api/v1/assets/calendar-events?${params.toString()}`);
        if (!cancelled) setEvents(result);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load calendar events.");
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [gridStart, gridEnd]);

  const filteredEvents = useMemo(() => {
    if (!events) return [];
    return typeFilter ? events.filter((e) => e.event_type === typeFilter) : events;
  }, [events, typeFilter]);

  const eventsByDate = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();
    for (const event of filteredEvents) {
      const list = map.get(event.date) ?? [];
      list.push(event);
      map.set(event.date, list);
    }
    return map;
  }, [filteredEvents]);

  const days = useMemo(() => {
    const list: Date[] = [];
    for (let i = 0; i < 42; i++) list.push(addDays(gridStart, i));
    return list;
  }, [gridStart]);

  const todayIso = toIsoDate(new Date());

  const overdue = useMemo(
    () =>
      filteredEvents
        .filter((e) => e.date < todayIso)
        .slice()
        .sort((a, b) => a.date.localeCompare(b.date)),
    [filteredEvents, todayIso]
  );
  const upcoming = useMemo(
    () =>
      filteredEvents
        .filter((e) => e.date >= todayIso)
        .slice()
        .sort((a, b) => a.date.localeCompare(b.date)),
    [filteredEvents, todayIso]
  );

  function assetLabel(assetId: string | null): string {
    if (!assetId) return "";
    return labelForAssetId(assetId);
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>Biomedical operations calendar</h1>
          <p>
            PM due dates, calibration due dates, AMC/CMC/warranty expiry, and service visits — from the real
            calendar-events endpoint built in Module 18A.
          </p>
        </div>
        <Link href="/dashboard" className="btn btn--ghost">
          Back to dashboard
        </Link>
      </div>

      <div className="calendar-toolbar">
        <div className="calendar-nav">
          <button className="btn btn--ghost" onClick={() => setMonthStart((m) => addMonths(m, -1))}>
            Previous
          </button>
          <h2>{monthStart.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</h2>
          <button className="btn btn--ghost" onClick={() => setMonthStart((m) => addMonths(m, 1))}>
            Next
          </button>
          <button className="btn btn--ghost" onClick={() => setMonthStart(startOfMonth(new Date()))}>
            Today
          </button>
        </div>
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">All event types</option>
          {EVENT_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>

      <div className="calendar-legend">
        <span className="calendar-legend__item">
          <span className="calendar-dot calendar-dot--maintenance" /> Maintenance (PM)
        </span>
        <span className="calendar-legend__item">
          <span className="calendar-dot calendar-dot--calibration" /> Calibration
        </span>
        <span className="calendar-legend__item">
          <span className="calendar-dot calendar-dot--contract" /> Contract expiry (AMC/CMC/Warranty)
        </span>
        <span className="calendar-legend__item">
          <span className="calendar-dot calendar-dot--service" /> Service visit
        </span>
        <span className="calendar-legend__item">
          <span className="calendar-dot calendar-dot--fulfillment" /> Expected delivery
        </span>
      </div>

      {error && <ErrorState message={error} />}
      {!error && !events && <LoadingState />}

      {events && (
        <div className="calendar-grid">
          {WEEKDAY_LABELS.map((w) => (
            <div className="calendar-grid__weekday" key={w}>
              {w}
            </div>
          ))}
          {days.map((day) => {
            const iso = toIsoDate(day);
            const dayEvents = eventsByDate.get(iso) ?? [];
            const outside = day.getMonth() !== monthStart.getMonth();
            const isToday = iso === todayIso;
            const shown = dayEvents.slice(0, 3);
            const extra = dayEvents.length - shown.length;
            return (
              <div
                key={iso}
                className={`calendar-day ${outside ? "calendar-day--outside" : ""} ${isToday ? "calendar-day--today" : ""}`}
              >
                <span className="calendar-day__number">{day.getDate()}</span>
                <div className="calendar-day__events">
                  {shown.map((event) => (
                    <Link
                      key={`${event.event_type}-${event.reference_id}`}
                      href={eventHref(event)}
                      className="calendar-event"
                      title={`${event.label}${event.asset_id ? ` — ${assetLabel(event.asset_id)}` : ""}`}
                    >
                      <span className={`calendar-dot calendar-dot--${categoryOf(event.event_type)}`} />
                      {event.label}
                    </Link>
                  ))}
                  {extra > 0 && (
                    <button
                      className="calendar-day__more"
                      onClick={() => setSelected({ date: iso, events: dayEvents })}
                    >
                      +{extra} more
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {selected && (
        <div className="dialog-overlay" onClick={() => setSelected(null)}>
          <div className="dialog" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>Events on {selected.date}</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {selected.events.map((event) => (
                <Link
                  key={`${event.event_type}-${event.reference_id}`}
                  href={eventHref(event)}
                  className="calendar-event"
                  onClick={() => setSelected(null)}
                >
                  <span className={`calendar-dot calendar-dot--${categoryOf(event.event_type)}`} />
                  {event.label}
                  {event.asset_id ? ` — ${assetLabel(event.asset_id)}` : ""}
                </Link>
              ))}
            </div>
            <div className="dialog__actions">
              <button className="btn btn--ghost" onClick={() => setSelected(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="card" style={{ marginTop: 24 }}>
        <h3 style={{ marginTop: 0 }}>Attention required (this view)</h3>
        <p className="dashboard-note" style={{ marginTop: 0 }}>
          Scoped to the dates currently shown above ({toIsoDate(gridStart)} to {toIsoDate(gridEnd)}). For global
          overdue/expiring counts across all dates, see the{" "}
          <Link href="/dashboard">dashboard</Link>.
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <div>
            <h4>Overdue / past due ({overdue.length})</h4>
            {overdue.length === 0 && <p className="dashboard-note">None in this view.</p>}
            {overdue.map((event) => (
              <Link
                key={`${event.event_type}-${event.reference_id}`}
                href={eventHref(event)}
                className="calendar-event"
                style={{ marginBottom: 4 }}
              >
                <span className={`calendar-dot calendar-dot--${categoryOf(event.event_type)}`} />
                {event.date} — {event.label}
                {event.asset_id ? ` (${assetLabel(event.asset_id)})` : ""}
              </Link>
            ))}
          </div>
          <div>
            <h4>Upcoming ({upcoming.length})</h4>
            {upcoming.length === 0 && <p className="dashboard-note">None in this view.</p>}
            {upcoming.map((event) => (
              <Link
                key={`${event.event_type}-${event.reference_id}`}
                href={eventHref(event)}
                className="calendar-event"
                style={{ marginBottom: 4 }}
              >
                <span className={`calendar-dot calendar-dot--${categoryOf(event.event_type)}`} />
                {event.date} — {event.label}
                {event.asset_id ? ` (${assetLabel(event.asset_id)})` : ""}
              </Link>
            ))}
          </div>
        </div>
      </div>

      <p className="dashboard-note">
        Event types shown are exactly what <code>GET /api/v1/assets/calendar-events</code> returns: PM due,
        Calibration due, AMC/CMC/Warranty expiry, Service visits, and (Module 19) Expected delivery — sourced from
        the expected delivery date of any in-flight fulfillment (allocated through out-for-delivery; delivered,
        completed, and cancelled fulfillments are excluded as history rather than an upcoming date). Requirement
        deadlines (the requester's own required-by date, before a fulfillment exists) are still not shown here —
        see the Requirements section of the dashboard instead. There is no week view or branch/hospital filter on
        this page: the backend calendar-events endpoint accepts only{" "}
        <code>start_date</code>/<code>end_date</code>, not a branch filter or a week-granularity query.
      </p>
    </ProtectedShell>
  );
}
