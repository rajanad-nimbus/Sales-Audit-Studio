'use client';

import { use, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api, money } from '@/lib/api';
import { CopyButton, SkeletonRows } from '@/components/Feedback';
import { StatusBadge } from '@/components/CaseTable';
import { CaseLink } from '@/components/MeProvider';

interface Linked { id: string; business_date: string; store_id: string; source_system: string; transaction_type: string; amount: string; reconciliation_status: string; payment_reference: string | null }
interface DLine { line_no: number; item_code: string; description: string | null; quantity: number; unit_price: string; gross_amount: string; tax_code: string | null; tax_rate: string; tax_amount: string; discount_amount: string; return_reason: string | null }
interface DDetail {
  status: 'No detail' | 'Balanced' | 'Unbalanced'; lines: DLine[];
  discounts: { kind: string; code: string | null; description: string | null; line_no: number | null; amount: string }[];
  taxes: { tax_code: string; tax_name: string; rate: string; taxable_amount: string; tax_amount: string }[];
  tenders: { tender_type: string; amount: string; reference: string | null; authorization: string | null }[];
  totals: { gross: string; discounts: string; tax: string; net: string; expected_total: string; tendered: string } | null;
  total_variance: string | null; tender_variance: string | null;
}
interface DReturns {
  is_return: boolean; original_reference?: string | null; reason?: string | null; over_returned?: boolean;
  original?: { id: string; business_date: string; amount: string; source_record_id: string | null } | null;
  items?: { item_code: string; description: string | null; returned_qty: string; sold_qty: string | null; already_returned_qty: string; reason: string | null; over_returned: boolean }[];
  returns?: { id: string; business_date: string; amount: string; reason: string | null; status: string; items: { item_code: string; quantity: string }[] }[];
}
interface Detail extends Linked {
  detail: DDetail; returns: DReturns | null;
  event_timestamp: string; processing_timestamp: string; register_id: string | null; tender_type: string | null; currency: string; settlement_reference: string | null; settlement_date: string;
  transaction_subtype: string | null; channel_id: string; quantity: number | null; source_lineage: string; disposition: string; source_record_id: string | null;
  source: null | { source_system: string; source_record_id: string; source_version: string; delivery_id: string; status: string; received_at: string; source_event_time: string; payload_hash: string; quarantine_reason: string | null; payload: { text: string | null; truncated: boolean } };
  cases: { id: string; case_number: string; case_type: string; status: string; link: string }[];
  linked_transactions: Linked[];
  adjustments: { id: string; adjustment_type: string; status: string; rationale: string; proposed_by: string; created_at: string }[];
}
const tone = (s: string) => (s === 'Matched' ? 'badge-success' : s === 'Exception' ? 'badge-error' : 'badge-warning');
const when = (iso: string) => new Date(iso).toLocaleString();

export default function TransactionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [d, setD] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setD(null);
    api.get(`/api/transactions/${id}`).then((r) => { setD(r.data); setError(null); }).catch(() => setError('Transaction not found'));
  }, [id]);

  if (error) return <div className="empty-state"><b>{error}</b><p>It may not exist or you may lack access.</p><Link href="/transactions">Back to transactions</Link></div>;
  if (!d) return <SkeletonRows rows={8} label="Loading transaction" />;
  const fields: [string, React.ReactNode][] = [
    ['Business date', d.business_date], ['Store', d.store_id], ['Register', d.register_id ?? '—'], ['Source', d.source_system], ['Type', d.transaction_subtype ? `${d.transaction_type} · ${d.transaction_subtype}` : d.transaction_type],
    ['Tender', d.tender_type ?? '—'], ['Channel', d.channel_id], ['Quantity', d.quantity ?? '—'], ['Payment reference', d.payment_reference ?? '—'], ['Settlement reference', d.settlement_reference ?? '—'],
    ['Settlement date', d.settlement_date], ['Event time', when(d.event_timestamp)], ['Processed', when(d.processing_timestamp)], ['Lineage', d.source_lineage], ['Disposition', d.disposition],
  ];
  const src = d.source;
  const det = d.detail;
  const isReturn = d.transaction_type === 'Return';
  const dKind: Record<string, string> = { Promotion: 'badge-info', Coupon: 'badge-primary', Voucher: 'badge-warning', Manual: 'badge-secondary' };
  const num = (v: string | number) => Number(v);
  return (
    <div className="case-workspace">
      <div className="case-hero">
        <div>
          <a href="/transactions" className="case-back" onClick={(e) => { if (window.history.length > 1) { e.preventDefault(); router.back(); } }}>← Back</a>
          <h1>{money(d.amount)} <small className="act-sub">{d.currency}</small></h1>
          <p>{d.transaction_type} · {d.source_system} · Store {d.store_id} · {d.business_date}</p>
        </div>
        <div className="case-hero-side"><span className={`badge badge-md ${tone(d.reconciliation_status)}`}>{d.reconciliation_status}</span></div>
      </div>

      <section className="info-card case-section">
        <h2>{isReturn ? 'Items returned' : 'Items'} {det.status !== 'No detail' && <span className={`badge badge-sm ${det.status === 'Balanced' ? 'badge-success' : 'badge-warning'}`} style={{ marginLeft: '.4rem' }}>{det.status}</span>}</h2>
        {det.status === 'No detail' ? (
          <p className="cc-empty">The feed sent only a total for this transaction, with no items, tax, discounts or tenders.</p>
        ) : det.lines.length === 0 ? <p className="cc-empty">No item lines were sent.</p> : (
          <div className="table-scroll"><table className="data-table">
            <thead><tr><th>#</th><th>Item</th><th className="num">Qty</th><th className="num">Unit price</th><th className="num">Amount</th><th className="num">Discount</th><th className="num">VAT</th><th className="num">Line total</th></tr></thead>
            <tbody>{det.lines.map((l) => (
              <tr key={l.line_no}><td>{l.line_no}</td>
                <td className="font-medium">{l.item_code}<small className="act-sub" style={{ display: 'block' }}>{l.description}{l.return_reason ? ` · ${l.return_reason}` : ''}</small></td>
                <td className="num">{Math.abs(l.quantity)}</td><td className="num">{money(l.unit_price)}</td><td className="num">{money(l.gross_amount)}</td>
                <td className="num">{num(l.discount_amount) ? money(l.discount_amount) : '—'}</td>
                <td className="num">{money(l.tax_amount)}{l.tax_code && <small className="act-sub" style={{ display: 'block' }}>{l.tax_code} {(num(l.tax_rate) * 100).toFixed(0)}%</small>}</td>
                <td className="num">{money(num(l.gross_amount) - num(l.discount_amount) + num(l.tax_amount))}</td></tr>
            ))}</tbody></table></div>
        )}
      </section>

      {det.status !== 'No detail' && (
        <div className="case-panel case-two">
          <section className="info-card case-section">
            <h2>Does it add up?</h2>
            {det.totals && det.lines.length > 0 && (
              <dl className="kv">
                <dt>Items</dt><dd>{money(det.totals.gross)}</dd>
                <dt>Discounts</dt><dd>{num(det.totals.discounts) ? `− ${money(Math.abs(num(det.totals.discounts)))}` : '—'}</dd>
                <dt>VAT</dt><dd>+ {money(det.totals.tax)}</dd>
                <dt>Calculated total</dt><dd><b>{money(det.totals.expected_total)}</b></dd>
                <dt>Transaction amount</dt><dd><b>{money(d.amount)}</b></dd>
                <dt>Difference</dt><dd className={Math.abs(num(det.total_variance ?? 0)) > 0.01 ? 'sd-neg' : ''}>{money(det.total_variance ?? 0)}</dd>
              </dl>
            )}
            {det.tenders.length > 0 && (
              <>
                <h3 className="sd-section" style={{ marginTop: '.9rem' }}>Paid with</h3>
                <div className="table-scroll"><table className="data-table">
                  <thead><tr><th>Tender</th><th>Reference</th><th className="num">Amount</th></tr></thead>
                  <tbody>{det.tenders.map((t, i) => <tr key={i}><td className="font-medium">{t.tender_type}</td><td>{t.reference ?? '—'}</td><td className="num">{money(t.amount)}</td></tr>)}</tbody></table></div>
                {Math.abs(num(det.tender_variance ?? 0)) > 0.01 && <p className="next-err">Tenders differ from the amount by {money(det.tender_variance ?? 0)}.</p>}
                {det.tenders.some((t) => t.tender_type !== 'Card') && <p className="hint" style={{ marginTop: '.4rem' }}>Only the card part reaches the payment processor, so reconciliation compares the processor with the card tender.</p>}
              </>
            )}
          </section>

          <div className="case-stack">
            <section className="info-card case-section">
              <h2>Discounts, coupons and vouchers</h2>
              {det.discounts.length === 0 ? <p className="cc-empty">No discounts were applied.</p> : (
                <div className="table-scroll"><table className="data-table">
                  <thead><tr><th>Type</th><th>Code</th><th>Applies to</th><th className="num">Amount</th></tr></thead>
                  <tbody>{det.discounts.map((x, i) => (
                    <tr key={i}><td><span className={`badge badge-sm ${dKind[x.kind] ?? 'badge-secondary'}`}>{x.kind}</span></td>
                      <td>{x.code ?? '—'}<small className="act-sub" style={{ display: 'block' }}>{x.description}</small></td>
                      <td>{x.line_no ? `Line ${x.line_no}` : 'Whole sale'}</td><td className="num">{money(x.amount)}</td></tr>
                  ))}</tbody></table></div>
              )}
            </section>
            <section className="info-card case-section">
              <h2>VAT</h2>
              {det.taxes.length === 0 ? <p className="cc-empty">No tax was charged.</p> : (
                <div className="table-scroll"><table className="data-table">
                  <thead><tr><th>Tax</th><th className="num">Rate</th><th className="num">Taxable amount</th><th className="num">Tax</th></tr></thead>
                  <tbody>{det.taxes.map((x) => <tr key={x.tax_code}><td className="font-medium">{x.tax_name}</td><td className="num">{(num(x.rate) * 100).toFixed(0)}%</td><td className="num">{money(x.taxable_amount)}</td><td className="num">{money(x.tax_amount)}</td></tr>)}</tbody></table></div>
              )}
            </section>
          </div>
        </div>
      )}

      {d.returns?.is_return && (
        <section className="info-card case-section">
          <h2>Return {d.returns.over_returned && <span className="badge badge-sm badge-error" style={{ marginLeft: '.4rem' }}>Returns more than was sold</span>}</h2>
          {d.returns.original ? (
            <p>Reverses sale <Link href={`/transactions/${d.returns.original.id}`}>{d.returns.original.source_record_id ?? 'original sale'}</Link> of {money(d.returns.original.amount)} on {d.returns.original.business_date}.{d.returns.reason ? ` Reason: ${d.returns.reason}.` : ''}</p>
          ) : d.returns.original_reference ? (
            <div className="alert alert-warning">The original sale <b>{d.returns.original_reference}</b> was not found in Nimbus. This return cannot be checked against a sale.</div>
          ) : <div className="alert alert-warning">No original sale was referenced. This is a return without a receipt.</div>}
          {d.returns.items && d.returns.items.length > 0 && (
            <div className="table-scroll"><table className="data-table">
              <thead><tr><th>Item</th><th className="num">Returned now</th><th className="num">Sold</th><th className="num">Returned before</th><th>Reason</th></tr></thead>
              <tbody>{d.returns.items.map((i) => (
                <tr key={i.item_code}><td className="font-medium">{i.item_code}<small className="act-sub" style={{ display: 'block' }}>{i.description}</small></td>
                  <td className="num">{i.returned_qty}</td><td className="num">{i.sold_qty ?? '—'}</td><td className="num">{i.already_returned_qty}</td>
                  <td>{i.reason ?? '—'}{i.over_returned && <span className="badge badge-sm badge-error" style={{ marginLeft: '.4rem' }}>{i.sold_qty === null ? 'Not on the original sale' : 'Over-returned'}</span>}</td></tr>
              ))}</tbody></table></div>
          )}
        </section>
      )}
      {d.returns && !d.returns.is_return && d.returns.returns && d.returns.returns.length > 0 && (
        <section className="info-card case-section">
          <h2>Returns against this sale</h2>
          <div className="table-scroll"><table className="data-table">
            <thead><tr><th>Return</th><th>Date</th><th>Items</th><th>Reason</th><th className="num">Amount</th></tr></thead>
            <tbody>{d.returns.returns.map((x) => (
              <tr key={x.id}><td><Link href={`/transactions/${x.id}`}>Open return</Link></td><td>{x.business_date}</td>
                <td>{x.items.map((i) => `${i.quantity} × ${i.item_code}`).join(', ')}</td><td>{x.reason ?? '—'}</td><td className="num">{money(x.amount)}</td></tr>
            ))}</tbody></table></div>
        </section>
      )}

      <div className="case-panel case-two">
        <section className="info-card case-section">
          <h2>Details</h2>
          <dl className="kv">{fields.map(([k, v]) => (<div key={k} style={{ display: 'contents' }}><dt>{k}</dt><dd>{v}</dd></div>))}</dl>
          <p className="hint" style={{ marginTop: '.5rem' }}>Transaction id <code>{d.id}</code> <CopyButton value={d.id} /></p>
        </section>

        <div className="case-stack">
          <section className="info-card case-section">
            <h2>Related cases</h2>
            {d.cases.length === 0 ? <p className="cc-empty">No case has been raised for this store and day.</p> : (
              <ul className="summary-list">{d.cases.map((c) => (
                <li key={c.id}><CaseLink id={c.id}>{c.case_number}</CaseLink> · {c.case_type} <StatusBadge s={c.status} />
                  <small className="act-sub" style={{ display: 'block' }}>{c.link === 'raised from this transaction' ? 'Raised from this transaction' : 'Same store and day. Not necessarily caused by this transaction.'}</small></li>
              ))}</ul>
            )}
          </section>
          <section className="info-card case-section">
            <h2>Linked transactions</h2>
            {d.linked_transactions.length === 0 ? <p className="cc-empty">No other transaction shares this payment or settlement reference.</p> : (
              <div className="table-scroll"><table className="data-table">
                <thead><tr><th>Source</th><th>Type</th><th className="num">Amount</th><th>Status</th></tr></thead>
                <tbody>{d.linked_transactions.map((t) => (
                  <tr key={t.id}><td><Link href={`/transactions/${t.id}`}>{t.source_system}</Link></td><td>{t.transaction_type}</td><td className="num">{money(t.amount)}</td>
                    <td><span className={`badge badge-sm ${tone(t.reconciliation_status)}`}>{t.reconciliation_status}</span></td></tr>
                ))}</tbody></table></div>
            )}
          </section>
          {d.adjustments.length > 0 && (
            <section className="info-card case-section">
              <h2>Adjustments</h2>
              {d.adjustments.map((a) => (
                <div key={a.id} className="indicator"><div style={{ flex: 1 }}><b>{a.adjustment_type}</b><p>{a.rationale} · {a.proposed_by} · {when(a.created_at)}</p></div>
                  <span className="badge badge-sm badge-secondary">{a.status}</span></div>
              ))}
              <Link href="/corrections">Open Transaction Corrections</Link>
            </section>
          )}
        </div>
      </div>

      <section className="info-card case-section">
        <h2>Source record</h2>
        {!src ? <p className="cc-empty">This transaction has no archived source record.</p> : (
          <>
            <p className="hint" style={{ marginBottom: '.6rem' }}>The archived, read-only record this transaction was normalized from.</p>
            <dl className="kv">
              {([['System', src.source_system], ['Source record id', src.source_record_id], ['Version', src.source_version], ['Delivery', src.delivery_id], ['Status', src.status],
                ['Received', when(src.received_at)], ['Source event time', when(src.source_event_time)], ['Payload hash', <><code>{src.payload_hash}</code> <CopyButton value={src.payload_hash} /></>]] as [string, React.ReactNode][]).map(([k, v]) => (
                <div key={k} style={{ display: 'contents' }}><dt>{k}</dt><dd>{v}</dd></div>))}
            </dl>
            {src.quarantine_reason && <div className="alert alert-warning" style={{ marginTop: '.6rem' }}>Quarantined: {src.quarantine_reason}</div>}
            <div className="sd-section">
              <h3>Raw payload {src.payload.text && <CopyButton value={src.payload.text} label="Copy payload" />}</h3>
              {src.payload.text ? <pre className="ex-pre" style={{ maxHeight: 360 }}>{src.payload.text}{src.payload.truncated ? '\n… truncated' : ''}</pre> : <p className="cc-empty">The payload is empty.</p>}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
