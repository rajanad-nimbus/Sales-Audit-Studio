import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { VoucherOptions } from './VoucherOptions';
import { ME, calls, renderAs } from '@/test/utils';

describe('Voucher escheat policy', () => {
  it('requires explicit valid aging and unique tender codes before saving', async () => {
    await renderAs(<VoucherOptions />, ME('it'), {
      'GET /api/vouchers/options': [],
      'PUT /api/vouchers/options': [
        {
          tender_type: 'GiftCard',
          escheat_days: 0,
          updated_by: 'it',
          updated_at: '2026-11-09T00:00:00Z',
        },
      ],
    });
    await screen.findByText('No tender aging rules configured.');
    await userEvent.click(screen.getByRole('button', { name: 'Add tender rule' }));
    const save = screen.getByRole('button', { name: 'Save voucher policy' });
    expect(save).toBeDisabled();
    const first = screen.getByRole('group', { name: 'Tender rule 1' });
    await userEvent.type(within(first).getByLabelText('Tender code'), 'GiftCard');
    await userEvent.type(within(first).getByLabelText(/^Escheat after days/), '0');
    expect(save).toBeEnabled();
    await userEvent.click(screen.getByRole('button', { name: 'Add tender rule' }));
    const second = screen.getByRole('group', { name: 'Tender rule 2' });
    await userEvent.type(within(second).getByLabelText('Tender code'), 'giftcard');
    await userEvent.type(within(second).getByLabelText(/^Escheat after days/), '30');
    expect(save).toBeDisabled();
    expect(screen.getByText('Each tender code can have only one rule.')).toBeInTheDocument();
    await userEvent.click(within(second).getByRole('button', { name: 'Remove tender rule 2' }));
    await userEvent.click(save);
    await waitFor(() => expect(calls('put', '/api/vouchers/options')).toHaveLength(1));
    expect(calls('put', '/api/vouchers/options')[0][1]).toEqual({
      options: [{ tender_type: 'GiftCard', escheat_days: 0, purge_days: null }],
    });
    await waitFor(() => expect(save).toBeDisabled());
  });

  it('lets an auditor inspect policies without mutation controls', async () => {
    await renderAs(<VoucherOptions />, ME('auditor'), {
      'GET /api/vouchers/options': [
        {
          tender_type: 'GiftCard',
          escheat_days: 30,
          updated_by: 'it',
          updated_at: '2026-11-09T00:00:00Z',
        },
      ],
    });
    expect(await screen.findByDisplayValue('GiftCard')).toBeDisabled();
    expect(screen.getByDisplayValue('30')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Save voucher policy' })).not.toBeInTheDocument();
  });
});
