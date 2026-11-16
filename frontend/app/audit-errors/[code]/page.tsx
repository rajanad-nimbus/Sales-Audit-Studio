'use client';

import { formatNumber } from '@/lib/preferences';
import { use, useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api, currencyBreakdown } from '@/lib/api';
import { useDataChanged } from '@/lib/sync';
import { ErrorDetail } from '@/components/AuditErrorDetail';
import type { ErrorType } from '@/components/AuditErrorDetail';
import { Alert, Badge, Button, MetricGrid, MetricTile, PageHeader } from '@/components/ui';

/** The window "Raised" counts over here, the same default the Audit Errors list opens on. */
const RAISED_DAYS = 90;

export default function AuditErrorPage({ params }: { params: Promise<{ code: string }> }) {
  const { code: raw } = use(params);
  const code = decodeURIComponent(raw);
  const [error, setError] = useState<ErrorType | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  // There is no single-error endpoint; the catalog is small, so find this error in it.
  const load = useCallback(async () => {
    try {
      const { data } = await api.get('/api/audit-errors', { params: { days: RAISED_DAYS } });
      const found = (data.items as ErrorType[]).find((i) => i.code === code) ?? null;
      setError(found);
      setProblem(found ? null : `There is no audit error with the code ${code}.`);
    } catch (e) {
      setProblem(
        (e as { response?: { status?: number } })?.response?.status === 403
          ? 'Your role cannot view audit errors.'
          : 'Could not load this audit error.'
      );
    }
  }, [code]);
  useEffect(() => {
    load();
  }, [load]);
  useDataChanged(() => {
    load();
  });

  return (
    <div className="page-container">
      <Link href="/audit-errors" className="back-link">
        ← All Audit Errors
      </Link>
      <PageHeader
        title={error?.label ?? code}
        subtitle={error ? `${error.code} · ${error.family}` : undefined}
        actions={
          error && (
            <Badge variant={error.open_count ? 'error' : 'secondary'}>
              {error.open_count ? `${formatNumber(error.open_count)} open` : 'None open'}
            </Badge>
          )
        }
      />
      {problem && (
        <Alert
          tone="error"
          action={
            <Button size="sm" className="alert-close" onClick={load}>
              Retry
            </Button>
          }
        >
          {problem}
        </Alert>
      )}
      {error && (
        <>
          <MetricGrid>
            <MetricTile label="Open" value={formatNumber(error.open_count)} />
            <MetricTile
              label={`Raised, ${RAISED_DAYS} days`}
              value={formatNumber(error.total_count)}
            />
            <MetricTile
              label="Open amount"
              value={error.open_count ? currencyBreakdown(error.open_by_currency, 'amount') : '—'}
            />
            <MetricTile
              label="Last raised"
              value={error.latest_at ? error.latest_at.slice(0, 10) : '—'}
            />
          </MetricGrid>
          <ErrorDetail error={error} />
        </>
      )}
    </div>
  );
}
