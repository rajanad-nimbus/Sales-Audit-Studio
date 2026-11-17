'use client';

import { useMe } from '@/components/MeProvider';
import { Alert } from '@/components/ui';

/** Tells a scoped user which portfolio is active and where an empty scope needs review. */
export function StoreScopeNote() {
  const { me } = useMe();
  if (!me?.store_scoped) return null;
  const stores = me.stores ?? [];
  const traits = me.store_location_traits ?? [];
  const local = me.store_assignment_source === 'local';
  return stores.length > 0 || traits.length > 0 ? (
    <Alert tone="info">
      {stores.length > 0 && (
        <>
          Explicit stores: <b>{stores.join(', ')}</b>.{' '}
        </>
      )}
      {traits.length > 0 && (
        <>
          Active stores with these location traits are included: <b>{traits.join(', ')}</b>.
        </>
      )}
      {!local && me.role !== 'analyst' && ' Your store list comes from Ontology Studio.'}
    </Alert>
  ) : (
    <Alert tone="warning">
      Your store-level counts and queues are empty because no stores are assigned to your portfolio.
      Ask an administrator to assign
      {me.role === 'analyst' || local
        ? ' your portfolio in ZeTSA.'
        : ' your stores in Ontology Studio.'}
    </Alert>
  );
}
