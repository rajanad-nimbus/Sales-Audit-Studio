'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { SkeletonRows, useToast } from '@/components/Feedback';
import { Alert, Button, Card, DataTable } from '@/components/ui';

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
}
const apiError = (e: unknown, fallback: string) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;

export default function RetentionPage() {
  const toast = useToast();
  const [policies, setPolicies] = useState<Policy[] | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, r] = await Promise.all([
        api.get('/api/operations/retention'),
        api.get('/api/operations/retention/runs'),
      ]);
      setPolicies(p.data);
      setRuns(r.data);
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not load retention policies.'));
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const preview = async (dataset: string) => {
    try {
      const { data } = await api.get(`/api/operations/retention/${dataset}/preview`);
      setNote(
        `${dataset}: ${data.eligible_records} records are eligible after ${new Date(data.cutoff).toLocaleDateString()}. This is a preview. Nothing is removed.`
      );
    } catch (e) {
      setError(apiError(e, 'Could not preview this policy.'));
    }
  };
  const save = async (p: Policy, days: number) => {
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
    <Card>
      <h2>Data retention</h2>
      <p className="hint" style={{ marginBottom: '.75rem' }}>
        How long each dataset is kept. Policies are previewed safely. Purging is a separate,
        supervised step.
      </p>
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
      {!policies ? (
        <SkeletonRows rows={3} label="Loading retention policies" />
      ) : policies.length === 0 ? (
        <p className="cc-empty">
          No retention policies are configured. IT can add policies through the operations API.
        </p>
      ) : (
        <DataTable
          columns={[
            { key: 'dataset', header: 'Dataset', primary: true, render: (p) => p.dataset },
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
                  onBlur={(e) => {
                    const d = Number(e.target.value);
                    if (d >= 30 && d !== p.retain_days) save(p, d);
                  }}
                />
              ),
            },
            {
              key: 'archive',
              header: 'Archive first',
              render: (p) => (p.archive_before_purge ? 'Yes' : 'No'),
            },
            {
              key: 'actions',
              header: '',
              render: (p) => (
                <div className="action-buttons">
                  <Button size="sm" onClick={() => preview(p.dataset)}>
                    Preview
                  </Button>
                  <Button size="sm" disabled={busy} onClick={() => archive(p)}>
                    Archive eligible
                  </Button>
                </div>
              ),
            },
          ]}
          rows={policies}
          rowKey={(p) => p.id}
        />
      )}
      <h3 className="sd-section">History</h3>
      {runs.length === 0 ? (
        <p className="cc-empty">No retention runs yet.</p>
      ) : (
        <DataTable
          columns={[
            { key: 'dataset', header: 'Dataset', render: (r) => r.dataset },
            {
              key: 'eligible',
              header: 'Eligible',
              numeric: true,
              render: (r) => r.eligible_records,
            },
            {
              key: 'archived',
              header: 'Archived',
              numeric: true,
              render: (r) => r.archived_records,
            },
            { key: 'purged', header: 'Purged', numeric: true, render: (r) => r.purged_records },
            {
              key: 'when',
              header: 'When',
              nowrap: true,
              render: (r) => new Date(r.created_at).toLocaleString(),
            },
          ]}
          rows={runs}
          rowKey={(r) => String(r.id)}
        />
      )}
    </Card>
  );
}
