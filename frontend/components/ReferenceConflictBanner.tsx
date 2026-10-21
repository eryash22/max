/** Shown in place of a plain error when a DELETE is blocked by a 409 reference
 * conflict — surfaces the backend's reason and puts the archive/deactivate
 * fallback one click away, instead of leaving the user to find that action
 * themselves elsewhere on the page. */
export function ReferenceConflictBanner({
  message,
  actionLabel,
  onAction,
  busy,
}: {
  message: string;
  actionLabel: string;
  onAction: () => void;
  busy?: boolean;
}) {
  return (
    <div
      className="state-panel state-panel--error"
      style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}
    >
      <span>{message}</span>
      <button className="btn btn--primary" onClick={onAction} disabled={busy}>
        {busy ? "Working..." : actionLabel}
      </button>
    </div>
  );
}
