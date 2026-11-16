import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { VoucherRoutingPolicy } from './VoucherRoutingPolicy';
import { ME, apiFailure, calls, renderAs } from '@/test/utils';

const DEFAULT = {
  enabled: false,
  hq_location: null,
  income_partner: null,
  rules: [],
  revision: null,
  updated_by: null,
  updated_at: null,
};

describe('Voucher jurisdiction policy', () => {
  it('requires the fallback partner and saves explicit hierarchy permissions with its reviewed revision', async () => {
    await renderAs(<VoucherRoutingPolicy />, ME('it'), {
      'GET /api/vouchers/routing-policy': DEFAULT,
      'PUT /api/vouchers/routing-policy': {
        ...DEFAULT,
        enabled: true,
        income_partner: 'INCOME',
        revision: 'v2',
        rules: [{ country: 'US', state: 'CA', locations: ['Store'] }],
      },
    });
    await userEvent.click(await screen.findByLabelText('Route escheatment by jurisdiction'));
    const save = screen.getByRole('button', { name: 'Save routing policy' });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Income adjustment partner'), 'INCOME');
    await userEvent.click(screen.getByRole('button', { name: 'Add jurisdiction' }));
    const group = screen.getByRole('group', { name: 'Jurisdiction rule 1' });
    expect(save).toBeDisabled();
    await userEvent.type(within(group).getByLabelText('Country'), 'us');
    await userEvent.type(within(group).getByLabelText('State / province'), 'ca');
    await userEvent.click(within(group).getByLabelText('Store'));
    await userEvent.click(save);
    await waitFor(() => expect(calls('put', '/api/vouchers/routing-policy')).toHaveLength(1));
    expect(calls('put', '/api/vouchers/routing-policy')[0][1]).toEqual({
      enabled: true,
      hq_location: null,
      income_partner: 'INCOME',
      rules: [{ country: 'US', state: 'CA', locations: ['Store'] }],
      expected_revision: null,
    });
  });

  it('keeps edits on a concurrent-change rejection and allows the user to reload the saved policy', async () => {
    let loads = 0;
    await renderAs(<VoucherRoutingPolicy />, ME('it'), {
      'GET /api/vouchers/routing-policy': () =>
        ++loads === 1 ? DEFAULT : { ...DEFAULT, income_partner: 'SAVED', revision: 'v2' },
      'PUT /api/vouchers/routing-policy': apiFailure(
        'Voucher routing policy changed. Reload it before saving.',
        409
      ),
    });
    await userEvent.type(await screen.findByLabelText('Income adjustment partner'), 'DRAFT');
    await userEvent.click(screen.getByRole('button', { name: 'Save routing policy' }));
    expect(
      await screen.findByText('Voucher routing policy changed. Reload it before saving.')
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Income adjustment partner')).toHaveValue('DRAFT');
    await userEvent.click(screen.getByRole('button', { name: 'Reload saved policy' }));
    await waitFor(() =>
      expect(screen.getByLabelText('Income adjustment partner')).toHaveValue('SAVED')
    );
  });

  it('shows routing to auditors without configuration controls', async () => {
    await renderAs(<VoucherRoutingPolicy />, ME('auditor'), {
      'GET /api/vouchers/routing-policy': DEFAULT,
    });
    expect(await screen.findByLabelText('Route escheatment by jurisdiction')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Save routing policy' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add jurisdiction' })).not.toBeInTheDocument();
  });
});
