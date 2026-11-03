'use client';
import { useEffect, useState } from 'react';
import { api, money } from '@/lib/api';
import { Alert, DataTable, MetricGrid, MetricTile, Segmented, Toolbar } from '@/components/ui';

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
  truncated: boolean;
  rows: Row[];
  totals: { declarations: number; short: string; over: string };
}

const label = (r: Row, group: Group) =>
  group === 'date'
    ? r.business_date
    : group === 'store'
      ? r.store_id
      : `${r.store_id} · ${(group === 'register' ? r.register_id : r.cashier_id) ?? 'None'}`;

/** Cash over and short, with short and over kept apart so one till cannot hide another. */
export function OverShortReport() {
  const [group, setGroup] = useState<Group>('store');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [report, setReport] = useState<Report | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    api
      .get<Report>('/api/cash-controls/over-short', {
        params: { group_by: group, start: start || undefined, end: end || undefined },
      })
      .then(({ data }) => {
        setReport(data);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  }, [group, start, end]);

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
      <Toolbar count={report?.truncated ? 'Showing the largest 500' : undefined}>
        <Segmented
          value={group}
          onChange={(v) => setGroup(v as Group)}
          options={[
            { value: 'store', label: 'By store' },
            { value: 'register', label: 'By register' },
            { value: 'cashier', label: 'By cashier' },
            { value: 'date', label: 'By day' },
          ]}
        />
        <input
          type="date"
          aria-label="From"
          value={start}
          max={end || undefined}
          onChange={(e) => setStart(e.target.value)}
        />
        <input
          type="date"
          aria-label="To"
          value={end}
          min={start || undefined}
          onChange={(e) => setEnd(e.target.value)}
        />
      </Toolbar>
      <DataTable
        rows={report ? report.rows : null}
        rowKey={(r) => `${r.store_id}|${r.register_id}|${r.cashier_id}|${r.business_date}`}
        emptyTitle="No cash declarations in this range"
        columns={[
          { key: 'who', header: 'Group', primary: true, render: (r) => label(r, group) },
          { key: 'n', header: 'Declarations', numeric: true, render: (r) => r.declarations },
          { key: 'oob', header: 'Out of balance', numeric: true, render: (r) => r.out_of_balance },
          { key: 'short', header: 'Short', numeric: true, render: (r) => money(r.short) },
          { key: 'over', header: 'Over', numeric: true, render: (r) => money(r.over) },
          { key: 'net', header: 'Net short', numeric: true, render: (r) => money(r.net_short) },
        ]}
      />
    </div>
  );
}
