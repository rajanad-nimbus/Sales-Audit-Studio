interface MetricTileProps {
  label: string;
  value: React.ReactNode;
  onClick?: () => void;
}

export function MetricTile({ label, value, onClick }: MetricTileProps) {
  if (onClick) {
    return (
      <button className="cc-kpi clickable ds-tile-btn" onClick={onClick}>
        <span>{label}</span>
        <b>{value}</b>
      </button>
    );
  }
  return (
    <div className="cc-kpi">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

/** Responsive row of tiles. */
export function MetricGrid({ children }: { children: React.ReactNode }) {
  return <div className="cc-kpis">{children}</div>;
}
