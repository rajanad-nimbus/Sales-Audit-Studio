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
export type Kind = 'Amount' | 'Tender' | 'Tax' | 'Reference' | 'Item' | 'MissingTransaction';
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
    key: 'Reference',
    title: 'Reference',
    help: 'Correct a payment, settlement or external reference.',
  },
  {
    key: 'Item',
    title: 'Item',
    help: 'Correct item detail supported by a receipt or source evidence.',
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
    case 'Tender':
      return `Tender becomes ${str(v.tender_type)}`;
    case 'Reference':
      return `Reference becomes ${str(v.reference)}`;
    case 'Item':
      return `Item becomes ${str(v.item)}`;
    case 'MissingTransaction':
      return `Add ${str(v.transaction_type)} ${money(Number(v.amount))} · ${str(v.tender_type)} · ${str(v.store_id)} on ${str(v.business_date)}`;
    default:
      return JSON.stringify(v);
  }
}
