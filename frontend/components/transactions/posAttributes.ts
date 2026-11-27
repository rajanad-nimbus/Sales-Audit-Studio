import { humanizeKey } from '@/lib/humanize';
import { summarizeValue } from '@/components/ui';

type Attributes = Record<string, string | number | boolean | null> | null | undefined;

/**
 * The extra fields a POS sent for an item or tender, as one short line, e.g. "Not taxable · Terminal 3".
 * `skip` drops values that say nothing new, such as a "Sale" status on a sale line.
 */
export function posAttributeLine(
  attributes: Attributes,
  skip: (key: string, value: unknown) => boolean = () => false
): string {
  return Object.entries(attributes ?? {})
    .filter(([key, value]) => value !== null && value !== '' && !skip(key, value))
    .map(([key, value]) => {
      const label = humanizeKey(key);
      if (typeof value === 'boolean') return value ? label : `Not ${label.toLowerCase()}`;
      return `${label} ${summarizeValue(value)}`;
    })
    .join(' · ');
}

/** Item-line defaults that repeat what the row already shows: a plain item, sold on a sale. */
export const itemDefault = (quantity: number) => (key: string, value: unknown) =>
  (key === 'item_type' && value === 'ITEM') ||
  (key === 'item_status' && value === (quantity < 0 ? 'Return' : 'Sale'));
