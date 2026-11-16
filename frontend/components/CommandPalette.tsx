'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useMe } from '@/components/MeProvider';
import { api } from '@/lib/api';

interface Command {
  id: string;
  label: string;
  description: string;
  href: string;
  group: string;
  keywords: string;
}

const TASKS = [
  {
    path: '/store-days',
    label: 'Audit a store day',
    description: 'Review arrivals, totals, exceptions, and audit status.',
    keywords: 'sales audit store day balance close totals late stuck',
  },
  {
    path: '/audit-errors',
    label: 'Resolve audit errors',
    description: 'Find transaction and store-day errors that need attention.',
    keywords: 'exceptions findings unhandled transaction error',
  },
  {
    path: '/transactions',
    label: 'Research transactions',
    description: 'Inspect sales, tenders, registers, and source details.',
    keywords: 'receipt sale pos oms cashier register tender item',
  },
  {
    path: '/actions',
    label: 'Triage open work',
    description: 'Prioritize cases, approvals, and items nearing their deadline.',
    keywords: 'action center cases queue sla approval priority',
  },
  {
    path: '/cash-office',
    label: 'Reconcile the cash office',
    description: 'Review cashier balances, deposits, and cash differences.',
    keywords: 'cash over short deposit declare cashier',
  },
  {
    path: '/ach',
    label: 'Review bank transactions',
    description: 'Reconcile bank activity and governed ACH adjustments.',
    keywords: 'ach bank settlement adjustment',
  },
  {
    path: '/imports',
    label: 'Check incoming sales data',
    description: 'Review feed imports, reconciliation, and batch status.',
    keywords: 'upload ingest pos oms feed batch data',
  },
  {
    path: '/exports',
    label: 'Deliver audited data',
    description: 'Prepare exports and review downstream acknowledgements.',
    keywords: 'export downstream gl rms delivery acknowledgement',
  },
  {
    path: '/settings/controls',
    label: 'Maintain totals and audit rules',
    description: 'Review governed totals, rule worksheets, and GL mapping.',
    keywords: 'configuration controls definitions rules worksheet',
  },
  {
    path: '/foundation',
    label: 'Maintain retail reference data',
    description: 'Review stores, tenders, and other foundation records.',
    keywords: 'configuration foundation reference stores locations',
  },
];

const normalize = (value: string) => value.toLocaleLowerCase().normalize('NFKD');

export function CommandPalette() {
  const router = useRouter();
  const { me } = useMe();
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const activeOption = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const [liveResults, setLiveResults] = useState<Command[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen(true);
        setQuery('');
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
    if (open) input.current?.focus();
  }, [open]);

  const allowedScreens = useMemo(
    () => (me?.screens ?? []).filter((screen) => screen.allowed),
    [me?.screens]
  );
  const commands = useMemo<Command[]>(() => {
    const allowedPaths = new Set(allowedScreens.map((screen) => screen.path));
    const tasks: Command[] = TASKS.filter((task) => allowedPaths.has(task.path)).map((task) => {
      const screen = allowedScreens.find((item) => item.path === task.path);
      return {
        id: `task:${task.path}`,
        label: task.label,
        description: task.description,
        href: task.path,
        group: 'Tasks',
        keywords: `${task.keywords} ${screen?.label ?? ''} ${screen?.group ?? ''}`,
      };
    });
    const screens = allowedScreens
      .filter((screen) => screen.path !== '/')
      .map((screen) => ({
        id: `screen:${screen.path}`,
        label: screen.label,
        description: `Open ${screen.group.toLocaleLowerCase()}.`,
        href: screen.path,
        group: screen.group === 'Configuration' ? 'Configuration' : 'Screens',
        keywords: `${screen.path} ${screen.group}`,
      }));
    return [...tasks, ...screens];
  }, [allowedScreens]);

  useEffect(() => {
    const term = query.trim();
    if (!open || term.length < 2) {
      setLiveResults([]);
      setSearching(false);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setLiveResults([]);
    setSearching(true);
    const timer = window.setTimeout(async () => {
      const requests: Promise<void>[] = [];
      const results: Command[] = [];
      if (allowedScreens.some((screen) => screen.path === '/transactions')) {
        requests.push(
          api
            .get('/api/transactions', {
              params: { q: term, page: 1, page_size: 5 },
              signal: controller.signal,
            })
            .then(({ data }) => {
              for (const row of data.items ?? []) {
                results.push({
                  id: `result:transaction:${row.id}`,
                  label: `${row.transaction_type} · ${row.store_id} · ${row.business_date}`,
                  description: `${row.transaction_number || row.id} · ${row.amount} ${row.currency} · ${row.reconciliation_status}`,
                  href: `/transactions/${encodeURIComponent(row.id)}`,
                  group: 'Transactions',
                  keywords: '',
                });
              }
            })
            .catch(() => {})
        );
      }
      if (term.length <= 50 && allowedScreens.some((screen) => screen.path === '/store-days')) {
        requests.push(
          api
            .get('/api/store-days', {
              params: { q: term, limit: 5 },
              signal: controller.signal,
            })
            .then(({ data }) => {
              for (const row of data ?? []) {
                results.push({
                  id: `result:store-day:${row.store_id}:${row.business_date}`,
                  label: `${row.store_id} · ${row.business_date}`,
                  description: `${row.status} · audit version ${row.audit_version}`,
                  href: `/store-days/${encodeURIComponent(row.store_id)}/${encodeURIComponent(row.business_date)}`,
                  group: 'Store Days',
                  keywords: '',
                });
              }
            })
            .catch(() => {})
        );
      }
      if (allowedScreens.some((screen) => screen.path === '/register-events')) {
        requests.push(
          api
            .get('/api/register-events', {
              params: { q: term, page: 1, page_size: 5, sort: 'time', dir: 'desc' },
              signal: controller.signal,
            })
            .then(({ data }) => {
              for (const row of data.items ?? []) {
                results.push({
                  id: `result:register-event:${row.id}`,
                  label: `${row.event_type} · ${row.store_id} · ${row.business_date}`,
                  description: `Register ${row.register_id || '—'} · transaction ${row.transaction_number || '—'} · ${row.reason_code || 'no reason'}`,
                  href: `/register-events?q=${encodeURIComponent(term)}&event_id=${encodeURIComponent(row.id)}`,
                  group: 'Register Events',
                  keywords: '',
                });
              }
            })
            .catch(() => {})
        );
      }
      if (allowedScreens.some((screen) => screen.path === '/actions')) {
        requests.push(
          api
            .get('/api/action-center/cases', {
              params: { q: term, page: 1, page_size: 5, sort: 'sla', dir: 'asc' },
              signal: controller.signal,
            })
            .then(({ data }) => {
              for (const row of data.items ?? []) {
                results.push({
                  id: `result:case:${row.id}`,
                  label: `${row.case_number} · ${row.case_type}`,
                  description: `${row.store_id} · ${row.business_date} · ${row.status} · exposure ${row.exposure}`,
                  href: `/cases/${encodeURIComponent(row.id)}`,
                  group: 'Cases',
                  keywords: '',
                });
              }
            })
            .catch(() => {})
        );
      }
      if (allowedScreens.some((screen) => screen.path === '/audit-errors')) {
        requests.push(
          api
            .get('/api/audit-errors', { params: { days: 90 }, signal: controller.signal })
            .then(({ data }) => {
              const needle = term.toLocaleLowerCase();
              for (const row of (data.items ?? [])
                .filter((item: any) =>
                  `${item.label} ${item.code} ${item.family}`.toLocaleLowerCase().includes(needle)
                )
                .slice(0, 5)) {
                results.push({
                  id: `result:audit-error:${row.code}`,
                  label: `${row.label} · ${row.code}`,
                  description: `${row.family} · ${row.open_count} open occurrence(s)`,
                  href: `/audit-errors/${encodeURIComponent(row.code)}`,
                  group: 'Audit Errors',
                  keywords: '',
                });
              }
            })
            .catch(() => {})
        );
      }
      if (allowedScreens.some((screen) => screen.path === '/corrections')) {
        requests.push(
          api
            .get('/api/transaction-adjustments', {
              params: { q: term, page: 1, page_size: 5 },
              signal: controller.signal,
            })
            .then(({ data }) => {
              for (const row of data.items ?? []) {
                results.push({
                  id: `result:correction:${row.id}`,
                  label: `${row.adjustment_type} correction · ${row.status}`,
                  description: `${row.transaction_id ? `Transaction ${row.transaction_id}` : 'New missing transaction'} · proposed by ${row.proposed_by}`,
                  href: `/corrections?q=${encodeURIComponent(term)}&adjustment_id=${encodeURIComponent(row.id)}`,
                  group: 'Corrections',
                  keywords: '',
                });
              }
            })
            .catch(() => {})
        );
      }
      await Promise.all(requests);
      if (!cancelled) {
        setLiveResults(results);
        setSearching(false);
      }
    }, 180);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [allowedScreens, open, query]);

  const trimmedQuery = query.trim();
  const filtered = useMemo(() => {
    const needle = normalize(trimmedQuery);
    const matches = commands.filter((command) =>
      normalize(`${command.label} ${command.description} ${command.keywords}`).includes(needle)
    );
    if (trimmedQuery.length >= 2) {
      const searchCommands: Command[] = [];
      if (allowedScreens.some((screen) => screen.path === '/transactions')) {
        searchCommands.push({
          id: 'search:transactions',
          label: `Search transactions for “${trimmedQuery}”`,
          description: 'Search transaction IDs, references, stores, registers, and cashiers.',
          href: `/transactions?q=${encodeURIComponent(trimmedQuery)}`,
          group: 'Search',
          keywords: '',
        });
      }
      if (
        trimmedQuery.length <= 50 &&
        allowedScreens.some((screen) => screen.path === '/store-days')
      ) {
        searchCommands.push({
          id: 'search:store-days',
          label: `Find store days for “${trimmedQuery}”`,
          description: 'Search store IDs, audit status, and store-day details.',
          href: `/store-days?q=${encodeURIComponent(trimmedQuery)}`,
          group: 'Search',
          keywords: '',
        });
      }
      if (allowedScreens.some((screen) => screen.path === '/register-events')) {
        searchCommands.push({
          id: 'search:register-events',
          label: `Search register events for “${trimmedQuery}”`,
          description: 'Search event types, register IDs, transaction references, and reasons.',
          href: `/register-events?q=${encodeURIComponent(trimmedQuery)}`,
          group: 'Search',
          keywords: '',
        });
      }
      if (allowedScreens.some((screen) => screen.path === '/actions')) {
        searchCommands.push({
          id: 'search:cases',
          label: `Search cases for “${trimmedQuery}”`,
          description: 'Search open work by case, store, exception type, or owner.',
          href: `/actions?q=${encodeURIComponent(trimmedQuery)}`,
          group: 'Search',
          keywords: '',
        });
      }
      return [...liveResults, ...searchCommands, ...matches];
    }
    return matches;
  }, [allowedScreens, commands, liveResults, trimmedQuery]);

  useEffect(() => setActiveIndex(0), [query]);
  useEffect(() => {
    setActiveIndex((index) => Math.min(index, Math.max(filtered.length - 1, 0)));
  }, [filtered.length]);
  useEffect(() => activeOption.current?.scrollIntoView({ block: 'nearest' }), [activeIndex]);

  const close = () => {
    setOpen(false);
    setQuery('');
  };
  const run = (command?: Command) => {
    if (!command) return;
    close();
    router.push(command.href);
  };

  const groups = useMemo(() => {
    const byGroup = new Map<string, { command: Command; index: number }[]>();
    filtered.forEach((command, index) => {
      const items = byGroup.get(command.group) ?? [];
      items.push({ command, index });
      byGroup.set(command.group, items);
    });
    return [...byGroup.entries()];
  }, [filtered]);

  return (
    <>
      <button
        className="top-search"
        type="button"
        onClick={() => {
          setQuery('');
          setOpen(true);
        }}
        aria-haspopup="dialog"
        aria-label="Find a task, screen, or record"
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
        <span className="top-search-placeholder">Find a task, screen, or record</span>
        <kbd>⌘K</kbd>
      </button>

      <dialog
        ref={dialog}
        className="command-palette"
        aria-label="Find work"
        onClose={() => {
          setOpen(false);
          setQuery('');
        }}
        onClick={(event) => {
          if (event.target === dialog.current) close();
        }}
      >
        <div className="command-palette-panel">
          <div className="command-palette-search">
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.5-3.5" />
            </svg>
            <input
              ref={input}
              value={query}
              maxLength={255}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'ArrowDown' && filtered.length) {
                  event.preventDefault();
                  setActiveIndex((index) => (index + 1) % filtered.length);
                }
                if (event.key === 'ArrowUp' && filtered.length) {
                  event.preventDefault();
                  setActiveIndex((index) => (index - 1 + filtered.length) % filtered.length);
                }
                if (event.key === 'Enter') {
                  event.preventDefault();
                  run(filtered[activeIndex]);
                }
              }}
              placeholder="What do you need to work on?"
              aria-label="Search tasks, screens, and records"
              aria-controls="command-palette-options"
              aria-activedescendant={
                filtered[activeIndex] ? `command-${filtered[activeIndex].id}` : undefined
              }
              role="combobox"
              aria-expanded="true"
              aria-autocomplete="list"
            />
            <button
              type="button"
              className="command-palette-close"
              onClick={close}
              aria-label="Close search"
            >
              Esc
            </button>
          </div>
          <div
            className="command-palette-results"
            id="command-palette-options"
            role="listbox"
            aria-label="Matching tasks and screens"
          >
            {searching && (
              <p className="command-search-status" role="status">
                Searching permitted records…
              </p>
            )}
            {groups.map(([group, items]) => (
              <section className="command-group" key={group} aria-label={group}>
                <h2>{group}</h2>
                {items.map(({ command, index }) => (
                  <button
                    ref={index === activeIndex ? activeOption : undefined}
                    className={`command-option${index === activeIndex ? ' active' : ''}`}
                    id={`command-${command.id}`}
                    key={command.id}
                    type="button"
                    role="option"
                    aria-selected={index === activeIndex}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => run(command)}
                  >
                    <span className="command-option-copy">
                      <strong>{command.label}</strong>
                      <small>{command.description}</small>
                    </span>
                    <span className="command-enter" aria-hidden="true">
                      ↵
                    </span>
                  </button>
                ))}
              </section>
            ))}
            {filtered.length === 0 && (
              <p className="command-empty">
                {searching
                  ? 'Searching your permitted records…'
                  : 'No matching tasks, screens, or records. Try a store-day, error, cash, or export term.'}
              </p>
            )}
          </div>
          <footer className="command-palette-footer">
            <span>
              <kbd>↑</kbd>
              <kbd>↓</kbd> to move
            </span>
            <span>
              <kbd>↵</kbd> to open
            </span>
            <span>Results follow your access</span>
          </footer>
        </div>
      </dialog>
    </>
  );
}
