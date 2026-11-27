'use client';

import { actorLabel } from '@/lib/activityUtil';
import { humanizeCodesInText, humanizeKey } from '@/lib/humanize';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/preferences';
import { apiError } from '@/lib/storeDays';
import { downloadBlob } from '@/lib/csv';
import { CaseLink } from '@/components/MeProvider';
import { Alert, Button, Card, StructuredValue } from '@/components/ui';

interface EventRow {
  id: string;
  seq: number | null;
  event_type: string;
  actor: string;
  object_type: string;
  object_id: string;
  case_id: string | null;
  description: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  created_at: string;
}
interface Result {
  items: EventRow[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export default function TransactionAuditTrail({ transactionId }: { transactionId: string }) {
  const [result, setResult] = useState<Result | null>(null);
  const [resultFor, setResultFor] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const requestId = useRef(0);
  const path = `/api/transactions/${encodeURIComponent(transactionId)}/audit-trail`;
  const load = useCallback(
    async (nextPage: number) => {
      const currentRequest = ++requestId.current;
      setLoading(true);
      try {
        const { data } = await api.get<Result>(path, { params: { page: nextPage, page_size: 25 } });
        if (currentRequest !== requestId.current) return;
        setResult(data);
        setResultFor(transactionId);
        setPage(nextPage);
        setError('');
      } catch (e) {
        if (currentRequest === requestId.current) {
          setError(apiError(e, 'Could not load this transaction’s audit trail.'));
        }
      } finally {
        if (currentRequest === requestId.current) setLoading(false);
      }
    },
    [path, transactionId]
  );
  useEffect(() => {
    setResult(null);
    setPage(1);
    setError('');
    void load(1);
    return () => {
      requestId.current += 1;
    };
  }, [load]);
  const visibleResult = resultFor === transactionId ? result : null;
  const waitingForCurrentTransaction = loading || (!visibleResult && !error);
  const exportTrail = async () => {
    setExporting(true);
    setError('');
    try {
      const { data } = await api.get<Blob>(path, {
        params: { format: 'csv' },
        responseType: 'blob',
      });
      downloadBlob(`transaction-${transactionId}-audit-trail.csv`, data);
    } catch (e) {
      const statusCode = (e as { response?: { status?: number } })?.response?.status;
      setError(
        statusCode === 413
          ? 'This transaction audit trail exceeds the 100,000-event export limit.'
          : apiError(e, 'Could not export this transaction’s audit trail.')
      );
    } finally {
      setExporting(false);
    }
  };

  return (
    <Card className="case-section">
      <h2>Audit trail</h2>
      <p className="hint">
        Comments, case decisions and corrections, in audit order. The CSV has every event, up to
        100,000.
      </p>
      <div className="toolbar">
        <Button
          variant="secondary"
          disabled={exporting || !visibleResult?.total}
          onClick={() => void exportTrail()}
        >
          {exporting ? 'Preparing CSV…' : 'Export CSV'}
        </Button>
      </div>
      {error && <Alert tone="error">{error}</Alert>}
      {waitingForCurrentTransaction && <p role="status">Loading audit events…</p>}
      {!waitingForCurrentTransaction && visibleResult?.items.length === 0 && (
        <p className="cc-empty">No audit events are linked to this transaction yet.</p>
      )}
      {visibleResult && visibleResult.items.length > 0 && (
        <>
          <ol className="case-timeline">
            {visibleResult.items.map((event) => (
              <li key={event.id}>
                <time>{formatDateTime(event.created_at)}</time>
                <div>
                  <b>{humanizeKey(event.event_type)}</b>
                  <p>{humanizeCodesInText(event.description)}</p>
                  <small>
                    {actorLabel(event.actor)}
                    {event.seq ? ` · audit sequence ${event.seq}` : ''}
                    {event.case_id ? (
                      <>
                        {' · '}
                        <CaseLink id={event.case_id}>Open case</CaseLink>
                      </>
                    ) : null}
                  </small>
                  {(event.before || event.after) && (
                    <details>
                      <summary>View recorded change</summary>
                      <StructuredValue
                        value={{ before: event.before ?? null, after: event.after ?? null }}
                      />
                    </details>
                  )}
                </div>
              </li>
            ))}
          </ol>
          {visibleResult.pages > 1 && (
            <div className="toolbar">
              <span className="hint">
                Page {page} of {visibleResult.pages} · {visibleResult.total} events
              </span>
              <Button disabled={loading || page <= 1} onClick={() => void load(page - 1)}>
                Previous
              </Button>
              <Button
                disabled={loading || page >= visibleResult.pages}
                onClick={() => void load(page + 1)}
              >
                Next
              </Button>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
