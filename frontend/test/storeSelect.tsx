import type { ReactNode } from 'react';

interface Props {
  value: string;
  onChange: (v: string) => void;
  emptyLabel?: string;
  label?: string;
}

/** Stands in for the searchable store picker, which loads its options from the API. */
export function FakeStoreSelect({ value, onChange, emptyLabel }: Props): ReactNode {
  return (
    <select
      aria-label={emptyLabel ?? 'Store'}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{emptyLabel ?? 'Choose a store'}</option>
      <option value="S1">S1</option>
      <option value="S2">S2</option>
    </select>
  );
}
