'use client';

import { codeLabel, humanizeKey, sourceLabel, statusLabel } from '@/lib/humanize';
import { use, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api, money } from '@/lib/api';
import { formatDateTime } from '@/lib/preferences';
import { downloadBlob, downloadCsv } from '@/lib/csv';
import { CopyButton, SkeletonRows } from '@/components/Feedback';
import { StatusBadge } from '@/components/CaseTable';
import { CaseLink, useMe } from '@/components/MeProvider';
import {
  ActionMenu,
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Drawer,
  StructuredValue,
  summarizeValue,
  Tabs,
} from '@/components/ui';
import TransactionComments from '@/components/TransactionComments';
import TransactionAuditTrail from '@/components/TransactionAuditTrail';
import { ItemLines } from '@/components/transactions/ItemLines';
import { posAttributeLine } from '@/components/transactions/posAttributes';

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
  paid_out_details: Record<string, string>;
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
                const label = humanizeKey(key);
                const display =
                  key === 'rate'
                    ? `${(Number(item) * 100).toFixed(2)}%`
                    : ['taxable_amount', 'tax_amount', 'amount'].includes(key)
                      ? money(String(item), currency)
                      : summarizeValue(item);
                return `${label}: ${display}`;
              })
              .join(' · ')
          : summarizeValue(value);

// Change-history fields arrive as backend names, e.g. "MissingTransaction" or "Retailer reference reference_1".
const fieldLabel = (field: string) =>
  /\s/.test(field) ? field.replace(/\breference_(\d+)\b/g, 'ref $1') : humanizeKey(field);
// Mapping versions such as "pos-map-v1" read as "POS mapping v1".
const lineageLabel = (lineage: string | null | undefined) => {
  if (!lineage) return '—';
  const m = lineage.match(/^([a-z]+)-map-v(\d+)$/i);
  return m
    ? `${m[1].length <= 3 ? m[1].toUpperCase() : humanizeKey(m[1])} mapping v${m[2]}`
    : lineage;
};

const parsedPayload = (text: string): unknown => {
  const t = text.trim();
  if (!/^[[{]/.test(t)) return null;
  try {
    return JSON.parse(t);
  } catch {
    return null;
  }
};

export default function TransactionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { me } = useMe();
  const [d, setD] = useState<Detail | null>(null);
  const [additionalCases, setAdditionalCases] = useState<Detail['cases']>([]);
  const [relatedCasesHasMore, setRelatedCasesHasMore] = useState(false);
  const [relatedCasesLoading, setRelatedCasesLoading] = useState(false);
  const [relatedCasesError, setRelatedCasesError] = useState('');
  const relatedCasesRequestId = useRef(0);
  const [additionalErrors, setAdditionalErrors] = useState<Detail['errors']>([]);
  const [transactionErrorsHasMore, setTransactionErrorsHasMore] = useState(false);
  const [transactionErrorsLoading, setTransactionErrorsLoading] = useState(false);
  const [transactionErrorsError, setTransactionErrorsError] = useState('');
  const transactionErrorsRequestId = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<ChangeHistory | null>(null);
  const [voucherEvidence, setVoucherEvidence] = useState<VoucherEvidence | null>(null);
  const [refreshingAudit, setRefreshingAudit] = useState(false);
  const [refreshAuditMessage, setRefreshAuditMessage] = useState('');
  const [auditRefreshVersion, setAuditRefreshVersion] = useState(0);
  const [detailExporting, setDetailExporting] = useState<string | null>(null);
  const [detailExportError, setDetailExportError] = useState<string | null>(null);
  const [tab, setTab] = useState<TxTab>('overview');
  const [payloadOpen, setPayloadOpen] = useState(false);
  // Open the tab named in the URL hash, e.g. #details.
  useEffect(() => {
    setTab(tabFromHash(window.location.hash));
  }, [id]);
  // A link to the history or the related list scrolls to it once the transaction has loaded.
  const loaded = d !== null;
  useEffect(() => {
    if (!loaded) return;
    const target = {
      '#history': 'transaction-history',
      '#transaction-audit-trail': 'transaction-audit-trail',
      '#related': 'transaction-related',
      '#comments': 'transaction-comments',
      '#transaction-comments': 'transaction-comments',
    }[window.location.hash];
    // With no corrections there is no history section, so #history lands on the audit trail.
    if (target)
      (
        document.getElementById(target) ?? document.getElementById('transaction-audit-trail')
      )?.scrollIntoView({ block: 'start' });
  }, [loaded]);
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
    relatedCasesRequestId.current += 1;
    transactionErrorsRequestId.current += 1;
    let active = true;
    setD(null);
    setAdditionalCases([]);
    setRelatedCasesHasMore(false);
    setRelatedCasesLoading(false);
    setRelatedCasesError('');
    setAdditionalErrors([]);
    setTransactionErrorsHasMore(false);
    setTransactionErrorsLoading(false);
    setTransactionErrorsError('');
    api
      .get(`/api/transactions/${id}`)
      .then((r) => {
        if (!active) return;
        setD(r.data);
        setRelatedCasesHasMore(r.data.cases_truncated);
        setTransactionErrorsHasMore(r.data.errors_truncated);
        setError(null);
      })
      .catch(() => {
        if (active) setError('Transaction not found');
      });
    return () => {
      active = false;
      relatedCasesRequestId.current += 1;
      transactionErrorsRequestId.current += 1;
    };
  }, [id]);

  const loadMoreRelatedCases = async () => {
    if (!d || relatedCasesLoading) return;
    const requestId = ++relatedCasesRequestId.current;
    setRelatedCasesLoading(true);
    setRelatedCasesError('');
    try {
      const offset = d.cases.length + additionalCases.length;
      const { data } = await api.get<{
        items: Detail['cases'];
        has_more: boolean;
      }>(`/api/transactions/${encodeURIComponent(id)}/related-cases`, { params: { offset } });
      if (requestId !== relatedCasesRequestId.current) return;
      setAdditionalCases((current) => [...current, ...data.items]);
      setRelatedCasesHasMore(data.has_more);
    } catch {
      if (requestId === relatedCasesRequestId.current) {
        setRelatedCasesError('Could not load more related cases.');
      }
    } finally {
      if (requestId === relatedCasesRequestId.current) setRelatedCasesLoading(false);
    }
  };

  const loadMoreTransactionErrors = async () => {
    if (!d || transactionErrorsLoading) return;
    const requestId = ++transactionErrorsRequestId.current;
    setTransactionErrorsLoading(true);
    setTransactionErrorsError('');
    try {
      const offset = d.errors.length + additionalErrors.length;
      const { data } = await api.get<{
        items: Detail['errors'];
        has_more: boolean;
      }>(`/api/transactions/${encodeURIComponent(id)}/errors`, { params: { offset } });
      if (requestId !== transactionErrorsRequestId.current) return;
      setAdditionalErrors((current) => [...current, ...data.items]);
      setTransactionErrorsHasMore(data.has_more);
    } catch {
      if (requestId === transactionErrorsRequestId.current) {
        setTransactionErrorsError('Could not load more transaction audit errors.');
      }
    } finally {
      if (requestId === transactionErrorsRequestId.current) setTransactionErrorsLoading(false);
    }
  };

  if (error)
    return (
      <div className="empty-state">
        <b>{error}</b>
        <p>It may not exist or you may lack access.</p>
        <Link href="/transactions">Back to transactions</Link>
      </div>
    );
  if (!d) return <SkeletonRows rows={8} label="Loading transaction" />;
  const relatedCases = [...d.cases, ...additionalCases];
  const transactionErrors = [...d.errors, ...additionalErrors];
  const eventCalendarDate = d.event_timestamp.slice(0, 10);
  const assignedToDifferentBusinessDay = eventCalendarDate !== d.business_date;
  const canCorrect = ['finance', 'it'].includes(me?.role ?? '');
  const correctHref = (kind: string, extra: Record<string, string | number> = {}) =>
    `/corrections/new?${new URLSearchParams({
      kind,
      transaction_id: d.id,
      ...Object.fromEntries(Object.entries(extra).map(([k, v]) => [k, String(v)])),
    }).toString()}`;
  const storeDayHref = `/store-days/${encodeURIComponent(d.store_id)}/${encodeURIComponent(d.business_date)}`;
  const PAID_OUT_LABELS: Record<string, string> = {
    vendor_number: 'Vendor',
    vendor_invoice_number: 'Vendor invoice',
    proof_of_delivery_number: 'Proof of delivery',
    payment_reference_number: 'Payment reference',
  };
  type Field = [string, React.ReactNode];
  // The record grouped the way a reviewer reads it; a group whose fields were all left empty is not shown.
  const fieldGroups: { title: string; fields: Field[] }[] = [
    {
      title: 'When',
      fields: [
        [
          'Business date',
          <span className="cluster" key="business-date">
            <Link href={storeDayHref} className="tx-link">
              {d.business_date}
            </Link>
            {assignedToDifferentBusinessDay && (
              <span title={`Event timestamp date: ${eventCalendarDate}`}>
                <Badge size="sm" variant="info">
                  POS-assigned day
                </Badge>
              </span>
            )}
          </span>,
        ],
        ['Event time', when(d.event_timestamp)],
        ['Processed', when(d.processing_timestamp)],
        ['Settlement date', d.settlement_date],
      ],
    },
    {
      title: 'Where and who',
      fields: [
        ['Store', d.store_id],
        ['Register', d.register_id ?? '—'],
        ['Channel', d.channel_id ? humanizeKey(d.channel_id) : '—'],
        [
          'Cashier',
          d.cashier_id || canCorrect ? (
            <span className="tx-value" key="cashier">
              {d.cashier_id ?? '—'}
              {canCorrect && (
                <EditLink href={correctHref('Employee')} label="Correct employee details" />
              )}
            </span>
          ) : (
            '—'
          ),
        ],
        ['Salesperson', d.salesperson_id ?? '—'],
      ],
    },
    {
      title: 'Sale and payment',
      fields: [
        [
          'Type',
          d.transaction_subtype
            ? `${d.transaction_type} · ${d.transaction_subtype}`
            : d.transaction_type,
        ],
        ['Tender', d.tender_type ?? '—'],
        ['Quantity', d.quantity ?? '—'],
        ['Disposition', d.disposition],
      ],
    },
    {
      title: 'References',
      fields: [
        ['Payment reference', d.payment_reference ?? '—'],
        ['Settlement reference', d.settlement_reference ?? '—'],
        [
          'Retailer references',
          Object.entries(d.user_references ?? {})
            .map(([field, value]) => `${field.replace('reference_', 'Ref ')}: ${value}`)
            .join(' · ') || '—',
        ],
        [
          'Paid-out references',
          Object.keys(d.paid_out_details ?? {}).length ? (
            <span className="stack stack-xs" key="paid-out-references">
              {Object.entries(d.paid_out_details ?? {}).map(([field, value]) => (
                <span key={field} className="tx-value">
                  <span>
                    <span className="tx-sublabel">{PAID_OUT_LABELS[field] ?? field}</span> {value}
                  </span>
                  {canCorrect && (
                    <EditLink
                      href={correctHref('PaidOutDetail')}
                      label={`Correct ${(PAID_OUT_LABELS[field] ?? field).toLowerCase()}`}
                    />
                  )}
                </span>
              ))}
            </span>
          ) : (
            '—'
          ),
        ],
      ],
    },
    {
      title: 'Customer',
      fields: [
        [
          'Customer reference (masked)',
          d.customer_id
            ? `${d.customer_id}${d.customer_identification_type ? ` · ${d.customer_identification_type}` : ''}`
            : '—',
        ],
        [
          'Customer attributes',
          Object.keys(d.customer_attributes ?? {}).length ? (
            <span className="stack stack-xs" key="customer-attributes">
              {Object.entries(d.customer_attributes ?? {}).map(([attributeType, value]) => (
                <span key={attributeType} className="tx-value">
                  <span>
                    <span className="tx-sublabel">{attributeType}</span> {value}
                  </span>
                  {canCorrect && (
                    <EditLink
                      href={correctHref('CustomerAttribute', { attribute_type: attributeType })}
                      label={`Correct ${attributeType}`}
                    />
                  )}
                </span>
              ))}
            </span>
          ) : (
            '—'
          ),
        ],
      ],
    },
    {
      title: 'Record',
      fields: [
        ['Source', sourceLabel(d.source_system)],
        ['Lineage', lineageLabel(d.source_lineage)],
        [
          'Transaction id',
          <span className="tx-value" key="id">
            <code>{d.id}</code> <CopyButton value={d.id} />
          </span>,
        ],
      ],
    },
  ];
  const isEmpty = (v: React.ReactNode) => v === '—' || v === '' || v == null;
  const src = d.source;
  const det = d.detail;
  const isReturn = d.transaction_type === 'Return';
  const truncatedSections = [
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
    transactionErrorsRequestId.current += 1;
    setTransactionErrorsLoading(false);
    try {
      const { data } = await api.post('/api/reconcile/refresh-errors', null, {
        params: { business_date: d.business_date, store_id: d.store_id },
      });
      const { data: transaction } = await api.get<Detail>(`/api/transactions/${id}`);
      setD(transaction);
      setAdditionalErrors([]);
      setTransactionErrorsHasMore(transaction.errors_truncated);
      setAuditRefreshVersion((version) => version + 1);
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
  const missingFields = fieldGroups.flatMap((g) => g.fields).filter(([, v]) => isEmpty(v));
  const hasReturnsAgainst =
    !!d.returns && !d.returns.is_return && !!d.returns.returns && d.returns.returns.length > 0;
  const relatedCount =
    relatedCases.length +
    d.linked_transactions.length +
    (hasReturnsAgainst ? d.returns!.returns!.length : 0) +
    d.adjustments.length;
  const correctionItems = canCorrect
    ? [
        {
          label: 'Employee details',
          onSelect: () => router.push(correctHref('Employee')),
        },
        {
          label: 'Add customer attribute',
          onSelect: () => router.push(correctHref('CustomerAttribute')),
        },
        {
          label: 'Add paid-out detail',
          onSelect: () => router.push(correctHref('PaidOutDetail')),
        },
        {
          label: 'Summary tax',
          disabled: det.taxes.length === 0,
          onSelect: () => router.push(correctHref('TaxDetail')),
        },
      ]
    : [];
  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'details', label: 'Details' },
  ];
  const changeTab = (next: string) => {
    setTab(next as TxTab);
    try {
      window.history.replaceState(
        null,
        '',
        next === 'overview' ? window.location.pathname + window.location.search : `#${next}`
      );
    } catch {
      /* ignore */
    }
  };
  const varianceOff = (v: string | null) => Math.abs(num(v ?? 0)) > 0.01;

  return (
    <div className="case-workspace tx-page">
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
          <h1 className="title-with-status">
            <span>
              {money(d.amount)} <small className="act-sub">{d.currency}</small>
            </span>
            <span className={`badge badge-md ${tone(d.reconciliation_status)}`}>
              {statusLabel(String(d.reconciliation_status))}
            </span>
          </h1>
          <p className="tx-facts">
            <span>
              {d.transaction_subtype
                ? `${d.transaction_type} · ${d.transaction_subtype}`
                : d.transaction_type}
            </span>
            <span>
              <Link href={storeDayHref} className="tx-link" title="Open this store day">
                Store {d.store_id} · {d.business_date}
              </Link>
              {d.register_id ? `, register ${d.register_id}` : ''}
            </span>
            {d.cashier_id && <span>Cashier {d.cashier_id}</span>}
            <span>{when(d.event_timestamp)}</span>
            <span>{sourceLabel(d.source_system)}</span>
          </p>
        </div>
        <div className="case-hero-side">
          <div className="cluster">
            {correctionItems.length > 0 && (
              <ActionMenu label="Propose correction" items={correctionItems} />
            )}
            <ActionMenu
              label="Download"
              busy={detailExporting !== null}
              items={[
                {
                  label: 'Items (CSV)',
                  disabled: det.line_count === 0,
                  onSelect: () => void downloadDetailSection('items'),
                },
                {
                  label: 'Discounts (CSV)',
                  disabled: det.discount_count === 0,
                  onSelect: () => void downloadDetailSection('discounts'),
                },
                {
                  label: 'Tax rows (CSV)',
                  disabled: det.tax_count === 0,
                  onSelect: () => void downloadDetailSection('taxes'),
                },
                {
                  label: 'Tenders (CSV)',
                  disabled: det.tender_count === 0,
                  onSelect: () => void downloadDetailSection('tenders'),
                },
                {
                  label: 'Audit errors (CSV)',
                  hint: 'The errors loaded on this page',
                  disabled: transactionErrors.length === 0,
                  onSelect: () =>
                    downloadCsv(
                      `transaction-${id}-audit-errors-loaded.csv`,
                      [
                        'Error',
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
                      transactionErrors.map((item) => [
                        codeLabel(item.code),
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
                    ),
                },
                {
                  label: 'Related cases (CSV)',
                  hint: 'The cases loaded on this page',
                  disabled: relatedCases.length === 0,
                  onSelect: () =>
                    downloadCsv(
                      `transaction-${id}-related-cases-loaded.csv`,
                      ['Case', 'Type', 'Status', 'Relationship'],
                      relatedCases.map((item) => [
                        item.case_number,
                        item.case_type,
                        item.status,
                        item.link,
                      ])
                    ),
                },
              ]}
            />
            {canRefreshAudit && (
              <Button
                size="sm"
                variant="secondary"
                title="Re-runs reconciliation for unmatched transactions in this store and business date. It does not trigger exports."
                disabled={refreshingAudit}
                onClick={refreshAudit}
              >
                {refreshingAudit ? 'Refreshing…' : 'Refresh audit errors'}
              </Button>
            )}
          </div>
          {refreshAuditMessage && <small role="status">{refreshAuditMessage}</small>}
        </div>
      </div>

      {(transactionErrors.length > 0 || transactionErrorsError) && (
        <section className="tx-flagged" aria-labelledby="tx-flagged-h">
          <h2 id="tx-flagged-h">Why this transaction is flagged</h2>
          {transactionErrorsError && <p role="alert">{transactionErrorsError}</p>}
          <ul className="tx-flag-list">
            {transactionErrors.map((error) => (
              <li key={error.id}>
                <div className="tx-flag-main">
                  <Link
                    href={`/audit-errors/${encodeURIComponent(error.code)}`}
                    title={`What ${error.code} means`}
                    className="tx-flag-title"
                  >
                    {codeLabel(error.code)}
                  </Link>
                  <small>
                    {error.close_impact === 'Blocks close'
                      ? 'Blocks the store day from closing'
                      : `${error.close_impact} for closing`}
                    {' · '}
                    {error.confidence} confidence · {error.detection_origin.toLowerCase()} check
                  </small>
                </div>
                <span className="tx-flag-amount">{money(error.amount, d.currency)}</span>
                <Badge
                  size="sm"
                  variant={
                    error.severity === 'High' || error.severity === 'Critical' ? 'error' : 'warning'
                  }
                >
                  {error.severity}
                </Badge>
                <span className="tx-go">
                  <CaseLink id={error.case_id}>
                    {error.case_status === 'Open'
                      ? `Open case ${error.case_number}`
                      : `Case ${error.case_number} (${error.case_status.toLowerCase()})`}
                  </CaseLink>
                </span>
              </li>
            ))}
          </ul>
          <p className="hint">
            Each error belongs to a case; a case can cover other transactions from the same store
            day too.
          </p>
          {transactionErrorsHasMore && (
            <Button
              size="sm"
              variant="secondary"
              disabled={transactionErrorsLoading}
              onClick={() => void loadMoreTransactionErrors()}
            >
              {transactionErrorsLoading ? 'Loading…' : 'Load more errors'}
            </Button>
          )}
        </section>
      )}

      {detailExportError && (
        <Alert tone="error" onDismiss={() => setDetailExportError(null)}>
          {detailExportError}
        </Alert>
      )}

      <div className="tx-layout">
        <div className="tx-main">
          <div className="tx-tabs">
            <Tabs items={tabs} value={tab} onChange={changeTab} />
          </div>

          {/* Overview: what was sold, and whether it adds up. */}
          <div role="tabpanel" aria-label="Overview" hidden={tab !== 'overview'}>
            <Card className="case-section">
              {d.returns?.is_return && (
                <section className="tx-sub tx-sub-first">
                  <h2 className="tx-h">
                    Return
                    {d.returns.over_returned && (
                      <Badge size="sm" variant="error">
                        Returns more than was sold
                      </Badge>
                    )}
                  </h2>
                  {d.returns.original ? (
                    <p>
                      Reverses sale{' '}
                      <Link href={`/transactions/${d.returns.original.id}`} className="tx-link">
                        {d.returns.original.source_record_id ?? 'original sale'}
                      </Link>{' '}
                      of {money(d.returns.original.amount)} on {d.returns.original.business_date}.
                      {d.returns.reason ? ` Reason: ${d.returns.reason}.` : ''}
                    </p>
                  ) : d.returns.original_reference ? (
                    <Alert tone="warning">
                      The original sale <b>{d.returns.original_reference}</b> was not found in
                      Nimbus. This return cannot be checked against a sale.
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
                                  {statusLabel(
                                    String(
                                      i.sold_qty === null
                                        ? 'Not on the original sale'
                                        : 'Over-returned'
                                    )
                                  )}
                                </span>
                              )}
                            </>
                          ),
                        },
                      ]}
                      rows={d.returns.items}
                      rowKey={(i) => String(i.item_code)}
                      csvFileName={`transaction-${id}-return-comparison-visible.csv`}
                    />
                  )}
                  {d.returns.items_truncated && (
                    <p className="hint">
                      Showing the first 100 item codes. Over-return checks include all items.
                    </p>
                  )}
                </section>
              )}

              {det.status !== 'No detail' && (
                <section
                  className={`tx-sub tx-split${d.returns?.is_return ? '' : ' tx-sub-first'}`}
                >
                  <div>
                    <h2 className="tx-h">
                      Does it add up?
                      <span
                        className={`badge badge-sm ${det.status === 'Balanced' ? 'badge-success' : 'badge-warning'}`}
                      >
                        {statusLabel(String(det.status))}
                      </span>
                    </h2>
                    {det.totals && det.lines.length > 0 ? (
                      <dl className="kv tx-totals">
                        <dt>Items</dt>
                        <dd>{money(det.totals.gross)}</dd>
                        <dt>Discounts</dt>
                        <dd>
                          {num(det.totals.discounts)
                            ? `− ${money(Math.abs(num(det.totals.discounts)))}`
                            : '—'}
                        </dd>
                        <dt>Tax</dt>
                        <dd>+ {money(det.totals.tax)}</dd>
                        <dt className="tx-total-row">Calculated total</dt>
                        <dd className="tx-total-row">
                          <b>{money(det.totals.expected_total)}</b>
                        </dd>
                        <dt>Transaction amount</dt>
                        <dd>
                          <b>{money(d.amount)}</b>
                        </dd>
                        <dt>Items vs amount</dt>
                        <dd className={varianceOff(det.total_variance) ? 'sd-neg' : 'tx-ok'}>
                          {varianceOff(det.total_variance)
                            ? money(det.total_variance ?? 0)
                            : 'Matches'}
                        </dd>
                        {det.tenders.length > 0 && (
                          <>
                            <dt>Paid</dt>
                            <dd>{money(det.totals.tendered)}</dd>
                            <dt>Paid vs amount</dt>
                            <dd className={varianceOff(det.tender_variance) ? 'sd-neg' : 'tx-ok'}>
                              {varianceOff(det.tender_variance)
                                ? `${money(Math.abs(num(det.tender_variance ?? 0)))} ${num(det.tender_variance ?? 0) < 0 ? 'short' : 'over'}`
                                : 'Matches'}
                            </dd>
                          </>
                        )}
                      </dl>
                    ) : (
                      <p className="cc-empty">No item lines to add up.</p>
                    )}
                  </div>
                  <div>
                    <h2 className="tx-h">Paid with</h2>
                    {det.tenders.length === 0 ? (
                      <p className="cc-empty">No tenders were sent.</p>
                    ) : (
                      <>
                        <div className="table-scroll">
                          <table className="data-table">
                            <thead>
                              <tr>
                                <th>Tender</th>
                                <th>Reference</th>
                                <th>Authorization</th>
                                <th className="num">Amount</th>
                              </tr>
                            </thead>
                            <tbody>
                              {det.tenders.map((t, i) => (
                                <tr key={i}>
                                  <td className="font-medium">
                                    {t.tender_type}
                                    {posAttributeLine(t.source_attributes) && (
                                      <small className="tx-pos">
                                        {posAttributeLine(t.source_attributes)}
                                      </small>
                                    )}
                                  </td>
                                  <td>{t.reference ?? '—'}</td>
                                  <td>{t.authorization ?? '—'}</td>
                                  <td className="num">{money(t.amount)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        {varianceOff(det.tender_variance) && (
                          <p className="next-err">
                            The tenders add up to {money(det.totals?.tendered ?? 0)},{' '}
                            {money(Math.abs(num(det.tender_variance ?? 0)))}{' '}
                            {num(det.tender_variance ?? 0) < 0 ? 'less' : 'more'} than the
                            transaction amount.
                          </p>
                        )}
                        {det.tenders.some((t) => t.tender_type !== 'Card') && (
                          <p className="hint" style={{ marginTop: '.4rem' }}>
                            Only the card part reaches the payment processor, so reconciliation
                            compares the processor with the card tender.
                          </p>
                        )}
                      </>
                    )}
                  </div>
                </section>
              )}

              <section
                className={`tx-sub${!d.returns?.is_return && det.status === 'No detail' ? ' tx-sub-first' : ''}`}
              >
                <h2 className="tx-h">
                  {isReturn ? 'Items returned' : 'Items'}
                  {det.line_count > 0 && (
                    <span className="tx-count">{det.line_count.toLocaleString()}</span>
                  )}
                </h2>
                {truncatedSections.length > 0 && (
                  <p className="hint">
                    Showing up to 500 rows of {truncatedSections.join(', ')}; financial totals and
                    balance checks use all stored rows. Use Download to export every row, up to
                    100,000.
                  </p>
                )}
                {det.status === 'No detail' ? (
                  <p className="cc-empty">
                    The feed sent only a total for this transaction, with no items, tax, discounts
                    or tenders.
                  </p>
                ) : (
                  <ItemLines
                    transactionId={d.id}
                    lineCount={det.line_count}
                    editLine={
                      canCorrect
                        ? (l) => (
                            <EditLink
                              href={correctHref('TaxLine', { line_no: l.line_no })}
                              label={`Correct tax on line ${l.line_no}`}
                            />
                          )
                        : undefined
                    }
                  />
                )}
              </section>

              {det.status !== 'No detail' && (
                <section className="tx-sub tx-split">
                  <div>
                    <h2 className="tx-h">Discounts, coupons and vouchers</h2>
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
                              {canCorrect && (
                                <th>
                                  <span className="sr-only">Correct</span>
                                </th>
                              )}
                            </tr>
                          </thead>
                          <tbody>
                            {det.discounts.map((x) => (
                              <tr key={x.id}>
                                <td>
                                  <span
                                    className={`badge badge-sm ${dKind[x.kind] ?? 'badge-secondary'}`}
                                  >
                                    {statusLabel(String(x.kind))}
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
                                {canCorrect && (
                                  <td className="tx-edit-cell">
                                    <EditLink
                                      href={correctHref('Discount', { discount_id: x.id })}
                                      label={`Correct discount ${x.code ?? ''}`.trim()}
                                    />
                                  </td>
                                )}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                    {voucherEvidence && voucherEvidence.items.length > 0 && (
                      <div className="tx-voucher">
                        <h3 className="tx-h3">Voucher ledger trace</h3>
                        {voucherEvidence.truncated && (
                          <p className="hint">Showing the first 200 voucher references.</p>
                        )}
                        <ul className="summary-list">
                          {voucherEvidence.items.map((item) => (
                            <li key={item.voucher_number}>
                              <span className="cluster">
                                <b>{item.voucher_number}</b>
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
                                <span className="tx-go">
                                  <Link
                                    href={`/cash-office?voucher=${encodeURIComponent(item.voucher_number)}#vouchers`}
                                  >
                                    Voucher ledger
                                  </Link>
                                </span>
                              </span>
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
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                  <div>
                    <h2 className="tx-h">Tax</h2>
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
                      />
                    )}
                  </div>
                </section>
              )}
            </Card>
          </div>

          {/* Details: every field the feed sent, grouped, and the archived source record. */}
          <div role="tabpanel" aria-label="Details" hidden={tab !== 'details'}>
            <Card className="case-section">
              <div className="tx-groups">
                {fieldGroups.map((group) => {
                  const present = group.fields.filter(([, v]) => !isEmpty(v));
                  if (present.length === 0) return null;
                  return (
                    <section key={group.title} className="tx-group">
                      <h3 className="tx-group-h">{group.title}</h3>
                      <dl className="tx-dl">
                        {present.map(([k, v]) => (
                          <div key={k}>
                            <dt>{k}</dt>
                            <dd>{v}</dd>
                          </div>
                        ))}
                      </dl>
                    </section>
                  );
                })}
              </div>
              {missingFields.length > 0 && (
                <p className="hint tx-missing">
                  Not sent by the feed: {missingFields.map(([k]) => k.toLowerCase()).join(', ')}.
                </p>
              )}
              <section className="tx-sub">
                <h2 className="tx-h">
                  Source record
                  {src?.payload.text && (
                    <Button
                      size="sm"
                      variant="secondary"
                      className="tx-h-action"
                      onClick={() => setPayloadOpen(true)}
                    >
                      View original payload
                    </Button>
                  )}
                </h2>
                {!src ? (
                  <p className="cc-empty">This transaction has no archived source record.</p>
                ) : (
                  <>
                    <p className="hint" style={{ marginBottom: '.6rem' }}>
                      The archived, read-only record this transaction was normalized from.
                    </p>
                    <dl className="tx-dl tx-dl-grid">
                      {(
                        [
                          ['System', sourceLabel(src.source_system)],
                          ['Source record id', src.source_record_id],
                          ['Version', src.source_version],
                          ['Status', src.status],
                          ['Received', when(src.received_at)],
                          ['Source event time', when(src.source_event_time)],
                          ['Delivery', <code key="delivery">{src.delivery_id}</code>],
                          [
                            'Payload hash',
                            <span className="tx-value" key="hash">
                              <code title={src.payload_hash}>{src.payload_hash.slice(0, 16)}…</code>
                              <CopyButton value={src.payload_hash} />
                            </span>,
                          ],
                        ] as [string, React.ReactNode][]
                      ).map(([k, v]) => (
                        <div key={k}>
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
                    {!src.payload.text && <p className="cc-empty">The payload is empty.</p>}
                  </>
                )}
              </section>
            </Card>
          </div>
        </div>

        {/* Related: cases, other transactions, returns and adjustments tied to this one. */}
        <aside className="tx-aside" id="transaction-related" aria-label="Related">
          <Card className="case-section">
            <h2 className="tx-h">
              Related
              {relatedCount > 0 && <span className="tx-count">{relatedCount}</span>}
            </h2>
            <section className="tx-rel">
              <h3 className="tx-group-h">Cases</h3>
              {relatedCases.length === 0 ? (
                <p className="cc-empty">No case for this store and day.</p>
              ) : (
                <ul className="tx-rel-list">
                  {relatedCases.map((c) => (
                    <li key={c.id}>
                      <div className="tx-rel-row">
                        <span className="tx-link-strong">
                          <CaseLink id={c.id}>{c.case_number}</CaseLink>
                        </span>
                        <StatusBadge s={c.status} />
                      </div>
                      <small>
                        {c.case_type} ·{' '}
                        {c.link === 'raised from this transaction'
                          ? 'raised from this transaction'
                          : 'same store and day'}
                      </small>
                    </li>
                  ))}
                </ul>
              )}
              {relatedCasesError && <p role="alert">{relatedCasesError}</p>}
              {relatedCasesHasMore && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={relatedCasesLoading}
                  onClick={() => void loadMoreRelatedCases()}
                >
                  {relatedCasesLoading ? 'Loading…' : 'Load more cases'}
                </Button>
              )}
            </section>
            <section className="tx-rel">
              <h3 className="tx-group-h">Linked transactions</h3>
              {d.linked_transactions.length === 0 ? (
                <p className="cc-empty">None share this payment or settlement reference.</p>
              ) : (
                <ul className="tx-rel-list">
                  {d.linked_transactions.map((t) => (
                    <li key={t.id}>
                      <div className="tx-rel-row">
                        <Link href={`/transactions/${t.id}`} className="tx-link">
                          {sourceLabel(t.source_system)} {t.transaction_type.toLowerCase()}
                        </Link>
                        <span className="tx-rel-amount">{money(t.amount)}</span>
                      </div>
                      <small>
                        <span className={`badge badge-sm ${tone(t.reconciliation_status)}`}>
                          {statusLabel(String(t.reconciliation_status))}
                        </span>{' '}
                        {t.business_date}
                      </small>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            {hasReturnsAgainst && (
              <section className="tx-rel">
                <h3 className="tx-group-h">Returns against this sale</h3>
                {d.returns!.returns_truncated && (
                  <p className="hint">Showing the first 200 returns.</p>
                )}
                <ul className="tx-rel-list">
                  {d.returns!.returns!.map((x) => (
                    <li key={x.id}>
                      <div className="tx-rel-row">
                        <Link href={`/transactions/${x.id}`} className="tx-link">
                          Return on {x.business_date}
                        </Link>
                        <span className="tx-rel-amount">{money(x.amount)}</span>
                      </div>
                      <small>
                        {x.items.map((i) => `${i.quantity} × ${i.item_code}`).join(', ')}
                        {x.items_truncated ? ' and more' : ''}
                        {x.reason ? ` · ${x.reason}` : ''}
                      </small>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {d.adjustments.length > 0 && (
              <section className="tx-rel">
                <h3 className="tx-group-h">
                  Adjustments
                  <span className="tx-go tx-h-action">
                    <Link href="/corrections">All corrections</Link>
                  </span>
                </h3>
                {d.adjustments_truncated && (
                  <p className="hint">Showing the latest 200 adjustments.</p>
                )}
                <ul className="tx-rel-list">
                  {d.adjustments.map((a) => (
                    <li key={a.id}>
                      <div className="tx-rel-row">
                        <b>{humanizeKey(a.adjustment_type)}</b>
                        <Badge variant="secondary" size="sm">
                          {a.status}
                        </Badge>
                      </div>
                      <small>
                        {a.rationale} · {a.proposed_by} · {when(a.created_at)}
                      </small>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </Card>
          <div id="transaction-audit-trail">
            <TransactionAuditTrail transactionId={id} />
          </div>
        </aside>
      </div>

      {/* History, at the bottom: every correction proposed against this transaction. */}
      {history && history.changes.length > 0 && (
        <section className="case-panel" id="transaction-history" aria-label="History">
          <Card className="case-section">
            <h2>Change history</h2>
            <p className="hint">
              The source record is never edited. Each correction is shown with the value before and
              after it; only approved corrections change the value in use.
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
                  csvValue: (c) => fieldLabel(c.field),
                  render: (c) => fieldLabel(c.field),
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
            />
          </Card>
        </section>
      )}

      {/* Comments, at the very bottom, after the history. */}
      <div className="case-panel" id="transaction-comments">
        <TransactionComments transactionId={id} refreshToken={auditRefreshVersion} />
      </div>

      {payloadOpen && src?.payload.text && (
        <Drawer
          title="Original payload as received"
          onClose={() => setPayloadOpen(false)}
          footer={<CopyButton value={src.payload.text} label="Copy payload" />}
        >
          {!src.payload.truncated && parsedPayload(src.payload.text) ? (
            <StructuredValue
              value={parsedPayload(src.payload.text)}
              raw={false}
              className="sd-payload"
            />
          ) : (
            <pre className="ex-pre">
              {src.payload.text}
              {src.payload.truncated ? '\n… truncated' : ''}
            </pre>
          )}
        </Drawer>
      )}
    </div>
  );
}

type TxTab = 'overview' | 'details';
const TX_TABS: TxTab[] = ['overview', 'details'];
// Links to sections that are not tabs (comments, history, related) open the overview, then scroll there.
const tabFromHash = (hash: string): TxTab => {
  const key = hash.replace(/^#/, '');
  return (TX_TABS as string[]).includes(key) ? (key as TxTab) : 'overview';
};

/** A small pencil button that opens the correction form for one value. */
function EditLink({ href, label }: { href: string; label: string }) {
  return (
    <Link href={href} className="tx-edit" aria-label={label} title={label}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M4 20h4L18.5 9.5a2.12 2.12 0 0 0-3-3L5 17v3Z"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
        />
        <path d="m14 8 2.5 2.5" stroke="currentColor" strokeWidth="1.8" />
      </svg>
    </Link>
  );
}
