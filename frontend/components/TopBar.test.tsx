import { renderToString } from 'react-dom/server';
import { hydrateRoot } from 'react-dom/client';
import { act, fireEvent } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ThemeProvider } from '@/lib/ThemeContext';
import { TopBar } from './TopBar';

vi.mock('./BusinessDate', () => ({ BusinessDate: () => null }));
vi.mock('./Notifications', () => ({ NotificationBell: () => null }));
vi.mock('./UserMenu', () => ({ UserMenu: () => null }));

it('hydrates in dark system mode, follows preference changes, and toggles the theme', async () => {
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn() });
  let dark = true;
  const listeners = new Set<() => void>();
  const remove = vi.fn((listener: () => void) => listeners.delete(listener));
  vi.stubGlobal('matchMedia', () => ({
    get matches() {
      return dark;
    },
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => remove(listener),
  }));
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  const tree = (
    <ThemeProvider>
      <TopBar />
    </ThemeProvider>
  );
  const container = document.createElement('div');
  container.innerHTML = renderToString(tree);
  document.body.append(container);
  const icon = () => container.querySelector('[aria-label="Toggle theme"] path')!.getAttribute('d');
  const serverIcon = icon();
  const root = hydrateRoot(container, tree);
  try {
    await act(async () => {});
    expect(error).not.toHaveBeenCalled();
    expect(icon()).not.toBe(serverIcon);
    act(() => {
      dark = false;
      listeners.forEach((listener) => listener());
    });
    expect(icon()).toBe(serverIcon);
    fireEvent.click(container.querySelector('[aria-label="Toggle theme"]')!);
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(icon()).not.toBe(serverIcon);
  } finally {
    act(() => root.unmount());
    container.remove();
    document.documentElement.removeAttribute('data-theme');
  }
  expect(remove).toHaveBeenCalled();
});
