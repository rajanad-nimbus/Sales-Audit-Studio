'use client';

import { useEffect, useRef, useState } from 'react';
import { formatNumber } from '@/lib/preferences';
import { api, money } from '@/lib/api';
import { apiError } from '@/lib/storeDays';
import { Alert, DataTable, Pager, Segmented } from '@/components/ui';
import { itemDefault, posAttributeLine } from '@/components/transactions/posAttributes';
import type { Column } from '@/components/ui';

export interface ItemLine {
  line_no: number;
  item_code: string;
  description: string | null;
  quantity: number | string;
  unit_price: string;
  original_unit_price: string | null;
  price_override_reason: string | null;
  source_attributes: Record<string, string | number | boolean | null> | null;
  gross_amount: string;
  tax_code: string | null;
  tax_rate: string;
  tax_amount: string;
  discount_amount: string;
  return_reason: string | null;
}
type Only = '' | 'discounted' | 'price_override' | 'returned';
interface Page {
  items: ItemLine[];
  total: number;
  counts: { all: number; discounted: number; price_override: number; returned: number };
}

// Search, filters and paging only earn their space once a basket is longer than a glance.
const TOOLS_FROM = 20;
const num = (v: string | number) => Number(v);

/**
 * A transaction's item lines, read a page at a time from the server so a basket of hundreds or
 * thousands of lines stays quick. The reviewer can find a line by item code or description, or
 * narrow to the lines worth a look: discounted, price overridden or returned.
 */
export function ItemLines({
  transactionId,
  lineCount,
  editLine,
}: {
  transactionId: string;
  lineCount: number;
  /** The pencil for correcting a line's tax, when the viewer may propose corrections. */
  editLine?: (line: ItemLine) => React.ReactNode;
}) {
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const [only, setOnly] = useState<Only>('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [data, setData] = useState<Page | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  // Wait for a pause in typing before searching, so each keystroke is not a request.
  useEffect(() => {
    const next = q.trim();
    if (next === query) return;
    const t = setTimeout(() => {
      setQuery(next);
      setPage(1);
    }, 250);
    return () => clearTimeout(t);
  }, [q, query]);

  useEffect(() => {
    const mine = ++seq.current;
    setError(null);
    api
      .get<Page>(`/api/transactions/${encodeURIComponent(transactionId)}/lines`, {
        params: {
          offset: (page - 1) * pageSize,
          limit: pageSize,
          ...(query ? { q: query } : {}),
          ...(only ? { only } : {}),
        },
      })
      .then(({ data }) => mine === seq.current && setData(data))
      .catch((e) => mine === seq.current && setError(apiError(e, 'Could not load the items.')));
  }, [transactionId, page, pageSize, query, only]);

  const showTools = lineCount > TOOLS_FROM;
  const counts = data?.counts;
  const filters: { value: Only; label: string }[] = [
    { value: '', label: `All ${formatNumber(counts?.all ?? lineCount)}` },
    ...(
      [
        ['discounted', 'Discounted'],
        ['price_override', 'Price overridden'],
        ['returned', 'Returned'],
      ] as [Exclude<Only, ''>, string][]
    )
      // Hide a filter with nothing in it, unless it is the one selected.
      .filter(([value]) => (counts?.[value] ?? 0) > 0 || only === value)
      .map(([value, label]) => ({
        value,
        label: `${label} ${formatNumber(counts?.[value] ?? 0)}`,
      })),
  ];

  const columns: Column<ItemLine>[] = [
    { key: 'no', header: '#', csvValue: (l) => l.line_no, render: (l) => l.line_no },
    {
      key: 'item',
      header: 'Item',
      primary: true,
      csvValue: (l) =>
        [
          l.item_code,
          l.description,
          l.return_reason,
          posAttributeLine(l.source_attributes, itemDefault(num(l.quantity))),
        ]
          .filter(Boolean)
          .join(' · '),
      render: (l) => {
        const pos = posAttributeLine(l.source_attributes, itemDefault(num(l.quantity)));
        return (
          <>
            {l.item_code}
            <small className="act-sub" style={{ display: 'block' }}>
              {l.description}
              {l.return_reason ? ` · ${l.return_reason}` : ''}
            </small>
            {pos && <small className="tx-pos">{pos}</small>}
          </>
        );
      },
    },
    {
      key: 'qty',
      header: 'Qty',
      numeric: true,
      csvValue: (l) => Math.abs(num(l.quantity)),
      render: (l) => Math.abs(num(l.quantity)),
    },
    {
      key: 'price',
      header: 'Unit price',
      numeric: true,
      csvValue: (l) =>
        [
          l.unit_price,
          l.original_unit_price ? `Original ${l.original_unit_price}` : '',
          l.price_override_reason ? `Override: ${l.price_override_reason}` : '',
        ]
          .filter(Boolean)
          .join(' · '),
      render: (l) => (
        <>
          {money(l.unit_price)}
          {(l.original_unit_price != null || l.price_override_reason) && (
            <small className="act-sub" style={{ display: 'block' }}>
              {l.original_unit_price != null && `${money(l.original_unit_price)} original`}
              {l.original_unit_price != null && l.price_override_reason ? ' · ' : ''}
              {l.price_override_reason && `Override: ${l.price_override_reason}`}
            </small>
          )}
        </>
      ),
    },
    {
      key: 'amount',
      header: 'Amount',
      numeric: true,
      csvValue: (l) => l.gross_amount,
      render: (l) => money(l.gross_amount),
    },
    {
      key: 'discount',
      header: 'Discount',
      numeric: true,
      csvValue: (l) => l.discount_amount,
      render: (l) => (num(l.discount_amount) ? money(l.discount_amount) : '—'),
    },
    {
      key: 'tax',
      header: 'Tax',
      numeric: true,
      csvValue: (l) =>
        `${l.tax_amount}${l.tax_code ? ` · ${l.tax_code} ${(num(l.tax_rate) * 100).toFixed(0)}%` : ''}`,
      render: (l) => (
        <>
          {money(l.tax_amount)}
          {l.tax_code && (
            <small className="act-sub" style={{ display: 'block' }}>
              {l.tax_code} {(num(l.tax_rate) * 100).toFixed(0)}%
            </small>
          )}
        </>
      ),
    },
    {
      key: 'total',
      header: 'Line total',
      numeric: true,
      csvValue: (l) => num(l.gross_amount) - num(l.discount_amount) + num(l.tax_amount),
      render: (l) => money(num(l.gross_amount) - num(l.discount_amount) + num(l.tax_amount)),
    },
    ...(editLine
      ? [
          {
            key: 'edit',
            header: '',
            csvSkip: true,
            render: (l: ItemLine) => editLine(l),
          } as Column<ItemLine>,
        ]
      : []),
  ];

  const filtered = !!query || !!only;
  return (
    <div className="tx-lines">
      {error && <Alert tone="error">{error}</Alert>}
      <DataTable
        columns={columns}
        rows={error ? [] : (data?.items ?? null)}
        rowKey={(l) => String(l.line_no)}
        failed={!!error}
        csvFileName={`transaction-${transactionId}-items-page.csv`}
        emptyTitle={filtered ? 'No item matches' : 'No item lines were sent.'}
        emptyHint={filtered ? 'Try another search or filter.' : undefined}
        toolbar={
          showTools ? (
            <div className="tx-lines-tools">
              <input
                type="search"
                className="tx-lines-search"
                placeholder="Find item code or description"
                aria-label="Find an item line"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              {filters.length > 1 && (
                <Segmented
                  options={filters}
                  value={only}
                  onChange={(value) => {
                    setOnly(value);
                    setPage(1);
                  }}
                />
              )}
            </div>
          ) : undefined
        }
        footer={
          data && data.total > pageSize ? (
            <Pager
              page={page}
              pages={Math.max(1, Math.ceil(data.total / pageSize))}
              total={data.total}
              pageSize={pageSize}
              pageSizes={[50, 100, 200]}
              onPage={setPage}
              onPageSize={(size) => {
                setPageSize(size);
                setPage(1);
              }}
            />
          ) : undefined
        }
      />
    </div>
  );
}
