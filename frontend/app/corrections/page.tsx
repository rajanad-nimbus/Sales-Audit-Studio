'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { useToast } from '@/components/Feedback';
import {
  Alert,
  Badge,
  Button,
  DataTable,
  MetricGrid,
  MetricTile,
  PageHeader,
  Segmented,
  Toolbar,
  Card,
} from '@/components/ui';
import { describe, pretty, statusLabel, statusVariant } from '@/lib/corrections';
import type { Adjustment } from '@/lib/corrections';

type Filter = 'All' | 'Proposed' | 'Approved' | 'Rejected';
const FILTERS: Filter[] = ['All', 'Proposed', 'Approved', 'Rejected'];

export default function CorrectionsPage() {
  const router = useRouter();
  const toast = useToast();
  const [rows, setRows] = useState<Adjustment[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>('All');
  const [q, setQ] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setRows((await api.get('/api/transaction-adjustments')).data);
      setError(null);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Unable to load correction queue.');
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const decide = async (id: string, decision: 'approve' | 'reject') => {
    setBusy(true);
    try {
      await api.post(`/api/transaction-adjustments/${id}/${decision}`);
      toast(`Correction ${decision}d.`, 'success');
      await load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? `Unable to ${decision} correction.`);
    } finally {
      setBusy(false);
    }
  };

  const all = rows ?? [];
  const count = (s: Filter) =>
    s === 'All' ? all.length : all.filter((x) => x.status === s).length;
  const needle = q.trim().toLowerCase();
  const shown = all
    .filter((r) => filter === 'All' || r.status === filter)
    .filter(
      (r) =>
        !needle ||
        `${r.adjustment_type} ${r.proposed_by} ${r.rationale} ${r.transaction_id ?? ''} ${describe(r.adjustment_type, r.proposed_values)}`
          .toLowerCase()
          .includes(needle)
    )
    .sort((a, b) => b.created_at.localeCompare(a.created_at));

  return (
    <div className="page-container">
      <PageHeader
        title="Transaction Corrections"
        subtitle="Correct the audited view without changing the original source. Every proposal is attributable, reviewed and retained."
        actions={
          <Button variant="primary" onClick={() => router.push('/corrections/new')}>
            New correction
          </Button>
        }
      />
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      <MetricGrid>
        <MetricTile
          label="Awaiting review"
          value={count('Proposed')}
          onClick={() => setFilter('Proposed')}
        />
        <MetricTile
          label="Approved"
          value={count('Approved')}
          onClick={() => setFilter('Approved')}
        />
        <MetricTile
          label="Rejected"
          value={count('Rejected')}
          onClick={() => setFilter('Rejected')}
        />
      </MetricGrid>
      <Card title={`Finance review queue (${shown.length})`}>
        <p className="hint">
          Approve only when the rationale and source evidence support the proposed change. Select a
          row to read the full rationale.
        </p>
        <Toolbar>
          <input
            className="grow"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search by type, person, transaction or rationale"
            aria-label="Search corrections"
          />
          <Segmented
            value={filter}
            onChange={setFilter}
            options={FILTERS.map((x) => ({
              value: x,
              label: `${x === 'Proposed' ? 'Awaiting review' : x} ${count(x)}`,
            }))}
          />
        </Toolbar>
        <DataTable
          columns={[
            {
              key: 'type',
              header: 'Correction',
              primary: true,
              render: (r) => (
                <>
                  {pretty(r.adjustment_type)}
                  <small className="act-sub" style={{ display: 'block' }}>
                    {r.proposed_by}
                  </small>
                </>
              ),
            },
            {
              key: 'effect',
              header: 'Proposed effect',
              render: (r) => describe(r.adjustment_type, r.proposed_values),
            },
            {
              key: 'when',
              header: 'Submitted',
              nowrap: true,
              render: (r) => new Date(r.created_at).toLocaleDateString(),
            },
            {
              key: 'status',
              header: 'Status',
              render: (r) => (
                <>
                  <Badge variant={statusVariant(r.status)} size="sm">
                    {statusLabel(r.status)}
                  </Badge>
                  {r.reviewed_by && (
                    <small className="act-sub" style={{ display: 'block' }}>
                      {r.reviewed_by}
                    </small>
                  )}
                </>
              ),
            },
            {
              key: 'actions',
              header: '',
              stopRowClick: true,
              render: (r) =>
                r.status === 'Proposed' && (
                  <div className="action-buttons">
                    <Button
                      variant="primary"
                      size="sm"
                      disabled={busy}
                      onClick={() => decide(r.id, 'approve')}
                    >
                      Approve
                    </Button>
                    <Button size="sm" disabled={busy} onClick={() => decide(r.id, 'reject')}>
                      Reject
                    </Button>
                  </div>
                ),
            },
          ]}
          rows={rows === null ? null : shown}
          rowKey={(r) => r.id}
          onRowClick={(r) => setOpenId(openId === r.id ? null : r.id)}
          expandedKey={openId}
          renderDetail={(r) => (
            <dl className="kv">
              <dt>Why</dt>
              <dd>{r.rationale}</dd>
              <dt>Target</dt>
              <dd>
                {r.transaction_id ? <code>{r.transaction_id}</code> : 'New missing transaction'}
              </dd>
              <dt>Submitted</dt>
              <dd>
                {new Date(r.created_at).toLocaleString()} by {r.proposed_by}
              </dd>
              {r.reviewed_by && (
                <>
                  <dt>Reviewed by</dt>
                  <dd>{r.reviewed_by}</dd>
                </>
              )}
              <dt>Proposal</dt>
              <dd>
                <code>{JSON.stringify(r.proposed_values)}</code>
              </dd>
            </dl>
          )}
          emptyTitle={all.length === 0 ? 'No corrections yet' : 'No corrections match'}
          emptyHint={
            all.length === 0 ? 'Use "New correction" to propose the first one.' : undefined
          }
        />
      </Card>
    </div>
  );
}
