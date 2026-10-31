'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { SearchSelect } from '@/components/SearchSelect';

type Kind = 'registers' | 'cashiers';
const SINGULAR: Record<Kind, string> = { registers: 'Register', cashiers: 'Cashier' };

/** Registers or cashiers, optionally only those seen at one store. */
function useLookup(kind: Kind, storeId?: string) {
  const [options, setOptions] = useState<{ value: string; hint?: string }[]>([]);
  useEffect(() => {
    let live = true;
    api
      .get(`/api/lookups/${kind}`, { params: storeId ? { store_id: storeId } : {} })
      .then((r) => {
        if (live)
          setOptions(
            (r.data as { code: string; name: string | null }[]).map((x) => ({
              value: x.code,
              hint: x.name ?? undefined,
            }))
          );
      })
      .catch(() => {
        if (live) setOptions([]);
      });
    return () => {
      live = false;
    };
  }, [kind, storeId]);
  return options;
}

interface LookupSelectProps {
  kind: Kind;
  value: string;
  onChange: (value: string) => void;
  storeId?: string;
  /** Allow a code that is not in the list yet. On by default: a new till or cashier may not be known. */
  allowCustom?: boolean;
  emptyLabel?: string;
  disabled?: boolean;
}

export function LookupSelect({
  kind,
  value,
  onChange,
  storeId,
  allowCustom = true,
  emptyLabel,
  disabled,
}: LookupSelectProps) {
  const options = useLookup(kind, storeId);
  return (
    <SearchSelect
      label={SINGULAR[kind]}
      value={value}
      onChange={onChange}
      options={options}
      allowCustom={allowCustom}
      emptyLabel={emptyLabel}
      placeholder={`Search ${kind}`}
      disabled={disabled}
    />
  );
}
