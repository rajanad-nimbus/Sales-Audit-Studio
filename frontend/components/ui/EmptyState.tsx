interface EmptyStateProps {
  title: string;
  children?: React.ReactNode;
}

export function EmptyState({ title, children }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <b>{title}</b>
      {children}
    </div>
  );
}
