'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { api, money } from '@/lib/api';
import { formatDateTime, formatNumber } from '@/lib/preferences';
import { downloadBlob } from '@/lib/csv';
import { useToast } from '@/components/Feedback';
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Field,
  Pager,
  Segmented,
  Toolbar,
} from '@/components/ui';

type Status = 'All' | 'Proposed' | 'Approved' | 'Rejected';
type PaidOutCorrection = {
  id: string;
  event_id: string;
  proposed_values: {
    before: Record<string, string | null>;
    after: Record<string, string | null>;
  };
  rationale: string;
  status: Exclude<Status, 'All'>;
  proposed_by: string;
  reviewed_by: string | null;
  created_at: string;
  store_id: string;
  business_date: string;
  register_id: string | null;
  transaction_number: string;
  amount: string | null;
  currency: string;
};
type Response = {
  items: PaidOutCorrection[];
  total: number;
  pages: number;
  page: number;
  page_size: number;
  status_counts: Record<string, number>;
};
const STATUSES: Status[] = ['All', 'Proposed', 'Approved', 'Rejected'];
const LABELS: Record<string, string> = {
  vendor_number: 'Vendor',
  vendor_invoice_number: 'Vendor invoice',
  proof_of_delivery_number: 'Proof of delivery',
  payment_reference_number: 'Payment reference',
};

export function PaidOutCorrectionQueue() {
  const toast = useToast();
  const [result, setResult] = useState<Response | null>(null);
  const [status, setStatus] = useState<Status>('Proposed');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [error, setError] = useState<string | null>(null);
  const [exportNotice, setExportNotice] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rejectionReasons, setRejectionReasons] = useState<Record<string, string>>({});

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);
  useEffect(() => setPage(1), [status, query, pageSize]);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Response>('/api/register-events/detail-corrections', {
        params: {
          page,
          page_size: pageSize,
          ...(status !== 'All' ? { status } : {}),
          ...(query ? { q: query } : {}),
        },
      });
      setResult(data);
      setError(null);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not load paid-out correction queue.');
    }
  }, [page, pageSize, query, status]);
  useEffect(() => {
    void load();
  }, [load]);

  const review = async (id: string, action: 'approve' | 'reject', reason = '') => {
    setBusy(true);
    try {
      if (action === 'approve') {
        await api.post(`/api/register-events/detail-corrections/${id}/approve`);
      } else {
        if (!reason.trim()) return;
        await api.post(`/api/register-events/detail-corrections/${id}/reject`, {
          reason: reason.trim(),
        });
      }
      toast(action === 'approve' ? 'Paid-out details corrected.' : 'Correction rejected.', 'success');
      await load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? `Could not ${action} this correction.`);
    } finally {
      setBusy(false);
    }
  };

  const count = (value: Status) =>
    value === 'All'
      ? Object.values(result?.status_counts ?? {}).reduce((sum, n) => sum + n, 0)
      : (result?.status_counts[value] ?? 0);

  const downloadFiltered = async () => {
    setExporting(true);
    setExportNotice(null);
    try {
      const params = new URLSearchParams({ format: 'csv' });
      if (status !== 'All') params.set('status', status);
      if (query) params.set('q', query);
      const response = await api.get(`/api/register-events/detail-corrections?${params}`, {
        responseType: 'blob',
      });
      downloadBlob('paid-out-corrections.csv', response.data);
      if (response.headers?.['x-report-truncated'] === 'true') {
        setExportNotice('The export is limited to the first 50,000 matching corrections. Narrow your filters to download fewer rows.');
      }
    } catch {
      setError('Could not download the filtered paid-out correction queue.');
    } finally {
      setExporting(false);
    }
  };

  return (
    <Card title={`Paid-out detail review (${formatNumber(result?.total ?? 0)})`}>
      <p className="hint">
        Review vendor and payment metadata corrections. Approval updates the audited event details
        while retaining the original POS source record.
      </p>
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      {exportNotice && (
        <Alert tone="warning" onDismiss={() => setExportNotice(null)}>
          {exportNotice}
        </Alert>
      )}
      <Toolbar>
        <input
          className="grow"
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search store, register, transaction, proposer or reason"
          aria-label="Search paid-out correction queue"
        />
        <Segmented
          value={status}
          onChange={(value) => setStatus(value as Status)}
          options={STATUSES.map((value) => ({
            value,
            label: `${value === 'Proposed' ? 'Awaiting review' : value} ${count(value)}`,
          }))}
        />
        <Button
          variant="secondary"
          disabled={!result?.total || exporting}
          onClick={() => void downloadFiltered()}
        >
          {exporting ? 'Preparing CSV…' : 'Download filtered CSV'}
        </Button>
      </Toolbar>
      <DataTable
        columns={[
          {
            key: 'event',
            header: 'Paid-out event',
            primary: true,
            render: (row: PaidOutCorrection) => (
              <Link href={`/register-events?event_id=${encodeURIComponent(row.event_id)}`}>
                {row.store_id} · {row.business_date} · {row.register_id || 'No register'}
                <small className="act-sub" style={{ display: 'block' }}>
                  Transaction {row.transaction_number} ·{' '}
                  {row.amount ? money(row.amount, row.currency) : 'No amount'}
                </small>
              </Link>
            ),
            csvValue: (row: PaidOutCorrection) =>
              `${row.store_id} · ${row.business_date} · ${row.register_id || 'No register'} · Transaction ${row.transaction_number}`,
          },
          {
            key: 'change',
            header: 'Proposed change',
            render: (row: PaidOutCorrection) => (
              <div className="stack stack-xs">
                {Object.entries(row.proposed_values.after).map(([field, value]) => (
                  <span key={field}>
                    <b>{LABELS[field] ?? field}:</b>{' '}
                    {row.proposed_values.before[field] || '—'} → {value || '—'}
                  </span>
                ))}
              </div>
            ),
            csvValue: (row: PaidOutCorrection) =>
              Object.entries(row.proposed_values.after)
                .map(
                  ([field, value]) =>
                    `${LABELS[field] ?? field}: ${row.proposed_values.before[field] || '—'} → ${value || '—'}`
                )
                .join('; '),
          },
          {
            key: 'proposer',
            header: 'Proposed by',
            render: (row: PaidOutCorrection) => (
              <>
                {row.proposed_by}
                <small className="act-sub" style={{ display: 'block' }}>
                  {formatDateTime(row.created_at, { dateStyle: 'medium' })}
                </small>
              </>
            ),
            csvValue: (row: PaidOutCorrection) =>
              `${row.proposed_by} · ${formatDateTime(row.created_at, { dateStyle: 'medium' })}`,
          },
          {
            key: 'status',
            header: 'Status',
            render: (row: PaidOutCorrection) => (
              <Badge
                variant={row.status === 'Approved' ? 'success' : row.status === 'Rejected' ? 'error' : 'warning'}
                size="sm"
              >
                {row.status === 'Proposed' ? 'Awaiting review' : row.status}
              </Badge>
            ),
            csvValue: (row: PaidOutCorrection) =>
              row.status === 'Proposed' ? 'Awaiting review' : row.status,
          },
          {
            key: 'actions',
            header: '',
            stopRowClick: true,
            csvSkip: true,
            render: (row: PaidOutCorrection) =>
              row.status === 'Proposed' && (
                <div className="stack stack-xs">
                  <Button size="sm" disabled={busy} onClick={() => void review(row.id, 'approve')}>
                    Approve
                  </Button>
                  <Field label="Rejection reason">
                    <input
                      value={rejectionReasons[row.id] ?? ''}
                      maxLength={2000}
                      onChange={(event) =>
                        setRejectionReasons((values) => ({ ...values, [row.id]: event.target.value }))
                      }
                    />
                  </Field>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={busy || !rejectionReasons[row.id]?.trim()}
                    onClick={() => void review(row.id, 'reject', rejectionReasons[row.id] ?? '')}
                  >
                    Reject
                  </Button>
                </div>
              ),
          },
        ]}
        rows={result?.items ?? null}
        rowKey={(row: PaidOutCorrection) => row.id}
        csvFileName={`paid-out-corrections-${status.toLowerCase()}.csv`}
        detachTitle={`Paid-out detail review · ${status === 'Proposed' ? 'awaiting review' : status.toLowerCase()}${query ? ` · search: ${query}` : ''}`}
        emptyTitle={count('All') === 0 ? 'No paid-out corrections yet' : 'No corrections match'}
        emptyHint="Corrections proposed from a paid-out event appear here for review."
      />
      {result && (
        <Pager
          page={page}
          pages={result.pages}
          total={result.total}
          pageSize={pageSize}
          onPage={setPage}
          onPageSize={setPageSize}
        />
      )}
    </Card>
  );
}
