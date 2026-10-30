export type BadgeVariant = 'primary' | 'secondary' | 'success' | 'warning' | 'error' | 'info';

interface BadgeProps {
  children: React.ReactNode;
  variant?: BadgeVariant;
  size?: 'sm' | 'md';
}

export function Badge({ children, variant = 'primary', size = 'md' }: BadgeProps) {
  return <span className={`badge badge-${variant} badge-${size}`}>{children}</span>;
}
