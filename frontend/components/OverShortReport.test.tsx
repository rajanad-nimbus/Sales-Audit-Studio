import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { OverShortReport } from '@/components/OverShortReport';
import { apiFailure, calls, mockApi, tileValue } from '@/test/utils';

const REPORT = {
  truncated: false,
  rows: [
    {
      store_id: 'S2',
      declarations: 1,
      out_of_balance: 1,
      short: '0',
      over: '10.00',
      net_short: '-10.00',
    },
    {
      store_id: 'S1',
      declarations: 3,
      out_of_balance: 2,
      short: '5.00',
      over: '3.00',
      net_short: '2.00',
    },
  ],
  totals: { declarations: 4, short: '5.00', over: '13.00' },
};

describe('OverShortReport', () => {
  it('shows short and over as separate totals and per store', async () => {
    mockApi({ 'GET /api/cash-controls/over-short': REPORT });
    render(<OverShortReport />);

    expect(await screen.findByText('S2')).toBeInTheDocument();
    expect(tileValue('Short')).toHaveTextContent('5.00');
    expect(tileValue('Over')).toHaveTextContent('13.00');
    expect(tileValue('Declarations')).toHaveTextContent('4');
    expect(calls('get', '/api/cash-controls/over-short')[0][1]).toEqual({
      params: { group_by: 'store', start: undefined, end: undefined },
    });
  });

  it('regroups and applies a date range', async () => {
    mockApi({
      'GET /api/cash-controls/over-short': (config) => ({
        ...REPORT,
        rows: [
          config?.params?.group_by === 'register'
            ? { ...REPORT.rows[1], store_id: 'S1', register_id: 'R1' }
            : REPORT.rows[0],
        ],
      }),
    });
    render(<OverShortReport />);
    await screen.findByText('S2');

    await userEvent.click(screen.getByRole('button', { name: 'By register' }));
    expect(await screen.findByText('S1 · R1')).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('From'), '2026-10-01');
    await waitFor(() =>
      expect(calls('get', '/api/cash-controls/over-short').at(-1)?.[1]).toEqual({
        params: { group_by: 'register', start: '2026-10-01', end: undefined },
      })
    );
  });

  it('shows a placeholder for a till with no register or cashier recorded', async () => {
    mockApi({
      'GET /api/cash-controls/over-short': {
        ...REPORT,
        rows: [{ ...REPORT.rows[1], register_id: null }],
      },
    });
    render(<OverShortReport />);
    await userEvent.click(await screen.findByRole('button', { name: 'By register' }));
    expect(await screen.findByText('S1 · None')).toBeInTheDocument();
  });

  it('reports a failure to load', async () => {
    mockApi({ 'GET /api/cash-controls/over-short': apiFailure('nope', 500) });
    render(<OverShortReport />);
    expect(await screen.findByText('Could not load the over/short report.')).toBeInTheDocument();
  });
});
