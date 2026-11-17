'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api, caseMoney } from '@/lib/api';
import type { CurrencyAmount } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { StatusBadge } from '@/components/CaseTable';
import { CaseLink, useCanOpen, useMe } from '@/components/MeProvider';
import { Alert, Button, Card, DataTable, PageNav, Segmented, Toolbar } from '@/components/ui';
import type { Column } from '@/components/ui';

/** One Sales Audit error type as GET /api/audit-errors lists it. */
export interface ErrorType {
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
  export_impact_configured: boolean;
  export_block_destinations: string[];
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
  transaction_ids?: string[];
  transactions_truncated?: boolean;
}
interface ErrorCases {
  items: ErrorCase[];
  page: number;
  page_size: number;
  has_more: boolean;
}

export const errorPath = (code: string) => `/audit-errors/${encodeURIComponent(code)}`;
const CASES_PER_PAGE = 25;

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
    key: 'transactions',
    header: 'Transactions',
    render: (c) =>
      c.transaction_ids?.length ? (
        <>
          {c.transaction_ids.slice(0, 3).map((id, index) => (
            <span key={id}>
              {index > 0 ? ', ' : ''}
              <Link href={`/transactions/${encodeURIComponent(id)}`}>{id.slice(0, 8)}</Link>
            </span>
          ))}
          {c.transaction_ids.length > 3 && <small>+{c.transaction_ids.length - 3} more</small>}
          {c.transactions_truncated && <small>20+ linked total</small>}
        </>
      ) : (
        '—'
      ),
  },
  {
    key: 'amount',
    header: 'Amount',
    numeric: true,
    render: (c) => caseMoney(c.amount, c.currency),
  },
];

/** What an error means, how it is fixed, and the cases that hold it, paged on the server. */
export function ErrorDetail({ error }: { error: ErrorType }) {
  const router = useRouter();
  const canOpen = useCanOpen();
  const { me } = useMe();
  const canConfigureImpact = me?.role === 'it' || me?.role === 'admin';
  const [destinations, setDestinations] = useState<{ name: string; enabled: boolean }[]>([]);
  const [blockedDestinations, setBlockedDestinations] = useState<string[]>(
    error.export_block_destinations ?? []
  );
  const [impactConfigured, setImpactConfigured] = useState(error.export_impact_configured);
  const [savingImpact, setSavingImpact] = useState(false);
  const [impactMessage, setImpactMessage] = useState('');
  const [destinationsLoaded, setDestinationsLoaded] = useState(false);
  const [destinationsLoading, setDestinationsLoading] = useState(false);
  const [destinationLoadAttempt, setDestinationLoadAttempt] = useState(0);
  const [cases, setCases] = useState<ErrorCases | null>(null);
  const [failed, setFailed] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportMessage, setExportMessage] = useState('');
  // An error with open cases opens on them; one with none (or only closed ones) shows every case.
  const [status, setStatus] = useState<'open' | 'closed' | 'all'>(
    error.open_count > 0 ? 'open' : 'all'
  );
  const [store, setStore] = useState('');
  const [debouncedStore, setDebouncedStore] = useState('');
  const [page, setPage] = useState(1);

  useEffect(() => {
    if (!canConfigureImpact) return;
    let live = true;
    setDestinationsLoading(true);
    setDestinationsLoaded(false);
    setImpactMessage('');
    setImpactConfigured(error.export_impact_configured);
    setBlockedDestinations(error.export_block_destinations ?? []);
    api
      .get('/api/exports/destinations')
      .then((r) => {
        if (live) {
          setDestinations(r.data);
          setDestinationsLoaded(true);
          if (!error.export_impact_configured) {
            setBlockedDestinations(r.data.map((destination: { name: string }) => destination.name));
          }
        }
      })
      .catch(() => {
        if (live) setImpactMessage('Could not load export destinations.');
      })
      .finally(() => {
        if (live) setDestinationsLoading(false);
      });
    return () => {
      live = false;
    };
  }, [canConfigureImpact, destinationLoadAttempt, error.code, error.export_impact_configured]);

  const saveExportImpact = async () => {
    setSavingImpact(true);
    setImpactMessage('');
    try {
      await api.put(`/api/audit-errors/${encodeURIComponent(error.code)}/export-impact`, {
        destinations: blockedDestinations,
      });
      setImpactConfigured(true);
      setImpactMessage('Export impact saved.');
    } catch {
      setImpactMessage('Could not save export impact.');
    } finally {
      setSavingImpact(false);
    }
  };

  useEffect(() => {
    const t = setTimeout(() => setDebouncedStore(store), 300);
    return () => clearTimeout(t);
  }, [store]);

  useEffect(() => {
    let live = true;
    setCases(null);
    setFailed(false);
    api
      .get(`/api/audit-errors/${encodeURIComponent(error.code)}/cases`, {
        params: {
          limit: CASES_PER_PAGE,
          status,
          store: debouncedStore.trim() || undefined,
          page,
        },
      })
      .then((r) => live && setCases(r.data))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [error.code, error.open_count, error.total_count, status, debouncedStore, page]);

  const narrowed = status !== 'all' || debouncedStore.trim() !== '';
  const fixIn = [error.form, error.tab].filter(Boolean).join(' › ');
  const exportCases = async () => {
    setExporting(true);
    setExportMessage('');
    try {
      const response = await api.get(`/api/audit-errors/${encodeURIComponent(error.code)}/cases`, {
        params: {
          status,
          store: debouncedStore.trim() || undefined,
          format: 'csv',
        },
        responseType: 'blob',
      });
      downloadBlob(`audit-error-${error.code}-cases.csv`, response.data);
    } catch (error) {
      const statusCode = (error as { response?: { status?: number } }).response?.status;
      setExportMessage(
        statusCode === 413
          ? 'This export exceeds 50,000 cases. Narrow the status or store filter and try again.'
          : 'Could not download the filtered cases.'
      );
    } finally {
      setExporting(false);
    }
  };
  return (
    <>
      <Card title="About this error">
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
      </Card>
      {canConfigureImpact && (
        <Card title="Downstream export impact">
          {!impactConfigured ? (
            <Alert tone="warning">
              No impact policy is saved. Open occurrences currently block export to every
              destination by default. All known destinations are selected below; save an explicit
              policy to keep this behavior or choose which destinations the error should block.
            </Alert>
          ) : (
            <p className="muted">
              Open occurrences block export only to the selected destinations. With none selected,
              the error remains visible for audit but does not block delivery.
            </p>
          )}
          <div className="stack-sm">
            {destinationsLoading && <p className="muted">Loading export destinations…</p>}
            {!destinationsLoading && !destinationsLoaded && impactMessage && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setDestinationLoadAttempt((n) => n + 1)}
              >
                Retry destination load
              </Button>
            )}
            {destinations.map((destination) => (
              <label key={destination.name} className="check-row">
                <input
                  type="checkbox"
                  checked={blockedDestinations.includes(destination.name)}
                  onChange={(event) =>
                    setBlockedDestinations((current) =>
                      event.target.checked
                        ? [...current, destination.name]
                        : current.filter((name) => name !== destination.name)
                    )
                  }
                />
                <span>
                  {destination.name}
                  {destination.enabled ? '' : ' (disabled)'}
                </span>
              </label>
            ))}
          </div>
          <Button
            size="sm"
            disabled={savingImpact || !destinationsLoaded || destinationsLoading}
            onClick={saveExportImpact}
          >
            {savingImpact ? 'Saving…' : 'Save export impact'}
          </Button>
          {impactMessage && <small role="status">{impactMessage}</small>}
        </Card>
      )}
      {!canConfigureImpact && (
        <Card title="Downstream export impact">
          {error.export_impact_configured ? (
            <p>
              Open occurrences block{' '}
              {error.export_block_destinations.length
                ? `export to ${error.export_block_destinations.join(', ')}.`
                : 'no downstream destinations.'}
            </p>
          ) : (
            <Alert tone="warning">
              No impact policy is configured. Open occurrences block export to every destination by
              default.
            </Alert>
          )}
        </Card>
      )}
      <Card title="Cases">
        <Toolbar>
          <Segmented
            value={status}
            onChange={(v) => {
              setStatus(v);
              setPage(1);
            }}
            options={[
              { value: 'open', label: 'Open' },
              { value: 'closed', label: 'Closed' },
              { value: 'all', label: 'All' },
            ]}
          />
          <input
            className="grow"
            type="search"
            value={store}
            onChange={(e) => {
              setStore(e.target.value);
              setPage(1);
            }}
            placeholder="Filter by store"
            aria-label="Filter cases by store"
          />
          <Button size="sm" disabled={exporting || !cases?.items.length} onClick={exportCases}>
            {exporting ? 'Preparing…' : 'Download filtered CSV'}
          </Button>
        </Toolbar>
        {exportMessage && <Alert tone="error">{exportMessage}</Alert>}
        {failed ? (
          <Alert tone="error">Could not load the cases for this error.</Alert>
        ) : (
          <DataTable
            columns={CASE_COLUMNS}
            rows={cases ? cases.items : null}
            rowKey={(c) => c.id}
            loadingRows={2}
            emptyTitle={narrowed ? 'No cases match these filters' : 'None raised yet'}
            emptyHint={narrowed ? 'Try another status, or clear the store filter.' : undefined}
            footer={
              cases && (cases.has_more || page > 1) ? (
                <PageNav page={page} hasMore={cases.has_more} onPage={setPage} />
              ) : undefined
            }
          />
        )}
      </Card>
    </>
  );
}
