'use client';

import Link from 'next/link';
import { formatNumber } from '@/lib/preferences';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, money } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { StoreSelect } from '@/components/StoreSelect';
import {
  Alert,
  Button,
  Card,
  DataTable,
  MetricGrid,
  MetricTile,
  PageHeader,
  Pager,
} from '@/components/ui';
import type { Column } from '@/components/ui';

interface JournalLine {
  transaction_id: string;
  store_id: string;
  business_date: string;
  debit_account: string;
  credit_account: string;
  amount: string;
  currency: string;
}
interface Preview {
  lines: JournalLine[];
  unmapped_transaction_ids: string[];
  total_transactions: number;
  page: number;
  page_size: number;
  pages: number;
}

const columns: Column<JournalLine>[] = [
  {
    key: 'business-date',
    header: 'Business date',
    csvValue: (row) => row.business_date,
    render: (row) => row.business_date,
  },
  {
    key: 'transaction',
    header: 'Transaction',
    primary: true,
    csvValue: (row) => row.transaction_id,
    render: (row) => (
      <Link href={`/transactions/${encodeURIComponent(row.transaction_id)}`}>
        <code>{row.transaction_id}</code>
      </Link>
    ),
  },
  {
    key: 'debit',
    header: 'Debit account',
    csvValue: (row) => row.debit_account,
    render: (row) => row.debit_account,
  },
  {
    key: 'credit',
    header: 'Credit account',
    csvValue: (row) => row.credit_account,
    render: (row) => row.credit_account,
  },
  {
    key: 'amount',
    header: 'Amount',
    numeric: true,
    csvValue: (row) => `${row.amount} ${row.currency}`,
    render: (row) => money(row.amount, row.currency),
  },
];

export default function LedgerPage() {
  const [initialQuery] = useState(() => {
    const query =
      typeof window === 'undefined' ? null : new URLSearchParams(window.location.search);
    const legacyDate = query?.get('business_date') ?? '';
    return {
      store: query?.get('store_id') ?? '',
      dateFrom: query?.get('date_from') ?? legacyDate,
      dateTo: query?.get('date_to') ?? query?.get('date_from') ?? legacyDate,
    };
  });
  const [store, setStore] = useState(initialQuery.store);
  const today = new Date().toISOString().slice(0, 10);
  const [dateFrom, setDateFrom] = useState(initialQuery.dateFrom || today);
  const [dateTo, setDateTo] = useState(initialQuery.dateTo || today);
  const [page, setPage] = useState(1);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const initialLoadDone = useRef(false);
  const load = useCallback(
    async (nextPage = 1) => {
      if (!store || !dateFrom || !dateTo || dateFrom > dateTo) return;
      setLoading(true);
      setError('');
      try {
        const { data } = await api.get<Preview>('/api/gl/journal-preview', {
          params: {
            store_id: store,
            date_from: dateFrom,
            date_to: dateTo,
            page: nextPage,
            page_size: 100,
          },
        });
        setPreview(data);
        setPage(nextPage);
      } catch (e: unknown) {
        const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
        setError(
          detail ||
            'The journal preview could not be loaded. Check the store and business-date range, then try again.'
        );
        setPreview(null);
      } finally {
        setLoading(false);
      }
    },
    [dateFrom, dateTo, store]
  );
  const downloadJournal = async () => {
    if (!preview) return;
    setExporting(true);
    setExportError('');
    try {
      const { data } = await api.get('/api/gl/journal-preview/export.csv', {
        params: { store_id: store, date_from: dateFrom, date_to: dateTo },
        responseType: 'blob',
      });
      downloadBlob(`journal-preview-${store}-${dateFrom}-${dateTo}.csv`, data);
    } catch {
      setExportError('The complete journal preview could not be downloaded.');
    } finally {
      setExporting(false);
    }
  };
  useEffect(() => {
    if (
      !initialLoadDone.current &&
      initialQuery.store &&
      initialQuery.dateFrom &&
      initialQuery.dateTo
    ) {
      initialLoadDone.current = true;
      void load(1);
    }
  }, [initialQuery, load]);
  useEffect(() => {
    const params = new URLSearchParams();
    if (store) params.set('store_id', store);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    const query = params.toString();
    history.replaceState(
      null,
      '',
      query ? `${window.location.pathname}?${query}` : window.location.pathname
    );
  }, [store, dateFrom, dateTo]);
  const pageTotals = new Map<string, number>();
  preview?.lines.forEach((line) =>
    pageTotals.set(line.currency, (pageTotals.get(line.currency) ?? 0) + Number(line.amount))
  );
  const balancedByCurrency =
    [...pageTotals.entries()]
      .map(
        ([currency, amount]) =>
          `${money(amount, currency)} debit / ${money(amount, currency)} credit`
      )
      .join(' · ') || '—';
  const mappedOnPage = preview?.lines.length ?? 0;
  const unmappedOnPage = preview?.unmapped_transaction_ids.length ?? 0;

  return (
    <main className="page-container">
      <PageHeader
        title="General Ledger View"
        subtitle="Inspect the journal lines produced from audited store transactions before export."
        actions={
          preview && (
            <Button
              size="sm"
              variant="secondary"
              disabled={loading || exporting}
              title="Download all mapped and unmapped transactions for this store and date range."
              onClick={() => void downloadJournal()}
            >
              {exporting ? 'Preparing CSV…' : 'Download full journal CSV'}
            </Button>
          )
        }
      />
      <Card>
        <div className="toolbar" style={{ alignItems: 'end' }}>
          <StoreSelect
            value={store}
            onChange={(value) => {
              setStore(value);
              setPreview(null);
            }}
          />
          <label className="field">
            <span>From business date</span>
            <input
              type="date"
              value={dateFrom}
              onChange={(event) => {
                setDateFrom(event.target.value);
                setPreview(null);
              }}
            />
          </label>
          <label className="field">
            <span>Through business date</span>
            <input
              type="date"
              value={dateTo}
              onChange={(event) => {
                setDateTo(event.target.value);
                setPreview(null);
              }}
            />
          </label>
          <Button
            variant="primary"
            disabled={!store || !dateFrom || !dateTo || dateFrom > dateTo || loading}
            onClick={() => void load(1)}
          >
            {loading ? 'Building preview…' : 'View journal'}
          </Button>
        </div>
      </Card>
      {error && <Alert tone="error">{error}</Alert>}
      {exportError && (
        <Alert tone="error" onDismiss={() => setExportError('')}>
          {exportError}
        </Alert>
      )}
      {preview && (
        <>
          <MetricGrid>
            <MetricTile
              label="Matching transactions"
              value={formatNumber(preview.total_transactions)}
            />
            <MetricTile label="Mapped on this page" value={formatNumber(mappedOnPage)} />
            <MetricTile label="Unmapped on this page" value={formatNumber(unmappedOnPage)} />
            <MetricTile label="Page debits / credits by currency" value={balancedByCurrency} />
          </MetricGrid>
          {unmappedOnPage > 0 && (
            <>
              <Alert tone="warning">
                {unmappedOnPage} transaction{unmappedOnPage === 1 ? '' : 's'} on this page have no
                active GL mapping. Add a tender-specific or default mapping under Settings →
                Controls before exporting.
              </Alert>
              <Card>
                <h2>Transactions needing a mapping</h2>
                <ul>
                  {preview.unmapped_transaction_ids.map((id) => (
                    <li key={id}>
                      <Link href={`/transactions/${encodeURIComponent(id)}`}>
                        <code>{id}</code>
                      </Link>
                    </li>
                  ))}
                </ul>
              </Card>
            </>
          )}
          <Card>
            <h2>
              {store} · {dateFrom} to {dateTo}
            </h2>
            <DataTable
              columns={columns}
              rows={loading ? null : preview.lines}
              csvFileName="journal-preview-visible.csv"
              rowKey={(row) => row.transaction_id}
              emptyTitle={
                preview.total_transactions
                  ? 'No mapped lines on this page'
                  : 'No transactions for this store and date range'
              }
              emptyHint={
                preview.total_transactions
                  ? 'Unmapped transactions are listed above.'
                  : 'Choose another store or date to review journal activity.'
              }
              footer={
                <Pager
                  page={page}
                  pages={preview.pages}
                  total={preview.total_transactions}
                  pageSize={100}
                  pageSizes={[100]}
                  onPage={(nextPage) => void load(nextPage)}
                  onPageSize={() => {}}
                />
              }
            />
          </Card>
        </>
      )}
      {!preview && !error && (
        <Card>
          <p>
            Select a store and business-date range to inspect its proposed journal. This view does
            not post to the ledger.
          </p>
        </Card>
      )}
    </main>
  );
}
