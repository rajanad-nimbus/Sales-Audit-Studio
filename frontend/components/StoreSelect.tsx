'use client';

import { SearchSelect } from '@/components/SearchSelect';
import { useStores } from '@/lib/useStores';

interface StoreSelectProps {
  value: string;
  onChange: (store: string) => void;
  /** Label of the empty choice, for example "All stores". Without it a store must be chosen. */
  emptyLabel?: string;
  label?: string;
  disabled?: boolean;
}

/** The one place a store is chosen. Type to search. A value outside the list (for example from a link) stays visible. */
export function StoreSelect({
  value,
  onChange,
  emptyLabel,
  label = 'Store',
  disabled,
}: StoreSelectProps) {
  const stores = useStores();
  const options = (value && !stores.includes(value) ? [value, ...stores] : stores).map((s) => ({
    value: s,
  }));
  return (
    <SearchSelect
      label={label}
      value={value}
      onChange={onChange}
      options={options}
      emptyLabel={emptyLabel}
      placeholder="Search stores"
      disabled={disabled}
    />
  );
}
