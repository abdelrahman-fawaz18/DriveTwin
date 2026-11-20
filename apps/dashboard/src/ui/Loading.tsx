export function Spinner() {
  return <span className="loading-spinner" aria-hidden="true" />;
}

export function Loading({ label }: { label: string }) {
  return (
    <span className="loading-inline" role="status">
      <Spinner />
      {label}
    </span>
  );
}
