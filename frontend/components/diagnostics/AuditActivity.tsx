'use client';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import {
  Alert,
  Badge,
  Card,
  DataTable,
  MetricGrid,
  MetricTile,
  PageHeader,
  Segmented,
} from '@/components/ui';
import { apiError } from '@/lib/storeDays';

interface Activity {
  window: {
    events: number;
    from_seq?: number;
    to_seq?: number;
    from_time?: string;
    to_time?: string;
  };
  by_actor: { actor: string; events: number; sensitive: number; last_at: string }[];
  by_type: { event_type: string; events: number }[];
  sensitive: {
    seq: number;
    created_at: string;
    actor: string;
    event_type: string;
    description: string;
  }[];
}

const SIZES = [
  { value: '1000', label: 'Latest 1,000' },
  { value: '5000', label: 'Latest 5,000' },
  { value: '20000', label: 'Latest 20,000' },
  { value: '100000', label: 'Latest 100,000' },
];
const kind = (t: string) => t.replaceAll('_', ' ').toLowerCase();

/** Who has been doing what, and the changes that alter money, access or a day's state. */
export function AuditActivity() {
  const [events, setEvents] = useState('5000');
  const [data, setData] = useState<Activity | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setData(null);
    try {
      setData((await api.get<Activity>('/api/diagnostics/activity', { params: { events } })).data);
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not load the audit activity.'));
    }
  }, [events]);
  useEffect(() => {
    load();
  }, [load]);

  const w = data?.window;
  return (
    <div className="page-container">
      <PageHeader
        title="Audit activity"
        subtitle="Read from the newest audit events, so it stays quick however large the trail is."
        actions={<Segmented value={events} onChange={setEvents} options={SIZES} />}
      />
      {error && <Alert tone="error">{error}</Alert>}
      <MetricGrid>
        <MetricTile label="Events read" value={w ? w.events.toLocaleString() : '—'} />
        <MetricTile label="People" value={data ? data.by_actor.length : '—'} />
        <MetricTile
          label="Important changes"
          value={data ? data.by_actor.reduce((n, a) => n + a.sensitive, 0).toLocaleString() : '—'}
        />
      </MetricGrid>
      {w?.from_time && w.to_time && (
        <p className="hint">
          {new Date(w.from_time).toLocaleString()} to {new Date(w.to_time).toLocaleString()}
        </p>
      )}
      <Card title="Important changes">
        <p className="hint">
          Definitions, tolerances, tenders, period and store-day closes, approvals and settings.
        </p>
        <DataTable
          rows={data ? data.sensitive : null}
          rowKey={(e) => String(e.seq)}
          emptyTitle="No important changes in this window"
          columns={[
            {
              key: 't',
              header: 'When',
              nowrap: true,
              render: (e) => new Date(e.created_at).toLocaleString(),
            },
            { key: 'a', header: 'Who', primary: true, render: (e) => e.actor },
            {
              key: 'k',
              header: 'Change',
              render: (e) => (
                <Badge variant="info" size="sm">
                  {kind(e.event_type)}
                </Badge>
              ),
            },
            { key: 'd', header: 'What', render: (e) => e.description },
          ]}
        />
      </Card>
      <Card title="By person">
        <DataTable
          rows={data ? data.by_actor : null}
          rowKey={(a) => a.actor}
          emptyTitle="No activity"
          columns={[
            { key: 'a', header: 'Who', primary: true, render: (a) => a.actor },
            { key: 'e', header: 'Events', numeric: true, render: (a) => a.events },
            { key: 's', header: 'Important changes', numeric: true, render: (a) => a.sensitive },
            {
              key: 'l',
              header: 'Last active',
              nowrap: true,
              render: (a) => new Date(a.last_at).toLocaleString(),
            },
          ]}
        />
      </Card>
      <Card title="By kind of event">
        <DataTable
          rows={data ? data.by_type : null}
          rowKey={(t) => t.event_type}
          emptyTitle="No activity"
          columns={[
            { key: 't', header: 'Event', primary: true, render: (t) => kind(t.event_type) },
            { key: 'n', header: 'Count', numeric: true, render: (t) => t.events },
          ]}
        />
      </Card>
    </div>
  );
}
