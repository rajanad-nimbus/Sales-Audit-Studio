'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api, money } from '@/lib/api';
import { useToast } from '@/components/Feedback';
import { downloadBlob } from '@/lib/csv';
import { StoreSelect } from '@/components/StoreSelect';
import {
  Alert,
  Button,
  DataTable,
  MetricGrid,
  MetricTile,
  PageNav,
  Segmented,
  Toolbar,
} from '@/components/ui';

type Group = 'store' | 'register' | 'cashier' | 'date';
interface Row {
  store_id?: string;
  business_date?: string;
  register_id?: string | null;
  cashier_id?: string | null;
  declarations: number;
  out_of_balance: number;
  short: string;
  over: string;
  net_short: string;
}
interface Report {
  has_more: boolean;
  page: number;
  page_size: number;
  rows: Row[];
  totals: { declarations: number; short: string; over: string };
}

const label = (r: Row, group: Group) =>
  group === 'date'
    ? r.business_date
    : group === 'store'
      ? r.store_id
      : `${r.store_id} · ${(group === 'register' ? r.register_id : r.cashier_id) ?? 'None'}`;

const validDate = (value: string | null) => {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return '';
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value ? value : '';
};

const cashHistoryHref = (
  row: Row,
  group: Group,
  storeFilter: string,
  start: string,
  end: string
) => {
  const params = new URLSearchParams();
  const store = group === 'store' ? row.store_id : row.store_id || storeFilter;
  const from = group === 'date' ? row.business_date : start;
  const to = group === 'date' ? row.business_date : end;
  if (store) params.set('cash_store', store);
  if (from) params.set('cash_start', from);
  if (to) params.set('cash_end', to);
  return `/cash-office?${params.toString()}`;
};

/** Cash over and short, with short and over kept apart so one till cannot hide another. */
export function OverShortReport() {
  const toast = useToast();
  const [group, setGroup] = useState<Group>('store');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [storeId, setStoreId] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [exporting, setExporting] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [failed, setFailed] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initialGroup = params.get('over_short_group');
    const initialPage = Number(params.get('over_short_page'));
    const initialPageSize = Number(params.get('over_short_page_size'));
    if (initialGroup === 'store' || initialGroup === 'register' || initialGroup === 'cashier' || initialGroup === 'date') setGroup(initialGroup);
    const initialStart = validDate(params.get('over_short_start'));
    const initialEnd = validDate(params.get('over_short_end'));
    setStart(initialStart && initialEnd && initialStart > initialEnd ? initialEnd : initialStart);
    setEnd(initialStart && initialEnd && initialStart > initialEnd ? initialStart : initialEnd);
    setStoreId(params.get('over_short_store') ?? '');
    if (Number.isInteger(initialPage) && initialPage > 0 && initialPage <= 100_000) setPage(initialPage);
    if ([25, 50, 100, 200].includes(initialPageSize)) setPageSize(initialPageSize);
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    const values: Record<string, string> = {
      over_short_group: group,
      over_short_page: String(page),
      over_short_page_size: String(pageSize),
      over_short_start: start,
      over_short_end: end,
      over_short_store: storeId,
    };
    for (const [key, value] of Object.entries(values)) {
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    window.history.replaceState(null, '', url);
  }, [group, page, pageSize, start, end, storeId]);

  const copyReportLink = async () => {
    try {
      const url = new URL(window.location.href);
      const values: Record<string, string> = {
        over_short_group: group,
        over_short_page: String(page),
        over_short_page_size: String(pageSize),
        over_short_start: start,
        over_short_end: end,
        over_short_store: storeId,
      };
      for (const [key, value] of Object.entries(values)) {
        if (value) url.searchParams.set(key, value);
        else url.searchParams.delete(key);
      }
      await navigator.clipboard.writeText(url.toString());
      setLinkCopied(true);
      window.setTimeout(() => setLinkCopied(false), 1800);
    } catch {
      toast('Could not copy the report link. You can copy it from the address bar.', 'error');
    }
  };

  useEffect(() => {
    setReport(null);
    setFailed(false);
    api
      .get<Report>('/api/cash-controls/over-short', {
        params: {
          group_by: group,
          start: start || undefined,
          end: end || undefined,
          ...(storeId ? { store_id: storeId } : {}),
          ...(page > 1 ? { page } : {}),
          ...(pageSize !== 50 ? { page_size: pageSize } : {}),
        },
      })
      .then(({ data }) => {
        setReport(data);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  }, [group, start, end, storeId, page, pageSize]);

  const exportReport = async () => {
    setExporting(true);
    try {
      const { data, headers } = await api.get('/api/cash-controls/over-short/export.csv', {
        params: {
          group_by: group,
          start: start || undefined,
          end: end || undefined,
          ...(storeId ? { store_id: storeId } : {}),
        },
        responseType: 'blob',
      });
      downloadBlob(`over-short-${storeId || 'all-stores'}-${group}-${start || 'start'}-${end || 'now'}.csv`, data);
      if (headers?.['x-over-short-truncated'] === 'true') {
        toast('The CSV reached 50,000 rows. Narrow the date range before exporting again.', 'info');
      } else {
        toast('Over / short report downloaded.', 'success');
      }
    } catch {
      toast('Could not export the over / short report.', 'error');
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="cc-card">
      <h3>Over / short</h3>
      <p className="page-sub">
        Short means the cash accounted for is below what the sales expected; over means it is above.
        Cash declarations only.
      </p>
      {failed && <Alert tone="error">Could not load the over/short report.</Alert>}
      <MetricGrid>
        <MetricTile label="Declarations" value={report ? report.totals.declarations : '—'} />
        <MetricTile label="Short" value={report ? money(report.totals.short) : '—'} />
        <MetricTile label="Over" value={report ? money(report.totals.over) : '—'} />
      </MetricGrid>
      <Toolbar count={report?.has_more ? 'More matching groups' : undefined}>
        <Button variant="secondary" onClick={() => void copyReportLink()}>
          {linkCopied ? 'Link copied' : 'Copy report link'}
        </Button>
        <Segmented
          value={group}
          onChange={(v) => {
            setGroup(v as Group);
            setPage(1);
          }}
          options={[
            { value: 'store', label: 'By store' },
            { value: 'register', label: 'By register' },
            { value: 'cashier', label: 'By cashier' },
            { value: 'date', label: 'By day' },
          ]}
        />
        <StoreSelect
          value={storeId}
          emptyLabel="All stores"
          onChange={(value) => {
            setStoreId(value);
            setPage(1);
          }}
        />
        <input
          type="date"
          aria-label="From"
          value={start}
          max={end || undefined}
          onChange={(e) => {
            setStart(e.target.value);
            setPage(1);
          }}
        />
        <input
          type="date"
          aria-label="To"
          value={end}
          min={start || undefined}
          onChange={(e) => {
            setEnd(e.target.value);
            setPage(1);
          }}
        />
        <Button
          variant="secondary"
          disabled={!report?.totals.declarations || exporting}
          onClick={() => void exportReport()}
        >
          {exporting ? 'Preparing CSV…' : 'Export matching CSV'}
        </Button>
      </Toolbar>
      <DataTable
        rows={report ? report.rows : null}
        rowKey={(r) => `${r.store_id}|${r.register_id}|${r.cashier_id}|${r.business_date}`}
        emptyTitle="No cash declarations in this range"
        columns={[
          {
            key: 'who',
            header: 'Group',
            primary: true,
            render: (r) => <Link href={cashHistoryHref(r, group, storeId, start, end)}>{label(r, group)}</Link>,
          },
          { key: 'n', header: 'Declarations', numeric: true, render: (r) => r.declarations },
          { key: 'oob', header: 'Out of balance', numeric: true, render: (r) => r.out_of_balance },
          { key: 'short', header: 'Short', numeric: true, render: (r) => money(r.short) },
          { key: 'over', header: 'Over', numeric: true, render: (r) => money(r.over) },
          { key: 'net', header: 'Net short', numeric: true, render: (r) => money(r.net_short) },
        ]}
        footer={
          report && (
            <PageNav
              page={page}
              hasMore={report.has_more}
              pageSize={pageSize}
              onPageSize={(size) => {
                setPageSize(size);
                setPage(1);
              }}
              onPage={setPage}
            />
          )
        }
      />
    </div>
  );
}
