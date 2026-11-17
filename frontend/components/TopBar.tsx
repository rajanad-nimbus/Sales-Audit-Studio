'use client';

import { useEffect, useState } from 'react';
import { useTheme } from '@/lib/ThemeContext';
import { CommandPalette } from '@/components/CommandPalette';
import { HelpCenter } from '@/components/HelpCenter';
import { NotificationBell } from '@/components/Notifications';
import { UserMenu } from '@/components/UserMenu';

export function TopBar() {
  const { theme, setTheme } = useTheme();

  // Keep the server and initial hydration render identical. Read the browser
  // preference after mounting, and track changes while system mode is selected.
  const [systemDark, setSystemDark] = useState(false);
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const update = () => setSystemDark(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  const isDark = theme === 'dark' || (theme === 'system' && systemDark);
  const toggleTheme = () => setTheme(isDark ? 'light' : 'dark');

  return (
    <nav className="navbar">
      <div className="navbar-container">
        <button
          className="nav-icon-btn"
          onClick={() => window.dispatchEvent(new Event('nimbus:toggle-nav'))}
          aria-label="Toggle menu"
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <path d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <CommandPalette />
        <div className="navbar-actions">
          <button
            className="nav-icon-btn"
            onClick={toggleTheme}
            aria-label="Toggle theme"
            title="Toggle theme"
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path
                d={
                  isDark
                    ? 'M12 4V2m0 20v-2M4 12H2m20 0h-2M6 6 4.500 4.500M19.500 19.500 18 18M6 18l-1.500 1.500M19.500 4.500 18 6M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z'
                    : 'M20 14.500A8 8 0 0 1 9.500 4 8 8 0 1 0 20 14.500Z'
                }
              />
            </svg>
          </button>
          <HelpCenter />
          <NotificationBell />
          <UserMenu />
        </div>
      </div>
    </nav>
  );
}
