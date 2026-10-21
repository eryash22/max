"use client";

import Link from "next/link";
import type { Tone } from "../../types/dashboard";

/** A headline number.
 *
 * Only rendered as a link when there is a real destination for it — a card without a
 * meaningful filtered view stays a static tile rather than pretending to be
 * clickable. State is carried by a text label as well as the tone colour, so the card
 * still reads correctly without colour perception. */
export function KpiCard({
  label,
  value,
  tone = "neutral",
  state,
  href,
  emphasis,
}: {
  label: string;
  value: number | string;
  tone?: Tone;
  state?: string;
  href?: string;
  /** `lead` renders the larger treatment used for the two or three figures that are
   * the actual point of a role's dashboard. */
  emphasis?: "lead";
}) {
  const body = (
    <>
      <span className="dash-kpi__label">{label}</span>
      <span className="dash-kpi__value">{value}</span>
      {state && <span className="dash-kpi__state">{state}</span>}
    </>
  );
  const className = `dash-kpi dash-kpi--${tone}${emphasis === "lead" ? " dash-kpi--lead" : ""}`;

  if (!href) {
    return <div className={className}>{body}</div>;
  }
  return (
    <Link href={href} className={`${className} dash-kpi--action`}>
      {body}
      <span className="dash-kpi__go" aria-hidden="true">
        →
      </span>
    </Link>
  );
}

/** Skeleton shown while the single summary request is in flight. Matches the real card
 * footprint so the layout does not jump when data arrives. */
export function KpiSkeleton({ count }: { count: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="dash-kpi dash-kpi--skeleton" aria-hidden="true" />
      ))}
    </>
  );
}
