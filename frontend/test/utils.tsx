import { render, screen, waitFor } from '@testing-library/react';
import type { ReactElement } from 'react';
import { expect, vi } from 'vitest';
import { api } from '@/lib/api';
import { ToastProvider } from '@/components/Feedback';
import { MeProvider } from '@/components/MeProvider';
import type { Me } from '@/components/MeProvider';

interface RequestConfig {
  params?: Record<string, unknown>;
  data?: unknown;
  responseType?: string;
}
type Handler = ((config?: RequestConfig) => unknown) | object | string | number | boolean | null;
export type Routes = Record<string, Handler>;

const mocked = api as unknown as Record<
  'get' | 'post' | 'put' | 'delete',
  ReturnType<typeof vi.fn>
>;

/** A response body that also carries headers, for the few screens that read them. */
export class WithHeaders {
  constructor(
    readonly data: unknown,
    readonly headers: Record<string, string>
  ) {}
}

/** The recorded calls to one endpoint, e.g. calls('post', '/api/vouchers'). */
export const calls = (method: 'get' | 'post' | 'put' | 'delete', path: string) =>
  mocked[method].mock.calls.filter(([url]) => url === path);

/**
 * Answer API calls from a table of `'GET /path'` to a response body (or a function of the request).
 * A route that is not listed fails the test loudly rather than returning undefined.
 */
export function mockApi(routes: Routes) {
  for (const method of ['get', 'post', 'put', 'delete'] as const) {
    mocked[method].mockImplementation(async (url: string, ...rest: unknown[]) => {
      const key = `${method.toUpperCase()} ${url}`;
      if (!(key in routes)) throw new Error(`Unmocked API call: ${key}`);
      const handler = routes[key];
      // axios: get(url, config) and post(url, body, config)
      const config =
        method === 'get' || method === 'delete'
          ? (rest[0] as { params?: Record<string, unknown> } | undefined)
          : { data: rest[0], ...((rest[1] as object) ?? {}) };
      const body = typeof handler === 'function' ? await handler(config) : handler;
      if (body instanceof Error) throw body;
      if (body instanceof WithHeaders) return { data: body.data, headers: body.headers };
      return { data: body };
    });
  }
}

/** A failed request, shaped like the axios error the screens read `detail` from. */
export const apiFailure = (detail: string, status = 422) =>
  Object.assign(new Error(detail), { response: { status, data: { detail } } });

export const ME = (role: string, user = 'tester', extra: Partial<Me> = {}): Me => ({
  user,
  role,
  screens: [],
  stores: null,
  ...extra,
});

/** Render inside the providers the app supplies, signed in as `me`. */
export async function renderAs(
  ui: ReactElement,
  me: Me | null = ME('finance'),
  routes: Routes = {}
) {
  mockApi({ 'GET /api/me': me ?? apiFailure('nope', 401), ...routes });
  const view = render(
    <MeProvider>
      <ToastProvider>{ui}</ToastProvider>
    </MeProvider>
  );
  if (me) await waitFor(() => expect(calls('get', '/api/me')).toHaveLength(1));
  return view;
}

export const text = (value: string | RegExp) => screen.findByText(value);

/** The value shown in the metric tile with this label, ignoring table headers that share the word. */
export function tileValue(label: string): HTMLElement {
  const tile = screen
    .getAllByText(label)
    .find((el) => el.parentElement?.classList.contains('cc-kpi'));
  if (!tile) throw new Error(`No metric tile labelled "${label}"`);
  return tile.nextElementSibling as HTMLElement;
}
