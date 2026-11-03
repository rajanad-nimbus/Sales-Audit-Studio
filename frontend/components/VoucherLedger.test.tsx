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
  status: 'Issued',
  redeemed_store_id: null,
  redeemed_on: null,
  ...over,
});

const ROWS = [
  voucher({}),
  voucher({ voucher_number: 'OLD', expires_on: '2026-01-01', status: 'Expired' }),
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
const ESCHEAT_ACTION = /^Escheat (\d+ )?expired$/;

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
      expires_on: '2027-01-31',
    });
  });

  it('redeems only after the redeeming store is chosen', async () => {
    await renderAs(
      <VoucherLedger />,
      ME('store_manager'),
      routes({ 'POST /api/vouchers/V1/redeem': {} })
    );
    await screen.findByText('V1');
    const redeem = screen.getByRole('button', { name: 'Redeem' });
    expect(redeem).toBeDisabled();

    await userEvent.selectOptions(screen.getByLabelText('Redeem at…'), 'S2');
    await userEvent.click(redeem);
    await waitFor(() => expect(calls('post', '/api/vouchers/V1/redeem')).toHaveLength(1));
    expect(calls('post', '/api/vouchers/V1/redeem')[0][1]).toEqual({ store_id: 'S2' });
  });

  it('shows the server reason when a redemption is refused', async () => {
    await renderAs(
      <VoucherLedger />,
      ME('finance'),
      routes({ 'POST /api/vouchers/V1/redeem': apiFailure('Already redeemed at S2', 409) })
    );
    await screen.findByText('V1');
    await userEvent.selectOptions(screen.getByLabelText('Redeem at…'), 'S1');
    await userEvent.click(screen.getByRole('button', { name: 'Redeem' }));
    expect(await screen.findByText('Already redeemed at S2')).toBeInTheDocument();
  });

  it('does not offer escheat to a role other than Finance', async () => {
    await renderAs(<VoucherLedger />, ME('it'), routes());
    await screen.findByText('V1');
    expect(screen.queryByRole('button', { name: ESCHEAT_ACTION })).not.toBeInTheDocument();
  });

  it('lets Finance escheat the expired vouchers shown', async () => {
    await renderAs(<VoucherLedger />, ME('finance'), routes({ 'POST /api/vouchers/escheat': {} }));
    await screen.findByText('V1');

    await userEvent.click(screen.getByRole('button', { name: ESCHEAT_ACTION }));
    await waitFor(() => expect(calls('post', '/api/vouchers/escheat')).toHaveLength(1));
    expect(calls('post', '/api/vouchers/escheat')[0][1]).toEqual({ voucher_numbers: ['OLD'] });
    const row = screen.getByText('OLD').closest('tr') as HTMLElement;
    expect(within(row).getByText('Expired')).toBeInTheDocument();
  });

  it('disables escheat when nothing has expired', async () => {
    await renderAs(<VoucherLedger />, ME('finance'), routes({ 'GET /api/vouchers': [ROWS[0]] }));
    await screen.findByText('V1');
    expect(screen.getByRole('button', { name: ESCHEAT_ACTION })).toBeDisabled();
  });
});
