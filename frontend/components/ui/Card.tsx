interface CardProps {
  title?: React.ReactNode;
  /** Remove padding, for tables and lists that run edge to edge. */
  flush?: boolean;
  className?: string;
  children: React.ReactNode;
}

export function Card({ title, flush, className = '', children }: CardProps) {
  const cls = `card${flush ? ' card-flush' : ''}${className ? ` ${className}` : ''}`;
  return (
    <section className={cls}>
      {title && <h2 className="card-title">{title}</h2>}
      {children}
    </section>
  );
}
