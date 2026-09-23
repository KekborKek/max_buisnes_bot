// Скелетон (D6): три серые плашки высотой со строку CellSimple.
export function Skeleton() {
  return (
    <div className="skeleton" aria-busy="true" data-testid="skeleton">
      <div className="skeleton-row" />
      <div className="skeleton-row" />
      <div className="skeleton-row" />
    </div>
  );
}
