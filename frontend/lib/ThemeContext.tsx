'use client';

import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { Theme } from './theme';

interface ThemeContextType {
  theme: Theme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>('system');
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    const stored = localStorage.getItem('theme') as Theme | null;
    if (stored) {
      setThemeState(stored);
    }
  }, []);

  useEffect(() => {
    if (!mounted) return;

    const root = document.documentElement;
    localStorage.setItem('theme', theme);

    if (theme === 'system') {
      root.removeAttribute('data-theme');
    } else {
      root.setAttribute('data-theme', theme);
    }
  }, [theme, mounted]);

  useEffect(() => {
    const applyPreferenceTheme = (event: Event) => {
      const value = (event as CustomEvent<Theme>).detail;
      if (value === 'system' || value === 'light' || value === 'dark') setThemeState(value);
    };
    window.addEventListener('nimbus:preference-theme', applyPreferenceTheme);
    return () => window.removeEventListener('nimbus:preference-theme', applyPreferenceTheme);
  }, []);

  const setTheme = useCallback((newTheme: Theme) => {
    setThemeState(newTheme);
  }, []);

  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error('useTheme must be used within ThemeProvider');
  }
  return context;
}
