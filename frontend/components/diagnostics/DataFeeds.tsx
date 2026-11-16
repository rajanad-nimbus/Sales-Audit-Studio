'use client';
import { useCallback, useState } from 'react';
import { api } from '@/lib/api';
import { useLive } from '@/lib/sync';
import { Alert, Badge, Button, Card, DataTable, PageHeader } from '@/components/ui';
import type { BadgeVariant } from '@/components/ui';
import { apiError } from '@/lib/storeDays';
import { formatDateTime } from '@/lib/preferences';

interface Feed {
  source_system: string;
  state: 'Current' | 'Stale' | 'No data';
  last_received_at: string | null;
  last_accepted_at: string | null;
  received_24h: number;
  quarantined_24h: number;
  quarantined_total: number;
}
interface Feeds {
  stale_after_hours: number;
  feeds: Feed[];
  quarantine_reasons_7d: { source_system: string; reason: string; records: number }[];
}

const VARIANT: Record<Feed['state'], BadgeVariant> = {
  Current: 'success',
  Stale: 'warning',
  'No data': 'secondary',
};
const when = (iso: string | null) => (iso ? formatDateTime(iso) : 'Never');

/** When each source last sent data, how much came in, and what was set aside and why. */
export function DataFeeds() {
  const [data, setData] = useState<Feeds | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData((await api.get<Feeds>('/api/diagnostics/feeds')).data);
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not load the data feeds.'));
    }
  }, []);
  useLive(() => void load(), 60000);

  return (
    <div className="page-container">
      <PageHeader
        title="Data feeds"
        subtitle="A feed is stale when it has had no accepted data for more than the limit below."
        actions={<Button onClick={() => void load()}>Refresh</Button>}
      />
      {error && <Alert tone="error">{error}</Alert>}
      {data && (
        <p className="hint">Stale after {data.stale_after_hours} hours without accepted data.</p>
      )}
      <DataTable
        rows={data ? data.feeds : null}
        rowKey={(f) => f.source_system}
        emptyTitle="No feeds"
        columns={[
          { key: 's', header: 'Source', primary: true, render: (f) => f.source_system },
          {
            key: 'state',
            header: 'State',
            render: (f) => (
              <Badge variant={VARIANT[f.state]} size="sm">
                {f.state}
              </Badge>
            ),
          },
          {
            key: 'a',
            header: 'Last accepted',
            nowrap: true,
            render: (f) => when(f.last_accepted_at),
          },
          {
            key: 'r',
            header: 'Last received',
            nowrap: true,
            render: (f) => when(f.last_received_at),
          },
          { key: 'n', header: 'Received, 24h', numeric: true, render: (f) => f.received_24h },
          {
            key: 'q',
            header: 'Set aside, 24h',
            numeric: true,
            render: (f) =>
              f.quarantined_24h > 0 ? (
                <Badge variant="warning" size="sm">
                  {f.quarantined_24h}
                </Badge>
              ) : (
                0
              ),
          },
          {
            key: 't',
            header: 'Set aside, total',
            numeric: true,
            render: (f) => f.quarantined_total,
          },
        ]}
      />
      {data && data.quarantine_reasons_7d.length > 0 && (
        <Card title="Why records were set aside, last 7 days">
          <DataTable
            rows={data.quarantine_reasons_7d}
            rowKey={(r) => `${r.source_system}|${r.reason}`}
            emptyTitle="Nothing set aside"
            columns={[
              { key: 's', header: 'Source', primary: true, render: (r) => r.source_system },
              { key: 'w', header: 'Reason', render: (r) => r.reason },
              { key: 'n', header: 'Records', numeric: true, render: (r) => r.records },
            ]}
          />
        </Card>
      )}
    </div>
  );
}
