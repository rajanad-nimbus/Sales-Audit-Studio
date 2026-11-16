'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/preferences';
import { apiError } from '@/lib/storeDays';
import { Alert, Button, Card } from '@/components/ui';

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
  const [page, setPage] = useState(1);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const path = `/api/transactions/${encodeURIComponent(transactionId)}/audit-trail`;
  const load = useCallback(
    async (nextPage: number) => {
      setLoading(true);
      try {
        const { data } = await api.get<Result>(path, { params: { page: nextPage, page_size: 25 } });
        setResult(data);
        setPage(nextPage);
        setError('');
      } catch (e) {
        setError(apiError(e, 'Could not load this transaction’s audit trail.'));
      } finally {
        setLoading(false);
      }
    },
    [path]
  );
  useEffect(() => {
    void load(1);
  }, [load]);

  return (
    <Card className="case-section">
      <h2>Transaction audit trail</h2>
      <p className="hint">
        Comments, related case decisions and correction actions, ordered by the append-only audit
        sequence.
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      {loading && <p role="status">Loading audit events…</p>}
      {!loading && result?.items.length === 0 && (
        <p className="cc-empty">No audit events are linked to this transaction yet.</p>
      )}
      {result && result.items.length > 0 && (
        <>
          <ol className="case-timeline">
            {result.items.map((event) => (
              <li key={event.id}>
                <time>{formatDateTime(event.created_at)}</time>
                <div>
                  <b>{event.event_type.replaceAll('_', ' ')}</b>
                  <p>{event.description}</p>
                  <small>
                    {event.actor}
                    {event.seq ? ` · audit sequence ${event.seq}` : ''}
                    {event.case_id ? ` · case ${event.case_id}` : ''}
                  </small>
                  {(event.before || event.after) && (
                    <details>
                      <summary>View recorded change</summary>
                      <pre className="ex-pre">
                        {JSON.stringify({ before: event.before, after: event.after }, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              </li>
            ))}
          </ol>
          {result.pages > 1 && (
            <div className="toolbar">
              <span className="hint">
                Page {page} of {result.pages} · {result.total} events
              </span>
              <Button disabled={loading || page <= 1} onClick={() => void load(page - 1)}>
                Previous
              </Button>
              <Button
                disabled={loading || page >= result.pages}
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
