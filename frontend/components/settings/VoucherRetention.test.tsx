import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { VoucherRetention } from './VoucherRetention';
import { ME, apiFailure, calls, renderAs } from '@/test/utils';

const PREVIEW = {
  tender_type: 'Voucher',
  purge_days: 30,
  eligible_records: 1,
  batch_size: 1,
  more_eligible: false,
  preview_token: 'a'.repeat(64),
  vouchers: [
    {
      voucher_number: 'CLOSED',
      store_id: 'S1',
      issued_on: '2026-01-01',
      finalized_on: '2026-02-01',
      status: 'Redeemed',
      amount: '25.00',
      currency: 'USD',
    },
  ],
};
const routes = (extra = {}) => ({
  'GET /api/vouchers/options': [{ tender_type: 'Voucher', purge_days: 30 }],
  'GET /api/vouchers/retention/preview': PREVIEW,
  'POST /api/vouchers/retention/run': {
    run_id: 'run-one',
    archived_records: 1,
    purged_records: 1,
    archive_hash: 'b'.repeat(64),
    more_eligible: false,
  },
  ...extra,
});
async function preview() {
  await userEvent.selectOptions(await screen.findByLabelText('Tender to retain'), 'Voucher');
  await userEvent.click(screen.getByRole('button', { name: 'Preview voucher batch' }));
  await screen.findByText('CLOSED');
}

describe('Voucher archive and purge', () => {
  it('requires a reviewed batch and explicit acknowledgment before sending a purge', async () => {
    await renderAs(<VoucherRetention />, ME('it'), routes());
    await preview();
    await userEvent.click(screen.getByRole('button', { name: 'Archive and purge…' }));
    const dialog = screen.getByRole('dialog', { name: 'Archive and purge vouchers' });
    const confirm = within(dialog).getByRole('button', { name: 'Confirm archive and purge' });
    expect(confirm).toBeDisabled();
    expect(calls('post', '/api/vouchers/retention/run')).toHaveLength(0);
    await userEvent.click(
      within(dialog).getByLabelText('Remove these vouchers after their archive is written')
    );
    await userEvent.click(confirm);
    await waitFor(() => expect(calls('post', '/api/vouchers/retention/run')).toHaveLength(1));
    expect(calls('post', '/api/vouchers/retention/run')[0][1]).toEqual({
      tender_type: 'Voucher',
      preview_token: PREVIEW.preview_token,
      confirm_purge: true,
    });
    expect(await screen.findByText(/Archived 1 voucher\(s\); purged 1/)).toBeInTheDocument();
  });

  it('archives without a purge when Archive batch is selected', async () => {
    await renderAs(<VoucherRetention />, ME('it'), routes());
    await preview();
    await userEvent.click(screen.getByRole('button', { name: 'Archive batch' }));
    await waitFor(() => expect(calls('post', '/api/vouchers/retention/run')).toHaveLength(1));
    expect(calls('post', '/api/vouchers/retention/run')[0][1]).toEqual({
      tender_type: 'Voucher',
      preview_token: PREVIEW.preview_token,
      confirm_purge: false,
    });
  });

  it('allows a fresh preview after a conflict and requires acknowledgment again', async () => {
    await renderAs(
      <VoucherRetention />,
      ME('it'),
      routes({
        'POST /api/vouchers/retention/run': apiFailure(
          'Voucher retention candidates or policy changed. Preview again.',
          409
        ),
      })
    );
    await preview();
    await userEvent.click(screen.getByRole('button', { name: 'Archive and purge…' }));
    const dialog = screen.getByRole('dialog', { name: 'Archive and purge vouchers' });
    await userEvent.click(
      within(dialog).getByLabelText('Remove these vouchers after their archive is written')
    );
    await userEvent.click(
      within(dialog).getByRole('button', { name: 'Confirm archive and purge' })
    );
    expect(
      await within(dialog).findByText(
        'Voucher retention candidates or policy changed. Preview again.'
      )
    ).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Preview again' }));
    await waitFor(() => expect(calls('get', '/api/vouchers/retention/preview')).toHaveLength(2));
    await screen.findByText('CLOSED');
    await userEvent.click(screen.getByRole('button', { name: 'Archive and purge…' }));
    expect(screen.getByRole('button', { name: 'Confirm archive and purge' })).toBeDisabled();
  });

  it('lets auditors preview without mutation controls', async () => {
    await renderAs(<VoucherRetention />, ME('auditor'), routes());
    await preview();
    expect(screen.queryByRole('button', { name: 'Archive batch' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Archive and purge…' })).not.toBeInTheDocument();
  });
});
