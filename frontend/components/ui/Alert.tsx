type Tone = 'info' | 'success' | 'warning' | 'error';

interface AlertProps {
  tone?: Tone;
  children: React.ReactNode;
  onDismiss?: () => void;
  /** Inline action pushed to the right, for example Retry. */
  action?: React.ReactNode;
  className?: string;
}

export function Alert({ tone = 'info', children, onDismiss, action, className = '' }: AlertProps) {
  return (
    <div
      className={`alert alert-${tone}${className ? ` ${className}` : ''}`}
      role={tone === 'error' ? 'alert' : 'status'}
    >
      <span>{children}</span>
      {action}
      {onDismiss && (
        <button
          className="btn-secondary btn-sm alert-close"
          onClick={onDismiss}
          aria-label="Dismiss"
        >
          ×
        </button>
      )}
    </div>
  );
}
