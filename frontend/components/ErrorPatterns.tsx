'use client';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import {
  Alert,
  Badge,
  DataTable,
  MetricGrid,
  MetricTile,
  Segmented,
  Toolbar,
} from '@/components/ui';
import type { BadgeVariant } from '@/components/ui';

interface PatternRow {
  store_id: string;
  register_id?: string;
  code: string;
  label: string;
  days: number;
  first_seen: string;
  last_seen: string;
  trend: string;
  repeating: boolean;
  sources: string[];
}
interface StoreSummary {
  store_id: string;
  error_days: number;
  error_codes: number;
  repeating_codes: number;
  trend: string;
}
interface Patterns {
  since: string;
  until: string;
  window_days: number;
  min_days: number;
  registers: PatternRow[];
  registers_truncated: boolean;
  stores: PatternRow[];
  stores_truncated: boolean;
  store_summary: StoreSummary[];
}

const trendVariant = (t: string): BadgeVariant =>
  t === 'Rising' ? 'error' : t === 'Falling' ? 'success' : 'secondary';
const Trend = ({ t }: { t: string }) => (
  <Badge size="sm" variant={trendVariant(t)}>
    {t}
  </Badge>
);
const Seen = ({ r }: { r: PatternRow }) => (
  <>
    {r.days} day{r.days === 1 ? '' : 's'}
    {r.repeating && (
      <>
        {' '}
        <Badge size="sm" variant="warning">
          Repeating
        </Badge>
      </>
    )}
  </>
);

/** The same error code coming back at one register or one store, counted in business days. */
export function ErrorPatterns() {
  const [data, setData] = useState<Patterns | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState('30');
  const [minDays, setMinDays] = useState('3');
  const [view, setView] = useState<'registers' | 'stores'>('registers');
  const [onlyRepeating, setOnlyRepeating] = useState(true);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Patterns>('/api/audit-errors/patterns', {
        params: { days, min_days: minDays },
      });
      setData(data);
      setError(null);
    } catch {
      setError('Could not load error patterns.');
    }
  }, [days, minDays]);
  useEffect(() => {
    load();
  }, [load]);

  const rows = data
    ? (view === 'registers' ? data.registers : data.stores).filter(
        (r) => !onlyRepeating || r.repeating
      )
    : null;
  const truncated =
    data && (view === 'registers' ? data.registers_truncated : data.stores_truncated);

  return (
    <div className="stack">
      <p className="hint">
        Errors are counted by the business days they happened on, so one bad day counts once and an
        error that comes back every day stands out. Registers come from the transactions each case
        was raised for and from register events.
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      <MetricGrid>
        <MetricTile
          label="Repeating at a register"
          value={data ? data.registers.filter((r) => r.repeating).length : '—'}
        />
        <MetricTile
          label="Repeating at a store"
          value={data ? data.stores.filter((r) => r.repeating).length : '—'}
        />
        <MetricTile
          label="Stores with rising errors"
          value={data ? data.store_summary.filter((s) => s.trend === 'Rising').length : '—'}
        />
      </MetricGrid>
      <Toolbar
        count={
          data
            ? `${data.since} to ${data.until}${truncated ? ' · showing the top 200' : ''}`
            : undefined
        }
      >
        <Segmented
          value={view}
          onChange={(v) => setView(v as 'registers' | 'stores')}
          options={[
            { value: 'registers', label: 'By register' },
            { value: 'stores', label: 'By store' },
          ]}
        />
        <Segmented
          value={onlyRepeating ? 'repeating' : 'all'}
          onChange={(v) => setOnlyRepeating(v === 'repeating')}
          options={[
            { value: 'repeating', label: 'Repeating' },
            { value: 'all', label: 'All' },
          ]}
        />
        <select aria-label="Window" value={days} onChange={(e) => setDays(e.target.value)}>
          {['14', '30', '60', '90'].map((d) => (
            <option key={d} value={d}>
              Last {d} days
            </option>
          ))}
        </select>
        <select
          aria-label="Repeating after"
          value={minDays}
          onChange={(e) => setMinDays(e.target.value)}
        >
          {['2', '3', '5', '7'].map((d) => (
            <option key={d} value={d}>
              Repeating on {d}+ days
            </option>
          ))}
        </select>
      </Toolbar>
      <DataTable
        rows={rows}
        rowKey={(r) => `${r.store_id}|${r.register_id ?? ''}|${r.code}`}
        emptyTitle={onlyRepeating ? 'Nothing is repeating' : 'No errors in this window'}
        emptyHint={onlyRepeating ? 'Switch to All to see every error by location.' : undefined}
        columns={[
          {
            key: 'where',
            header: view === 'registers' ? 'Store · register' : 'Store',
            primary: true,
            nowrap: true,
            render: (r) => (r.register_id ? `${r.store_id} · ${r.register_id}` : r.store_id),
          },
          {
            key: 'error',
            header: 'Error',
            render: (r) => (
              <>
                {r.label}
                <small className="act-sub" style={{ display: 'block' }}>
                  {r.code} · {r.sources.join(', ')}
                </small>
              </>
            ),
          },
          { key: 'days', header: 'Seen on', render: (r) => <Seen r={r} /> },
          {
            key: 'range',
            header: 'First · last',
            nowrap: true,
            render: (r) => `${r.first_seen} · ${r.last_seen}`,
          },
          { key: 'trend', header: 'Trend', render: (r) => <Trend t={r.trend} /> },
        ]}
      />
      {data && data.store_summary.length > 0 && (
        <>
          <h3>Stores</h3>
          <DataTable
            rows={data.store_summary}
            rowKey={(s) => s.store_id}
            columns={[
              { key: 'store', header: 'Store', primary: true, render: (s) => s.store_id },
              {
                key: 'days',
                header: 'Days with errors',
                numeric: true,
                render: (s) => s.error_days,
              },
              { key: 'codes', header: 'Error types', numeric: true, render: (s) => s.error_codes },
              {
                key: 'repeat',
                header: 'Repeating types',
                numeric: true,
                render: (s) => s.repeating_codes,
              },
              { key: 'trend', header: 'Trend', render: (s) => <Trend t={s.trend} /> },
            ]}
          />
        </>
      )}
    </div>
  );
}
