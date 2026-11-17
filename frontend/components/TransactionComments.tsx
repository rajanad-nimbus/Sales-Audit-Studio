'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/preferences';
import { apiError } from '@/lib/storeDays';
import { useMe } from '@/components/MeProvider';
import { Card } from '@/components/ui';

type Comment = { id: string; text: string; actor: string; created_at: string };
type Page = { items: Comment[]; has_more: boolean };
type TransactionErrorPage = { items: TransactionError[]; has_more: boolean };
type TransactionError = {
  id: string;
  code: string;
  family: string;
  detection_origin: string;
  severity: string;
  confidence: string;
  status: string;
  close_impact: string;
  amount: string;
  case_id: string;
  case_number: string;
  case_status: string;
  business_date: string;
};

export default function TransactionComments({
  transactionId,
  refreshToken = 0,
}: {
  transactionId: string;
  refreshToken?: number;
}) {
  const { me } = useMe();
  const [items, setItems] = useState<Comment[]>([]);
  const [errors, setErrors] = useState<TransactionError[]>([]);
  const [errorHistoryMessage, setErrorHistoryMessage] = useState('');
  const [errorsHasMore, setErrorsHasMore] = useState(false);
  const [errorsLoading, setErrorsLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const requestId = useRef(0);
  const errorsRequestId = useRef(0);
  const currentTransactionId = useRef(transactionId);
  currentTransactionId.current = transactionId;
  const path = `/api/transactions/${encodeURIComponent(transactionId)}/comments`;
  const errorsPath = `/api/transactions/${encodeURIComponent(transactionId)}/errors`;
  const loadErrors = useCallback(
    async (offset: number) => {
      const currentRequest = ++errorsRequestId.current;
      setErrorsLoading(true);
      try {
        const { data } = await api.get<TransactionErrorPage>(errorsPath, { params: { offset } });
        if (currentRequest !== errorsRequestId.current) return;
        setErrors((previous) => (offset ? [...previous, ...data.items] : data.items));
        setErrorsHasMore(data.has_more);
        setErrorHistoryMessage('');
      } catch (e) {
        if (currentRequest === errorsRequestId.current) {
          setErrorHistoryMessage(apiError(e, 'Could not load associated audit errors.'));
        }
      } finally {
        if (currentRequest === errorsRequestId.current) setErrorsLoading(false);
      }
    },
    [errorsPath]
  );
  const load = useCallback(
    async (offset: number) => {
      const currentRequest = ++requestId.current;
      setLoading(true);
      const commentsRequest = api.get<Page>(path, { params: { offset } });
      const commentsResult = await commentsRequest.then(
        (value) => ({ status: 'fulfilled' as const, value }),
        (reason) => ({ status: 'rejected' as const, reason })
      );

      if (currentRequest !== requestId.current) return;

      if (commentsResult.status === 'fulfilled') {
        const { data } = commentsResult.value;
        setItems((previous) => (offset ? [...previous, ...data.items] : data.items));
        setHasMore(data.has_more);
        setError('');
      } else {
        setError(apiError(commentsResult.reason, 'Could not load transaction comments.'));
      }
      setLoading(false);
    },
    [path]
  );
  useEffect(() => {
    setItems([]);
    setErrors([]);
    setErrorsHasMore(false);
    setError('');
    setErrorHistoryMessage('');
    setHasMore(false);
    setText('');
    void load(0);
    void loadErrors(0);
    return () => {
      requestId.current += 1;
      errorsRequestId.current += 1;
    };
  }, [load, loadErrors, refreshToken]);
  const canWrite = me && ['admin', 'finance', 'analyst'].includes(me.role);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      await api.post(path, { text });
      if (currentTransactionId.current !== transactionId) return;
      setText('');
      await load(0);
    } catch (e) {
      if (currentTransactionId.current === transactionId) {
        setError(apiError(e, 'Could not add the comment.'));
      }
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
        {!errors.length && !errorsLoading && !errorHistoryMessage && <p>No linked audit errors.</p>}
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
        {errorsLoading && <p role="status">Loading associated errors…</p>}
        {errorsHasMore && (
          <button
            type="button"
            disabled={errorsLoading}
            onClick={() => void loadErrors(errors.length)}
          >
            Load more associated errors
          </button>
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
