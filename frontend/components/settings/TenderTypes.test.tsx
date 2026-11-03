import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { TenderTypes } from '@/components/settings/TenderTypes';
import { ME, apiFailure, calls, renderAs } from '@/test/utils';

const LISTING = {
  known_codes: ['Card', 'Cash', 'Voucher'],
  groups: ['Cash', 'Card', 'Voucher', 'Other'],
  items: [
    {
      id: 't1',
      code: 'Cash',
      name: 'Physical cash',
      active: true,
      tender_group: 'Cash',
      requires_reference: false,
      max_amount: '500.00',
      tender_lines: 42,
    },
  ],
};

describe('TenderTypes', () => {
  it('lists each tender with its group, reference rule, limit and use', async () => {
    await renderAs(<TenderTypes />, ME('analyst'), { 'GET /api/tender-types': LISTING });

    const row = (await screen.findByText('Cash · Physical cash')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Optional')).toBeInTheDocument();
    expect(within(row).getByText('$500.00')).toBeInTheDocument();
    expect(within(row).getByText('42')).toBeInTheDocument();
    expect(within(row).getByText('Active')).toBeInTheDocument();
  });

  it('is read-only for a role that cannot change tenders', async () => {
    await renderAs(<TenderTypes />, ME('analyst'), { 'GET /api/tender-types': LISTING });
    await screen.findByText('Cash · Physical cash');
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
    expect(screen.queryByText('Add a tender type')).not.toBeInTheDocument();
  });

  it('offers only the codes the feed can send that are not yet defined', async () => {
    await renderAs(<TenderTypes />, ME('finance'), { 'GET /api/tender-types': LISTING });
    await screen.findByText('Add a tender type');

    const choices = within(screen.getByLabelText(/^Tender/))
      .getAllByRole('option')
      .map((o) => o.textContent);
    expect(choices).toEqual(['Choose…', 'Card', 'Voucher']);
  });

  it('adds a tender with its rules', async () => {
    await renderAs(<TenderTypes />, ME('finance'), {
      'GET /api/tender-types': LISTING,
      'POST /api/tender-types': {},
    });
    await screen.findByText('Add a tender type');

    await userEvent.selectOptions(screen.getByLabelText(/^Tender/), 'Voucher');
    await userEvent.selectOptions(screen.getByLabelText('Group'), 'Voucher');
    await userEvent.type(screen.getByLabelText(/Limit per payment/), '250');
    await userEvent.click(screen.getByLabelText('Requires a reference'));
    await userEvent.click(screen.getByRole('button', { name: 'Add tender type' }));

    await waitFor(() => expect(calls('post', '/api/tender-types')).toHaveLength(1));
    expect(calls('post', '/api/tender-types')[0][1]).toEqual({
      code: 'Voucher',
      name: 'Voucher',
      active: true,
      tender_group: 'Voucher',
      requires_reference: true,
      max_amount: '250',
    });
  });

  it('edits a tender without letting its code change, and shows a server refusal', async () => {
    await renderAs(<TenderTypes />, ME('it'), {
      'GET /api/tender-types': LISTING,
      'PUT /api/tender-types/t1': apiFailure('Limit must be positive'),
    });
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }));

    expect(screen.getByLabelText(/^Tender/)).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Limit must be positive')).toBeInTheDocument();
    expect(calls('put', '/api/tender-types/t1')[0][1]).toMatchObject({
      code: 'Cash',
      max_amount: '500.00',
    });
  });
});
