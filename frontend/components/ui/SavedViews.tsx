'use client';

import { useEffect, useRef, useState } from 'react';

interface SavedViewsProps {
  views: { name: string }[];
  onApply: (index: number) => void;
  onDelete: (index: number) => void;
  onSave: () => void;
  /** The views could not sync to the account, so they live on this device only. */
  syncError?: boolean;
}

/** A compact menu of saved filter sets, so saved views do not crowd the filters themselves. */
export function SavedViews({ views, onApply, onDelete, onSave, syncError }: SavedViewsProps) {
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

  return (
    <div className="saved-views" ref={box}>
      <button
        type="button"
        className={`filter-bar-toggle${open ? ' on' : ''}`}
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen(!open)}
      >
        Saved views
        {views.length > 0 && <span className="saved-views-count">{views.length}</span>}
      </button>
      {open && (
        <div className="saved-views-menu" role="menu">
          {views.length === 0 ? (
            <p className="saved-views-empty">
              Save the filters you use often to get back to them in one click.
            </p>
          ) : (
            <ul>
              {views.map((view, index) => (
                <li key={`${view.name}-${index}`}>
                  <button
                    type="button"
                    role="menuitem"
                    className="saved-views-apply"
                    onClick={() => {
                      onApply(index);
                      setOpen(false);
                    }}
                  >
                    {view.name}
                  </button>
                  <button
                    type="button"
                    className="saved-views-delete"
                    aria-label={`Delete view ${view.name}`}
                    title="Delete view"
                    onClick={() => onDelete(index)}
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            role="menuitem"
            className="saved-views-save"
            onClick={() => {
              setOpen(false);
              onSave();
            }}
          >
            Save current view
          </button>
          {syncError && (
            <p className="saved-views-note">
              Saved on this device only. They could not sync to your account.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
