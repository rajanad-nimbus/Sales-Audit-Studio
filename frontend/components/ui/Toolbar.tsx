'use client';

import { useState } from 'react';

interface ToolbarProps {
  /** The filters people use most: search, store, dates. Shown on one row. */
  children: React.ReactNode;
  /** Summary under the filters, for example "42 results". */
  count?: React.ReactNode;
  /** Less-used filters, tucked behind "More filters". */
  more?: React.ReactNode;
  /** How many of the `more` filters are set, so the toggle can say so while closed. */
  moreActive?: number;
  /** Resets every filter. The button only shows when `canClear` is true. */
  onClear?: () => void;
  canClear?: boolean;
  /** Right-hand slot, for example saved views. */
  aside?: React.ReactNode;
  label?: string;
  /** A light row instead of a panel, for a single switch or action. */
  plain?: boolean;
}

/**
 * The filter panel above a list. Filters sit in one bordered surface, separate from the page's
 * tabs above and the table's quick filters below, so it is always clear what narrows the list.
 */
export function Toolbar({
  children,
  count,
  more,
  moreActive = 0,
  onClear,
  canClear = false,
  aside,
  label = 'Filters',
  plain = false,
}: ToolbarProps) {
  const [open, setOpen] = useState(moreActive > 0);
  const hasEnd = !!more || (!!onClear && canClear) || !!aside;
  return (
    <section className={`filter-bar${plain ? ' plain' : ''}`} aria-label={label}>
      <div className="filter-bar-main">
        <div className="filter-bar-fields">{children}</div>
        {hasEnd && (
          <div className="filter-bar-end">
            {more && (
              <button
                type="button"
                className={`filter-bar-toggle${open ? ' on' : ''}`}
                aria-expanded={open}
                onClick={() => setOpen(!open)}
              >
                More filters
                {moreActive > 0 && <span className="filter-bar-badge">{moreActive}</span>}
              </button>
            )}
            {onClear && canClear && (
              <button type="button" className="filter-bar-clear" onClick={onClear}>
                Clear filters
              </button>
            )}
            {aside}
          </div>
        )}
      </div>
      {more && open && <div className="filter-bar-more">{more}</div>}
      {count !== undefined && count !== null && count !== '' && (
        <div className="filter-bar-foot">{count}</div>
      )}
    </section>
  );
}

/** A small label over one filter control, so date and select filters say what they filter. */
export function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="filter-field">
      <span>{label}</span>
      {children}
    </label>
  );
}
