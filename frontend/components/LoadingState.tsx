export function LoadingState({ label = "Loading..." }: { label?: string }) {
  return <div className="state-panel state-panel--loading">{label}</div>;
}
