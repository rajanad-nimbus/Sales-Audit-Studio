'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { formatDateTime } from '@/lib/preferences';
import { SkeletonRows, useToast } from '@/components/Feedback';
import { Alert, Button, Card, DataTable, Pager, Toolbar } from '@/components/ui';
import { SettingsSection } from '@/components/settings/SettingsSection';

interface Policy {
  id: string;
  dataset: string;
  retain_days: number;
  archive_before_purge: boolean;
}
interface Run {
  id: string;
  dataset: string;
  eligible_records: number;
  archived_records: number;
  purged_records: number;
  status: string;
  created_at: string;
  archive_location: string | null;
  archive_hash: string | null;
  error: string | null;
  performed_by: string;
}
interface RunsResult {
  items: Run[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}
const apiError = (e: unknown, fallback: string) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;

export default function RetentionPage() {
  const toast = useToast();
  const [policies, setPolicies] = useState<Policy[] | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runTotal, setRunTotal] = useState(0);
  const [runPages, setRunPages] = useState(1);
  const [runPage, setRunPage] = useState(1);
  const [runPageSize, setRunPageSize] = useState(50);
  const [runQuery, setRunQuery] = useState('');
  const [runDataset, setRunDataset] = useState('');
  const [runStatus, setRunStatus] = useState('');
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsExporting, setRunsExporting] = useState(false);
  const [runsNotice, setRunsNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    async (isCurrent: () => boolean = () => true) => {
      setRunsLoading(true);
      try {
        const [p, r] = await Promise.all([
          api.get('/api/operations/retention'),
          api.get<RunsResult>('/api/operations/retention/runs', {
            params: {
              q: runQuery.trim() || undefined,
              dataset: runDataset || undefined,
              status: runStatus || undefined,
              page: runPage,
              page_size: runPageSize,
            },
          }),
        ]);
        if (!isCurrent()) return;
        setPolicies(p.data);
        setRuns(r.data.items);
        setRunTotal(r.data.total);
        setRunPages(r.data.pages);
        setError(null);
      } catch (e) {
        if (isCurrent()) setError(apiError(e, 'Could not load retention policies.'));
      } finally {
        if (isCurrent()) setRunsLoading(false);
      }
    },
    [runQuery, runDataset, runStatus, runPage, runPageSize]
  );
  useEffect(() => {
    let current = true;
    const timer = window.setTimeout(() => void load(() => current), 180);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [load]);

  const exportRuns = async () => {
    setRunsExporting(true);
    setRunsNotice(null);
    try {
      const params = new URLSearchParams({ format: 'csv' });
      if (runQuery.trim()) params.set('q', runQuery.trim());
      if (runDataset) params.set('dataset', runDataset);
      if (runStatus) params.set('status', runStatus);
      const { data, headers } = await api.get(
        `/api/operations/retention/runs?${params.toString()}`,
        { responseType: 'blob' }
      );
      downloadBlob('retention-runs.csv', data);
      if (headers?.['x-retention-runs-truncated'] === 'true') {
        setRunsNotice(
          'The export reached 50,000 runs. Narrow the dataset or search filter to export more.'
        );
      }
    } catch (e) {
      setError(apiError(e, 'Could not export retention run history.'));
    } finally {
      setRunsExporting(false);
    }
  };

  const preview = async (dataset: string) => {
    try {
      const { data } = await api.get(`/api/operations/retention/${dataset}/preview`);
      setNote(
        data.retention_supported
          ? `${dataset}: ${data.eligible_records} records are eligible after ${formatDateTime(data.cutoff, { dateStyle: 'medium' })}. This is a preview. Nothing is removed.`
          : `${dataset}: ${data.eligible_records} records match the configured age. Audit events are append-only and hash-chained, so retention cannot archive or remove them.`
      );
    } catch (e) {
      setError(apiError(e, 'Could not preview this policy.'));
    }
  };
  const save = async (p: Policy, days: number) => {
    if (p.dataset === 'audit_events') return;
    setBusy(true);
    try {
      await api.put(`/api/operations/retention/${p.dataset}`, {
        retain_days: days,
        archive_before_purge: p.archive_before_purge,
      });
      toast('Retention policy saved.', 'success');
      await load();
    } catch (e) {
      toast(apiError(e, 'Could not save the policy.'), 'error');
    } finally {
      setBusy(false);
    }
  };
  const archive = async (p: Policy) => {
    if (p.dataset === 'audit_events') return;
    setBusy(true);
    try {
      await api.post(`/api/operations/retention/${p.dataset}/run`, { confirm_purge: false });
      toast(
        'Eligible records archived. Purging needs explicit confirmation through the API.',
        'success'
      );
      await load();
    } catch (e) {
      toast(apiError(e, 'Could not archive.'), 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsSection
      title="Data retention"
      description="How long each dataset is kept. Policies are previewed safely. Purging is a separate, supervised step."
    >
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      {note && (
        <Alert tone="info" onDismiss={() => setNote(null)}>
          {note}
        </Alert>
      )}
      <Card title="Policies">
        {!policies ? (
          <SkeletonRows rows={3} label="Loading retention policies" />
        ) : policies.length === 0 ? (
          <p className="cc-empty">
            No retention policies are configured. IT can add policies through the operations API.
          </p>
        ) : (
          <DataTable
            columns={[
              {
                key: 'dataset',
                header: 'Dataset',
                primary: true,
                render: (p) =>
                  p.dataset === 'audit_events' ? 'audit_events (append-only)' : p.dataset,
                csvValue: (p) =>
                  p.dataset === 'audit_events' ? 'audit_events (append-only)' : p.dataset,
              },
              {
                key: 'days',
                header: 'Retain (days)',
                numeric: true,
                render: (p) => (
                  <input
                    aria-label={`${p.dataset} retention days`}
                    type="number"
                    min={30}
                    defaultValue={p.retain_days}
                    style={{ width: '6rem' }}
                    disabled={p.dataset === 'audit_events'}
                    onBlur={(e) => {
                      const d = Number(e.target.value);
                      if (d >= 30 && d !== p.retain_days) save(p, d);
                    }}
                  />
                ),
                csvValue: (p) => p.retain_days,
              },
              {
                key: 'archive',
                header: 'Archive first',
                render: (p) =>
                  p.dataset === 'audit_events'
                    ? 'Not applicable'
                    : p.archive_before_purge
                      ? 'Yes'
                      : 'No',
                csvValue: (p) =>
                  p.dataset === 'audit_events'
                    ? 'Not applicable'
                    : p.archive_before_purge
                      ? 'Yes'
                      : 'No',
              },
              {
                key: 'actions',
                header: '',
                csvSkip: true,
                render: (p) => (
                  <div className="action-buttons">
                    <Button size="sm" onClick={() => preview(p.dataset)}>
                      Preview
                    </Button>
                    {p.dataset === 'audit_events' ? (
                      <span className="hint">Append-only</span>
                    ) : (
                      <Button size="sm" disabled={busy} onClick={() => archive(p)}>
                        Archive eligible
                      </Button>
                    )}
                  </div>
                ),
              },
            ]}
            rows={policies}
            rowKey={(p) => p.id}
            csvFileName="retention-policies-visible.csv"
            detachTitle="Data retention policies"
          />
        )}
      </Card>
      <Card title="History">
        <Toolbar count={`${runTotal} matching runs`}>
          <input
            className="grow"
            type="search"
            aria-label="Search retention runs"
            placeholder="Search run, archive, error, or operator"
            value={runQuery}
            maxLength={255}
            onChange={(event) => {
              setRunQuery(event.target.value);
              setRunPage(1);
            }}
          />
          <select
            aria-label="Filter retention runs by dataset"
            value={runDataset}
            onChange={(event) => {
              setRunDataset(event.target.value);
              setRunPage(1);
            }}
          >
            <option value="">All datasets</option>
            {(policies ?? []).map((policy) => (
              <option key={policy.dataset} value={policy.dataset}>
                {policy.dataset}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter retention runs by status"
            value={runStatus}
            onChange={(event) => {
              setRunStatus(event.target.value);
              setRunPage(1);
            }}
          >
            <option value="">All statuses</option>
            <option value="Succeeded">Succeeded</option>
            <option value="Failed">Failed</option>
          </select>
          <Button
            size="sm"
            variant="secondary"
            disabled={!runTotal || runsExporting}
            onClick={() => void exportRuns()}
          >
            {runsExporting ? 'Preparing CSV…' : 'Download matching CSV'}
          </Button>
        </Toolbar>
        {runsNotice && (
          <Alert tone="warning" onDismiss={() => setRunsNotice(null)}>
            {runsNotice}
          </Alert>
        )}
        {runsLoading ? (
          <SkeletonRows rows={3} label="Loading retention run history" />
        ) : runTotal === 0 ? (
          <p className="cc-empty">
            {runQuery.trim() || runDataset || runStatus
              ? 'No retention runs match these filters.'
              : 'No retention runs yet.'}
          </p>
        ) : (
          <DataTable
            columns={[
              {
                key: 'dataset',
                header: 'Dataset',
                primary: true,
                render: (r) => r.dataset,
                csvValue: (r) => r.dataset,
              },
              {
                key: 'status',
                header: 'Status',
                render: (r) => r.status,
                csvValue: (r) => r.status,
              },
              {
                key: 'eligible',
                header: 'Eligible',
                numeric: true,
                render: (r) => r.eligible_records,
                csvValue: (r) => r.eligible_records,
              },
              {
                key: 'archived',
                header: 'Archived',
                numeric: true,
                render: (r) => r.archived_records,
                csvValue: (r) => r.archived_records,
              },
              {
                key: 'purged',
                header: 'Purged',
                numeric: true,
                render: (r) => r.purged_records,
                csvValue: (r) => r.purged_records,
              },
              {
                key: 'archive',
                header: 'Archive',
                render: (r) => r.archive_location || '—',
                csvValue: (r) => r.archive_location || '—',
              },
              {
                key: 'hash',
                header: 'Archive hash',
                render: (r) =>
                  r.archive_hash ? (
                    <code title={r.archive_hash}>{r.archive_hash.slice(0, 12)}…</code>
                  ) : (
                    '—'
                  ),
                csvValue: (r) => r.archive_hash || '—',
              },
              {
                key: 'error',
                header: 'Run detail',
                render: (r) =>
                  r.error ? (
                    <span title={r.error}>
                      {r.error.length > 140 ? `${r.error.slice(0, 140)}…` : r.error}
                    </span>
                  ) : (
                    'Completed'
                  ),
                csvValue: (r) => r.error || 'Completed',
              },
              {
                key: 'operator',
                header: 'Performed by',
                render: (r) => r.performed_by,
                csvValue: (r) => r.performed_by,
              },
              {
                key: 'when',
                header: 'When',
                nowrap: true,
                render: (r) => formatDateTime(r.created_at),
                csvValue: (r) => r.created_at,
              },
            ]}
            rows={runs}
            rowKey={(r) => String(r.id)}
            csvFileName="retention-runs-visible.csv"
            detachTitle={`Retention run history · ${runDataset || 'all datasets'} · ${runStatus || 'all statuses'}`}
            footer={
              <Pager
                page={runPage}
                pages={runPages}
                total={runTotal}
                pageSize={runPageSize}
                pageSizes={[25, 50, 100, 200]}
                onPage={setRunPage}
                onPageSize={(size) => {
                  setRunPageSize(size);
                  setRunPage(1);
                }}
              />
            }
          />
        )}
      </Card>
    </SettingsSection>
  );
}
