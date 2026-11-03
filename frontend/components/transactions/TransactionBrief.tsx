'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api, money } from '@/lib/api';
import { Alert, Badge } from '@/components/ui';
import { apiError } from '@/lib/storeDays';

interface Brief {
  id: string;
  currency: string;
  detail_status: string;
  original_reference: string | null;
  return_reason: string | null;
  transaction_number: string | null;
  line_count: number;
  lines: {
    line_no: number;
    item_code: string;
    description: string | null;
    quantity: string;
    unit_price: string;
    gross_amount: string;
  }[];
  tenders: { tender_type: string; amount: string; reference: string | null }[];
  taxes: { tax_code: string; tax_name: string; rate: string; tax_amount: string }[];
  discount_count: number;
  corrections: number;
}

/**
 * A transaction's payment, first lines and tax, shown inline under its row. It loads when the row
 * opens and holds only a few of each kind, so a basket of hundreds of lines stays quick to open.
 */
export function TransactionBrief({ id }: { id: string }) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    api
      .get<Brief>(`/api/transactions/${id}/brief`)
      .then(({ data }) => current && setBrief(data))
      .catch((e) => current && setError(apiError(e, 'Could not load this transaction.')));
    return () => {
      current = false;
    };
  }, [id]);

  if (error) return <Alert tone="error">{error}</Alert>;
  if (!brief) return <p className="hint">Loading…</p>;

  const c = brief.currency;
  return (
    <div className="stack stack-sm">
      <p>
        {brief.transaction_number && <>Transaction {brief.transaction_number} · </>}
        <Badge variant={brief.detail_status === 'Unbalanced' ? 'error' : 'secondary'} size="sm">
          {brief.detail_status}
        </Badge>
        {brief.original_reference && (
          <>
            {' '}
            · return of {brief.original_reference}
            {brief.return_reason ? ` (${brief.return_reason})` : ''}
          </>
        )}
      </p>
      {brief.tenders.length > 0 && (
        <div>
          <b>Paid with</b>
          <ul>
            {brief.tenders.map((t, i) => (
              <li key={`${t.tender_type}-${i}`}>
                {t.tender_type}: {money(t.amount, c)}
                {t.reference ? ` · ${t.reference}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      {brief.line_count > 0 && (
        <div>
          <b>
            {brief.line_count.toLocaleString()} line{brief.line_count === 1 ? '' : 's'}
          </b>
          <ul>
            {brief.lines.map((l) => (
              <li key={l.line_no}>
                {l.item_code}
                {l.description ? ` · ${l.description}` : ''} · {Number(l.quantity)} ×{' '}
                {money(l.unit_price, c)} = {money(l.gross_amount, c)}
              </li>
            ))}
          </ul>
          {brief.line_count > brief.lines.length && (
            <p className="hint">
              Showing the first {brief.lines.length}; the rest are on the transaction.
            </p>
          )}
        </div>
      )}
      {brief.taxes.length > 0 && (
        <p>
          <b>Tax</b>{' '}
          {brief.taxes
            .map((t) => `${t.tax_name || t.tax_code}: ${money(t.tax_amount, c)}`)
            .join(' · ')}
        </p>
      )}
      <p className="hint">
        {brief.discount_count} discount{brief.discount_count === 1 ? '' : 's'} · {brief.corrections}{' '}
        correction{brief.corrections === 1 ? '' : 's'}
      </p>
      <Link href={`/transactions/${id}`}>Open the transaction</Link>
    </div>
  );
}
