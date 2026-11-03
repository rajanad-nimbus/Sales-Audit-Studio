import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TransactionBrief } from '@/components/transactions/TransactionBrief';
import { apiFailure, calls, mockApi } from '@/test/utils';

const PATH = '/api/transactions/t1/brief';
const brief = (over: object = {}) => ({
  id: 't1',
  currency: 'USD',
  detail_status: 'Balanced',
  original_reference: null,
  return_reason: null,
  transaction_number: '42',
  line_count: 2,
  lines: [
    {
      line_no: 1,
      item_code: 'SKU-1',
      description: 'Rice',
      quantity: '2',
      unit_price: '3.00',
      gross_amount: '6.00',
    },
    {
      line_no: 2,
      item_code: 'SKU-2',
      description: null,
      quantity: '1',
      unit_price: '4.00',
      gross_amount: '4.00',
    },
  ],
  tenders: [
    { tender_type: 'Card', amount: '8.00', reference: null },
    { tender_type: 'Voucher', amount: '2.00', reference: 'GC-1' },
  ],
  taxes: [{ tax_code: 'VAT', tax_name: 'VAT 10%', rate: '0.1', tax_amount: '1.00' }],
  discount_count: 1,
  corrections: 2,
  ...over,
});

describe('TransactionBrief', () => {
  it('shows how it was paid, the lines and the tax, and links to the full transaction', async () => {
    mockApi({ [`GET ${PATH}`]: brief() });
    render(<TransactionBrief id="t1" />);

    expect(await screen.findByText('Card: $8.00')).toBeInTheDocument();
    expect(screen.getByText('Voucher: $2.00 · GC-1')).toBeInTheDocument();
    expect(screen.getByText('SKU-1 · Rice · 2 × $3.00 = $6.00')).toBeInTheDocument();
    expect(screen.getByText('SKU-2 · 1 × $4.00 = $4.00')).toBeInTheDocument(); // no description
    expect(screen.getByText('2 lines')).toBeInTheDocument();
    expect(screen.getByText('VAT 10%: $1.00')).toBeInTheDocument();
    expect(screen.getByText('1 discount · 2 corrections')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the transaction' })).toHaveAttribute(
      'href',
      '/transactions/t1'
    );
    expect(calls('get', PATH)).toHaveLength(1);
  });

  it('shows only the first few lines of a big basket and says how many there are', async () => {
    mockApi({ [`GET ${PATH}`]: brief({ line_count: 312 }) });
    render(<TransactionBrief id="t1" />);
    expect(await screen.findByText('312 lines')).toBeInTheDocument();
    expect(
      screen.getByText('Showing the first 2; the rest are on the transaction.')
    ).toBeInTheDocument();
  });

  it('flags an unbalanced transaction and names what a return is a return of', async () => {
    mockApi({
      [`GET ${PATH}`]: brief({
        detail_status: 'Unbalanced',
        original_reference: 'POS-9',
        return_reason: 'Damaged',
      }),
    });
    render(<TransactionBrief id="t1" />);
    expect(await screen.findByText('Unbalanced')).toBeInTheDocument();
    expect(screen.getByText(/return of POS-9 \(Damaged\)/)).toBeInTheDocument();
  });

  it('copes with a transaction that has no detail at all', async () => {
    mockApi({
      [`GET ${PATH}`]: brief({
        line_count: 0,
        lines: [],
        tenders: [],
        taxes: [],
        discount_count: 0,
        corrections: 0,
      }),
    });
    render(<TransactionBrief id="t1" />);
    expect(await screen.findByText('0 discounts · 0 corrections')).toBeInTheDocument();
    expect(screen.queryByText('Paid with')).not.toBeInTheDocument();
    expect(screen.queryByText(/lines?$/)).not.toBeInTheDocument();
  });

  it('shows why it could not be loaded', async () => {
    mockApi({ [`GET ${PATH}`]: apiFailure('You are not assigned to store S1.', 403) });
    render(<TransactionBrief id="t1" />);
    expect(await screen.findByText('You are not assigned to store S1.')).toBeInTheDocument();
  });
});
