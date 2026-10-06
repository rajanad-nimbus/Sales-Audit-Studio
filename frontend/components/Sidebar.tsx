'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { api } from '@/lib/api';
import { useLive } from '@/lib/sync';
import { useMe } from './UserMenu';

const icons: Record<string, React.ReactNode> = {
  home: <path d="M3 11.5 12 4l9 7.5M5 10v10h14V10" />,
  cases: <path d="M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7Z" />,
  transactions: <path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" />,
  pipeline: <path d="M4 6h6v4H4zM14 14h6v4h-6zM10 8h2a2 2 0 0 1 2 2v4" />,
  totals: <path d="M5 5h14M5 12h14M5 19h14M8 3v4m8 2v6m-8 2v4" />,
  cash: <path d="M4 7h16v10H4zM7 11h4m3 2h3M7 17v2h10v-2" />,
  controls: <path d="M5 5h14M5 12h14M5 19h14M8 3v4m8 2v6m-8 2v4" />,
  corrections: <path d="M5 4h11l3 3v13H5V4Zm3 6h8m-8 4h8m-8 4h5" />,
  activity: <path d="M3 12h4l2.5-7 4 14 2.5-7H21" />,
  actions: <path d="M13 3 4 14h7l-1 7 9-11h-7l1-7Z" />,
  exports: <path d="M12 3v12m0-12 4 4m-4-4-4 4M5 15v4h14v-4" />,
  settings: <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.3l2-1.5-2-3.4-2.3.9a7.5 7.5 0 0 0-2.2-1.3L14.4 3h-4l-.4 2.4a7.5 7.5 0 0 0-2.2 1.3l-2.3-.9-2 3.4 2 1.5a7.400 7.400 0 0 0 0 2.600l-2 1.500 2 3.400 2.300-.9a7.500 7.500 0 0 0 2.200 1.300l.4 2.400h4l.4-2.400a7.500 7.500 0 0 0 2.200-1.300l2.300.9 2-3.400-2-1.500c.1-.4.1-.9.1-1.300Z" />,
  audit: <path d="M8 4h8l3 3v13H5V4h3Zm0 6h8M8 14h8M8 18h5" />,
};

const sections = [
  { title: 'Workspace', items: [
    { label: 'Command Center', href: '/', icon: 'home' },
    { label: 'Action Center', href: '/actions', icon: 'actions', badge: true },
    { label: 'Cases', href: '/cases', icon: 'cases' },
    { label: 'Agent Activity', href: '/activity', icon: 'activity' },
  ] },
  { title: 'Data & Control', items: [
    { label: 'Store Days & Totals', href: '/store-days', icon: 'totals' },
    { label: 'Transactions', href: '/transactions', icon: 'transactions' },
    { label: 'Cash Office', href: '/cash-office', icon: 'cash' },
    { label: 'Transaction Corrections', href: '/corrections', icon: 'corrections' },
    { label: 'Data Pipeline', href: '/pipeline', icon: 'pipeline' },
    { label: 'Exports', href: '/exports', icon: 'exports' },
    { label: 'Audit Trail', href: '/audit', icon: 'audit' },
  ] },
  { title: 'Configuration', items: [
    { label: 'Configuration', href: '/settings', icon: 'settings' },
  ] },
];

const Icon = ({ name }: { name: string }) => (
  <span className="nav-tile"><svg className="nav-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">{icons[name]}</svg></span>
);

export function Sidebar() {
  const pathname = usePathname();
  const [pending, setPending] = useState(0);
  const { me } = useMe();
  // The server decides which screens this role may open (GET /api/me). Until it answers, show none.
  const allowed = new Set((me?.screens ?? []).filter((s) => s.allowed).map((s) => s.path));
  const canSee = (href: string) => allowed.has(href);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    try { setCollapsed(localStorage.getItem('nimbus_nav_collapsed') === '1'); } catch { /* ignore */ }
  }, []);

  useLive(() => { api.get('/api/action-center/summary').then((r) => setPending(r.data.needs_decision)).catch(() => {}); }, 15000);

  useEffect(() => { setMobileOpen(false); }, [pathname]);

  // The hamburger in the top bar toggles the menu: collapse on desktop, drawer on small screens.
  useEffect(() => {
    const onToggle = () => {
      if (window.matchMedia('(max-width: 768px)').matches) { setMobileOpen((o) => !o); return; }
      setCollapsed((c) => {
        try { localStorage.setItem('nimbus_nav_collapsed', c ? '0' : '1'); } catch { /* ignore */ }
        return !c;
      });
    };
    window.addEventListener('nimbus:toggle-nav', onToggle);
    return () => window.removeEventListener('nimbus:toggle-nav', onToggle);
  }, []);

  const isActive = (href: string) => (href === '/' ? pathname === '/' : pathname.startsWith(href));
  const q = filter.trim().toLowerCase();
  const shown = sections.map((s) => ({ ...s, items: s.items.filter((i) => canSee(i.href) && (!q || i.label.toLowerCase().includes(q))) })).filter((s) => s.items.length);

  return (
    <>
      {mobileOpen && <div className="sidebar-overlay" onClick={() => setMobileOpen(false)} />}

      <aside className={`sidebar${collapsed ? ' collapsed' : ''}${mobileOpen ? ' open' : ''}`}>
        <Link href="/" className="sidebar-brand" title="Nimbus home">
          <div className="brand-mark">
            <svg width="20" height="20" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M16 3 27 9.500v13L16 29 5 22.500v-13L16 3Z" /><path d="M16 11v10M11 13.500 16 16l5-2.500" />
            </svg>
          </div>
          <div className="brand-text"><b>Nimbus</b><span>Sales Audit</span></div>
        </Link>

        <div className="nav-search">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.500-3.500" /></svg>
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Quick search menus..." aria-label="Quick search menus" />
        </div>

        <nav className="sidebar-nav" aria-label="Main">
          {shown.map((s) => (
            <div key={s.title} className="nav-section">
              <div className="nav-section-title">{s.title}</div>
              {s.items.map((item) => (
                <Link key={item.href} href={item.href} title={item.label}
                  className={`menu-link${isActive(item.href) ? ' active' : ''}`}>
                  <Icon name={item.icon} />
                  <span className="menu-label">{item.label}</span>
                  {item.badge && pending > 0 && <span className="menu-badge">{pending > 999 ? `${Math.floor(pending / 1000)}k` : pending}</span>}
                </Link>
              ))}
            </div>
          ))}
          {shown.length === 0 && <p className="cc-empty">No menu matches.</p>}
        </nav>

        <div className="sidebar-footer"><span>© Nimbus Consulting</span></div>
      </aside>
    </>
  );
}
