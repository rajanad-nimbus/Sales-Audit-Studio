'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import { StoreSelect } from '@/components/StoreSelect';
import { Alert, Button, Card, DataTable, Pager, PageHeader } from '@/components/ui';
import type { Column } from '@/components/ui';
import { api, money } from '@/lib/api';
import { formatRollupKey } from '@/lib/storeDays';
import { downloadBlob } from '@/lib/csv';
import { formatNumber } from '@/lib/preferences';

interface TotalHistoryRow {
  store_id: string;
  business_date: string;
  store_day_status: string;
  current_version: number;
  version: number;
  total_name: string;
  level: string;
  dimension: string | null;
  rollup_key: string | null;
  currency: string;
  calculated_amount: string;
  declared_amount: string | null;
  variance_amount: string | null;
  previous_amount: string | null;
  change_amount: string | null;
  definition_revision: number | null;
  previous_definition_revision: number | null;
  created_at: string;
}
interface Result {
  items: TotalHistoryRow[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}
interface HistoryFilters {
  store: string;
  dateFrom: string;
  dateTo: string;
  name: string;
  level: string;
  dimension: string;
  minVersion: string;
}

const emptyFilters: HistoryFilters = {
  store: '',
  dateFrom: '',
  dateTo: '',
  name: '',
  level: '',
  dimension: '',
  minVersion: '',
};

function writeHistoryUrl(filters: HistoryFilters, page: number) {
  const params = new URLSearchParams();
  const values: [string, string][] = [
    ['store_id', filters.store],
    ['date_from', filters.dateFrom],
    ['date_to', filters.dateTo],
    ['total_name', filters.name],
    ['level', filters.level],
    ['dimension', filters.dimension],
    ['min_version', filters.minVersion],
  ];
  for (const [key, value] of values) if (value) params.set(key, value);
  if (page > 1) params.set('page', String(page));
  const query = params.toString();
  history.replaceState(null, '', query ? `${location.pathname}?${query}` : location.pathname);
}

const columns: Column<TotalHistoryRow>[] = [
  {
    key: 'total',
    header: 'Total',
    primary: true,
    csvValue: (row) =>
      [row.total_name, row.level, row.dimension, row.rollup_key && formatRollupKey(row.rollup_key)]
        .filter(Boolean)
        .join(' · '),
    render: (row) => (
      <>
        <strong>{row.total_name}</strong>
        <small>
          {row.level}
          {row.dimension ? ` · ${row.dimension}` : ''}
          {row.rollup_key ? ` · ${formatRollupKey(row.rollup_key)}` : ''}
        </small>
      </>
    ),
  },
  {
    key: 'store_day',
    header: 'Store day',
    csvValue: (row) => `${row.store_id} · ${row.business_date}`,
    render: (row) => (
      <Link href={`/store-days/${encodeURIComponent(row.store_id)}/${row.business_date}`}>
        {row.store_id} · {row.business_date}
      </Link>
    ),
  },
  {
    key: 'version',
    header: 'Version',
    csvValue: (row) =>
      [
        `v${row.version}${row.version === row.current_version ? ' · current' : ''}`,
        row.store_day_status,
        `definition r${row.definition_revision ?? '—'}`,
      ].join(' · '),
    render: (row) => (
      <>
        v{row.version} {row.version === row.current_version ? '· current' : ''}
        <small>
          {row.store_day_status} · definition r{row.definition_revision ?? '—'}
        </small>
      </>
    ),
  },
  {
    key: 'calculated',
    header: 'Calculated',
    numeric: true,
    csvValue: (row) => `${row.calculated_amount} ${row.currency}`,
    render: (row) => money(row.calculated_amount, row.currency),
  },
  {
    key: 'declared',
    header: 'Declared',
    numeric: true,
    csvValue: (row) =>
      row.declared_amount === null ? '—' : `${row.declared_amount} ${row.currency}`,
    render: (row) =>
      row.declared_amount === null ? '—' : money(row.declared_amount, row.currency),
  },
  {
    key: 'variance',
    header: 'Variance',
    numeric: true,
    csvValue: (row) =>
      row.variance_amount === null ? '—' : `${row.variance_amount} ${row.currency}`,
    render: (row) =>
      row.variance_amount === null ? '—' : money(row.variance_amount, row.currency),
  },
  {
    key: 'change',
    header: 'Change vs prior version',
    numeric: true,
    csvValue: (row) =>
      row.change_amount === null ? 'First version' : `${row.change_amount} ${row.currency}`,
    render: (row) =>
      row.change_amount === null ? 'First version' : money(row.change_amount, row.currency),
  },
];

export default function TotalHistoryPage() {
  const [store, setStore] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [name, setName] = useState('');
  const [level, setLevel] = useState('');
  const [dimension, setDimension] = useState('');
  const [minVersion, setMinVersion] = useState('');
  const [applied, setApplied] = useState<HistoryFilters>(emptyFilters);
  const [result, setResult] = useState<Result | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState('');
  const [linkCopied, setLinkCopied] = useState(false);
  const [linkNotice, setLinkNotice] = useState('');
  const requestId = useRef(0);

  const load = useCallback(async (nextPage = 1, filters: HistoryFilters = emptyFilters) => {
    const currentRequest = ++requestId.current;
    setBusy(true);
    setError('');
    try {
      const { data } = await api.get<Result>('/api/total-history', {
        params: {
          store_id: filters.store || undefined,
          date_from: filters.dateFrom || undefined,
          date_to: filters.dateTo || undefined,
          total_name: filters.name || undefined,
          level: filters.level || undefined,
          dimension: filters.dimension || undefined,
          min_version: filters.minVersion || undefined,
          page: nextPage,
          page_size: 50,
        },
      });
      if (currentRequest !== requestId.current) return;
      setResult(data);
      setPage(data.page);
      writeHistoryUrl(filters, data.page);
    } catch (err) {
      if (currentRequest !== requestId.current) return;
      setError(
        (err as { response?: { data?: { detail?: string } } }).response?.data?.detail ??
          'Could not load the total audit trail.'
      );
    } finally {
      if (currentRequest === requestId.current) setBusy(false);
    }
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const filters: HistoryFilters = {
      store: params.get('store_id') ?? '',
      dateFrom: params.get('date_from') ?? '',
      dateTo: params.get('date_to') ?? '',
      name: params.get('total_name') ?? '',
      level: params.get('level') ?? '',
      dimension: params.get('dimension') ?? '',
      minVersion: params.get('min_version') ?? '',
    };
    const requestedPage = Number(params.get('page'));
    const initialPage = Number.isInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
    setStore(filters.store);
    setDateFrom(filters.dateFrom);
    setDateTo(filters.dateTo);
    setName(filters.name);
    setLevel(filters.level);
    setDimension(filters.dimension);
    setMinVersion(filters.minVersion);
    setApplied(filters);
    setPage(initialPage);
    void load(initialPage, filters);
    return () => {
      requestId.current += 1;
    };
  }, [load]);

  function apply(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const filters = { store, dateFrom, dateTo, name, level, dimension, minVersion };
    setApplied(filters);
    setResult(null);
    writeHistoryUrl(filters, 1);
    void load(1, filters);
  }

  async function copyHistoryLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setLinkCopied(true);
      setLinkNotice('');
      window.setTimeout(() => setLinkCopied(false), 1800);
    } catch {
      setLinkNotice('Could not copy the link. You can copy it from the address bar.');
    }
  }

  async function exportHistory() {
    setExportBusy(true);
    setExportError('');
    try {
      const { data } = await api.get('/api/total-history/export.csv', {
        params: {
          store_id: applied.store || undefined,
          date_from: applied.dateFrom || undefined,
          date_to: applied.dateTo || undefined,
          total_name: applied.name || undefined,
          level: applied.level || undefined,
          dimension: applied.dimension || undefined,
          min_version: applied.minVersion || undefined,
        },
        responseType: 'blob',
      });
      downloadBlob('total-audit-history.csv', data);
    } catch (cause) {
      const statusCode = (cause as { response?: { status?: number } })?.response?.status;
      setExportError(
        statusCode === 413
          ? 'The matching history exceeds 50,000 rows. Add a store, date, total, or version filter and try again.'
          : 'Could not export the filtered total history.'
      );
    } finally {
      setExportBusy(false);
    }
  }

  const detachTitle = [
    'Total Audit Trail',
    applied.store ? `Store ${applied.store}` : 'All stores',
    applied.dateFrom || applied.dateTo
      ? `${applied.dateFrom || 'any date'} → ${applied.dateTo || 'latest'}`
      : '',
    applied.name || 'All totals',
    applied.level || 'All levels',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <main className="page-container">
      <PageHeader
        title="Total Audit Trail"
        subtitle="Search calculated total revisions across store days and compare each version with its predecessor."
        actions={
          result && (
            <div className="row-actions">
              <Button size="sm" variant="secondary" onClick={() => void copyHistoryLink()}>
                {linkCopied ? 'Link copied' : 'Copy report link'}
              </Button>
              <Button
                size="sm"
                disabled={busy || exportBusy || result.total > 50_000}
                title={
                  result.total > 50_000
                    ? 'Narrow the search to export up to 50,000 matching revisions.'
                    : 'Export every row matching the applied search filters.'
                }
                onClick={() => void exportHistory()}
              >
                {exportBusy ? 'Preparing CSV…' : 'Export matching CSV'}
              </Button>
            </div>
          )
        }
      />
      {linkNotice && <Alert tone="warning">{linkNotice}</Alert>}
      <Card>
        <form onSubmit={apply} className="toolbar" style={{ alignItems: 'end', flexWrap: 'wrap' }}>
          <StoreSelect value={store} onChange={setStore} emptyLabel="All stores" />
          <label className="field">
            <span>From</span>
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          </label>
          <label className="field">
            <span>To</span>
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </label>
          <label className="field">
            <span>Total name</span>
            <input
              value={name}
              maxLength={100}
              onChange={(e) => setName(e.target.value)}
              placeholder="Search totals"
            />
          </label>
          <label className="field">
            <span>Level</span>
            <input
              value={level}
              maxLength={24}
              onChange={(e) => setLevel(e.target.value)}
              placeholder="Store, Register…"
            />
          </label>
          <label className="field">
            <span>Dimension</span>
            <input
              value={dimension}
              maxLength={100}
              onChange={(e) => setDimension(e.target.value)}
              placeholder="Register, tender…"
            />
          </label>
          <label className="field">
            <span>From version</span>
            <input
              type="number"
              min="1"
              value={minVersion}
              onChange={(e) => setMinVersion(e.target.value)}
              placeholder="Any"
            />
          </label>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Searching…' : 'Search trail'}
          </Button>
        </form>
      </Card>
      {error && <Alert tone="error">{error}</Alert>}
      {exportError && (
        <Alert tone="error" onDismiss={() => setExportError('')}>
          {exportError}
        </Alert>
      )}
      {result && result.total > 50_000 && (
        <Alert tone="warning">
          {formatNumber(result.total)} revisions match. Narrow the search to export up to 50,000
          rows.
        </Alert>
      )}
      {result && (
        <Card>
          <DataTable
            columns={columns}
            rows={busy ? null : result.items}
            csvFileName="total-history-visible.csv"
            detachTitle={detachTitle}
            rowKey={(row) =>
              `${row.store_id}:${row.business_date}:${row.total_name}:${row.level}:${row.dimension ?? ''}:${row.version}`
            }
            emptyTitle="No total revisions found"
            emptyHint="Change the store, date range, total name, or version and search again."
            footer={
              <Pager
                page={page}
                pages={result.pages}
                total={result.total}
                pageSize={50}
                pageSizes={[50]}
                onPage={(next) => void load(next, applied)}
                onPageSize={() => {}}
              />
            }
          />
        </Card>
      )}
      {!result && !error && (
        <Card>
          <p>
            Search across versioned totals to review historical values and recalculation changes.
          </p>
        </Card>
      )}
    </main>
  );
}
