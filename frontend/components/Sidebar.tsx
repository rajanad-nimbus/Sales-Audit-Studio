'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { api } from '@/lib/api';
import { useLive } from '@/lib/sync';
import { MENU_ONLY, navSections } from '@/lib/nav';
import { Logo } from '@/components/Logo';
import { useMe } from './UserMenu';

const icons: Record<string, React.ReactNode> = {
  home: <path d="M3 11.5 12 4l9 7.5M5 10v10h14V10" />,
  tasks: <path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01" />,
  cases: <path d="M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7Z" />,
  transactions: <path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" />,
  ledger: <path d="M4 5h16v14H4zM8 9h3m2 0h3M8 13h3m2 0h3M8 17h3m2 0h3" />,
  registerEvents: <path d="M7 4h10v16H7zM10 8h4m-4 4h4m-4 4h2" />,
  reports: <path d="M6 3h9l3 3v15H6zM9 11h6m-6 4h6M9 7h3" />,
  auditErrors: <path d="M12 4 3 20h18L12 4Zm0 6v5m0 3h.01" />,
  diagnostics: <path d="M3 12h4l2-6 4 12 2-6h6" />,
  imports: <path d="M12 3v12m0 0-4-4m4 4 4-4M5 17v3h14v-3" />,
  pipeline: <path d="M4 6h6v4H4zM14 14h6v4h-6zM10 8h2a2 2 0 0 1 2 2v4" />,
  foundation: (
    <path d="M12 4c4.4 0 8 1.1 8 2.5S16.400 9 12 9 4 7.900 4 6.500 7.600 4 12 4Zm8 2.500v5c0 1.400-3.600 2.500-8 2.500s-8-1.100-8-2.500v-5m16 5v5c0 1.400-3.600 2.500-8 2.500s-8-1.100-8-2.500v-5" />
  ),
  totals: <path d="M5 5h14M5 12h14M5 19h14M8 3v4m8 2v6m-8 2v4" />,
  cash: <path d="M4 7h16v10H4zM7 11h4m3 2h3M7 17v2h10v-2" />,
  controls: <path d="M5 5h14M5 12h14M5 19h14M8 3v4m8 2v6m-8 2v4" />,
  corrections: <path d="M4 20h4L19 9l-4-4L4 16v4Zm9-13 4 4" />,
  activity: <path d="M3 12h4l2.5-7 4 14 2.5-7H21" />,
  actions: <path d="M13 3 4 14h7l-1 7 9-11h-7l1-7Z" />,
  exports: <path d="M12 3v12m0-12 4 4m-4-4-4 4M5 15v4h14v-4" />,
  settings: (
    <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.3l2-1.5-2-3.4-2.3.9a7.5 7.5 0 0 0-2.2-1.3L14.4 3h-4l-.4 2.4a7.5 7.5 0 0 0-2.2 1.3l-2.3-.9-2 3.4 2 1.5a7.400 7.400 0 0 0 0 2.600l-2 1.500 2 3.400 2.300-.9a7.500 7.500 0 0 0 2.200 1.300l.4 2.400h4l.4-2.400a7.500 7.500 0 0 0 2.200-1.300l2.300.9 2-3.400-2-1.500c.1-.4.1-.9.1-1.300Z" />
  ),
  audit: <path d="M8 4h8l3 3v13H5V4h3Zm0 6h8M8 14h8M8 18h5" />,
  storeDays: <path d="M5 6h14v14H5zM5 10h14M9 3v4m6-4v4" />,
  history: <path d="M4 12a8 8 0 1 0 2.5-5.8M4 5v4h4M12 8v4l3 2" />,
  sum: <path d="M17 6V4H7l6 8-6 8h10v-2" />,
  items: <path d="M4 4h7l9 9-7 7-9-9V4Zm4 4h.01" />,
  tender: <path d="M3 6h18v12H3zM3 10h18M7 15h3" />,
  bank: <path d="M4 10l8-6 8 6H4Zm2 0v8m4-8v8m4-8v8m4-8v8M3 20h18" />,
  cashier: <path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 8a7 7 0 0 1 14 0" />,
  agent: (
    <path d="M7 7h10v10H7zM10 10h4v4h-4zM9 3v4m6-4v4M9 17v4m6-4v4M3 9h4m-4 6h4m10-6h4m-4 6h4" />
  ),
};

const Icon = ({ name }: { name: string }) => (
  <span className="nav-tile">
    <svg
      className="nav-icon"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {icons[name]}
    </svg>
  </span>
);

export function Sidebar() {
  const pathname = usePathname();
  const [pending, setPending] = useState(0);
  const { me } = useMe();
  // The server decides which screens this role may open (GET /api/me). Until it answers, show none.
  const sections = useMemo(() => navSections(me?.screens ?? [], MENU_ONLY), [me]);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [filter, setFilter] = useState('');
  const searchInput = useRef<HTMLInputElement>(null);
  const [closedSections, setClosedSections] = useState<string[]>([]);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem('nimbus_nav_collapsed') === '1');
    } catch {
      /* ignore */
    }
  }, []);

  useLive(() => {
    api
      .get('/api/action-center/summary')
      .then((r) => setPending(r.data.needs_decision))
      .catch(() => {});
  }, 15000);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    searchInput.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMobileOpen(false);
        document.querySelector<HTMLButtonElement>('[aria-label="Toggle menu"]')?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mobileOpen]);

  // The hamburger in the top bar toggles the menu: collapse on desktop, drawer on small screens.
  useEffect(() => {
    const onToggle = () => {
      if (window.matchMedia('(max-width: 768px)').matches) {
        setMobileOpen((o) => !o);
        return;
      }
      setCollapsed((c) => {
        try {
          localStorage.setItem('nimbus_nav_collapsed', c ? '0' : '1');
        } catch {
          /* ignore */
        }
        return !c;
      });
    };
    window.addEventListener('nimbus:toggle-nav', onToggle);
    return () => window.removeEventListener('nimbus:toggle-nav', onToggle);
  }, []);

  const isActive = (href: string) =>
    href === '/' ? pathname === '/' : pathname === href || pathname.startsWith(href + '/');
  const q = filter.trim().toLowerCase();
  const shown = sections
    .map((s) => ({
      ...s,
      items: s.items.filter((i) => !q || i.label.toLowerCase().includes(q)),
    }))
    .filter((s) => s.items.length);

  return (
    <>
      {mobileOpen && <div className="sidebar-overlay" onClick={() => setMobileOpen(false)} />}

      <aside className={`sidebar${collapsed ? ' collapsed' : ''}${mobileOpen ? ' open' : ''}`}>
        <button
          className="nav-icon-btn sidebar-close"
          aria-label="Close menu"
          onClick={() => {
            setMobileOpen(false);
            document.querySelector<HTMLButtonElement>('[aria-label="Toggle menu"]')?.focus();
          }}
        >
          <span aria-hidden="true">×</span>
        </button>
        <Link href="/" className="sidebar-brand" title="Sales Audit Studio">
          <div className="brand-mark">
            <Logo size={22} />
          </div>
          <div className="brand-text">
            <b>Sales Audit</b>
            <span>Studio</span>
          </div>
        </Link>

        <div className="nav-search">
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.500-3.500" />
          </svg>
          <input
            ref={searchInput}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape' && filter) {
                e.stopPropagation();
                setFilter('');
              }
            }}
            placeholder="Find a page…"
            aria-label="Quick search menus"
          />
          {filter && (
            <button
              className="nav-search-clear"
              aria-label="Clear menu search"
              onClick={() => {
                setFilter('');
                searchInput.current?.focus();
              }}
            >
              <span aria-hidden="true">×</span>
            </button>
          )}
        </div>

        <nav className="sidebar-nav" aria-label="Main">
          {shown.map((s) => (
            <div key={s.title} className="nav-section">
              <button
                className="nav-section-title nav-section-toggle"
                aria-expanded={!!q || !closedSections.includes(s.title)}
                aria-controls={`nav-group-${s.title.replace(/\s+/g, '-')}`}
                onClick={() =>
                  setClosedSections((current) =>
                    current.includes(s.title)
                      ? current.filter((title) => title !== s.title)
                      : [...current, s.title]
                  )
                }
              >
                {s.title}
                <svg
                  className="nav-section-chevron"
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  aria-hidden="true"
                >
                  <path d="m6 9 6 6 6-6" />
                </svg>
              </button>
              <div
                className="nav-section-items"
                id={`nav-group-${s.title.replace(/\s+/g, '-')}`}
                hidden={!q && closedSections.includes(s.title)}
              >
                {s.items.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    title={item.label}
                    aria-current={isActive(item.href) ? 'page' : undefined}
                    className={`menu-link${isActive(item.href) ? ' active' : ''}`}
                  >
                    <Icon name={item.icon} />
                    <span className="menu-label">{item.label}</span>
                    {item.badge && pending > 0 && (
                      <span className="menu-badge">
                        {pending > 999 ? `${Math.floor(pending / 1000)}k` : pending}
                      </span>
                    )}
                  </Link>
                ))}
              </div>
            </div>
          ))}
          {q && (
            <p className="nav-search-status" role="status">
              {shown.length === 0
                ? `No pages match “${filter.trim()}”. Try another name.`
                : `${shown.reduce((count, section) => count + section.items.length, 0)} matching pages`}
            </p>
          )}
        </nav>

        <div className="sidebar-footer">
          <span>© Nimbus Consulting</span>
        </div>
      </aside>
    </>
  );
}
