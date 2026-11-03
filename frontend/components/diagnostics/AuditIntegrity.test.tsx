import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { AuditIntegrity } from '@/components/diagnostics/AuditIntegrity';
import { apiFailure, calls, mockApi, tileValue } from '@/test/utils';

const PATH = '/api/diagnostics/integrity';
const win = (over: object = {}) => ({
  supported: true,
  from_seq: 1,
  checked: 100,
  intact: true,
  broken_events: 0,
  first_broken_seq: null,
  next_seq: null,
  done: true,
  head_seq: 100,
  head_hash: 'abc123',
  first_seq: 1,
  verified_at: '2026-10-10T12:00:00Z',
  note: 'Record the head hash with an external system.',
  ...over,
});
const params = (n: number) => calls('get', PATH)[n]?.[1]?.params;

describe('AuditIntegrity', () => {
  it('verifies the newest events on arrival and shows the head hash', async () => {
    mockApi({ [`GET ${PATH}`]: win() });
    render(<AuditIntegrity />);

    expect(await screen.findByText('The newest 100 events are intact.')).toBeInTheDocument();
    expect(screen.getByText('abc123')).toBeInTheDocument();
    expect(tileValue('Latest event')).toHaveTextContent('100');
    expect(params(0)).toBeUndefined();
  });

  it('says plainly when events do not match, and where it starts', async () => {
    mockApi({
      [`GET ${PATH}`]: win({ intact: false, broken_events: 2, first_broken_seq: 57 }),
    });
    render(<AuditIntegrity />);
    expect(
      await screen.findByText(/2 event\(s\) do not match their hash, starting at event 57/)
    ).toBeInTheDocument();
    expect(screen.getByText(/may have been altered/)).toBeInTheDocument();
  });

  it('explains when the chain cannot be checked here, and offers no full check', async () => {
    mockApi({
      [`GET ${PATH}`]: {
        supported: false,
        reason: 'The audit hash chain is computed by PostgreSQL.',
      },
    });
    render(<AuditIntegrity />);
    expect(await screen.findByText(/computed by PostgreSQL/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Verify the whole trail' })).toBeDisabled();
  });

  it('walks the whole trail window by window, following where each one ends', async () => {
    mockApi({
      [`GET ${PATH}`]: (config) => {
        const from = (config?.params as { from_seq?: number } | undefined)?.from_seq;
        if (from === undefined) return win();
        const windows: Record<number, object> = {
          0: win({ from_seq: 0, checked: 20000, next_seq: 20001, done: false, head_seq: 45000 }),
          20001: win({
            from_seq: 20001,
            checked: 20000,
            next_seq: 40001,
            done: false,
            head_seq: 45000,
          }),
          40001: win({
            from_seq: 40001,
            checked: 5000,
            next_seq: null,
            done: true,
            head_seq: 45000,
          }),
        };
        return windows[from];
      },
    });
    render(<AuditIntegrity />);
    await screen.findByText(/intact/);

    await userEvent.click(screen.getByRole('button', { name: 'Verify the whole trail' }));
    expect(await screen.findByText('All 45,000 events are intact.')).toBeInTheDocument();
    const scans = calls('get', PATH).filter(([, c]) => c?.params?.from_seq !== undefined);
    expect(scans.map(([, c]) => c?.params)).toEqual([
      { from_seq: 0, size: 20000 },
      { from_seq: 20001, size: 20000 },
      { from_seq: 40001, size: 20000 },
    ]);
  });

  it('reports a break found part-way through the whole-trail check', async () => {
    mockApi({
      [`GET ${PATH}`]: (config) => {
        const from = (config?.params as { from_seq?: number } | undefined)?.from_seq;
        if (from === undefined) return win();
        return from === 0
          ? win({ checked: 20000, next_seq: 20001, done: false, head_seq: 30000 })
          : win({
              checked: 10000,
              broken_events: 1,
              first_broken_seq: 25512,
              done: true,
              head_seq: 30000,
            });
      },
    });
    render(<AuditIntegrity />);
    await screen.findByText(/intact/);
    await userEvent.click(screen.getByRole('button', { name: 'Verify the whole trail' }));
    expect(
      await screen.findByText('1 event(s) do not match, starting at event 25512.')
    ).toBeInTheDocument();
  });

  it('can be stopped part-way and says how far it got', async () => {
    let resolveSecond: (v: object) => void = () => {};
    let calledSecond = false;
    mockApi({
      [`GET ${PATH}`]: (config) => {
        const from = (config?.params as { from_seq?: number } | undefined)?.from_seq;
        if (from === undefined) return win();
        if (from === 0)
          return win({ checked: 20000, next_seq: 20001, done: false, head_seq: 90000 });
        calledSecond = true;
        return new Promise((resolve) => (resolveSecond = resolve));
      },
    });
    render(<AuditIntegrity />);
    await screen.findByText(/intact/);
    await userEvent.click(screen.getByRole('button', { name: 'Verify the whole trail' }));
    await waitFor(() => expect(calledSecond).toBe(true));

    await userEvent.click(screen.getByRole('button', { name: 'Stop' }));
    resolveSecond(win({ checked: 20000, next_seq: 40001, done: false, head_seq: 90000 }));
    expect(
      await screen.findByText(/Stopped after 40,000 events, through #40,000 of #90,000/)
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Verify the whole trail' })).toBeEnabled();
  });

  it('shows when a window could not be checked', async () => {
    mockApi({ [`GET ${PATH}`]: apiFailure('Diagnostics cover every store', 403) });
    render(<AuditIntegrity />);
    expect(await screen.findByText('Diagnostics cover every store')).toBeInTheDocument();
  });
});
