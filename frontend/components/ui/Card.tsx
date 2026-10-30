interface CardProps {
  title?: React.ReactNode;
  /** Remove padding, for tables and lists that run edge to edge. */
  flush?: boolean;
  children: React.ReactNode;
}

export function Card({ title, flush, children }: CardProps) {
  return (
    <section className={`card${flush ? ' card-flush' : ''}`}>
      {title && <h2 className="card-title">{title}</h2>}
      {children}
    </section>
  );
}
