import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { ItemLines } from '@/components/transactions/ItemLines';
import { calls, mockApi } from '@/test/utils';

const URL = '/api/transactions/t1/lines';
const line = (n: number) => ({
  line_no: n,
  item_code: `SKU-${n}`,
  description: `Item ${n}`,
  quantity: '1',
  unit_price: '2.00',
  original_unit_price: null,
  price_override_reason: null,
  source_attributes: null,
  gross_amount: '2.00',
  tax_code: null,
  tax_rate: '0',
  tax_amount: '0.00',
  discount_amount: '0',
  return_reason: null,
});
const COUNTS = { all: 1200, discounted: 120, price_override: 24, returned: 0 };
const params = () => calls('get', URL).at(-1)?.[1]?.params;

function open(lineCount = 1200) {
  mockApi({
    [`GET ${URL}`]: (config) => {
      const p = (config?.params ?? {}) as { offset: number; limit: number; only?: string };
      const total = p.only === 'price_override' ? 24 : 1200;
      return {
        items: Array.from({ length: Math.min(p.limit, total - p.offset) }, (_, i) =>
          line(p.offset + i + 1)
        ),
        total,
        counts: COUNTS,
      };
    },
  });
  render(<ItemLines transactionId="t1" lineCount={lineCount} />);
}

describe('Item lines of a large basket', () => {
  it('loads one page of lines, not the whole basket', async () => {
    open();
    expect(await screen.findByText('SKU-50')).toBeInTheDocument();
    expect(screen.queryByText('SKU-51')).not.toBeInTheDocument();
    expect(params()).toMatchObject({ offset: 0, limit: 50 });
    expect(screen.getByText(/1–50 of 1,200/)).toBeInTheDocument();
  });

  it('pages through to lines far past the first few hundred', async () => {
    open();
    await screen.findByText('SKU-1');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('SKU-51')).toBeInTheDocument();
    expect(params()).toMatchObject({ offset: 50, limit: 50 });
  });

  it('narrows to one kind of line with counts for the whole basket, hiding empty kinds', async () => {
    open();
    await screen.findByText('SKU-1');
    expect(screen.getByRole('button', { name: 'Discounted 120' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Returned/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Price overridden 24' }));
    await waitFor(() => expect(params()).toMatchObject({ only: 'price_override', offset: 0 }));
  });

  it('searches by item code or description on the server', async () => {
    open();
    await screen.findByText('SKU-1');
    await userEvent.type(screen.getByRole('searchbox', { name: 'Find an item line' }), 'rice');
    await waitFor(() => expect(params()).toMatchObject({ q: 'rice', offset: 0 }));
  });

  it('keeps a short basket plain: no search, filters or pager', async () => {
    open(3);
    await screen.findByText('SKU-1');
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Discounted/ })).not.toBeInTheDocument();
  });
});
