'use client';

import { useEffect, useId, useMemo, useRef, useState } from 'react';

export interface SearchOption {
  value: string;
  /** Shown instead of the value. */
  label?: string;
  /** Secondary text, searched too. */
  hint?: string;
}

interface SearchSelectProps {
  value: string;
  onChange: (value: string) => void;
  options: SearchOption[];
  /** Accessible name. */
  label: string;
  placeholder?: string;
  /** Adds a first choice that clears the value, for example "All stores". */
  emptyLabel?: string;
  /** Accept a value that is not in the list (a new register, say). */
  allowCustom?: boolean;
  disabled?: boolean;
}

/** A dropdown you can type into: it filters as you type and works from the keyboard. */
export function SearchSelect({
  value,
  onChange,
  options,
  label,
  placeholder = 'Search…',
  emptyLabel,
  allowCustom,
  disabled,
}: SearchSelectProps) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);

  const current = options.find((o) => o.value === value);
  const shown = current ? (current.label ?? current.value) : value;

  const q = query.trim().toLowerCase();
  const matches = useMemo(
    () =>
      q
        ? options.filter((o) =>
            `${o.value} ${o.label ?? ''} ${o.hint ?? ''}`.toLowerCase().includes(q)
          )
        : options,
    [options, q]
  );
  const rows: SearchOption[] = [
    ...(emptyLabel && !q ? [{ value: '', label: emptyLabel }] : []),
    ...matches,
    ...(allowCustom && q && !options.some((o) => o.value.toLowerCase() === q)
      ? [{ value: query.trim(), label: `Use "${query.trim()}"` }]
      : []),
  ];

  useEffect(() => setActive(0), [q, open]);
  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', away);
    return () => document.removeEventListener('mousedown', away);
  }, []);

  const choose = (v: string) => {
    onChange(v);
    setOpen(false);
    setQuery('');
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, rows.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' && open && rows[active]) {
      e.preventDefault();
      choose(rows[active].value);
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      setOpen(false);
      setQuery('');
    }
  };

  return (
    <div className="ss" ref={root}>
      <input
        role="combobox"
        aria-label={label}
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        autoComplete="off"
        disabled={disabled}
        placeholder={shown || placeholder}
        value={open ? query : shown}
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onKeyDown={onKey}
      />
      {open && (
        <ul className="ss-list" id={`${id}-list`} role="listbox">
          {rows.length === 0 ? (
            <li className="ss-empty">No match</li>
          ) : (
            rows.map((o, i) => (
              <li
                key={`${o.value}-${i}`}
                role="option"
                aria-selected={o.value === value}
                className={`ss-opt${i === active ? ' on' : ''}${o.value === value ? ' picked' : ''}`}
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActive(i)}
                onClick={(e) => {
                  e.preventDefault(); // inside a <label>, a click would otherwise refocus and reopen the list
                  choose(o.value);
                }}
              >
                <span>{o.label ?? o.value}</span>
                {o.hint && <small>{o.hint}</small>}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
