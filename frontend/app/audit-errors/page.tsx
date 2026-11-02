'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api, caseMoney, currencyBreakdown } from '@/lib/api';
import type { CurrencyAmount } from '@/lib/api';
import { useDataChanged } from '@/lib/sync';
import { StatusBadge } from '@/components/CaseTable';
import { CaseLink, useCanOpen } from '@/components/MeProvider';
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
} from '@/components/ui';
import type { Column } from '@/components/ui';

interface ErrorType {
  code: string;
  label: string;
  family: string;
  description: string | null;
  form: string | null;
  tab: string | null;
  recommended_solution: string | null;
  known_causes: string[];
  resolution: string | null;
  open_count: number;
  total_count: number;
  latest_at: string | null;
  open_by_currency: CurrencyAmount[];
}
interface Overview {
  items: ErrorType[];
  open_total: number;
  open_types: number;
}
interface ErrorCase {
  id: string;
  case_number: string;
  status: string;
  store_id: string;
  business_date: string;
  amount: string;
  currency: string | null;
  priority: string;
  errors: number;
}
interface ErrorCases {
  items: ErrorCase[];
  truncated: boolean;
}

const COLUMNS: Column<ErrorType>[] = [
  {
    key: 'error',
    header: 'Error',
    primary: true,
    render: (r) => (
      <>
        {r.label}
        <div className="act-sub">{r.code}</div>
      </>
    ),
  },
  { key: 'family', header: 'Family', render: (r) => r.family },
  {
    key: 'open',
    header: 'Open',
    numeric: true,
    render: (r) => (
      <Badge variant={r.open_count ? 'error' : 'secondary'} size="sm">
        {r.open_count.toLocaleString()}
      </Badge>
    ),
  },
  { key: 'total', header: 'Raised', numeric: true, render: (r) => r.total_count.toLocaleString() },
  {
    key: 'amount',
    header: 'Open amount',
    numeric: true,
    render: (r) => (r.open_count ? currencyBreakdown(r.open_by_currency, 'amount') : '—'),
  },
  {
    key: 'latest',
    header: 'Last raised',
    nowrap: true,
    render: (r) => (r.latest_at ? r.latest_at.slice(0, 10) : '—'),
  },
];

const CASE_COLUMNS: Column<ErrorCase>[] = [
  {
    key: 'case',
    header: 'Case',
    primary: true,
    stopRowClick: true,
    render: (c) => <CaseLink id={c.id}>{c.case_number}</CaseLink>,
  },
  { key: 'store', header: 'Store', render: (c) => c.store_id },
  { key: 'date', header: 'Business date', nowrap: true, render: (c) => c.business_date },
  { key: 'status', header: 'Status', render: (c) => <StatusBadge s={c.status} /> },
  { key: 'errors', header: 'Errors', numeric: true, render: (c) => c.errors },
  {
    key: 'amount',
    header: 'Amount',
    numeric: true,
    render: (c) => caseMoney(c.amount, c.currency),
  },
];

function ErrorDetail({ error }: { error: ErrorType }) {
  const router = useRouter();
  const canOpen = useCanOpen();
  const [cases, setCases] = useState<ErrorCases | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setCases(null);
    setFailed(false);
    if (!error.total_count) return;
    api
      .get(`/api/audit-errors/${encodeURIComponent(error.code)}/cases`, { params: { limit: 25 } })
      .then((r) => live && setCases(r.data))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [error.code, error.total_count]);

  const fixIn = [error.form, error.tab].filter(Boolean).join(' › ');
  return (
    <div className="case-stack">
      <dl className="kv">
        <dt>What it means</dt>
        <dd>{error.description ?? error.label}</dd>
        {fixIn && (
          <>
            <dt>Fix in</dt>
            <dd>{fixIn}</dd>
          </>
        )}
        {error.recommended_solution && (
          <>
            <dt>How to fix</dt>
            <dd>{error.recommended_solution.replaceAll(' -> ', ' → ')}</dd>
          </>
        )}
        {error.known_causes.length > 0 && (
          <>
            <dt>Common causes</dt>
            <dd>{error.known_causes.join(' · ')}</dd>
          </>
        )}
        {error.resolution && (
          <>
            <dt>Resolution</dt>
            <dd>{error.resolution}</dd>
          </>
        )}
      </dl>
      {error.form === 'Transaction Detail' && canOpen('/corrections') && (
        <div>
          <Button size="sm" onClick={() => router.push('/corrections')}>
            Open Transaction Corrections
          </Button>
        </div>
      )}
      {error.total_count === 0 ? (
        <p className="hint">None raised yet.</p>
      ) : failed ? (
        <Alert tone="error">Could not load the cases for this error.</Alert>
      ) : (
        <DataTable
          columns={CASE_COLUMNS}
          rows={cases ? cases.items : null}
          rowKey={(c) => c.id}
          loadingRows={2}
          footer={
            cases?.truncated ? (
              <p className="hint">Showing the first {cases.items.length} cases.</p>
            ) : undefined
          }
        />
      )}
    </div>
  );
}

export default function AuditErrorsPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [show, setShow] = useState<'open' | 'all'>('open');
  const [family, setFamily] = useState('');
  const [q, setQ] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get('/api/audit-errors');
      setData(data);
      setError(null);
    } catch (e) {
      setError(
        (e as { response?: { status?: number } })?.response?.status === 403
          ? 'Your role cannot view audit errors.'
          : 'Could not load audit errors.'
      );
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  useDataChanged(() => {
    load();
  });
  // A link such as /audit-errors?code=TRAN_OUT_BAL opens that error's detail straight away.
  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get('code');
    if (code) {
      setShow('all');
      setExpanded(code);
    }
  }, []);

  const families = data ? [...new Set(data.items.map((i) => i.family))].sort() : [];
  const term = q.trim().toLowerCase();
  const rows = data
    ? data.items.filter(
        (i) =>
          (show === 'all' || i.open_count > 0) &&
          (!family || i.family === family) &&
          (!term || `${i.label} ${i.code}`.toLowerCase().includes(term))
      )
    : null;

  return (
    <div className="page-container">
      <PageHeader
        title="Audit Errors"
        subtitle="Every Sales Audit error type, how many are open, and how each one is fixed. Open one to see what it means, where it is corrected, and the cases that hold it."
      />
      {error && (
        <Alert
          tone="error"
          action={
            <Button size="sm" className="alert-close" onClick={load}>
              Retry
            </Button>
          }
        >
          {error}
        </Alert>
      )}

      <MetricGrid>
        <MetricTile label="Open errors" value={data ? data.open_total.toLocaleString() : '—'} />
        <MetricTile
          label="Error types affected"
          value={data ? data.open_types.toLocaleString() : '—'}
        />
        <MetricTile label="Error types known" value={data ? data.items.length : '—'} />
      </MetricGrid>

      <Toolbar count={rows ? `${rows.length} shown` : ''}>
        <Segmented
          options={[
            { value: 'open', label: 'With open errors' },
            { value: 'all', label: 'All error types' },
          ]}
          value={show}
          onChange={setShow}
        />
        <input
          className="grow"
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search error name or code"
          aria-label="Search audit errors"
        />
        <select aria-label="Family" value={family} onChange={(e) => setFamily(e.target.value)}>
          <option value="">All families</option>
          {families.map((f) => (
            <option key={f}>{f}</option>
          ))}
        </select>
      </Toolbar>

      <DataTable
        columns={COLUMNS}
        rows={rows}
        rowKey={(r) => r.code}
        onRowClick={(r) => setExpanded((k) => (k === r.code ? null : r.code))}
        expandedKey={expanded}
        renderDetail={(r) => <ErrorDetail error={r} />}
        emptyTitle={show === 'open' ? 'No open audit errors' : 'No error types match'}
        emptyHint={
          show === 'open'
            ? 'Nothing needs fixing. Switch to All error types to see what the audit checks for.'
            : 'Try clearing the search or family.'
        }
      />
    </div>
  );
}
