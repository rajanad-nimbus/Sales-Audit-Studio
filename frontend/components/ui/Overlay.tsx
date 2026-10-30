'use client';

import { useEffect, useRef } from 'react';

interface OverlayProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  /** "modal" is centred; "drawer" slides in from the right. */
  kind: 'modal' | 'drawer';
}

function Overlay({ title, onClose, children, footer, kind }: OverlayProps) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      prev?.focus();
    };
  }, [onClose]);
  return (
    <div className="ds-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`ds-panel ds-${kind}`}
      >
        <header className="ds-panel-head">
          <h2>{title}</h2>
          <button className="btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>
        <div className="ds-panel-body">{children}</div>
        {footer && <footer className="ds-panel-foot">{footer}</footer>}
      </div>
    </div>
  );
}

type Props = Omit<OverlayProps, 'kind'>;
export const Modal = (p: Props) => <Overlay kind="modal" {...p} />;
export const Drawer = (p: Props) => <Overlay kind="drawer" {...p} />;
