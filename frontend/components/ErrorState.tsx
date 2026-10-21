export function ErrorState({ message }: { message: string }) {
  return <div className="state-panel state-panel--error">{message}</div>;
}
