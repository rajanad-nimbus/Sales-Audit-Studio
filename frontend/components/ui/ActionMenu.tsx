'use client';

import { useEffect, useRef, useState } from 'react';

export interface ActionMenuItem {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  hint?: string;
}

/** One button that opens a short list of actions, e.g. every CSV download for a page. */
export function ActionMenu({
  label,
  items,
  busy = false,
}: {
  label: string;
  items: ActionMenuItem[];
  busy?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);

  const shown = items.filter((item) => !item.disabled);
  if (shown.length === 0) return null;
  return (
    <div className="saved-views action-menu" ref={box}>
      <button
        type="button"
        className={`btn-secondary btn-sm${open ? ' on' : ''}`}
        aria-expanded={open}
        aria-haspopup="true"
        disabled={busy}
        onClick={() => setOpen(!open)}
      >
        {busy ? 'Preparing…' : label}
      </button>
      {open && (
        <div className="saved-views-menu" role="menu">
          <ul>
            {shown.map((item) => (
              <li key={item.label}>
                <button
                  type="button"
                  role="menuitem"
                  className="saved-views-apply"
                  onClick={() => {
                    setOpen(false);
                    item.onSelect();
                  }}
                >
                  <span>
                    {item.label}
                    {item.hint && <small className="action-menu-hint">{item.hint}</small>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
