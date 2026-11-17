'use client';

import { use, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api, money } from '@/lib/api';
import { formatDateTime } from '@/lib/preferences';
import { downloadBlob, downloadCsv } from '@/lib/csv';
import { CopyButton, SkeletonRows } from '@/components/Feedback';
import { StatusBadge } from '@/components/CaseTable';
import { CaseLink, useMe } from '@/components/MeProvider';
import { Alert, Badge, Button, Card, DataTable } from '@/components/ui';
import TransactionComments from '@/components/TransactionComments';
import TransactionAuditTrail from '@/components/TransactionAuditTrail';

interface Linked {
  id: string;
  business_date: string;
  store_id: string;
  source_system: string;
  transaction_type: string;
  amount: string;
  reconciliation_status: string;
  payment_reference: string | null;
  customer_id?: string | null;
  customer_identification_type?: string | null;
  customer_attributes?: Record<string, string> | null;
  cashier_id?: string | null;
  salesperson_id?: string | null;
  user_references?: Record<string, string> | null;
}
interface DLine {
  line_no: number;
  item_code: string;
  description: string | null;
  quantity: number;
  unit_price: string;
  original_unit_price: string | null;
  price_override_reason: string | null;
  source_attributes: Record<string, string | number | boolean | null> | null;
  gross_amount: string;
  tax_code: string | null;
  tax_rate: string;
  tax_amount: string;
  discount_amount: string;
  return_reason: string | null;
}
interface DDetail {
  status: 'No detail' | 'Balanced' | 'Unbalanced';
  line_count: number;
  lines_truncated: boolean;
  lines: DLine[];
  discount_count: number;
  discounts_truncated: boolean;
  discounts: {
    id: string;
    kind: string;
    code: string | null;
    description: string | null;
    line_no: number | null;
    amount: string;
  }[];
  tax_count: number;
  taxes_truncated: boolean;
  taxes: {
    id: string;
    tax_code: string;
    tax_name: string;
    rate: string;
    taxable_amount: string;
    tax_amount: string;
  }[];
  tender_count: number;
  tenders_truncated: boolean;
  tenders: {
    tender_type: string;
    amount: string;
    reference: string | null;
    authorization: string | null;
    source_attributes: Record<string, string | number | boolean | null> | null;
  }[];
  totals: {
    gross: string;
    discounts: string;
    tax: string;
    net: string;
    expected_total: string;
    tendered: string;
  } | null;
  total_variance: string | null;
  tender_variance: string | null;
}
interface VoucherEvidence {
  items: {
    voucher_number: string;
    contexts: { source: string; detail: string | null; amount: string | null }[];
    status: string;
    amount: string;
    currency: string | null;
    issued_store_id: string | null;
    redeemed_store_id: string | null;
    redeemed_on: string | null;
    transaction_linked: boolean;
    ledger_transaction_mismatch: boolean;
  }[];
  truncated: boolean;
}
interface DReturns {
  is_return: boolean;
  items_truncated?: boolean;
  original_reference?: string | null;
  reason?: string | null;
  over_returned?: boolean;
  original?: {
    id: string;
    business_date: string;
    amount: string;
    source_record_id: string | null;
  } | null;
  items?: {
    item_code: string;
    description: string | null;
    returned_qty: string;
    sold_qty: string | null;
    already_returned_qty: string;
    reason: string | null;
    over_returned: boolean;
  }[];
  returns?: {
    id: string;
    business_date: string;
    amount: string;
    reason: string | null;
    status: string;
    items: { item_code: string; quantity: string }[];
    items_truncated: boolean;
  }[];
  returns_truncated?: boolean;
}
interface Detail extends Linked {
  detail: DDetail;
  returns: DReturns | null;
  event_timestamp: string;
  processing_timestamp: string;
  register_id: string | null;
  tender_type: string | null;
  currency: string;
  settlement_reference: string | null;
  settlement_date: string;
  transaction_subtype: string | null;
  channel_id: string;
  quantity: number | null;
  source_lineage: string;
  disposition: string;
  source_record_id: string | null;
  source: null | {
    source_system: string;
    source_record_id: string;
    source_version: string;
    delivery_id: string;
    status: string;
    received_at: string;
    source_event_time: string;
    payload_hash: string;
    quarantine_reason: string | null;
    payload: { text: string | null; truncated: boolean };
  };
  cases: { id: string; case_number: string; case_type: string; status: string; link: string }[];
  cases_truncated: boolean;
  errors: {
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
  }[];
  errors_truncated: boolean;
  linked_transactions: Linked[];
  adjustments: {
    id: string;
    adjustment_type: string;
    status: string;
    rationale: string;
    proposed_by: string;
    created_at: string;
  }[];
  adjustments_truncated: boolean;
}
const tone = (s: string) =>
  s === 'Matched' ? 'badge-success' : s === 'Exception' ? 'badge-error' : 'badge-warning';
const when = (iso: string) => formatDateTime(iso);
interface FieldChange {
  id: string;
  field: string;
  before: unknown;
  after: unknown;
  change: unknown;
  status: string;
  applied: boolean;
  rationale: string;
  proposed_by: string;
  proposed_at: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  invalid?: boolean;
}
interface ChangeHistory {
  currency: string;
  truncated: boolean;
  changes: FieldChange[];
}
const MONEY_FIELDS = ['Amount', 'Tax'];
const shown = (c: FieldChange, value: unknown, currency: string) =>
  value === null || value === undefined
    ? '—'
    : MONEY_FIELDS.includes(c.field)
      ? money(String(value), currency)
      : typeof value === 'string'
        ? value
        : value && typeof value === 'object' && !Array.isArray(value)
          ? Object.entries(value as Record<string, unknown>)
              .map(([key, item]) => {
                const label = key.replaceAll('_', ' ');
                const display =
                  key === 'rate'
                    ? `${(Number(item) * 100).toFixed(2)}%`
                    : ['taxable_amount', 'tax_amount', 'amount'].includes(key)
                      ? money(String(item), currency)
                      : String(item ?? '—');
                return `${label}: ${display}`;
              })
              .join(' · ')
          : JSON.stringify(value);

export default function TransactionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { me } = useMe();
  const [d, setD] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<ChangeHistory | null>(null);
  const [voucherEvidence, setVoucherEvidence] = useState<VoucherEvidence | null>(null);
  const [refreshingAudit, setRefreshingAudit] = useState(false);
  const [refreshAuditMessage, setRefreshAuditMessage] = useState('');
  const [detailExporting, setDetailExporting] = useState<string | null>(null);
  const [detailExportError, setDetailExportError] = useState<string | null>(null);
  useEffect(() => {
    setHistory(null);
    api
      .get<ChangeHistory>(`/api/transactions/${id}/changes`)
      .then((r) => setHistory(r.data))
      .catch(() => setHistory(null));
  }, [id]);
  useEffect(() => {
    if (!['finance', 'it'].includes(me?.role ?? '')) {
      setVoucherEvidence(null);
      return;
    }
    let active = true;
    api
      .get<VoucherEvidence>(`/api/vouchers/transactions/${id}/context`)
      .then(({ data }) => {
        if (active) setVoucherEvidence(data);
      })
      .catch(() => {
        if (active) setVoucherEvidence({ items: [], truncated: false });
      });
    return () => {
      active = false;
    };
  }, [id, me?.role]);
  useEffect(() => {
    setD(null);
    api
      .get(`/api/transactions/${id}`)
      .then((r) => {
        setD(r.data);
        setError(null);
      })
      .catch(() => setError('Transaction not found'));
  }, [id]);

  if (error)
    return (
      <div className="empty-state">
        <b>{error}</b>
        <p>It may not exist or you may lack access.</p>
        <Link href="/transactions">Back to transactions</Link>
      </div>
    );
  if (!d) return <SkeletonRows rows={8} label="Loading transaction" />;
  const eventCalendarDate = d.event_timestamp.slice(0, 10);
  const assignedToDifferentBusinessDay = eventCalendarDate !== d.business_date;
  const fields: [string, React.ReactNode][] = [
    [
      'Business date',
      <span className="cluster" key="business-date">
        {d.business_date}
        {assignedToDifferentBusinessDay && (
          <span title={`Event timestamp date: ${eventCalendarDate}`}>
            <Badge size="sm" variant="info">
              POS-assigned day
            </Badge>
          </span>
        )}
      </span>,
    ],
    ['Store', d.store_id],
    ['Register', d.register_id ?? '—'],
    ['Source', d.source_system],
    [
      'Type',
      d.transaction_subtype
        ? `${d.transaction_type} · ${d.transaction_subtype}`
        : d.transaction_type,
    ],
    ['Tender', d.tender_type ?? '—'],
    ['Channel', d.channel_id],
    [
      'Cashier',
      <span className="cluster" key="cashier">
        {d.cashier_id ?? '—'}
        {['finance', 'it'].includes(me?.role ?? '') && (
          <Link href={`/corrections/new?kind=Employee&transaction_id=${encodeURIComponent(d.id)}`}>
            Correct employee details
          </Link>
        )}
      </span>,
    ],
    ['Quantity', d.quantity ?? '—'],
    ['Payment reference', d.payment_reference ?? '—'],
    [
      'Customer reference (masked)',
      d.customer_id
        ? `${d.customer_id}${d.customer_identification_type ? ` · ${d.customer_identification_type}` : ''}`
        : '—',
    ],
    [
      'Customer attributes',
      <span className="stack stack-xs" key="customer-attributes">
        {Object.entries(d.customer_attributes ?? {}).length ? (
          Object.entries(d.customer_attributes ?? {}).map(([attributeType, value]) => (
            <span key={attributeType}>
              {attributeType}: {value}{' '}
              {['finance', 'it'].includes(me?.role ?? '') && (
                <Link
                  href={`/corrections/new?kind=CustomerAttribute&transaction_id=${encodeURIComponent(d.id)}&attribute_type=${encodeURIComponent(attributeType)}`}
                >
                  Correct
                </Link>
              )}
            </span>
          ))
        ) : (
          <span>None recorded</span>
        )}
        {['finance', 'it'].includes(me?.role ?? '') && (
          <Link
            href={`/corrections/new?kind=CustomerAttribute&transaction_id=${encodeURIComponent(d.id)}`}
          >
            Add customer attribute
          </Link>
        )}
      </span>,
    ],
    ['Salesperson', d.salesperson_id ?? '—'],
    [
      'Retailer references',
      Object.entries(d.user_references ?? {})
        .map(([field, value]) => `${field.replace('reference_', 'Ref ')}: ${value}`)
        .join(' · ') || '—',
    ],
    ['Settlement reference', d.settlement_reference ?? '—'],
    ['Settlement date', d.settlement_date],
    ['Event time', when(d.event_timestamp)],
    ['Processed', when(d.processing_timestamp)],
    ['Lineage', d.source_lineage],
    ['Disposition', d.disposition],
  ];
  const src = d.source;
  const det = d.detail;
  const isReturn = d.transaction_type === 'Return';
  const truncatedSections = [
    det.lines_truncated && `${det.line_count} item lines`,
    det.discounts_truncated && `${det.discount_count} discounts`,
    det.taxes_truncated && `${det.tax_count} tax rows`,
    det.tenders_truncated && `${det.tender_count} tender rows`,
  ].filter(Boolean);
  const dKind: Record<string, string> = {
    Promotion: 'badge-info',
    Coupon: 'badge-primary',
    Voucher: 'badge-warning',
    Manual: 'badge-secondary',
  };
  const num = (v: string | number) => Number(v);
  const canRefreshAudit = ['finance', 'it', 'admin'].includes(me?.role ?? '');
  const downloadDetailSection = async (section: 'items' | 'discounts' | 'taxes' | 'tenders') => {
    setDetailExporting(section);
    setDetailExportError(null);
    try {
      const { data } = await api.get(`/api/transactions/${id}/detail/${section}.csv`, {
        responseType: 'blob',
      });
      downloadBlob(`transaction-${id}-${section}.csv`, data);
    } catch (cause) {
      const statusCode = (cause as { response?: { status?: number } })?.response?.status;
      setDetailExportError(
        statusCode === 413
          ? 'This detail section exceeds the 100,000 row export limit.'
          : `Could not export transaction ${section}.`
      );
    } finally {
      setDetailExporting(null);
    }
  };
  const refreshAudit = async () => {
    if (!d) return;
    setRefreshingAudit(true);
    setRefreshAuditMessage('');
    try {
      const { data } = await api.post('/api/reconcile', null, {
        params: { business_date: d.business_date, store_id: d.store_id },
      });
      const result = await api.get<Detail>(`/api/transactions/${id}`);
      setD(result.data);
      setRefreshAuditMessage(
        `Audit refreshed for ${data.transactions_examined} transaction(s); ${data.cases_created} case(s) created.`
      );
    } catch (e: any) {
      setRefreshAuditMessage(
        e?.response?.data?.detail ?? 'Could not refresh this store day’s transaction audit.'
      );
    } finally {
      setRefreshingAudit(false);
    }
  };
  return (
    <div className="case-workspace">
      <div className="case-hero">
        <div>
          <a
            href="/transactions"
            className="case-back"
            onClick={(e) => {
              if (window.history.length > 1) {
                e.preventDefault();
                router.back();
              }
            }}
          >
            ← Back
          </a>
          <h1>
            {money(d.amount)} <small className="act-sub">{d.currency}</small>
          </h1>
          <p>
            {d.transaction_type} · {d.source_system} · Store {d.store_id} · {d.business_date}
          </p>
        </div>
        <div className="case-hero-side">
          <span className={`badge badge-md ${tone(d.reconciliation_status)}`}>
            {d.reconciliation_status}
          </span>
          {canRefreshAudit && (
            <Button size="sm" variant="secondary" disabled={refreshingAudit} onClick={refreshAudit}>
              {refreshingAudit ? 'Refreshing…' : 'Refresh audit errors'}
            </Button>
          )}
          {refreshAuditMessage && <small role="status">{refreshAuditMessage}</small>}
        </div>
      </div>

      {detailExportError && (
        <Alert tone="error" onDismiss={() => setDetailExportError(null)}>
          {detailExportError}
        </Alert>
      )}

      <Card className="case-section">
        <div className="section-heading">
          <h2>
            {isReturn ? 'Items returned' : 'Items'}{' '}
            {det.status !== 'No detail' && (
              <span
                className={`badge badge-sm ${det.status === 'Balanced' ? 'badge-success' : 'badge-warning'}`}
                style={{ marginLeft: '.4rem' }}
              >
                {det.status}
              </span>
            )}
          </h2>
          {det.line_count > 0 && (
            <Button
              size="sm"
              variant="secondary"
              disabled={detailExporting !== null}
              onClick={() => void downloadDetailSection('items')}
            >
              {detailExporting === 'items' ? 'Preparing…' : 'Export all items CSV'}
            </Button>
          )}
        </div>
        {truncatedSections.length > 0 && (
          <p className="hint">
            Showing up to 500 rows per detail section. {truncatedSections.join(', ')} are truncated;
            financial totals and balance checks use all stored rows. Use each section's CSV action
            to export every row, up to 100,000.
          </p>
        )}
        {det.status === 'No detail' ? (
          <p className="cc-empty">
            The feed sent only a total for this transaction, with no items, tax, discounts or
            tenders.
          </p>
        ) : det.lines.length === 0 ? (
          <p className="cc-empty">No item lines were sent.</p>
        ) : (
          <DataTable
            columns={[
              { key: 'c0', header: '#', csvValue: (l) => l.line_no, render: (l) => l.line_no },
              {
                key: 'c1',
                header: 'Item',
                primary: true,
                csvValue: (l) =>
                  [
                    l.item_code,
                    l.description,
                    l.return_reason,
                    l.source_attributes ? JSON.stringify(l.source_attributes) : '',
                  ]
                    .filter(Boolean)
                    .join(' · '),
                render: (l) => (
                  <>
                    {l.item_code}
                    <small className="act-sub" style={{ display: 'block' }}>
                      {l.description}
                      {l.return_reason ? ` · ${l.return_reason}` : ''}
                    </small>
                    {l.source_attributes && Object.keys(l.source_attributes).length > 0 && (
                      <details style={{ marginTop: '.25rem' }}>
                        <summary className="act-sub">
                          POS item details ({Object.keys(l.source_attributes).length})
                        </summary>
                        <div className="act-sub">
                          {Object.entries(l.source_attributes).map(([key, value]) => (
                            <span key={key} style={{ display: 'block' }}>
                              {key.replaceAll('_', ' ')}: {value == null ? '—' : String(value)}
                            </span>
                          ))}
                        </div>
                      </details>
                    )}
                  </>
                ),
              },
              {
                key: 'c2',
                header: 'Qty',
                numeric: true,
                csvValue: (l) => Math.abs(l.quantity),
                render: (l) => Math.abs(l.quantity),
              },
              {
                key: 'c3',
                header: 'Unit price',
                numeric: true,
                csvValue: (l) =>
                  [
                    l.unit_price,
                    l.original_unit_price ? `Original ${l.original_unit_price}` : '',
                    l.price_override_reason ? `Override: ${l.price_override_reason}` : '',
                  ]
                    .filter(Boolean)
                    .join(' · '),
                render: (l) => (
                  <>
                    {money(l.unit_price)}
                    {(l.original_unit_price != null || l.price_override_reason) && (
                      <small className="act-sub" style={{ display: 'block' }}>
                        {l.original_unit_price != null &&
                          `${money(l.original_unit_price)} original`}
                        {l.original_unit_price != null && l.price_override_reason ? ' · ' : ''}
                        {l.price_override_reason && `Override: ${l.price_override_reason}`}
                      </small>
                    )}
                  </>
                ),
              },
              {
                key: 'c4',
                header: 'Amount',
                numeric: true,
                csvValue: (l) => l.gross_amount,
                render: (l) => money(l.gross_amount),
              },
              {
                key: 'c5',
                header: 'Discount',
                numeric: true,
                csvValue: (l) => l.discount_amount,
                render: (l) => (num(l.discount_amount) ? money(l.discount_amount) : '—'),
              },
              {
                key: 'c6',
                header: 'VAT',
                numeric: true,
                csvValue: (l) =>
                  `${l.tax_amount}${l.tax_code ? ` · ${l.tax_code} ${(num(l.tax_rate) * 100).toFixed(0)}%` : ''}`,
                render: (l) => (
                  <>
                    {money(l.tax_amount)}
                    {l.tax_code && (
                      <small className="act-sub" style={{ display: 'block' }}>
                        {l.tax_code} {(num(l.tax_rate) * 100).toFixed(0)}%
                      </small>
                    )}
                    {['finance', 'it'].includes(me?.role ?? '') && (
                      <Link
                        href={`/corrections/new?kind=TaxLine&transaction_id=${encodeURIComponent(d.id)}&line_no=${l.line_no}`}
                        style={{ display: 'block' }}
                      >
                        Correct line tax
                      </Link>
                    )}
                  </>
                ),
              },
              {
                key: 'c7',
                header: 'Line total',
                numeric: true,
                csvValue: (l) =>
                  num(l.gross_amount) - num(l.discount_amount) + num(l.tax_amount),
                render: (l) =>
                  money(num(l.gross_amount) - num(l.discount_amount) + num(l.tax_amount)),
              },
            ]}
            rows={det.lines}
            rowKey={(l) => String(l.line_no)}
            csvFileName={`transaction-${id}-items-visible.csv`}
            detachTitle={`Transaction ${d.source_record_id ?? id} · Items`}
          />
        )}
      </Card>

      {det.status !== 'No detail' && (
        <div className="case-panel case-two">
          <Card className="case-section">
            <h2>Does it add up?</h2>
            {det.totals && det.lines.length > 0 && (
              <dl className="kv">
                <dt>Items</dt>
                <dd>{money(det.totals.gross)}</dd>
                <dt>Discounts</dt>
                <dd>
                  {num(det.totals.discounts)
                    ? `− ${money(Math.abs(num(det.totals.discounts)))}`
                    : '—'}
                </dd>
                <dt>VAT</dt>
                <dd>+ {money(det.totals.tax)}</dd>
                <dt>Calculated total</dt>
                <dd>
                  <b>{money(det.totals.expected_total)}</b>
                </dd>
                <dt>Transaction amount</dt>
                <dd>
                  <b>{money(d.amount)}</b>
                </dd>
                <dt>Difference</dt>
                <dd className={Math.abs(num(det.total_variance ?? 0)) > 0.01 ? 'sd-neg' : ''}>
                  {money(det.total_variance ?? 0)}
                </dd>
              </dl>
            )}
            {det.tenders.length > 0 && (
              <>
                <div className="section-heading" style={{ marginTop: '.9rem' }}>
                  <h3 className="sd-section">Paid with</h3>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={det.tenders.length === 0}
                    onClick={() =>
                      downloadCsv(
                        `transaction-${id}-tenders-visible.csv`,
                        ['Tender', 'Reference', 'Authorization', 'POS details', 'Amount'],
                        det.tenders.map((t) => [
                          t.tender_type,
                          t.reference,
                          t.authorization,
                          t.source_attributes ? JSON.stringify(t.source_attributes) : '',
                          t.amount,
                        ])
                      )
                    }
                  >
                    Export visible tenders CSV
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={detailExporting !== null}
                    onClick={() => void downloadDetailSection('tenders')}
                  >
                    {detailExporting === 'tenders' ? 'Preparing…' : 'Export all tenders CSV'}
                  </Button>
                </div>
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Tender</th>
                        <th>Reference</th>
                        <th>Authorization</th>
                        <th>POS details</th>
                        <th className="num">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {det.tenders.map((t, i) => (
                        <tr key={i}>
                          <td className="font-medium">{t.tender_type}</td>
                          <td>{t.reference ?? '—'}</td>
                          <td>{t.authorization ?? '—'}</td>
                          <td>
                            {t.source_attributes && Object.keys(t.source_attributes).length > 0 ? (
                              <details>
                                <summary>
                                  View {Object.keys(t.source_attributes).length} fields
                                </summary>
                                <div className="act-sub">
                                  {Object.entries(t.source_attributes).map(([key, value]) => (
                                    <div key={key}>
                                      {key.replaceAll('_', ' ')}:{' '}
                                      {value == null ? '—' : String(value)}
                                    </div>
                                  ))}
                                </div>
                              </details>
                            ) : (
                              '—'
                            )}
                          </td>
                          <td className="num">{money(t.amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {Math.abs(num(det.tender_variance ?? 0)) > 0.01 && (
                  <p className="next-err">
                    Tenders differ from the amount by {money(det.tender_variance ?? 0)}.
                  </p>
                )}
                {det.tenders.some((t) => t.tender_type !== 'Card') && (
                  <p className="hint" style={{ marginTop: '.4rem' }}>
                    Only the card part reaches the payment processor, so reconciliation compares the
                    processor with the card tender.
                  </p>
                )}
              </>
            )}
          </Card>

          <div className="case-stack">
            <Card className="case-section">
              <div className="section-heading">
                <h2>Discounts, coupons and vouchers</h2>
                {det.discounts.length > 0 && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      downloadCsv(
                        `transaction-${id}-discounts-visible.csv`,
                        ['Type', 'Code', 'Description', 'Applies to', 'Amount'],
                        det.discounts.map((item) => [
                          item.kind,
                          item.code,
                          item.description,
                          item.line_no === null ? 'Whole sale' : item.line_no,
                          item.amount,
                        ])
                      )
                    }
                  >
                    Export visible discounts CSV
                  </Button>
                )}
                {det.discount_count > 0 && (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={detailExporting !== null}
                    onClick={() => void downloadDetailSection('discounts')}
                  >
                    {detailExporting === 'discounts' ? 'Preparing…' : 'Export all discounts CSV'}
                  </Button>
                )}
              </div>
              {det.discounts.length === 0 ? (
                <p className="cc-empty">No discounts were applied.</p>
              ) : (
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Type</th>
                        <th>Code</th>
                        <th>Applies to</th>
                        <th className="num">Amount</th>
                        {['finance', 'it'].includes(me?.role ?? '') && <th>Correction</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {det.discounts.map((x) => (
                        <tr key={x.id}>
                          <td>
                            <span
                              className={`badge badge-sm ${dKind[x.kind] ?? 'badge-secondary'}`}
                            >
                              {x.kind}
                            </span>
                          </td>
                          <td>
                            {x.code ?? '—'}
                            <small className="act-sub" style={{ display: 'block' }}>
                              {x.description}
                            </small>
                          </td>
                          <td>{x.line_no ? `Line ${x.line_no}` : 'Whole sale'}</td>
                          <td className="num">{money(x.amount)}</td>
                          {['finance', 'it'].includes(me?.role ?? '') && (
                            <td>
                              <Link
                                href={`/corrections/new?kind=Discount&transaction_id=${encodeURIComponent(d.id)}&discount_id=${encodeURIComponent(x.id)}`}
                              >
                                Correct
                              </Link>
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
            {voucherEvidence && voucherEvidence.items.length > 0 && (
              <Card className="case-section">
                <h2>Voucher ledger trace</h2>
                {voucherEvidence.truncated && (
                  <p className="hint">Showing the first 200 voucher references.</p>
                )}
                <ul className="summary-list">
                  {voucherEvidence.items.map((item) => (
                    <li key={item.voucher_number}>
                      <b>{item.voucher_number}</b> ·{' '}
                      <Badge
                        variant={
                          item.ledger_transaction_mismatch || item.status === 'Escheated'
                            ? 'error'
                            : item.status === 'Not in ledger' || item.status === 'Issued'
                              ? 'warning'
                              : 'success'
                        }
                        size="sm"
                      >
                        {item.status}
                      </Badge>
                      <small className="act-sub" style={{ display: 'block' }}>
                        {item.contexts.length
                          ? item.contexts
                              .map(
                                (context) =>
                                  `${context.source}${context.detail ? ` · ${context.detail}` : ''}${context.amount ? ` · ${money(context.amount, item.currency ?? d.currency)}` : ''}`
                              )
                              .join(' | ')
                          : 'Voucher ledger links this transaction without a matching POS detail reference.'}
                        {item.transaction_linked
                          ? ' · Ledger redemption linked to this transaction'
                          : item.ledger_transaction_mismatch
                            ? ' · Ledger redemption points to another transaction'
                            : item.status === 'Issued'
                              ? ' · POS reference exists but the ledger remains open'
                              : ''}
                      </small>
                      <Link
                        href={`/cash-office?voucher=${encodeURIComponent(item.voucher_number)}#vouchers`}
                      >
                        Open voucher ledger
                      </Link>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
            <Card className="case-section">
              <div className="section-heading">
                <h2>VAT</h2>
                <div className="cluster">
                  {det.tax_count > 0 && (
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={detailExporting !== null}
                      onClick={() => void downloadDetailSection('taxes')}
                    >
                      {detailExporting === 'taxes' ? 'Preparing…' : 'Export all tax rows CSV'}
                    </Button>
                  )}
                  {['finance', 'it'].includes(me?.role ?? '') && det.taxes.length > 0 && (
                    <Link
                      href={`/corrections/new?kind=TaxDetail&transaction_id=${encodeURIComponent(d.id)}`}
                    >
                      Propose summary tax correction
                    </Link>
                  )}
                </div>
              </div>
              {det.taxes.length === 0 ? (
                <p className="cc-empty">No tax was charged.</p>
              ) : (
                <DataTable
                  columns={[
                    {
                      key: 'c0',
                      header: 'Tax',
                      primary: true,
                      csvValue: (x) => `${x.tax_name} (${x.tax_code})`,
                      render: (x) => x.tax_name,
                    },
                    {
                      key: 'c1',
                      header: 'Rate',
                      numeric: true,
                      csvValue: (x) => x.rate,
                      render: (x) => <>{(num(x.rate) * 100).toFixed(2)}%</>,
                    },
                    {
                      key: 'c2',
                      header: 'Taxable amount',
                      numeric: true,
                      csvValue: (x) => x.taxable_amount,
                      render: (x) => money(x.taxable_amount),
                    },
                    {
                      key: 'c3',
                      header: 'Tax',
                      numeric: true,
                      csvValue: (x) => x.tax_amount,
                      render: (x) => money(x.tax_amount),
                    },
                  ]}
                  rows={det.taxes}
                  rowKey={(x) => x.id}
                  csvFileName={`transaction-${id}-taxes-visible.csv`}
                  detachTitle={`Transaction ${d.source_record_id ?? id} · Taxes`}
                />
              )}
            </Card>
          </div>
        </div>
      )}

      {d.returns?.is_return && (
        <Card className="case-section">
          <h2>
            Return{' '}
            {d.returns.over_returned && (
              <span className="badge badge-sm badge-error" style={{ marginLeft: '.4rem' }}>
                Returns more than was sold
              </span>
            )}
          </h2>
          {d.returns.original ? (
            <p>
              Reverses sale{' '}
              <Link href={`/transactions/${d.returns.original.id}`}>
                {d.returns.original.source_record_id ?? 'original sale'}
              </Link>{' '}
              of {money(d.returns.original.amount)} on {d.returns.original.business_date}.
              {d.returns.reason ? ` Reason: ${d.returns.reason}.` : ''}
            </p>
          ) : d.returns.original_reference ? (
            <Alert tone="warning">
              The original sale <b>{d.returns.original_reference}</b> was not found in Nimbus. This
              return cannot be checked against a sale.
            </Alert>
          ) : (
            <Alert tone="warning">
              No original sale was referenced. This is a return without a receipt.
            </Alert>
          )}
          {d.returns.items && d.returns.items.length > 0 && (
            <DataTable
              columns={[
                {
                  key: 'c0',
                  header: 'Item',
                  primary: true,
                  csvValue: (i) => [i.item_code, i.description].filter(Boolean).join(' · '),
                  render: (i) => (
                    <>
                      {i.item_code}
                      <small className="act-sub" style={{ display: 'block' }}>
                        {i.description}
                      </small>
                    </>
                  ),
                },
                {
                  key: 'c1',
                  header: 'Returned now',
                  numeric: true,
                  csvValue: (i) => i.returned_qty,
                  render: (i) => i.returned_qty,
                },
                {
                  key: 'c2',
                  header: 'Sold',
                  numeric: true,
                  csvValue: (i) => i.sold_qty ?? '',
                  render: (i) => i.sold_qty ?? '—',
                },
                {
                  key: 'c3',
                  header: 'Returned before',
                  numeric: true,
                  csvValue: (i) => i.already_returned_qty,
                  render: (i) => i.already_returned_qty,
                },
                {
                  key: 'c4',
                  header: 'Reason',
                  csvValue: (i) =>
                    `${i.reason ?? ''}${i.over_returned ? ` · ${i.sold_qty === null ? 'Not on the original sale' : 'Over-returned'}` : ''}`,
                  render: (i) => (
                    <>
                      {i.reason ?? '—'}
                      {i.over_returned && (
                        <span
                          className="badge badge-sm badge-error"
                          style={{ marginLeft: '.4rem' }}
                        >
                          {i.sold_qty === null ? 'Not on the original sale' : 'Over-returned'}
                        </span>
                      )}
                    </>
                  ),
                },
              ]}
              rows={d.returns.items}
              rowKey={(i) => String(i.item_code)}
              csvFileName={`transaction-${id}-return-comparison-visible.csv`}
              detachTitle={`Transaction ${d.source_record_id ?? id} · Return comparison`}
            />
          )}
          {d.returns.items_truncated && (
            <p className="hint">
              Showing the first 100 item codes. Over-return checks include all items.
            </p>
          )}
        </Card>
      )}
      {d.returns && !d.returns.is_return && d.returns.returns && d.returns.returns.length > 0 && (
        <Card className="case-section">
          <h2>Returns against this sale</h2>
          {(d.returns.returns_truncated ||
            d.returns.returns.some((item) => item.items_truncated)) && (
            <p className="hint">
              {d.returns.returns_truncated && 'Showing up to 200 returns. '}
              {d.returns.returns.some((item) => item.items_truncated) &&
                'Some returns show only the first 100 item lines.'}
            </p>
          )}
          <DataTable
            columns={[
              {
                key: 'c0',
                header: 'Return',
                csvValue: (x) => x.id,
                render: (x) => (
                  <>
                    <Link href={`/transactions/${x.id}`}>Open return</Link>
                  </>
                ),
              },
              {
                key: 'c1',
                header: 'Date',
                csvValue: (x) => x.business_date,
                render: (x) => x.business_date,
              },
              {
                key: 'c2',
                header: 'Items',
                csvValue: (x) => x.items.map((i) => `${i.quantity} × ${i.item_code}`).join(', '),
                render: (x) => (
                  <>{x.items.map((i) => `${i.quantity} × ${i.item_code}`).join(', ')}</>
                ),
              },
              {
                key: 'c3',
                header: 'Reason',
                csvValue: (x) => x.reason ?? '',
                render: (x) => x.reason ?? '—',
              },
              {
                key: 'c4',
                header: 'Amount',
                numeric: true,
                csvValue: (x) => x.amount,
                render: (x) => money(x.amount),
              },
            ]}
            rows={d.returns.returns}
            rowKey={(x) => String(x.id)}
            csvFileName={`transaction-${id}-returns-visible.csv`}
            detachTitle={`Transaction ${d.source_record_id ?? id} · Returns`}
          />
        </Card>
      )}

      <div className="case-panel case-two">
        <Card className="case-section">
          <h2>Details</h2>
          <dl className="kv">
            {fields.map(([k, v]) => (
              <div key={k} style={{ display: 'contents' }}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
          <p className="hint" style={{ marginTop: '.5rem' }}>
            Transaction id <code>{d.id}</code> <CopyButton value={d.id} />
          </p>
        </Card>

        <div className="case-stack">
          <Card className="case-section">
            <div className="section-heading">
              <h2>Audit errors</h2>
              {d.errors.length > 0 && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    downloadCsv(
                      `transaction-${id}-audit-errors-loaded.csv`,
                      [
                        'Code',
                        'Family',
                        'Severity',
                        'Status',
                        'Detection origin',
                        'Confidence',
                        'Closure impact',
                        'Amount',
                        'Case',
                        'Case status',
                      ],
                      d.errors.map((item) => [
                        item.code,
                        item.family,
                        item.severity,
                        item.status,
                        item.detection_origin,
                        item.confidence,
                        item.close_impact,
                        item.amount,
                        item.case_number,
                        item.case_status,
                      ])
                    )
                  }
                >
                  Export loaded errors CSV
                </Button>
              )}
            </div>
            {d.errors_truncated && (
              <p className="hint">Showing the latest 100 transaction-linked audit errors.</p>
            )}
            {d.errors.length > 0 && (
              <p className="hint">
                These errors belong to cases raised from this transaction; a case may include other
                transactions too.
              </p>
            )}
            {d.errors.length === 0 ? (
              <p className="cc-empty">No audit errors were raised from this transaction.</p>
            ) : (
              <ul className="summary-list">
                {d.errors.map((error) => (
                  <li key={error.id}>
                    <Link href={`/audit-errors/${encodeURIComponent(error.code)}`}>
                      <b>{error.code}</b>
                    </Link>{' '}
                    · {error.family} · {error.severity} · {error.status}
                    <small className="act-sub" style={{ display: 'block' }}>
                      {error.detection_origin} · {error.confidence} confidence ·{' '}
                      {error.close_impact} close impact · {money(error.amount, d.currency)} · Case{' '}
                      {error.case_number} ({error.case_status})
                    </small>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card className="case-section">
            <div className="section-heading">
              <h2>Related cases</h2>
              {d.cases.length > 0 && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    downloadCsv(
                      `transaction-${id}-related-cases-loaded.csv`,
                      ['Case', 'Type', 'Status', 'Relationship'],
                      d.cases.map((item) => [
                        item.case_number,
                        item.case_type,
                        item.status,
                        item.link,
                      ])
                    )
                  }
                >
                  Export loaded cases CSV
                </Button>
              )}
            </div>
            {d.cases_truncated && (
              <p className="hint">
                Showing up to 200 matches, with cases raised from this transaction first. Additional
                matches are not shown.
              </p>
            )}
            {d.cases.length === 0 ? (
              <p className="cc-empty">No case has been raised for this store and day.</p>
            ) : (
              <ul className="summary-list">
                {d.cases.map((c) => (
                  <li key={c.id}>
                    <CaseLink id={c.id}>{c.case_number}</CaseLink> · {c.case_type}{' '}
                    <StatusBadge s={c.status} />
                    <small className="act-sub" style={{ display: 'block' }}>
                      {c.link === 'raised from this transaction'
                        ? 'Raised from this transaction'
                        : 'Same store and day. Not necessarily caused by this transaction.'}
                    </small>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card className="case-section">
            <h2>Linked transactions</h2>
            {d.linked_transactions.length === 0 ? (
              <p className="cc-empty">
                No other transaction shares this payment or settlement reference.
              </p>
            ) : (
              <DataTable
                columns={[
                  {
                    key: 'c0',
                    header: 'Source',
                    csvValue: (t) => t.source_system,
                    render: (t) => (
                      <>
                        <Link href={`/transactions/${t.id}`}>{t.source_system}</Link>
                      </>
                    ),
                  },
                  {
                    key: 'c1',
                    header: 'Type',
                    csvValue: (t) => t.transaction_type,
                    render: (t) => t.transaction_type,
                  },
                  {
                    key: 'c2',
                    header: 'Amount',
                    numeric: true,
                    csvValue: (t) => t.amount,
                    render: (t) => money(t.amount),
                  },
                  {
                    key: 'c3',
                    header: 'Status',
                    csvValue: (t) => t.reconciliation_status,
                    render: (t) => (
                      <>
                        <span className={`badge badge-sm ${tone(t.reconciliation_status)}`}>
                          {t.reconciliation_status}
                        </span>
                      </>
                    ),
                  },
                ]}
                rows={d.linked_transactions}
                rowKey={(t) => String(t.id)}
                csvFileName={`transaction-${id}-linked-transactions-visible.csv`}
                detachTitle={`Transaction ${d.source_record_id ?? id} · Linked transactions`}
              />
            )}
          </Card>
          {history && history.changes.length > 0 && (
            <Card className="case-section">
              <h2>Change history</h2>
              <p className="hint">
                The source record is never edited. Each correction is shown with the value before
                and after it; only approved corrections change the value in use.
              </p>
              {history.truncated && <p className="hint">Showing the first 200 corrections.</p>}
              <DataTable
                rows={history.changes}
                rowKey={(c) => c.id}
                columns={[
                  {
                    key: 'f',
                    header: 'Field',
                    primary: true,
                    csvValue: (c) => c.field,
                    render: (c) => c.field,
                  },
                  {
                    key: 'b',
                    header: 'Before',
                    csvValue: (c) => String(shown(c, c.before, history.currency)),
                    render: (c) => shown(c, c.before, history.currency),
                  },
                  {
                    key: 'a',
                    header: 'After',
                    csvValue: (c) =>
                      c.invalid ? 'Invalid value' : String(shown(c, c.after, history.currency)),
                    render: (c) =>
                      c.invalid ? (
                        <Badge variant="error" size="sm">
                          Invalid value
                        </Badge>
                      ) : (
                        shown(c, c.after, history.currency)
                      ),
                  },
                  {
                    key: 's',
                    header: 'Status',
                    csvValue: (c) => `${c.status}${c.applied ? '' : ' · not applied'}`,
                    render: (c) => (
                      <Badge
                        size="sm"
                        variant={
                          c.status === 'Approved'
                            ? 'success'
                            : c.status === 'Rejected'
                              ? 'error'
                              : 'warning'
                        }
                      >
                        {c.status}
                        {c.applied ? '' : ' · not applied'}
                      </Badge>
                    ),
                  },
                  {
                    key: 'w',
                    header: 'Who and why',
                    csvValue: (c) =>
                      `${c.rationale} · Proposed by ${c.proposed_by} · ${when(c.proposed_at)}${c.reviewed_by && c.reviewed_at ? ` · ${c.status.toLowerCase()} by ${c.reviewed_by} · ${when(c.reviewed_at)}` : ''}`,
                    render: (c) => (
                      <>
                        {c.rationale}
                        <small className="act-sub" style={{ display: 'block' }}>
                          Proposed by {c.proposed_by} · {when(c.proposed_at)}
                          {c.reviewed_by && c.reviewed_at
                            ? ` · ${c.status.toLowerCase()} by ${c.reviewed_by} · ${when(c.reviewed_at)}`
                            : ''}
                        </small>
                      </>
                    ),
                  },
                ]}
                csvFileName={`transaction-${id}-correction-history-visible.csv`}
                detachTitle={`Transaction ${d.source_record_id ?? id} · Correction history`}
              />
            </Card>
          )}
          {d.adjustments.length > 0 && (
            <Card className="case-section">
              <h2>Adjustments</h2>
              {d.adjustments_truncated && (
                <p className="hint">Showing the latest 200 adjustments.</p>
              )}
              {d.adjustments.map((a) => (
                <div key={a.id} className="indicator">
                  <div style={{ flex: 1 }}>
                    <b>{a.adjustment_type}</b>
                    <p>
                      {a.rationale} · {a.proposed_by} · {when(a.created_at)}
                    </p>
                  </div>
                  <Badge variant="secondary" size="sm">
                    {a.status}
                  </Badge>
                </div>
              ))}
              <Link href="/corrections">Open Transaction Corrections</Link>
            </Card>
          )}
        </div>
      </div>

      <Card className="case-section">
        <h2>Source record</h2>
        {!src ? (
          <p className="cc-empty">This transaction has no archived source record.</p>
        ) : (
          <>
            <p className="hint" style={{ marginBottom: '.6rem' }}>
              The archived, read-only record this transaction was normalized from.
            </p>
            <dl className="kv">
              {(
                [
                  ['System', src.source_system],
                  ['Source record id', src.source_record_id],
                  ['Version', src.source_version],
                  ['Delivery', src.delivery_id],
                  ['Status', src.status],
                  ['Received', when(src.received_at)],
                  ['Source event time', when(src.source_event_time)],
                  [
                    'Payload hash',
                    <>
                      <code>{src.payload_hash}</code> <CopyButton value={src.payload_hash} />
                    </>,
                  ],
                ] as [string, React.ReactNode][]
              ).map(([k, v]) => (
                <div key={k} style={{ display: 'contents' }}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
            {src.quarantine_reason && (
              <Alert tone="warning" className="mt-sm">
                Quarantined: {src.quarantine_reason}
              </Alert>
            )}
            <div className="sd-section">
              <h3>
                Raw payload{' '}
                {src.payload.text && <CopyButton value={src.payload.text} label="Copy payload" />}
              </h3>
              {src.payload.text ? (
                <pre className="ex-pre" style={{ maxHeight: 360 }}>
                  {src.payload.text}
                  {src.payload.truncated ? '\n… truncated' : ''}
                </pre>
              ) : (
                <p className="cc-empty">The payload is empty.</p>
              )}
            </div>
          </>
        )}
      </Card>
      <TransactionAuditTrail transactionId={id} />
      <TransactionComments transactionId={id} />
    </div>
  );
}
