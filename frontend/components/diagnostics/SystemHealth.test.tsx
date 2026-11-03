import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { SystemHealth } from '@/components/diagnostics/SystemHealth';
import { apiFailure, calls, mockApi, tileValue } from '@/test/utils';

const check = (name: string, status: string, detail: string) => ({
  name,
  status,
  detail,
  value: null,
});
const HEALTH = {
  status: 'critical',
  checked_at: '2026-10-10T12:00:00Z',
  checks: [
    check('Database', 'ok', 'Answered a query in 3 ms'),
    check('Daily batch', 'critical', 'The most recent batch run failed'),
    check('Data feeds', 'warning', 'No recent data from Bank'),
    check('Failed exports', 'ok', 'No export has failed'),
  ],
};

describe('SystemHealth', () => {
  it('lists the worst checks first, each with what was found', async () => {
    mockApi({ 'GET /api/diagnostics/health': HEALTH });
    render(<SystemHealth />);

    expect(await screen.findByText('The most recent batch run failed')).toBeInTheDocument();
    const rows = screen.getAllByRole('row').slice(1);
    expect(rows.map((r) => within(r).getAllByRole('cell')[0].textContent)).toEqual([
      'Daily batch',
      'Data feeds',
      'Database',
      'Failed exports',
    ]);
    expect(within(rows[0]).getByText('Critical')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Warning')).toBeInTheDocument();
    expect(within(rows[2]).getByText('OK')).toBeInTheDocument();
  });

  it('counts passing, warning and critical checks and says how many need attention', async () => {
    mockApi({ 'GET /api/diagnostics/health': HEALTH });
    render(<SystemHealth />);
    await screen.findByText('The most recent batch run failed');

    expect(tileValue('Passing')).toHaveTextContent('2');
    expect(tileValue('Warnings')).toHaveTextContent('1');
    expect(tileValue('Critical')).toHaveTextContent('1');
    expect(screen.getByText(/2 of 4 checks need attention/)).toBeInTheDocument();
  });

  it('says so when everything passes', async () => {
    mockApi({
      'GET /api/diagnostics/health': {
        ...HEALTH,
        status: 'ok',
        checks: HEALTH.checks.filter((c) => c.status === 'ok'),
      },
    });
    render(<SystemHealth />);
    expect(await screen.findByText(/All 2 checks pass/)).toBeInTheDocument();
  });

  it('runs the checks again on request', async () => {
    mockApi({ 'GET /api/diagnostics/health': HEALTH });
    render(<SystemHealth />);
    await screen.findByText('The most recent batch run failed');
    expect(calls('get', '/api/diagnostics/health')).toHaveLength(1);

    await userEvent.click(screen.getByRole('button', { name: 'Check now' }));
    await screen.findByText('The most recent batch run failed');
    expect(calls('get', '/api/diagnostics/health')).toHaveLength(2);
  });

  it('shows why the checks could not run', async () => {
    mockApi({
      'GET /api/diagnostics/health': apiFailure('Diagnostics cover every store', 403),
    });
    render(<SystemHealth />);
    expect(await screen.findByText('Diagnostics cover every store')).toBeInTheDocument();
  });
});
