import { describe, expect, it } from 'vitest';
import { itemDefault, posAttributeLine } from '@/components/transactions/posAttributes';

describe('POS attributes shown inline', () => {
  it('reads as one short line, with yes/no fields as words', () => {
    expect(posAttributeLine({ taxable: false, terminal: '3', swiped: true })).toBe(
      'Not taxable · Terminal 3 · Swiped'
    );
  });

  it('drops item defaults that repeat the row', () => {
    expect(posAttributeLine({ item_type: 'ITEM', item_status: 'Sale' }, itemDefault(1))).toBe('');
    expect(posAttributeLine({ item_type: 'GCN', item_status: 'Sale' }, itemDefault(-1))).toBe(
      'Item type GCN · Item status Sale'
    );
  });

  it('shows nothing for empty attributes', () => {
    expect(posAttributeLine(null)).toBe('');
    expect(posAttributeLine({ reference_1: '' })).toBe('');
  });
});
