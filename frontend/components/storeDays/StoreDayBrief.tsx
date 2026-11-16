'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api, caseMoney, money } from '@/lib/api';
import { CaseLink } from '@/components/MeProvider';
import { Alert, Badge } from '@/components/ui';
import {
  apiError,
  dayApi,
  dayPath,
  formatRollupKey,
  outcomeVariant,
  readinessVariant,
} from '@/lib/storeDays';
import type { StoreDayBrief as Brief } from '@/lib/storeDays';

/**
 * What holds one store day back, shown inline under its row. It loads only when the row opens and
 * only a few items of each kind, so opening many rows on a busy day stays cheap.
 */
export function StoreDayBrief({
  storeId,
  businessDate,
}: {
  storeId: string;
  businessDate: string;
}) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    api
      .get<Brief>(`${dayApi({ store_id: storeId, business_date: businessDate })}/brief`)
      .then(({ data }) => current && setBrief(data))
      .catch((e) => current && setError(apiError(e, 'Could not load this store day.')));
    return () => {
      current = false;
    };
  }, [storeId, businessDate]);

  if (error) return <Alert tone="error">{error}</Alert>;
  if (!brief) return <p className="hint">Loading…</p>;

  const { readiness, blockers, variances, cases } = brief;
  const parts = [
    [
      'Sales audit',
      readiness.audit.overall_status ??
        (readiness.audit.state === 'Clean' &&
        readiness.ingest.state === 'Complete' &&
        readiness.audit.signed_off
          ? 'Sales Audit Complete'
          : 'Sales Audit In Progress'),
      'Overall store-day audit lifecycle',
    ],
    ['Data', readiness.ingest.state, readiness.ingest.detail],
    ...(readiness.ingest.day_close
      ? [
          [
            'Store-level POS close',
            readiness.ingest.day_close.state,
            readiness.ingest.day_close.markers
              ? `${readiness.ingest.day_close.markers} store-level close marker(s) received`
              : readiness.ingest.day_close.required
                ? 'The required store-level POS Day Close marker has not been received'
                : readiness.ingest.day_close.events
                  ? 'Register events arrived without the store-level Day Close marker'
                  : 'The POS feed has not supplied register events or the store-level close marker',
          ] as const,
        ]
      : []),
    ['Audit', readiness.audit.resa_status ?? readiness.audit.state, readiness.audit.detail],
    [
      'Export',
      readiness.export.state,
      readiness.export.records
        ? `${readiness.export.records} record(s), ${readiness.export.acknowledged} acknowledged`
        : 'Nothing exported',
    ],
  ] as const;

  return (
    <div className="stack stack-sm">
      <dl className="kv">
        {parts.map(([label, state, note]) => (
          <div key={label} style={{ display: 'contents' }}>
            <dt>{label}</dt>
            <dd>
              <Badge variant={readinessVariant(state)} size="sm">
                {state}
              </Badge>{' '}
              <span className="hint">{note}</span>
            </dd>
          </div>
        ))}
      </dl>
      {blockers.length > 0 ? (
        <p>
          <b>Holding it back:</b> {blockers.join(' · ')}
        </p>
      ) : (
        <p className="hint">Nothing is holding this day back.</p>
      )}
      {brief.registers_with_gaps > 0 && (
        <p>
          Transaction numbers are missing from {brief.registers_with_gaps} register
          {brief.registers_with_gaps === 1 ? '' : 's'}.
        </p>
      )}
      {variances.unexplained > 0 && (
        <div>
          <b>
            {variances.unexplained} unexplained variance{variances.unexplained === 1 ? '' : 's'}
          </b>
          <ul>
            {variances.top_unexplained.map((v) => (
              <li key={`${v.name}|${v.level}|${v.dimension ?? ''}|${v.rollup_key ?? ''}`}>
                {v.name}
                {v.dimension ? ` · ${v.dimension}` : ''}
                {v.rollup_key ? ` · ${formatRollupKey(v.rollup_key)}` : ''}:{' '}
                {money(v.variance, v.currency)}{' '}
                <Badge variant={outcomeVariant(v.outcome, false)} size="sm">
                  {v.outcome}
                </Badge>
              </li>
            ))}
          </ul>
          {variances.unexplained > variances.top_unexplained.length && (
            <p className="hint">
              Showing the largest {variances.top_unexplained.length}; the rest are on the store day.
            </p>
          )}
        </div>
      )}
      {cases.open > 0 && (
        <div>
          <b>
            {cases.open} open case{cases.open === 1 ? '' : 's'}
          </b>
          <ul>
            {cases.top.map((c) => (
              <li key={c.id}>
                <CaseLink id={c.id}>{c.case_number}</CaseLink> · {c.case_type} · {c.status} ·{' '}
                {caseMoney(c.exposure, c.currency)}
              </li>
            ))}
          </ul>
          {cases.open > cases.top.length && (
            <p className="hint">Showing the {cases.top.length} with the most exposure.</p>
          )}
        </div>
      )}
      <Link href={dayPath({ store_id: storeId, business_date: businessDate })}>
        Open {storeId} · {businessDate}
      </Link>
    </div>
  );
}
