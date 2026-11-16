'use client';

import { useState } from 'react';
import { api, BatchStatus } from '@/lib/api';
import { formatBusinessDate } from '@/lib/preferences';
import { useLive } from '@/lib/sync';

export function BusinessDate() {
  const [st, setSt] = useState<BatchStatus | null>(null);
  useLive(() => {
    api
      .get('/api/batch/status')
      .then((r) => setSt(r.data))
      .catch(() => {});
  }, 60000);
  if (!st) return null;
  const d = st.data_as_of;
  const label = d
    ? formatBusinessDate(d, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      })
    : 'No data loaded';
  return (
    <span
      className="business-date"
      title={st.freshness === 'fresh' ? 'Latest business date loaded' : `Data is ${st.freshness}`}
    >
      Business date <b>{label}</b>
    </span>
  );
}
