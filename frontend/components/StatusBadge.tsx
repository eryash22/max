const TONE_BY_KEYWORD: { match: RegExp; tone: string }[] = [
  { match: /^(active|ok|completed|ready|approved|resolved|connected|up_to_date)$/i, tone: "success" },
  { match: /^(pending|uploaded|validating|deferred|not_provided)$/i, tone: "neutral" },
  { match: /^(review_queue|duplicate_candidate|warning|partially_rolled_back)$/i, tone: "warning" },
  { match: /^(error|rejected|failed|blocked|unreachable|rollback_blocked)$/i, tone: "danger" },
  { match: /location_asset_verification_required|misplaced/i, tone: "danger" },
];

function toneFor(value: string): string {
  const found = TONE_BY_KEYWORD.find((entry) => entry.match.test(value));
  return found?.tone ?? "neutral";
}

// Accessibility (dashboard redesign, Part 8) — status is never conveyed by color alone
// anywhere this badge is used: every tone also gets a fixed glyph, in addition to the
// always-present text label.
const GLYPH_BY_TONE: Record<string, string> = {
  success: "✓",
  warning: "⚠",
  danger: "✕",
  info: "ℹ",
  neutral: "●",
};

export function StatusBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="badge badge--neutral">Not provided</span>;
  const tone = toneFor(value);
  return (
    <span className={`badge badge--${tone}`}>
      <span aria-hidden="true">{GLYPH_BY_TONE[tone]}</span> {value.replace(/_/g, " ")}
    </span>
  );
}
