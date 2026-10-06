'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTheme } from '@/lib/ThemeContext';
import { BusinessDate } from '@/components/BusinessDate';
import { NotificationBell } from '@/components/Notifications';
import { UserMenu } from '@/components/UserMenu';

export function TopBar() {
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const [q, setQ] = useState('');
  const box = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); box.current?.focus(); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const effectiveTheme = theme === 'system'
    ? (typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : theme;
  const isDark = effectiveTheme === 'dark';
  const toggleTheme = () => setTheme(isDark ? 'light' : 'dark');

  return (
    <nav className="navbar">
      <div className="navbar-container">
        <button className="nav-icon-btn" onClick={() => window.dispatchEvent(new Event('nimbus:toggle-nav'))} aria-label="Toggle menu">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
        </button>
        <form className="top-search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) router.push(`/actions?q=${encodeURIComponent(q.trim())}`); }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.500-3.500" /></svg>
          <input ref={box} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search cases, stores, exception types..." aria-label="Search" />
          <kbd>⌘K</kbd>
        </form>
        <div className="navbar-actions">
          <BusinessDate />
          <button className="nav-icon-btn" onClick={toggleTheme} aria-label="Toggle theme" title="Toggle theme">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
              <path d={isDark ? 'M12 4V2m0 20v-2M4 12H2m20 0h-2M6 6 4.500 4.500M19.500 19.500 18 18M6 18l-1.500 1.500M19.500 4.500 18 6M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z' : 'M20 14.500A8 8 0 0 1 9.500 4 8 8 0 1 0 20 14.500Z'} />
            </svg>
          </button>
          <NotificationBell />
          <UserMenu />
        </div>
      </div>
    </nav>
  );
}
