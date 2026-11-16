import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { VoucherLedger } from '@/components/VoucherLedger';
import { ME, apiFailure, calls, renderAs, tileValue } from '@/test/utils';

vi.mock('@/components/StoreSelect', async () => ({
  StoreSelect: (await import('@/test/storeSelect')).FakeStoreSelect,
}));

const voucher = (over: object) => ({
  voucher_number: 'V1',
  issued_store_id: 'S1',
  issued_on: '2026-09-01',
  expires_on: '2027-09-01',
  amount: '25.00',
  currency: 'USD',
  tender_type: 'Voucher',
  eligible_on: '2027-09-02',
  escheat_eligible: false,
  eligibility_basis: 'Expiry date',
  status: 'Issued',
  redeemed_store_id: null,
  redeemed_on: null,
  ...over,
});

const ROWS = [
  voucher({}),
  voucher({
    voucher_number: 'OLD',
    expires_on: '2026-01-01',
    status: 'Expired',
    eligible_on: '2026-01-02',
    escheat_eligible: true,
  }),
  voucher({
    voucher_number: 'USED',
    status: 'Redeemed',
    redeemed_store_id: 'S2',
    redeemed_on: '2026-09-15',
  }),
];
const SUMMARY = {
  outstanding: { USD: { vouchers: 1, amount: '25.00' } },
  expired: { USD: { vouchers: 1, amount: '25.00' } },
};
// The status filter has an "Escheated" button too, so match the action button exactly.
const ESCHEAT_ACTION = /^Review (\d+ )?for escheat$/;

const routes = (extra = {}) => ({
  'GET /api/vouchers': ROWS,
  'GET /api/vouchers/summary': SUMMARY,
  ...extra,
});

describe('VoucherLedger', () => {
  it('shows the outstanding and expired liability and each voucher with where it was redeemed', async () => {
    await renderAs(<VoucherLedger />, ME('auditor'), routes());

    expect(await screen.findByText('V1')).toBeInTheDocument();
    expect(tileValue('Outstanding')).toHaveTextContent('$25.00 (1)');
    expect(tileValue('Expired, unclaimed')).toHaveTextContent('$25.00 (1)');
    expect(screen.getByText('S2 · 2026-09-15')).toBeInTheDocument();
  });

  it('is read-only for a role that cannot issue, redeem or escheat', async () => {
    await renderAs(<VoucherLedger />, ME('auditor'), routes());
    await screen.findByText('V1');

    expect(screen.queryByRole('button', { name: 'Issue' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Redeem' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: ESCHEAT_ACTION })).not.toBeInTheDocument();
  });

  it('filters by status and by search text on the server', async () => {
    await renderAs(<VoucherLedger />, ME('auditor'), routes());
    await screen.findByText('V1');

    await userEvent.click(screen.getByRole('button', { name: 'Expired' }));
    await waitFor(() =>
      expect(calls('get', '/api/vouchers').at(-1)?.[1]).toEqual({
        params: { status: 'Expired', q: undefined },
      })
    );
    await userEvent.type(screen.getByLabelText('Search vouchers'), 'OL');
    await waitFor(() =>
      expect(calls('get', '/api/vouchers').at(-1)?.[1]).toEqual({
        params: { status: 'Expired', q: 'OL' },
      })
    );
  });

  it('issues a voucher once the number, store and amount are given', async () => {
    await renderAs(<VoucherLedger />, ME('finance'), routes({ 'POST /api/vouchers': {} }));
    await screen.findByText('V1');
    const issue = screen.getByRole('button', { name: 'Issue' });
    expect(issue).toBeDisabled();

    await userEvent.type(screen.getByLabelText('Voucher number'), 'NEW-9');
    await userEvent.selectOptions(screen.getByLabelText('Issuing store'), 'S1');
    await userEvent.type(screen.getByLabelText('Amount'), '40');
    await userEvent.type(screen.getByLabelText('Expires'), '2027-01-31');
    await userEvent.click(issue);

    await waitFor(() => expect(calls('post', '/api/vouchers')).toHaveLength(1));
    expect(calls('post', '/api/vouchers')[0][1]).toEqual({
      voucher_number: 'NEW-9',
      store_id: 'S1',
      amount: '40',
      tender_type: 'Voucher',
      expires_on: '2027-01-31',
    });
  });

  it('redeems only after the redeeming store is chosen', async () => {
    await renderAs(
      <VoucherLedger />,
      ME('store_manager'),
      routes({
        'GET /api/vouchers/redeem-candidates': { items: [], truncated: false },
        'POST /api/vouchers/V1/redeem': {},
      })
    );
    await screen.findByText('V1');
    const redeem = screen.getByRole('button', { name: 'Redeem' });
    expect(redeem).toBeDisabled();

    await userEvent.selectOptions(screen.getByLabelText('Redeem at…'), 'S2');
    await userEvent.click(redeem);
    // Redeeming opens a dialog that looks for the matching POS transaction to link.
    await userEvent.click(await screen.findByRole('button', { name: 'Redeem without link' }));
    await waitFor(() => expect(calls('post', '/api/vouchers/V1/redeem')).toHaveLength(1));
    expect(calls('post', '/api/vouchers/V1/redeem')[0][1]).toEqual({ store_id: 'S2' });
  });

  it('shows the server reason when a redemption is refused', async () => {
    await renderAs(
      <VoucherLedger />,
      ME('finance'),
      routes({
        'GET /api/vouchers/redeem-candidates': { items: [], truncated: false },
        'POST /api/vouchers/V1/redeem': apiFailure('Already redeemed at S2', 409),
      })
    );
    await screen.findByText('V1');
    await userEvent.selectOptions(screen.getByLabelText('Redeem at…'), 'S1');
    await userEvent.click(screen.getByRole('button', { name: 'Redeem' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Redeem without link' }));
    expect(await screen.findByText('Already redeemed at S2')).toBeInTheDocument();
  });

  it('does not offer escheat to a role other than Finance', async () => {
    await renderAs(<VoucherLedger />, ME('it'), routes());
    await screen.findByText('V1');
    expect(screen.queryByRole('button', { name: ESCHEAT_ACTION })).not.toBeInTheDocument();
  });

  it('requires Finance to review the eligible vouchers shown before recording escheatment', async () => {
    await renderAs(<VoucherLedger />, ME('finance'), routes({ 'POST /api/vouchers/escheat': {} }));
    await screen.findByText('V1');

    await userEvent.click(screen.getByRole('button', { name: ESCHEAT_ACTION }));
    expect(calls('post', '/api/vouchers/escheat')).toHaveLength(0);
    const dialog = screen.getByRole('dialog', { name: 'Review voucher escheatment' });
    expect(within(dialog).getByText('OLD')).toBeInTheDocument();
    expect(within(dialog).queryByText('V1')).not.toBeInTheDocument();
    await userEvent.type(
      within(dialog).getByLabelText('Escheatment note'),
      'Reviewed for remittance'
    );
    await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm escheatment of 1' }));
    await waitFor(() => expect(calls('post', '/api/vouchers/escheat')).toHaveLength(1));
    expect(calls('post', '/api/vouchers/escheat')[0][1]).toEqual({
      voucher_numbers: ['OLD'],
      note: 'Reviewed for remittance',
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    const row = screen.getByText('OLD').closest('tr') as HTMLElement;
    expect(within(row).getByText('Expired')).toBeInTheDocument();
  });

  it('disables escheat when nothing has expired', async () => {
    await renderAs(<VoucherLedger />, ME('finance'), routes({ 'GET /api/vouchers': [ROWS[0]] }));
    await screen.findByText('V1');
    expect(screen.getByRole('button', { name: ESCHEAT_ACTION })).toBeDisabled();
  });

  it('uses server eligibility for non-expiring vouchers and displays rejection within the review', async () => {
    await renderAs(
      <VoucherLedger />,
      ME('finance'),
      routes({
        'GET /api/vouchers': [
          voucher({ expires_on: null, escheat_eligible: true, eligibility_basis: 'Tender policy' }),
        ],
        'POST /api/vouchers/escheat': apiFailure(
          'Policy changed; voucher is no longer eligible',
          422
        ),
      })
    );
    await screen.findByText('V1');
    await userEvent.click(screen.getByRole('button', { name: ESCHEAT_ACTION }));
    const dialog = screen.getByRole('dialog', { name: 'Review voucher escheatment' });
    expect(within(dialog).getByText('Tender policy')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm escheatment of 1' }));
    expect(
      await within(dialog).findByText('Policy changed; voucher is no longer eligible')
    ).toBeInTheDocument();
  });

  it('shows the routing destination and sends the exact reviewed preview token', async () => {
    const token = 'a'.repeat(64);
    await renderAs(
      <VoucherLedger />,
      ME('finance'),
      routes({
        'GET /api/vouchers': [
          voucher({
            escheat_eligible: true,
            routing_token: token,
            routing: {
              destination_type: 'Government',
              label: 'US / CA',
              location_of: 'Store',
              trace: [
                {
                  location_of: 'Store',
                  source: 'Voucher',
                  jurisdiction: { country: 'US', state: 'CA' },
                  outcome: 'Selected',
                },
              ],
            },
          }),
        ],
        'POST /api/vouchers/escheat': {},
      })
    );
    await screen.findByText('V1');
    await userEvent.click(screen.getByRole('button', { name: ESCHEAT_ACTION }));
    const dialog = screen.getByRole('dialog', { name: 'Review voucher escheatment' });
    expect(within(dialog).getByText('US / CA')).toBeInTheDocument();
    expect(within(dialog).getByText('From Store')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByText('Decision path'));
    expect(within(dialog).getByText(/Store: US \/ CA.*Selected/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm escheatment of 1' }));
    await waitFor(() => expect(calls('post', '/api/vouchers/escheat')).toHaveLength(1));
    expect(calls('post', '/api/vouchers/escheat')[0][1]).toEqual({
      voucher_numbers: ['V1'],
      note: null,
      routing_tokens: { V1: token },
    });
  });

  it('preserves jurisdiction edits when stale evidence is rejected', async () => {
    const original = { Store: { country: 'US', state: 'CA' } };
    await renderAs(
      <VoucherLedger />,
      ME('store_manager'),
      routes({
        'GET /api/vouchers': [voucher({ escheat_locations: original })],
        'PUT /api/vouchers/V1/jurisdictions': apiFailure(
          'Voucher jurisdictions changed. Reload before saving.',
          409
        ),
      })
    );
    await screen.findByText('V1');
    await userEvent.click(screen.getByRole('button', { name: 'Jurisdictions' }));
    const dialog = screen.getByRole('dialog', { name: 'Voucher jurisdictions: V1' });
    await userEvent.type(within(dialog).getByLabelText('Issuer country'), 'ca');
    await userEvent.type(within(dialog).getByLabelText('Issuer state / province'), 'on');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save jurisdictions' }));
    expect(
      await within(dialog).findByText('Voucher jurisdictions changed. Reload before saving.')
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Issuer state / province')).toHaveValue('ON');
    expect(calls('put', '/api/vouchers/V1/jurisdictions')[0][1]).toEqual({
      locations: { ...original, Issuer: { country: 'CA', state: 'ON' } },
      expected_locations: original,
    });
  });

  it('shows retired voucher identities with an archive reference and no redemption action', async () => {
    await renderAs(
      <VoucherLedger />,
      ME('finance'),
      routes({
        'GET /api/vouchers/retired': [
          {
            voucher_number: 'RETIRED-1',
            issued_store_id: 'S1',
            issued_on: '2020-01-01',
            tender_type: 'Voucher',
            amount: '25.00',
            currency: 'USD',
            final_status: 'Redeemed',
            finalized_on: '2020-01-02',
            retired_at: '2026-11-09T00:00:00Z',
            retention_run_id: 'archive-run',
          },
        ],
      })
    );
    await screen.findByText('V1');
    await userEvent.click(screen.getByRole('button', { name: 'Retired' }));
    expect(await screen.findByText('RETIRED-1')).toBeInTheDocument();
    expect(screen.getByText('archive-run')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Redeem' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: ESCHEAT_ACTION })).toBeDisabled();
  });
});
