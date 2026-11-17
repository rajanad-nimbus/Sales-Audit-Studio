'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber } from '@/lib/preferences';
import { useToast } from '@/components/Feedback';
import {
  Alert,
  Badge,
  Button,
  DataTable,
  MetricGrid,
  MetricTile,
  PageHeader,
  Pager,
  Segmented,
  Toolbar,
  Card,
} from '@/components/ui';
import { describe, pretty, statusLabel, statusVariant } from '@/lib/corrections';
import type { Adjustment } from '@/lib/corrections';
import { PaidOutCorrectionQueue } from '@/components/registerEvents/PaidOutCorrectionQueue';

type Filter = 'All' | 'Proposed' | 'Approved' | 'Rejected';
const FILTERS: Filter[] = ['All', 'Proposed', 'Approved', 'Rejected'];

export default function CorrectionsPage() {
  const router = useRouter();
  const toast = useToast();
  const [rows, setRows] = useState<Adjustment[] | null>(null);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>('All');
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [openId, setOpenId] = useState<string | null>(null);
  const [adjustmentId, setAdjustmentId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [filtersReady, setFiltersReady] = useState(false);

  useEffect(() => {
    const initialQuery = new URLSearchParams(window.location.search).get('q') ?? '';
    const initialAdjustmentId =
      new URLSearchParams(window.location.search).get('adjustment_id') ?? null;
    setQ(initialQuery);
    setDebouncedQ(initialQuery);
    setAdjustmentId(initialAdjustmentId);
    setFiltersReady(true);
  }, []);

  useEffect(() => {
    if (!filtersReady) return;
    const params = new URLSearchParams();
    if (debouncedQ) params.set('q', debouncedQ);
    if (adjustmentId) params.set('adjustment_id', adjustmentId);
    const query = params.toString();
    history.replaceState(
      null,
      '',
      query ? `${window.location.pathname}?${query}` : window.location.pathname
    );
  }, [adjustmentId, debouncedQ, filtersReady]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => clearTimeout(timer);
  }, [q]);

  useEffect(() => setPage(1), [filter, debouncedQ, pageSize]);

  const load = useCallback(async () => {
    if (!filtersReady) return;
    try {
      const response = await api.get('/api/transaction-adjustments', {
        params: {
          page,
          page_size: pageSize,
          ...(adjustmentId ? { adjustment_id: adjustmentId } : {}),
          ...(filter !== 'All' ? { status: filter } : {}),
          ...(debouncedQ ? { q: debouncedQ } : {}),
        },
      });
      setRows(response.data.items);
      setTotal(response.data.total);
      setPages(response.data.pages);
      setStatusCounts(response.data.status_counts);
      setOpenId(
        adjustmentId && response.data.items.some((row: Adjustment) => row.id === adjustmentId)
          ? adjustmentId
          : null
      );
      setError(null);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Unable to load correction queue.');
    }
  }, [adjustmentId, debouncedQ, filter, filtersReady, page, pageSize]);
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
    s === 'All' ? Object.values(statusCounts).reduce((a, b) => a + b, 0) : (statusCounts[s] ?? 0);
  const shown = all;

  return (
    <div className="page-container">
      <PageHeader
        title="Corrections"
        subtitle="Review governed transaction corrections and paid-out detail repairs while preserving original source records."
        actions={
          <>
            <Button variant="primary" onClick={() => router.push('/corrections/new')}>
              New correction
            </Button>
          </>
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
      <PaidOutCorrectionQueue />
      <Card title={`Finance review queue (${formatNumber(total)})`}>
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
            onChange={(next) => setFilter(next as Filter)}
            options={FILTERS.map((x) => ({
              value: x,
              label: `${x === 'Proposed' ? 'Awaiting review' : x} ${count(x)}`,
            }))}
          />
          {(q || adjustmentId) && (
            <Button
              size="sm"
              onClick={() => {
                setQ('');
                setDebouncedQ('');
                setAdjustmentId(null);
                setOpenId(null);
              }}
            >
              Clear search
            </Button>
          )}
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
              csvValue: (r) => `${pretty(r.adjustment_type)} (${r.proposed_by})`,
            },
            {
              key: 'effect',
              header: 'Proposed effect',
              render: (r) => describe(r.adjustment_type, r.proposed_values),
              csvValue: (r) => describe(r.adjustment_type, r.proposed_values),
            },
            {
              key: 'when',
              header: 'Submitted',
              nowrap: true,
              render: (r) => formatDateTime(r.created_at, { dateStyle: 'medium' }),
              csvValue: (r) => r.created_at,
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
              csvValue: (r) =>
                `${statusLabel(r.status)}${r.reviewed_by ? ` · ${r.reviewed_by}` : ''}`,
            },
            {
              key: 'actions',
              header: '',
              stopRowClick: true,
              csvSkip: true,
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
          csvFileName="corrections-visible.csv"
          onRowClick={(r) => {
            const next = openId === r.id ? null : r.id;
            setOpenId(next);
            setAdjustmentId(next);
          }}
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
                {formatDateTime(r.created_at)} by {r.proposed_by}
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
          emptyTitle={count('All') === 0 ? 'No corrections yet' : 'No corrections match'}
          emptyHint={
            count('All') === 0 ? 'Use "New correction" to propose the first one.' : undefined
          }
        />
        <Pager
          page={page}
          pages={pages}
          total={total}
          pageSize={pageSize}
          onPage={setPage}
          onPageSize={setPageSize}
        />
      </Card>
    </div>
  );
}
