"use client";

// Dashboard charts, hand-drawn as SVG.
//
// Deliberately no charting library: every colour here resolves from the app's
// `--color-*` custom properties, which the admin-controlled theme (hooks/useTheme.ts)
// rewrites at runtime for both light and dark surface modes. A library like Recharts
// or Chart.js wants literal colour values at render time, which would either bypass
// theme customization or need a re-render on every theme change — and would break
// dark mode outright. Drawing to `currentColor`/`var(--color-*)` keeps one source of
// truth for colour.
//
// Interaction contract: a chart segment is a real <button> when `onSelect` is given,
// so it is tabbable, has a visible focus ring, and announces its value to a screen
// reader. Meaning is never colour-only — every segment also carries a text label and
// number in the legend, and the selected state is shown by an outline plus an
// aria-pressed flag, not just a hue change.

import type { ReactNode } from "react";
import type { Tone } from "../../types/dashboard";

export interface Slice {
  key: string;
  label: string;
  value: number;
  tone: Tone;
}

function toneVar(tone: Tone): string {
  return tone === "neutral" ? "var(--color-neutral-text)" : `var(--color-${tone}-text)`;
}

function pct(value: number, total: number): string {
  if (total <= 0) return "0%";
  return `${Math.round((value / total) * 100)}%`;
}

/** Donut — a part-to-whole breakdown where the total is meaningful (asset lifecycle,
 * incident severity). Percentages are shown only because every slice comes from one
 * mutually-exclusive GROUP BY, so they genuinely sum to 100. */
export function DonutChart({
  slices,
  total,
  centerLabel,
  selectedKey,
  onSelect,
}: {
  slices: Slice[];
  total: number;
  centerLabel: string;
  selectedKey?: string | null;
  onSelect?: (key: string) => void;
}) {
  const nonZero = slices.filter((s) => s.value > 0);
  const size = 168;
  const stroke = 22;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;

  let offset = 0;
  const arcs = nonZero.map((slice) => {
    const fraction = total > 0 ? slice.value / total : 0;
    const dash = fraction * circumference;
    const arc = { slice, dash, offset };
    offset += dash;
    return arc;
  });

  return (
    <div className="chart chart--donut">
      <div className="chart__figure">
        <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${centerLabel}: ${total} total`}>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--color-surface-elevated)"
            strokeWidth={stroke}
          />
          {arcs.map(({ slice, dash, offset: o }) => (
            <circle
              key={slice.key}
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke={toneVar(slice.tone)}
              strokeWidth={selectedKey === slice.key ? stroke + 4 : stroke}
              strokeDasharray={`${dash} ${circumference - dash}`}
              strokeDashoffset={-o}
              transform={`rotate(-90 ${size / 2} ${size / 2})`}
              opacity={selectedKey && selectedKey !== slice.key ? 0.35 : 1}
            >
              <title>{`${slice.label}: ${slice.value} (${pct(slice.value, total)})`}</title>
            </circle>
          ))}
          <text x="50%" y="47%" textAnchor="middle" className="chart__donut-value">
            {total}
          </text>
          <text x="50%" y="61%" textAnchor="middle" className="chart__donut-label">
            {centerLabel}
          </text>
        </svg>
      </div>
      <ChartLegend slices={slices} total={total} selectedKey={selectedKey} onSelect={onSelect} />
    </div>
  );
}

function ChartLegend({
  slices,
  total,
  selectedKey,
  onSelect,
}: {
  slices: Slice[];
  total: number;
  selectedKey?: string | null;
  onSelect?: (key: string) => void;
}) {
  return (
    <ul className="chart__legend">
      {slices.map((slice) => {
        const selected = selectedKey === slice.key;
        const content = (
          <>
            <span className="chart__swatch" style={{ background: toneVar(slice.tone) }} aria-hidden="true" />
            <span className="chart__legend-label">{slice.label}</span>
            <span className="chart__legend-value">{slice.value}</span>
            <span className="chart__legend-pct">{pct(slice.value, total)}</span>
          </>
        );
        if (!onSelect) {
          return (
            <li key={slice.key} className="chart__legend-row">
              {content}
            </li>
          );
        }
        return (
          <li key={slice.key}>
            <button
              type="button"
              className={`chart__legend-row chart__legend-row--action${selected ? " is-selected" : ""}`}
              onClick={() => onSelect(slice.key)}
              aria-pressed={selected}
            >
              {content}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** Horizontal bars — a ranked comparison across categories (branches, statuses).
 * Horizontal rather than vertical so long branch names stay readable and the chart
 * grows downward instead of squeezing sideways on a phone. */
export function BarChart({
  slices,
  selectedKey,
  onSelect,
  emptyLabel = "Nothing to show yet.",
  valueLabel,
}: {
  slices: Slice[];
  selectedKey?: string | null;
  onSelect?: (key: string) => void;
  emptyLabel?: string;
  valueLabel?: string;
}) {
  const max = Math.max(1, ...slices.map((s) => s.value));
  if (slices.length === 0) {
    return <p className="dash-empty">{emptyLabel}</p>;
  }
  return (
    <div className="chart chart--bars" role="group" aria-label={valueLabel}>
      {slices.map((slice) => {
        const selected = selectedKey === slice.key;
        const width = `${Math.max(slice.value === 0 ? 0 : 3, (slice.value / max) * 100)}%`;
        const inner = (
          <>
            <span className="bar__label">{slice.label}</span>
            <span className="bar__track">
              <span className="bar__fill" style={{ width, background: toneVar(slice.tone) }} />
            </span>
            <span className="bar__value">{slice.value}</span>
          </>
        );
        if (!onSelect) {
          return (
            <div key={slice.key} className="bar">
              {inner}
            </div>
          );
        }
        return (
          <button
            key={slice.key}
            type="button"
            className={`bar bar--action${selected ? " is-selected" : ""}`}
            onClick={() => onSelect(slice.key)}
            aria-pressed={selected}
            title={`${slice.label}: ${slice.value}`}
          >
            {inner}
          </button>
        );
      })}
    </div>
  );
}

/** Pipeline — an ordered workflow (requirement approval, fulfillment delivery).
 * Order carries real meaning here: these are the backend's actual status sequences,
 * so the chart is a funnel read left-to-right, not an arbitrary ranking. */
export function PipelineChart({
  stages,
  onSelect,
}: {
  stages: Slice[];
  onSelect?: (key: string) => void;
}) {
  const max = Math.max(1, ...stages.map((s) => s.value));
  return (
    <ol className="chart chart--pipeline">
      {stages.map((stage, index) => {
        const height = `${Math.max(stage.value === 0 ? 4 : 12, (stage.value / max) * 100)}%`;
        const inner = (
          <>
            <span className="pipeline__column">
              <span
                className="pipeline__fill"
                style={{ height, background: toneVar(stage.tone) }}
                aria-hidden="true"
              />
            </span>
            <span className="pipeline__value">{stage.value}</span>
            <span className="pipeline__label">{stage.label}</span>
          </>
        );
        return (
          <li key={stage.key} className="pipeline__stage">
            {onSelect ? (
              <button
                type="button"
                className="pipeline__button"
                onClick={() => onSelect(stage.key)}
                title={`${stage.label}: ${stage.value}`}
              >
                {inner}
              </button>
            ) : (
              <div className="pipeline__button pipeline__button--static">{inner}</div>
            )}
            {index < stages.length - 1 && <span className="pipeline__arrow" aria-hidden="true" />}
          </li>
        );
      })}
    </ol>
  );
}

/** A labelled block of the dashboard. Each one owns its loading/empty/error state so a
 * single failed section never blanks the page. */
export function Panel({
  title,
  hint,
  action,
  children,
  span,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
  children: ReactNode;
  span?: "full" | "half";
}) {
  return (
    <section className={`dash-panel${span === "full" ? " dash-panel--full" : ""}`}>
      <header className="dash-panel__head">
        <div>
          <h2>{title}</h2>
          {hint && <p>{hint}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}
