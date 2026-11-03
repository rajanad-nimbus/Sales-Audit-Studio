'use client';
import { useEffect, useState } from 'react';
import { api, money } from '@/lib/api';
import { Alert, Badge } from '@/components/ui';
import { apiError } from '@/lib/storeDays';

interface Item {
  id: string;
  event_time: string;
  event_type: string;
  sub_type: string | null;
  transaction_number: string;
  amount: string | null;
  currency: string;
  audit_code: string | null;
  cashier_id: string | null;
}
interface Context {
  event_id: string;
  store_id: string;
  register_id: string | null;
  business_date: string;
  earlier_not_shown: boolean;
  later_not_shown: boolean;
  events: Item[];
}

/**
 * The events either side of this one on the same register and day. A void or a cash pull reads
 * differently next to what came before it. Loads when the row opens, a few events at a time.
 */
export function EventContext({ id, typeLabel }: { id: string; typeLabel: (t: string) => string }) {
  const [context, setContext] = useState<Context | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    api
      .get<Context>(`/api/register-events/${id}/context`)
      .then(({ data }) => current && setContext(data))
      .catch((e) => current && setError(apiError(e, 'Could not load the surrounding events.')));
    return () => {
      current = false;
    };
  }, [id]);

  if (error) return <Alert tone="error">{error}</Alert>;
  if (!context) return <p className="hint">Loading…</p>;

  return (
    <div className="stack stack-sm">
      <p>
        <b>
          {context.store_id} · {context.register_id ?? 'no register'} · {context.business_date}
        </b>
      </p>
      {context.earlier_not_shown && (
        <p className="hint">Earlier events on this register are not shown.</p>
      )}
      <ol className="case-timeline">
        {context.events.map((e) => (
          <li key={e.id} aria-current={e.id === context.event_id ? 'true' : undefined}>
            <time>{e.event_time.slice(11, 16)}</time>
            <div>
              <b>
                {e.id === context.event_id ? '▶ ' : ''}
                {typeLabel(e.event_type)}
                {e.sub_type ? ` · ${e.sub_type}` : ''}
              </b>
              <p>
                {e.transaction_number === '0'
                  ? 'No transaction number'
                  : `Transaction ${e.transaction_number}`}
                {e.amount ? ` · ${money(e.amount, e.currency)}` : ''}
                {e.cashier_id ? ` · ${e.cashier_id}` : ''}{' '}
                {e.audit_code && (
                  <Badge variant="error" size="sm">
                    {e.audit_code}
                  </Badge>
                )}
              </p>
            </div>
          </li>
        ))}
      </ol>
      {context.later_not_shown && (
        <p className="hint">Later events on this register are not shown.</p>
      )}
    </div>
  );
}
