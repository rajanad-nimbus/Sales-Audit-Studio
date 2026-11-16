import { money } from '@/lib/api';
import type { BadgeVariant } from '@/components/ui';

export type Adjustment = {
  id: string;
  transaction_id: string | null;
  adjustment_type: string;
  proposed_values: Record<string, unknown>;
  rationale: string;
  status: string;
  proposed_by: string;
  reviewed_by: string | null;
  created_at: string;
};
export type Kind =
  | 'Amount'
  | 'Tender'
  | 'Tax'
  | 'TaxDetail'
  | 'TaxLine'
  | 'Discount'
  | 'Reference'
  | 'Item'
  | 'Customer'
  | 'Employee'
  | 'CustomerAttribute'
  | 'UserReference'
  | 'MissingTransaction';
export const KINDS: { key: Kind; title: string; help: string }[] = [
  {
    key: 'Amount',
    title: 'Amount',
    help: 'Correct a signed amount with a positive or negative adjustment.',
  },
  {
    key: 'Tender',
    title: 'Tender',
    help: 'Reclassify the tender without changing the source record.',
  },
  { key: 'Tax', title: 'Tax', help: 'Record a corrected tax amount or a tax adjustment.' },
  {
    key: 'TaxDetail',
    title: 'Tax detail',
    help: 'Correct one tax code, name, rate, taxable amount, and tax amount.',
  },
  {
    key: 'TaxLine',
    title: 'Line tax',
    help: 'Correct the tax code, rate, and tax amount recorded for one item line.',
  },
  {
    key: 'Discount',
    title: 'Discount',
    help: 'Add a missing discount, correct a recorded discount, or remove an incorrect one with Finance approval.',
  },
  {
    key: 'Reference',
    title: 'Reference',
    help: 'Correct a payment, settlement or external reference.',
  },
  {
    key: 'Item',
    title: 'Item',
    help: 'Replace a specific transaction item with the code supported by receipt or source evidence.',
  },
  {
    key: 'Customer',
    title: 'Customer',
    help: 'Correct a customer identifier or its identification type using transaction evidence.',
  },
  {
    key: 'Employee',
    title: 'Employee details',
    help: 'Correct the cashier or salesperson recorded on a transaction, with Finance approval.',
  },
  {
    key: 'CustomerAttribute',
    title: 'Customer attribute',
    help: 'Add, correct, or clear one retailer-defined customer attribute with approval.',
  },
  {
    key: 'UserReference',
    title: 'Retailer reference',
    help: 'Correct one of the four retailer-defined transaction reference fields.',
  },
  {
    key: 'MissingTransaction',
    title: 'Missing transaction',
    help: 'Create a governed manual record when a source transaction is absent.',
  },
];

export const statusVariant = (s: string): BadgeVariant =>
  s === 'Approved'
    ? 'success'
    : s === 'Rejected'
      ? 'error'
      : s === 'Proposed'
        ? 'warning'
        : 'secondary';
export const statusLabel = (s: string) => (s === 'Proposed' ? 'Awaiting review' : s);
export const pretty = (v: string) =>
  v
    .replaceAll('_', ' ')
    .replace(/([A-Z])/g, ' $1')
    .trim();

const signed = (n: number) => `${n > 0 ? '+' : ''}${money(n)}`;

/** The proposal in words, so a reviewer does not have to read JSON. */
export function describe(type: string, v: Record<string, unknown>): string {
  const str = (x: unknown) => String(x ?? '');
  switch (type) {
    case 'Amount':
      return `Amount ${signed(Number(v.amount_delta))}`;
    case 'Tax':
      return `Tax ${signed(Number(v.tax_delta))}`;
    case 'TaxDetail':
      return `Tax row ${str(v.tax_code)} · ${str(v.tax_name)} · ${Number(v.rate) * 100}% · ${money(Number(v.tax_amount))}`;
    case 'TaxLine':
      return `Line ${str(v.line_no)} tax ${str(v.tax_code)} · ${Number(v.rate) * 100}% · ${money(Number(v.tax_amount))}`;
    case 'Discount':
      if (v.operation === 'delete')
        return `Remove discount ${str(v.before_code) || str(v.before_kind)} · ${str(v.before_kind)} · ${money(Number(v.before_amount))}`;
      if (v.operation === 'add')
        return `Add discount ${str(v.code) || str(v.kind)} · ${str(v.kind)} · ${money(Number(v.amount))}`;
      return `Correct discount ${str(v.code) || str(v.kind)} · ${str(v.kind)} · ${money(Number(v.amount))}`;
    case 'Tender':
      return `Tender becomes ${str(v.tender_type)}`;
    case 'Reference':
      return `Reference becomes ${str(v.reference)}`;
    case 'Item':
      return v.line_no
        ? `Item line ${str(v.line_no)} becomes ${str(v.item)}`
        : `Item becomes ${str(v.item)}`;
    case 'Customer':
      return `Customer information corrected${v.customer_identification_type ? ` · ${str(v.customer_identification_type)}` : ''}`;
    case 'Employee':
      return `Employee details corrected${v.cashier_id !== undefined ? ` · cashier ${str(v.cashier_id) || 'cleared'}` : ''}${v.salesperson_id !== undefined ? ` · salesperson ${str(v.salesperson_id) || 'cleared'}` : ''}`;
    case 'CustomerAttribute':
      return `Customer attribute ${str(v.attribute_type)} ${v.value ? `becomes ${str(v.value)}` : 'cleared'}`;
    case 'UserReference':
      return `${str(v.reference_field).replace('_', ' ')} ${v.value ? `becomes ${str(v.value)}` : 'cleared'}`;
    case 'MissingTransaction':
      return `Add ${str(v.transaction_type)} ${money(Number(v.amount))} · ${str(v.tender_type)} · ${str(v.store_id)} on ${str(v.business_date)}`;
    default:
      return JSON.stringify(v);
  }
}
