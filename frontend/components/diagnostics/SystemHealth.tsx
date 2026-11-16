'use client';
import { useCallback, useState } from 'react';
import { api } from '@/lib/api';
import { useLive } from '@/lib/sync';
import {
  Alert,
  Badge,
  Button,
  DataTable,
  MetricGrid,
  MetricTile,
  PageHeader,
} from '@/components/ui';
import type { BadgeVariant } from '@/components/ui';
import { apiError } from '@/lib/storeDays';
import { formatDateTime } from '@/lib/preferences';

type Status = 'ok' | 'warning' | 'critical';
interface Check {
  name: string;
  status: Status;
  detail: string;
  value: string | number | null;
}
interface Health {
  status: Status;
  checked_at: string;
  checks: Check[];
}

const RANK: Record<Status, number> = { critical: 0, warning: 1, ok: 2 };
const LABEL: Record<Status, string> = { ok: 'OK', warning: 'Warning', critical: 'Critical' };
const VARIANT: Record<Status, BadgeVariant> = {
  ok: 'success',
  warning: 'warning',
  critical: 'error',
};

/** One list of checks on the database, the daily batch, the data feeds and the work waiting. */
export function SystemHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Health>('/api/diagnostics/health');
      setHealth(data);
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not run the health checks.'));
    }
  }, []);
  // Refreshes every minute, and whenever something is saved elsewhere in the app.
  useLive(() => void load(), 60000);

  const count = (s: Status) => (health ? health.checks.filter((c) => c.status === s).length : '—');
  const ordered = health
    ? [...health.checks].sort((a, b) => RANK[a.status] - RANK[b.status])
    : null;

  return (
    <div className="page-container">
      <PageHeader
        title="System health"
        subtitle="The database, the daily batch, the data feeds and the work waiting. Refreshes every minute."
        actions={<Button onClick={() => void load()}>Check now</Button>}
      />
      {error && <Alert tone="error">{error}</Alert>}
      {health && (
        <Alert
          tone={
            health.status === 'ok' ? 'success' : health.status === 'warning' ? 'warning' : 'error'
          }
        >
          {health.status === 'ok'
            ? `All ${health.checks.length} checks pass.`
            : `${health.checks.filter((c) => c.status !== 'ok').length} of ${health.checks.length} checks need attention.`}{' '}
          Checked {formatDateTime(health.checked_at, { hour: 'numeric', minute: '2-digit' })}.
        </Alert>
      )}
      <MetricGrid>
        <MetricTile label="Passing" value={count('ok')} />
        <MetricTile label="Warnings" value={count('warning')} />
        <MetricTile label="Critical" value={count('critical')} />
      </MetricGrid>
      <DataTable
        rows={ordered}
        rowKey={(c) => c.name}
        emptyTitle="No checks ran"
        columns={[
          { key: 'name', header: 'Check', primary: true, render: (c) => c.name },
          {
            key: 'status',
            header: 'Status',
            render: (c) => (
              <Badge variant={VARIANT[c.status]} size="sm">
                {LABEL[c.status]}
              </Badge>
            ),
          },
          { key: 'detail', header: 'What was found', render: (c) => c.detail },
        ]}
      />
    </div>
  );
}
