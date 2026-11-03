import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { AuditActivity } from '@/components/diagnostics/AuditActivity';
import { apiFailure, calls, mockApi, tileValue } from '@/test/utils';

const PATH = '/api/diagnostics/activity';
const ACTIVITY = {
  window: {
    events: 5000,
    from_seq: 1,
    to_seq: 5000,
    from_time: '2026-10-09T08:00:00Z',
    to_time: '2026-10-10T12:00:00Z',
  },
  by_actor: [
    { actor: 'analyst.one', events: 4000, sensitive: 0, last_at: '2026-10-10T11:00:00Z' },
    { actor: 'finance', events: 1000, sensitive: 3, last_at: '2026-10-10T12:00:00Z' },
  ],
  by_type: [
    { event_type: 'CASE_NOTE_ADDED', events: 4000 },
    { event_type: 'PERIOD_CLOSED', events: 2 },
  ],
  sensitive: [
    {
      seq: 4999,
      created_at: '2026-10-10T12:00:00Z',
      actor: 'finance',
      event_type: 'PERIOD_CLOSED',
      description: 'Closed through 2026-09-30',
    },
    {
      seq: 4990,
      created_at: '2026-10-10T11:30:00Z',
      actor: 'approver',
      event_type: 'TOTAL_DEFINITION_ACTIVATED',
      description: 'Activated Sales r2',
    },
  ],
};

describe('AuditActivity', () => {
  it('lists the important changes with who made them and what they did', async () => {
    mockApi({ [`GET ${PATH}`]: ACTIVITY });
    render(<AuditActivity />);

    const row = (await screen.findByText('Closed through 2026-09-30')).closest('tr') as HTMLElement;
    expect(within(row).getByText('finance')).toBeInTheDocument();
    expect(within(row).getByText('period closed')).toBeInTheDocument();
    expect(screen.getByText('Activated Sales r2')).toBeInTheDocument();
    expect(screen.getByText('total definition activated')).toBeInTheDocument();
  });

  it('summarizes the window and breaks activity down by person and by kind', async () => {
    mockApi({ [`GET ${PATH}`]: ACTIVITY });
    render(<AuditActivity />);
    await screen.findByText('Closed through 2026-09-30');

    expect(tileValue('Events read')).toHaveTextContent('5,000');
    expect(tileValue('People')).toHaveTextContent('2');
    expect(tileValue('Important changes')).toHaveTextContent('3');
    const person = screen.getByText('analyst.one').closest('tr') as HTMLElement;
    expect(within(person).getByText('4000')).toBeInTheDocument();
    expect(screen.getByText('case note added')).toBeInTheDocument();
  });

  it('reads a wider window when asked, so the screen stays fast by default', async () => {
    mockApi({ [`GET ${PATH}`]: ACTIVITY });
    render(<AuditActivity />);
    await screen.findByText('Closed through 2026-09-30');
    expect(calls('get', PATH)[0][1]).toEqual({ params: { events: '5000' } });

    await userEvent.click(screen.getByRole('button', { name: 'Latest 100,000' }));
    await waitFor(() =>
      expect(calls('get', PATH).at(-1)?.[1]).toEqual({ params: { events: '100000' } })
    );
  });

  it('says so when nothing important happened in the window', async () => {
    mockApi({ [`GET ${PATH}`]: { ...ACTIVITY, sensitive: [] } });
    render(<AuditActivity />);
    expect(await screen.findByText('No important changes in this window')).toBeInTheDocument();
  });

  it('shows why the activity could not be loaded', async () => {
    mockApi({ [`GET ${PATH}`]: apiFailure('Forbidden', 403) });
    render(<AuditActivity />);
    expect(await screen.findByText('Forbidden')).toBeInTheDocument();
  });
});
