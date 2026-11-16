'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/preferences';
import { apiError } from '@/lib/storeDays';
import { useMe } from '@/components/MeProvider';
import { Card } from '@/components/ui';

type Comment = { id: string; text: string; actor: string; created_at: string };
type Page = { items: Comment[]; has_more: boolean };
type TransactionError = {
  id: string;
  code: string;
  severity: string;
  status: string;
  case_id: string;
  case_number: string;
  case_status: string;
  business_date: string;
};

export default function TransactionComments({ transactionId }: { transactionId: string }) {
  const { me } = useMe();
  const [items, setItems] = useState<Comment[]>([]);
  const [errors, setErrors] = useState<TransactionError[]>([]);
  const [errorHistoryMessage, setErrorHistoryMessage] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const path = `/api/transactions/${encodeURIComponent(transactionId)}/comments`;
  const load = useCallback(
    async (offset: number) => {
      setLoading(true);
      const commentsRequest = api.get<Page>(path, { params: { offset } });
      const errorsRequest = offset
        ? null
        : api.get<{ items: TransactionError[] }>(path.replace(/\/comments$/, '/errors'));
      const [commentsResult, errorsResult] = await Promise.all([
        commentsRequest.then(
          (value) => ({ status: 'fulfilled' as const, value }),
          (reason) => ({ status: 'rejected' as const, reason })
        ),
        errorsRequest?.then(
          (value) => ({ status: 'fulfilled' as const, value }),
          (reason) => ({ status: 'rejected' as const, reason })
        ),
      ]);

      if (commentsResult.status === 'fulfilled') {
        const { data } = commentsResult.value;
        setItems((previous) => (offset ? [...previous, ...data.items] : data.items));
        setHasMore(data.has_more);
        setError('');
      } else {
        setError(apiError(commentsResult.reason, 'Could not load transaction comments.'));
      }
      if (errorsResult) {
        if (errorsResult.status === 'fulfilled') {
          setErrors(errorsResult.value.data.items);
          setErrorHistoryMessage('');
        } else {
          setErrorHistoryMessage(
            apiError(errorsResult.reason, 'Could not load associated audit errors.')
          );
        }
      }
      setLoading(false);
    },
    [path]
  );
  useEffect(() => {
    setItems([]);
    void load(0);
  }, [load]);
  const canWrite = me && ['admin', 'finance', 'analyst'].includes(me.role);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      await api.post(path, { text });
      setText('');
      await load(0);
    } catch (e) {
      setError(apiError(e, 'Could not add the comment.'));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card className="case-section">
      <h2>Transaction comments</h2>
      <p className="hint">Comments are recorded in the audit trail and cannot be edited.</p>
      {error && <p role="alert">{error}</p>}
      {canWrite && (
        <form onSubmit={submit}>
          <label htmlFor="transaction-comment">Add a comment</label>
          <textarea
            id="transaction-comment"
            value={text}
            maxLength={2000}
            onChange={(e) => setText(e.target.value)}
          />
          <button type="submit" disabled={busy || !text.trim()}>
            {busy ? 'Saving…' : 'Add comment'}
          </button>
        </form>
      )}
      <section aria-labelledby="transaction-errors-title">
        <h3 id="transaction-errors-title">Associated audit errors</h3>
        {errorHistoryMessage && <p role="status">{errorHistoryMessage}</p>}
        {!errors.length && !errorHistoryMessage && <p>No linked audit errors.</p>}
        {errors.length > 0 && (
          <ul>
            {errors.map((item) => (
              <li key={item.id}>
                <strong>{item.code}</strong> · {item.severity} · {item.status} ·{' '}
                {item.business_date}
                {['admin', 'finance', 'analyst', 'auditor', 'it'].includes(me?.role ?? '') ? (
                  <>
                    {' '}
                    · <a href={`/cases/${encodeURIComponent(item.case_id)}`}>{item.case_number}</a>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
      {loading && <p role="status">Loading comments…</p>}
      {!loading && !items.length && !error && <p>No comments yet.</p>}
      {items.map((item) => (
        <article key={item.id}>
          <p style={{ whiteSpace: 'pre-wrap' }}>{item.text}</p>
          <p className="hint">
            {item.actor} · {formatDateTime(item.created_at)}
          </p>
        </article>
      ))}
      {hasMore && (
        <button type="button" disabled={loading} onClick={() => void load(items.length)}>
          Load more
        </button>
      )}
    </Card>
  );
}
