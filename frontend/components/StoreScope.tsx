'use client';

import { useMe } from '@/components/MeProvider';

/** Tells a store manager which stores they can see, and why they may see none. Nothing for roles that are not store-scoped. */
export function StoreScopeNote() {
  const { me } = useMe();
  if (!me?.store_scoped) return null;
  const stores = me.stores ?? [];
  return stores.length > 0 ? (
    <div className="alert alert-info" role="status">You can see {stores.length === 1 ? 'store' : 'stores'}: <b>{stores.join(', ')}</b>. Your stores are assigned in Ontology Studio.</div>
  ) : (
    <div className="alert alert-warning" role="status">No stores are assigned to you, so there is nothing to show. Ask an administrator to assign your stores in Ontology Studio.</div>
  );
}
