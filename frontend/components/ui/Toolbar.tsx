interface ToolbarProps {
  children: React.ReactNode;
  /** Right-aligned summary, for example "42 results". */
  count?: React.ReactNode;
}

/** Filter and search row above a table. */
export function Toolbar({ children, count }: ToolbarProps) {
  return (
    <div className="toolbar">
      {children}
      {count !== undefined && <span className="toolbar-count">{count}</span>}
    </div>
  );
}
