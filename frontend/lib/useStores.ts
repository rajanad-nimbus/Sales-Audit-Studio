'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

let cached: string[] | null = null;

/** Stores the signed-in user may pick: every known store, or only a store manager's own. */
export function useStores(): string[] {
  const [stores, setStores] = useState<string[]>(cached ?? []);
  useEffect(() => {
    if (cached) return;
    let live = true;
    api
      .get('/api/stores')
      .then((r) => {
        cached = r.data;
        if (live) setStores(r.data);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  return stores;
}
