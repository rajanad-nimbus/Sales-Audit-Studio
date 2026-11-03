import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EventContext } from '@/components/registerEvents/EventContext';
import { apiFailure, mockApi } from '@/test/utils';

const PATH = '/api/register-events/e2/context';
const item = (id: string, time: string, type: string, over: object = {}) => ({
  id,
  event_time: `2026-10-01T${time}:00Z`,
  event_type: type,
  sub_type: null,
  transaction_number: '7',
  amount: null,
  currency: 'USD',
  audit_code: null,
  cashier_id: 'amy',
  ...over,
});
const context = (over: object = {}) => ({
  event_id: 'e2',
  store_id: 'S1',
  register_id: 'R3',
  business_date: '2026-10-01',
  earlier_not_shown: false,
  later_not_shown: false,
  events: [
    item('e1', '09:00', 'open'),
    item('e2', '09:05', 'void', {
      transaction_number: '0',
      audit_code: 'VOID_NO_ORIG',
      amount: '12.50',
    }),
    item('e3', '09:10', 'no_sale', { sub_type: 'drawer' }),
  ],
  ...over,
});
const label = (t: string) => t.toUpperCase();

describe('EventContext', () => {
  it('shows the events around this one in order, marking which one it is', async () => {
    mockApi({ [`GET ${PATH}`]: context() });
    render(<EventContext id="e2" typeLabel={label} />);

    expect(await screen.findByText('S1 · R3 · 2026-10-01')).toBeInTheDocument();
    const rows = screen.getAllByRole('listitem');
    expect(
      rows.map((r) => within(r).getByText(/^(▶ )?(OPEN|VOID|NO_SALE)( · .*)?$/).textContent)
    ).toEqual(['OPEN', '▶ VOID', 'NO_SALE · drawer']);
    expect(rows[1]).toHaveAttribute('aria-current', 'true');
    expect(rows[0]).not.toHaveAttribute('aria-current');
    expect(within(rows[1]).getByText('VOID_NO_ORIG')).toBeInTheDocument();
    expect(within(rows[1]).getByText(/No transaction number · \$12\.50 · amy/)).toBeInTheDocument();
  });

  it('says when there are more events than the few shown', async () => {
    mockApi({ [`GET ${PATH}`]: context({ earlier_not_shown: true, later_not_shown: true }) });
    render(<EventContext id="e2" typeLabel={label} />);
    expect(
      await screen.findByText('Earlier events on this register are not shown.')
    ).toBeInTheDocument();
    expect(screen.getByText('Later events on this register are not shown.')).toBeInTheDocument();
  });

  it('names a day with no register recorded', async () => {
    mockApi({ [`GET ${PATH}`]: context({ register_id: null }) });
    render(<EventContext id="e2" typeLabel={label} />);
    expect(await screen.findByText('S1 · no register · 2026-10-01')).toBeInTheDocument();
  });

  it('shows why it could not be loaded', async () => {
    mockApi({ [`GET ${PATH}`]: apiFailure('Register event not found', 404) });
    render(<EventContext id="e2" typeLabel={label} />);
    expect(await screen.findByText('Register event not found')).toBeInTheDocument();
  });
});
