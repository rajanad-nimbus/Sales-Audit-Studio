import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { Sidebar } from './Sidebar';

vi.mock('@/lib/sync', () => ({ useLive: () => {} }));
vi.mock('./UserMenu', () => ({
  useMe: () => ({
    me: {
      screens: [
        {
          key: 'cases',
          path: '/cases',
          label: 'Cases',
          group: 'Workspace',
          allowed: true,
          roles: [],
        },
      ],
    },
  }),
}));

it('clears a menu search before Escape dismisses the mobile menu and restores focus', () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true }));
  render(
    <>
      <button aria-label="Toggle menu" />
      <Sidebar />
    </>
  );
  act(() => {
    window.dispatchEvent(new Event('nimbus:toggle-nav'));
  });
  const search = screen.getByRole('textbox', { name: 'Quick search menus' });
  expect(search).toHaveFocus();
  fireEvent.change(search, { target: { value: 'missing' } });
  expect(screen.getByRole('status')).toHaveTextContent('No pages match');
  expect(screen.queryByRole('link', { name: 'Cases' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Clear menu search' }));
  expect(search).toHaveFocus();
  expect(screen.getByRole('link', { name: 'Cases' })).toBeInTheDocument();
  fireEvent.change(search, { target: { value: 'missing' } });
  fireEvent.keyDown(search, { key: 'Escape' });
  expect(search).toHaveValue('');
  expect(document.querySelector('.sidebar')).toHaveClass('open');
  fireEvent.keyDown(search, { key: 'Escape' });
  expect(document.querySelector('.sidebar')).not.toHaveClass('open');
  expect(screen.getByRole('button', { name: 'Toggle menu' })).toHaveFocus();
});
