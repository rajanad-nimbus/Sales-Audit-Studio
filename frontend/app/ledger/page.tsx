'use client';

import { formatNumber } from '@/lib/preferences';
import { useCallback, useEffect, useState } from 'react';
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
    key: 'transaction',
    header: 'Transaction',
    primary: true,
    render: (row) => <code>{row.transaction_id}</code>,
  },
  { key: 'debit', header: 'Debit account', render: (row) => row.debit_account },
  { key: 'credit', header: 'Credit account', render: (row) => row.credit_account },
  {
    key: 'amount',
    header: 'Amount',
    numeric: true,
    render: (row) => money(row.amount, row.currency),
  },
];

export default function LedgerPage() {
  const [initialQuery] = useState(() => {
    const query =
      typeof window === 'undefined' ? null : new URLSearchParams(window.location.search);
    return {
      store: query?.get('store_id') ?? '',
      businessDate: query?.get('business_date') ?? '',
    };
  });
  const [store, setStore] = useState(initialQuery.store);
  const [businessDate, setBusinessDate] = useState(
    initialQuery.businessDate || new Date().toISOString().slice(0, 10)
  );
  const [page, setPage] = useState(1);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const load = useCallback(
    async (nextPage = 1) => {
      if (!store || !businessDate) return;
      setLoading(true);
      setError('');
      try {
        const { data } = await api.get<Preview>('/api/gl/journal-preview', {
          params: { store_id: store, business_date: businessDate, page: nextPage, page_size: 100 },
        });
        setPreview(data);
        setPage(nextPage);
      } catch (e: unknown) {
        const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
        setError(
          detail ||
            'The journal preview could not be loaded. Check the store and business date, then try again.'
        );
        setPreview(null);
      } finally {
        setLoading(false);
      }
    },
    [businessDate, store]
  );
  const downloadJournal = async () => {
    if (!preview) return;
    setExporting(true);
    setExportError('');
    try {
      const { data } = await api.get('/api/gl/journal-preview/export.csv', {
        params: { store_id: store, business_date: businessDate },
        responseType: 'blob',
      });
      downloadBlob(`journal-preview-${store}-${businessDate}.csv`, data);
    } catch {
      setExportError('The complete journal preview could not be downloaded.');
    } finally {
      setExporting(false);
    }
  };
  useEffect(() => {
    if (initialQuery.store && initialQuery.businessDate) void load(1);
  }, [initialQuery, load]);
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
              title="Download all mapped and unmapped transactions for this store day."
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
            <span>Business date</span>
            <input
              type="date"
              value={businessDate}
              onChange={(event) => {
                setBusinessDate(event.target.value);
                setPreview(null);
              }}
            />
          </label>
          <Button
            variant="primary"
            disabled={!store || !businessDate || loading}
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
              label="Transactions in store day"
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
                      <code>{id}</code>
                    </li>
                  ))}
                </ul>
              </Card>
            </>
          )}
          <Card>
            <h2>
              {store} · {businessDate}
            </h2>
            <DataTable
              columns={columns}
              rows={loading ? null : preview.lines}
              rowKey={(row) => row.transaction_id}
              emptyTitle={
                preview.total_transactions
                  ? 'No mapped lines on this page'
                  : 'No transactions for this store day'
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
            Select a store and date to inspect its proposed journal. This view does not post to the
            ledger.
          </p>
        </Card>
      )}
    </main>
  );
}
