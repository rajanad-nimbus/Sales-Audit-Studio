'use client';

import { Fragment, use, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { api, money } from '@/lib/api';
import { formatDateTime, formatNumber } from '@/lib/preferences';
import { downloadBlob, downloadCsv } from '@/lib/csv';
import { SkeletonRows, useToast } from '@/components/Feedback';
import { StoreScopeNote } from '@/components/StoreScope';
import { CaseLink, useMe } from '@/components/MeProvider';
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  Field,
  MetricGrid,
  MetricTile,
  Modal,
  PageHeader,
  Pager,
  Segmented,
  Tabs,
  Toolbar,
} from '@/components/ui';
import type { Column } from '@/components/ui';
import {
  apiError,
  dayApi,
  dayStatusVariant,
  formatRollupKey,
  outcomeVariant,
  qualityVariant,
  readinessVariant,
} from '@/lib/storeDays';
import type {
  Closure,
  DayCase,
  EvidenceOverride,
  HistoryItem,
  Readiness,
  Reopen,
  StoreDay,
  Total,
} from '@/lib/storeDays';

type DetailTab =
  | 'totals'
  | 'analysis'
  | 'closure'
  | 'cases'
  | 'errors'
  | 'history'
  | 'io'
  | 'missing';

interface DayIoLog {
  imports: {
    source_system: string;
    delivery_id: string;
    received_at: string;
    store_day_records: number;
    delivery_records: number;
    delivery_processed: number;
    delivery_quarantined: number;
  }[];
  exports: {
    batch_id: string;
    destination: string;
    dataset: string;
    status: string;
    created_at: string;
    sent_at: string | null;
    acked_at: string | null;
    batch_records: number;
    store_day_records: number;
    error: string | null;
    response_ref: string | null;
    retries: number;
  }[];
}
interface SequenceGapRegister {
  register_id: string;
  missing: number[];
  count: number;
  dismissed: { number: number; reason: string; actor: string; decided_at: string }[];
}
interface SequenceGapPagination {
  page: number;
  page_size: number;
  total: number;
  pages: number;
}
interface DayAuditError {
  id: string;
  code: string;
  family: string;
  detection_origin: string;
  detection_rule_id: string;
  detection_rule_name: string | null;
  severity: string;
  confidence: string;
  close_impact: string;
  close_blocker_reason: string | null;
  status: string;
  amount: string;
  exposure: string;
  currency: string | null;
  created_at: string;
  case_id: string;
  case_number: string;
  case_status: string;
}
interface DayAuditErrorPage {
  items: DayAuditError[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

const HISTORY_PAGE = 15;
const totalKey = (t: Total) => `${t.name}|${t.level}|${t.dimension ?? ''}|${t.rollup_key ?? ''}`;
const hasVariance = (t: Total) => t.variance_amount !== null && Number(t.variance_amount) !== 0;
const signed = (n: number, currency: string) => `${n > 0 ? '+' : ''}${money(n, currency)}`;
const needsExplaining = (t: Total) =>
  (t.outcome === 'Needs reason' || t.outcome === 'Needs case') && !t.resolved;

interface TotalGroup {
  key: string;
  title: string;
  hint: string;
  rows: Total[];
  /** Rows carry a declared amount, so they show declared, variance and tolerance. */
  compared: boolean;
  /** What a breakdown's rows are split by (Register, Tender ...), or null for store figures. */
  dimension: string | null;
}

/** Comparisons first, then store figures, then one group per breakdown level. */
function groupTotals(totals: Total[]): TotalGroup[] {
  const compared = totals.filter((t) => t.declared_amount !== null);
  const store = totals.filter((t) => t.declared_amount === null && t.level === 'Store');
  const levels = Array.from(
    new Set(
      totals.filter((t) => t.declared_amount === null && t.level !== 'Store').map((t) => t.level)
    )
  ).sort();
  const groups: TotalGroup[] = [
    {
      key: 'compared',
      title: 'Balancing',
      hint: 'Calculated totals checked against a declared or control figure.',
      rows: compared,
      compared: true,
      dimension: null,
    },
    {
      key: 'store',
      title: 'Store totals',
      hint: 'Calculated for the whole store day.',
      rows: store,
      compared: false,
      dimension: null,
    },
    ...levels.map((level) => ({
      key: `level-${level}`,
      title: `By ${level.toLowerCase()}`,
      hint: `Calculated for each ${level.toLowerCase()}.`,
      rows: totals
        .filter((t) => t.declared_amount === null && t.level === level)
        .sort(
          (a, b) =>
            a.name.localeCompare(b.name) || (a.dimension ?? '').localeCompare(b.dimension ?? '')
        ),
      compared: false,
      dimension: level,
    })),
  ];
  return groups.filter((g) => g.rows.length > 0);
}

export default function StoreDayDetailPage({
  params,
}: {
  params: Promise<{ storeId: string; date: string }>;
}) {
  const { storeId: rawStore, date } = use(params);
  const storeId = decodeURIComponent(rawStore);
  const toast = useToast();
  const { me } = useMe();
  const [day, setDay] = useState<StoreDay | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [totals, setTotals] = useState<Total[]>([]);
  const [prevTotals, setPrevTotals] = useState<{ version: number; rows: Total[] } | null>(null);
  const [versions, setVersions] = useState<number[]>([]);
  const [version, setVersion] = useState<number | null>(null);
  const [blockers, setBlockers] = useState<string[]>([]);
  const [cases, setCases] = useState<DayCase[]>([]);
  const [closures, setClosures] = useState<Closure[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [ioLog, setIoLog] = useState<DayIoLog>({ imports: [], exports: [] });
  const [ioError, setIoError] = useState('');
  const [gapReasons, setGapReasons] = useState<Record<string, string>>({});
  const [gapBusy, setGapBusy] = useState('');
  const gapLoadSequence = useRef(0);
  const [overrides, setOverrides] = useState<EvidenceOverride[]>([]);
  const [rationale, setRationale] = useState('');
  const [reviewRationale, setReviewRationale] = useState('');
  const [evidenceBasis, setEvidenceBasis] = useState('');
  const [signoffRationale, setSignoffRationale] = useState('');
  const [busy, setBusy] = useState(false);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removeReason, setRemoveReason] = useState('');
  const [removeConfirmation, setRemoveConfirmation] = useState('');
  const [tab, setTab] = useState<DetailTab>('totals');
  const [linkCopied, setLinkCopied] = useState(false);
  const [onlyVariance, setOnlyVariance] = useState(false);
  const [historyType, setHistoryType] = useState('');
  const [historyQ, setHistoryQ] = useState('');
  const [historyEventTypes, setHistoryEventTypes] = useState<string[]>([]);
  const [historyPagination, setHistoryPagination] = useState({ total: 0, page: 1, pages: 1 });
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [auditSignoff, setAuditSignoff] = useState<{
    signed_off: boolean;
    signed_version: number | null;
    signed_by: string | null;
    signed_at: string | null;
    signoff_rationale: string | null;
  } | null>(null);
  const [quality, setQuality] = useState<{ state: string; detail: string } | null>(null);
  const [reopens, setReopens] = useState<Reopen[]>([]);
  const [reopenBlockedBy, setReopenBlockedBy] = useState<string[]>([]);
  const [gaps, setGaps] = useState<SequenceGapRegister[]>([]);
  const [gapPagination, setGapPagination] = useState<SequenceGapPagination>({
    page: 1,
    page_size: 100,
    total: 0,
    pages: 1,
  });
  const [dayErrors, setDayErrors] = useState<DayAuditErrorPage | null>(null);
  const [dayErrorsView, setDayErrorsView] = useState<'open' | 'all'>('open');
  const [dayErrorsQuery, setDayErrorsQuery] = useState('');
  const [dayErrorsPage, setDayErrorsPage] = useState(1);
  const [dayErrorsLoading, setDayErrorsLoading] = useState(false);
  const [dayErrorsExporting, setDayErrorsExporting] = useState(false);
  const [dayErrorsError, setDayErrorsError] = useState('');
  const dayErrorsSequence = useRef(0);
  const [explaining, setExplaining] = useState<Total | null>(null);
  const [explainReason, setExplainReason] = useState('');
  const [explainCase, setExplainCase] = useState('');
  const [explainCorrection, setExplainCorrection] = useState('');

  const loadSequenceGaps = useCallback(
    async (page = 1, pageSize = 100) => {
      const sequence = ++gapLoadSequence.current;
      try {
        const { data } = await api.get<{
          registers: SequenceGapRegister[];
          pagination: SequenceGapPagination;
        }>(`${dayApi({ store_id: storeId, business_date: date })}/sequence-gaps`, {
          params: { page, page_size: pageSize },
        });
        if (sequence !== gapLoadSequence.current) return;
        setGaps(data.registers);
        setGapPagination(data.pagination);
      } catch {
        if (sequence !== gapLoadSequence.current) return;
        setGaps([]);
        setGapPagination({ page: 1, page_size: pageSize, total: 0, pages: 1 });
      }
    },
    [storeId, date]
  );

  const loadDayErrors = useCallback(async () => {
    const sequence = ++dayErrorsSequence.current;
    setDayErrorsLoading(true);
    setDayErrorsError('');
    try {
      const { data } = await api.get<DayAuditErrorPage>(
        `${dayApi({ store_id: storeId, business_date: date })}/errors`,
        {
          params: {
            status: dayErrorsView,
            q: dayErrorsQuery.trim() || undefined,
            page: dayErrorsPage,
            page_size: 50,
          },
        }
      );
      if (sequence === dayErrorsSequence.current) setDayErrors(data);
    } catch (e) {
      if (sequence !== dayErrorsSequence.current) return;
      setDayErrors(null);
      setDayErrorsError(apiError(e, 'Could not load the Store Day error list.'));
    } finally {
      if (sequence === dayErrorsSequence.current) setDayErrorsLoading(false);
    }
  }, [storeId, date, dayErrorsView, dayErrorsQuery, dayErrorsPage]);

  useEffect(() => {
    if (tab !== 'errors') return;
    const timer = window.setTimeout(() => void loadDayErrors(), 180);
    return () => {
      window.clearTimeout(timer);
      dayErrorsSequence.current += 1;
    };
  }, [tab, loadDayErrors]);

  const load = useCallback(
    async (v?: number, historyFilters?: { q?: string; eventType?: string }) => {
      const ref = { store_id: storeId, business_date: date };
      try {
        const { data } = await api.get(dayApi(ref), {
          params: {
            ...(v ? { version: v } : {}),
            history_page: 1,
            history_page_size: HISTORY_PAGE,
            history_q: historyFilters?.q ?? undefined,
            history_event_type: historyFilters?.eventType || undefined,
          },
        });
        setDay(data.store_day);
        setTotals(data.totals);
        setVersions(data.versions);
        setVersion(data.selected_version);
        setBlockers(data.close_blockers);
        setCases(data.cases);
        setClosures(data.closure_requests);
        setHistory(data.history);
        setHistoryPagination(
          data.history_pagination ?? { total: data.history.length, page: 1, pages: 1 }
        );
        setHistoryEventTypes(data.history_event_types ?? []);
        api
          .get<DayIoLog>(`${dayApi(ref)}/io-log`)
          .then(({ data: log }) => {
            setIoLog(log);
            setIoError('');
          })
          .catch((e) => {
            setIoError(apiError(e, 'Could not load this Store Day’s import/export log.'));
          });
        setOverrides(data.evidence_overrides ?? []);
        setReadiness(data.readiness ?? null);
        setAuditSignoff(data.readiness?.audit ?? null);
        setQuality(data.data_quality ?? null);
        setReopens(data.reopens ?? []);
        setReopenBlockedBy(data.reopen_blocked_by ?? []);
        setError(null);
        // Keep missing-number review independently paged from the store-day summary.
        void loadSequenceGaps(1, 100);
        // Compare against the revision just before this one, so changes between versions are visible.
        const earlier = Math.max(
          0,
          ...(data.versions as number[]).filter((x) => x < data.selected_version)
        );
        setPrevTotals(null);
        if (earlier > 0) {
          try {
            const prev = await api.get(dayApi(ref), { params: { version: earlier } });
            setPrevTotals({ version: earlier, rows: prev.data.totals });
          } catch {
            /* the comparison is a nicety; the totals themselves loaded */
          }
        }
      } catch (e) {
        setError(apiError(e, 'Could not open this Store Day.'));
      } finally {
        setLoading(false);
      }
    },
    [storeId, date, loadSequenceGaps]
  );
  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const syncTabFromUrl = () => {
      const requestedTab = new URLSearchParams(window.location.search).get('tab');
      if (
        requestedTab &&
        [
          'totals',
          'analysis',
          'closure',
          'cases',
          'errors',
          'history',
          'io',
          'missing',
        ].includes(requestedTab)
      ) {
        setTab(requestedTab as DetailTab);
      }
    };
    syncTabFromUrl();
    window.addEventListener('popstate', syncTabFromUrl);
    return () => window.removeEventListener('popstate', syncTabFromUrl);
  }, []);

  const selectTab = (nextTab: DetailTab) => {
    if (nextTab === tab) return;
    setTab(nextTab);
    setLinkCopied(false);
    const url = new URL(window.location.href);
    url.searchParams.set('tab', nextTab);
    window.history.pushState({}, '', url);
  };

  const copySectionLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setLinkCopied(true);
      window.setTimeout(() => setLinkCopied(false), 1800);
    } catch {
      toast('Could not copy the section link.', 'error');
    }
  };

  const exportLoadedHistory = () => {
    downloadCsv(
      `${storeId}-${date}-audit-history-loaded.csv`,
      ['Recorded at', 'Event', 'Description', 'Actor'],
      history.map((item) => [
        item.created_at,
        item.event_type,
        item.description,
        item.actor,
      ])
    );
  };

  const exportClosureRequests = () => {
    downloadCsv(
      `${storeId}-${date}-closure-requests.csv`,
      ['Requested at', 'Status', 'Requested by', 'Evidence basis', 'Reviewed by', 'Review reason'],
      closures.map((item) => [
        item.created_at,
        item.status,
        item.requested_by,
        item.evidence_basis,
        item.reviewed_by,
        item.review_reason,
      ])
    );
  };

  const exportLoadedSequenceGaps = () => {
    const rows = gaps.flatMap((register) => [
      ...register.missing.map((number) => [
        register.register_id,
        number,
        'Needs review',
        '',
        '',
        '',
      ]),
      ...register.dismissed.map((item) => [
        register.register_id,
        item.number,
        'Not a transaction',
        item.reason,
        item.actor,
        item.decided_at,
      ]),
    ]);
    downloadCsv(
      `${storeId}-${date}-missing-transactions-loaded.csv`,
      ['Register', 'Transaction number', 'Status', 'Reason', 'Decided by', 'Decided at'],
      rows
    );
  };

  const exportDayErrors = async () => {
    setDayErrorsExporting(true);
    setDayErrorsError('');
    try {
      const { data } = await api.get(
        `${dayApi({ store_id: storeId, business_date: date })}/errors`,
        {
          params: {
            status: dayErrorsView,
            q: dayErrorsQuery.trim() || undefined,
            format: 'csv',
          },
          responseType: 'blob',
        }
      );
      downloadBlob(`${storeId}-${date}-audit-errors.csv`, data);
    } catch (e) {
      setDayErrorsError(apiError(e, 'Could not download the matching audit errors.'));
    } finally {
      setDayErrorsExporting(false);
    }
  };

  const act = async (path: string, body?: object, done?: () => void, message?: string) => {
    setBusy(true);
    try {
      const { data } = await api.post(path, body);
      toast(
        message ??
          (data.transactions !== undefined
            ? `Re-totaled ${data.transactions} transaction(s); ${data.totals_created} totals created.`
            : `Store Day is now ${data.status}.`),
        'success'
      );
      for (const warning of (data.warnings as string[] | undefined) ?? []) toast(warning, 'info');
      done?.();
      await load();
    } catch (e) {
      toast(apiError(e, 'Action failed'), 'error');
    } finally {
      setBusy(false);
    }
  };

  const closeRemoveModal = useCallback(() => setRemoveOpen(false), []);
  const removeEmptyDay = async () => {
    if (!removeReason.trim() || removeConfirmation !== `${storeId}/${date}` || busy) return;
    setBusy(true);
    try {
      await api.post(`${dayApi({ store_id: storeId, business_date: date })}/delete`, {
        reason: removeReason.trim(),
        confirmation: removeConfirmation,
      });
      toast('Empty Store Day removed and recorded in the audit trail.', 'success');
      window.location.assign('/store-days');
    } catch (e) {
      toast(apiError(e, 'Could not remove this Store Day.'), 'error');
    } finally {
      setBusy(false);
    }
  };

  const loadMoreHistory = async () => {
    if (!day || historyPagination.page >= historyPagination.pages) return;
    try {
      const { data } = await api.get(dayApi(day), {
        params: {
          version: day.audit_version || undefined,
          history_page: historyPagination.page + 1,
          history_page_size: HISTORY_PAGE,
          history_q: historyQ.trim() || undefined,
          history_event_type: historyType || undefined,
        },
      });
      setHistory((current) => [...current, ...data.history]);
      setHistoryPagination(data.history_pagination);
    } catch (e) {
      toast(apiError(e, 'Could not load more audit history.'), 'error');
    }
  };

  const decideGap = async (registerId: string, number: number, decision: 'dismiss' | 'restore') => {
    const key = `${registerId}:${number}`;
    const reason = gapReasons[key]?.trim();
    if (decision === 'dismiss' && !reason) {
      toast('Enter why this transaction number is not expected.', 'error');
      return;
    }
    setGapBusy(key);
    try {
      await api.post(
        `${dayApi({ store_id: storeId, business_date: date })}/sequence-gaps/${encodeURIComponent(registerId)}/${number}`,
        { decision, reason: reason || 'Restored to missing transaction review' }
      );
      await loadSequenceGaps(gapPagination.page, gapPagination.page_size);
      setGapReasons((current) => ({ ...current, [key]: '' }));
      toast(
        decision === 'dismiss' ? 'Gap marked as not a transaction.' : 'Gap restored to review.',
        'success'
      );
    } catch (e) {
      toast(apiError(e, 'Could not update the missing transaction.'), 'error');
    } finally {
      setGapBusy('');
    }
  };

  const startExplaining = (t: Total) => {
    setExplaining(t);
    setExplainReason('');
    setExplainCase('');
    setExplainCorrection('');
  };
  const unexplained = totals.filter(needsExplaining).length;
  const pending = closures.some((c) => c.status === 'Pending');
  // Closure needs a second person: the requester cannot approve their own request.
  const ownRequest = closures.some(
    (c) => c.status === 'Pending' && !!me?.user && c.requested_by === me.user
  );
  const closed = day?.status === 'Closed';
  const canSignOff = me?.role === 'analyst';
  const signoffReady = readiness?.ingest.state === 'Complete' && readiness.audit.state === 'Clean';
  const hasCurrentSignoff =
    auditSignoff?.signed_off === true && auditSignoff.signed_version === day?.audit_version;
  const viewingOld = !!day && version !== null && version !== day.audit_version;
  const base = day ? dayApi(day) : '';

  // Totals revision view
  const outOfBalance = totals.filter(hasVariance);
  const worst = outOfBalance.reduce<Total | null>(
    (w, t) =>
      !w || Math.abs(Number(t.variance_amount)) > Math.abs(Number(w.variance_amount)) ? t : w,
    null
  );
  const shownTotals = onlyVariance ? outOfBalance : totals;
  const prevAmounts = useMemo(
    () => new Map((prevTotals?.rows ?? []).map((t) => [totalKey(t), Number(t.calculated_amount)])),
    [prevTotals]
  );

  // Totals grouped by what they are: comparisons first, then store figures, then each breakdown.
  const nameColumn: Column<Total> = {
    key: 'total',
    header: 'Total',
    primary: true,
    csvValue: (t) =>
      [
        t.name,
        t.level !== 'Store' ? `${t.level}${t.dimension ? ` · ${t.dimension}` : ''}` : '',
        t.rollup_key ? formatRollupKey(t.rollup_key) : '',
      ]
        .filter(Boolean)
        .join(' · '),
    render: (t) => (
      <>
        {t.name}
        {t.level !== 'Store' && (
          <small className="act-sub" style={{ display: 'block' }}>
            {t.level}
            {t.dimension ? ` · ${t.dimension}` : ''}
          </small>
        )}
        {t.rollup_key && (
          <small className="act-sub" style={{ display: 'block' }}>
            {formatRollupKey(t.rollup_key)}
          </small>
        )}
      </>
    ),
  };
  const calculatedColumn: Column<Total> = {
    key: 'calc',
    header: 'Calculated',
    numeric: true,
    csvValue: (t) => t.calculated_amount,
    render: (t) => money(t.calculated_amount, t.currency),
  };
  const changeColumns: Column<Total>[] = prevTotals
    ? [
        {
          key: 'change',
          header: `vs v${prevTotals.version}`,
          numeric: true,
          csvValue: (t) => {
            const before = prevAmounts.get(totalKey(t));
            if (before === undefined) return 'New';
            const delta = Number(t.calculated_amount) - before;
            return delta === 0 ? '' : signed(delta, t.currency);
          },
          render: (t) => {
            const before = prevAmounts.get(totalKey(t));
            if (before === undefined)
              return (
                <Badge variant="info" size="sm">
                  New
                </Badge>
              );
            const delta = Number(t.calculated_amount) - before;
            return delta === 0 ? '—' : signed(delta, t.currency);
          },
        },
      ]
    : [];
  const qualityColumn: Column<Total> = {
    key: 'quality',
    header: 'Data',
    csvValue: (t) => t.data_quality ?? '',
    render: (t) =>
      t.data_quality ? (
        <Badge variant={qualityVariant(t.data_quality)} size="sm">
          {t.data_quality}
        </Badge>
      ) : (
        '—'
      ),
  };
  const comparedColumns: Column<Total>[] = [
    nameColumn,
    calculatedColumn,
    ...changeColumns,
    {
      key: 'declared',
      header: 'Declared',
      numeric: true,
      csvValue: (t) => t.declared_amount,
      render: (t) => (t.declared_amount === null ? '—' : money(t.declared_amount, t.currency)),
    },
    {
      key: 'variance',
      header: 'Variance',
      numeric: true,
      csvValue: (t) => t.variance_amount,
      render: (t) =>
        t.variance_amount === null ? (
          '—'
        ) : hasVariance(t) ? (
          <span className="sd-neg">{money(t.variance_amount, t.currency)}</span>
        ) : (
          <Badge variant="success" size="sm">
            Balanced
          </Badge>
        ),
    },
    {
      key: 'drillback',
      header: 'Review',
      stopRowClick: true,
      csvSkip: true,
      render: (t) =>
        t.name === 'POS to GL Variance' && ['finance', 'it', 'admin'].includes(me?.role ?? '') ? (
          <Link
            href={`/ledger?store_id=${encodeURIComponent(storeId)}&business_date=${encodeURIComponent(date)}`}
            title={viewingOld ? 'Journal preview uses current transaction data' : undefined}
          >
            {viewingOld ? 'Current journal' : 'View journal'}
          </Link>
        ) : (
          '—'
        ),
    },
    {
      key: 'outcome',
      header: 'Tolerance',
      csvValue: (t) => {
        if (!t.outcome || t.outcome === 'Balanced') return '';
        const state = t.resolved && t.explanation ? 'Explained' : t.outcome;
        return t.explanation ? `${state} · ${t.explanation.reason}` : state;
      },
      render: (t) =>
        !t.outcome || t.outcome === 'Balanced' ? (
          '—'
        ) : (
          <>
            <span
              title={
                t.tolerance
                  ? `Within ${money(t.tolerance.within_amount, t.currency)}; case above ${money(t.tolerance.case_amount, t.currency)}${t.tolerance.rule_id ? '' : ' (no rule set)'}`
                  : undefined
              }
            >
              <Badge variant={outcomeVariant(t.outcome, t.resolved)} size="sm">
                {t.resolved && t.explanation ? 'Explained' : t.outcome}
              </Badge>
            </span>
            {t.explanation && (
              <small className="act-sub" style={{ display: 'block' }}>
                {t.explanation.reason}
                {t.explanation.case_id &&
                  ` · case ${cases.find((c) => c.id === t.explanation?.case_id)?.case_number ?? t.explanation.case_id}`}
                {t.explanation.correction_id &&
                  ` · correction ${t.explanation.correction_id.slice(0, 8)}`}
                {' · '}
                {t.explanation.explained_by}
              </small>
            )}
            {needsExplaining(t) && !viewingOld && !closed && (
              <Button size="sm" onClick={() => startExplaining(t)}>
                Explain…
              </Button>
            )}
          </>
        ),
    },
    qualityColumn,
  ];
  // A breakdown names each row by its register, tender and so on; a store figure by the total.
  const plainColumns = (dimension: string | null): Column<Total>[] => [
    dimension
      ? {
          key: 'total',
          header: dimension,
          primary: true,
          csvValue: (t) =>
            [t.dimension, t.rollup_key ? formatRollupKey(t.rollup_key) : '', t.name]
              .filter(Boolean)
              .join(' · '),
          render: (t) => (
            <>
              {t.dimension ?? '—'}
              {t.rollup_key && (
                <small className="act-sub" style={{ display: 'block' }}>
                  {formatRollupKey(t.rollup_key)}
                </small>
              )}
              <small className="act-sub" style={{ display: 'block' }}>
                {t.name}
              </small>
            </>
          ),
        }
      : nameColumn,
    calculatedColumn,
    ...changeColumns,
    qualityColumn,
  ];
  const groups = groupTotals(totals);
  const shownGroups = onlyVariance
    ? groups
        .map((g) => ({ ...g, rows: g.rows.filter(hasVariance) }))
        .filter((g) => g.rows.length > 0)
    : groups;

  // History pages are filtered and ordered by the audit sequence on the server.
  const historyByDay = history.reduce<[string, HistoryItem[]][]>((groups, h) => {
    const label = formatDateTime(h.created_at, {
      weekday: 'short',
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
    const last = groups[groups.length - 1];
    if (last && last[0] === label) last[1].push(h);
    else groups.push([label, [h]]);
    return groups;
  }, []);

  const steps = day
    ? [
        {
          label: 'Totals',
          note: day.totals_stale
            ? 'Out of date'
            : hasCurrentSignoff
              ? `Signed off · v${auditSignoff?.signed_version}`
              : readiness?.audit.state === 'Clean'
                ? `Awaiting auditor sign-off · v${day.audit_version}`
                : `Version ${day.audit_version}`,
          state: day.totals_stale
            ? 'warn'
            : hasCurrentSignoff
              ? 'done'
              : totals.length > 0 || closed
                ? 'active'
                : 'todo',
        },
        {
          label: 'Closure request',
          note: closed ? 'Approved' : pending ? 'Awaiting approval' : 'Not requested',
          state: closed ? 'done' : pending ? 'active' : 'todo',
        },
        { label: 'Closed', note: closed ? 'Locked' : 'Open', state: closed ? 'done' : 'todo' },
      ]
    : [];

  // Everything that stands between this day and closure, in one list, worst first.
  const attention: { tone: 'error' | 'warning'; content: React.ReactNode; key: string }[] = [];
  if (day) {
    if (blockers.length > 0)
      attention.push({
        key: 'blockers',
        tone: 'error',
        content: (
          <div className="stack stack-sm">
            <b>Closure blockers ({blockers.length})</b>
            <ul className="summary-list">
              {blockers.map((reason, index) => {
                const lower = reason.toLowerCase();
                const action = lower.includes('case')
                  ? { text: 'Review cases', tab: 'cases' as DetailTab }
                  : lower.includes('transaction number gap')
                    ? { text: 'Review missing transactions', tab: 'missing' as DetailTab }
                    : lower.includes('variance') ||
                        lower.includes('reconciliation totals') ||
                        lower.includes('stale')
                      ? { text: 'Review totals', tab: 'totals' as DetailTab }
                      : lower.includes('sign-off')
                        ? { text: 'Review audit sign-off', tab: 'closure' as DetailTab }
                        : { text: 'Review closure', tab: 'closure' as DetailTab };
                return (
                  <li key={`${reason}-${index}`}>
                    {reason}{' '}
                    <button
                      type="button"
                      className="link-button"
                      aria-label={`${action.text}: ${reason}`}
                      onClick={() => selectTab(action.tab)}
                    >
                      {action.text}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ),
      });
    if (day.totals_stale)
      attention.push({
        key: 'stale',
        tone: 'warning',
        content: (
          <>
            New data arrived after these totals were calculated.{' '}
            {closed
              ? 'Reopen the store day, then re-total it.'
              : 'Re-total the store day before closure.'}
          </>
        ),
      });
    if (unexplained > 0 && !closed)
      attention.push({
        key: 'variances',
        tone: 'warning',
        content: (
          <>
            {unexplained} variance{unexplained > 1 ? 's need' : ' needs'} an explanation.{' '}
            <button type="button" className="link-button" onClick={() => selectTab('totals')}>
              Review totals
            </button>
          </>
        ),
      });
    const activeGapCount = gaps.reduce((count, register) => count + register.count, 0);
    if (activeGapCount > 0)
      attention.push({
        key: 'gaps',
        tone: 'warning',
        content: (
          <div className="stack stack-sm">
            <b>
              {activeGapCount} transaction number{activeGapCount === 1 ? ' is' : 's are'} missing
            </b>
            {gaps.map((g) => (
              <span key={g.register_id}>
                {g.register_id}: {g.missing.slice(0, 12).join(', ')}
                {g.count > 12 ? ` and ${g.count - 12} more` : ''}{' '}
                <button type="button" className="link-button" onClick={() => selectTab('missing')}>
                  Review gaps
                </button>
              </span>
            ))}
          </div>
        ),
      });
    if (overrides.length > 0)
      attention.push({
        key: 'overrides',
        tone: 'warning',
        content: (
          <div className="stack stack-sm">
            <b>
              {overrides.length} evidence gap{overrides.length > 1 ? 's' : ''}{' '}
              {overrides.some((o) => o.status === 'Override Pending')
                ? 'overridden or awaiting approval'
                : 'overridden'}{' '}
              on this day
            </b>
            {overrides.map((o) => (
              <span key={`${o.case_id}-${o.requirement}`}>
                <CaseLink id={o.case_id}>{o.case_number}</CaseLink> · {o.requirement} ·{' '}
                {o.status === 'Override Pending'
                  ? `awaiting a second approver (requested by ${o.requested_by})`
                  : `approved by ${o.approved_by}`}{' '}
                · impact {money(o.financial_impact)}
                {o.reason ? ` · ${o.reason}` : ''}
              </span>
            ))}
          </div>
        ),
      });
  }
  const openCases = cases.filter((c) => !/closed|resolved/i.test(c.status)).length;

  return (
    <div className="page-container">
      <Link href="/store-days" className="back-link">
        ← All Store Days
      </Link>
      <PageHeader
        title={`${storeId} · ${date}`}
        subtitle={day ? `Totals version ${day.audit_version}` : undefined}
        actions={
          day && (
            <>
              <Button variant="secondary" size="sm" onClick={copySectionLink}>
                {linkCopied ? 'Link copied' : 'Copy section link'}
              </Button>
              <Badge variant={dayStatusVariant(day.status)}>{day.status}</Badge>
              {me?.role === 'it' && !closed && day.audit_version === 0 && (
                <Button variant="danger" disabled={busy} onClick={() => setRemoveOpen(true)}>
                  Delete empty day…
                </Button>
              )}
              <Button disabled={busy || closed} onClick={() => act(`${base}/retotal`)}>
                Re-total
              </Button>
              {closed ? (
                <Button onClick={() => selectTab('closure')}>Reopen…</Button>
              ) : pending ? (
                <Button
                  variant="primary"
                  disabled={busy || blockers.length > 0 || ownRequest}
                  title={
                    ownRequest
                      ? 'You requested this closure; another user must approve it'
                      : blockers.length
                        ? 'Resolve the blockers first'
                        : ''
                  }
                  onClick={() => act(`${base}/close`)}
                >
                  Approve &amp; close
                </Button>
              ) : (
                <Button variant="primary" onClick={() => selectTab('closure')}>
                  {canSignOff && signoffReady && !hasCurrentSignoff
                    ? 'Sign audit…'
                    : 'Request closure…'}
                </Button>
              )}
            </>
          )
        }
      />
      <StoreScopeNote />
      {day && !closed && canSignOff && signoffReady && !hasCurrentSignoff && (
        <Card title={`Auditor sign-off · totals version ${day.audit_version}`}>
          <p className="hint">
            Confirm the current clean totals and record the evidence supporting your review. Any
            re-total requires a new sign-off.
          </p>
          <div className="form-row">
            <div className="grow">
              <Field label="Audit sign-off rationale">
                <input
                  value={signoffRationale}
                  onChange={(e) => setSignoffRationale(e.target.value)}
                  placeholder="Summarize the checks and evidence reviewed"
                />
              </Field>
            </div>
            <Button
              variant="primary"
              disabled={busy || signoffRationale.trim().length < 10}
              onClick={() =>
                act(
                  `${base}/audit-signoff`,
                  { rationale: signoffRationale },
                  () => setSignoffRationale(''),
                  'Auditor sign-off recorded for the current totals version.'
                )
              }
            >
              Sign off audit
            </Button>
          </div>
        </Card>
      )}
      {error && (
        <Alert
          tone="error"
          action={
            <Button size="sm" className="alert-close" onClick={() => load()}>
              Retry
            </Button>
          }
        >
          {error}
        </Alert>
      )}
      {loading && <SkeletonRows rows={6} />}

      {day && (
        <>
          <Card title="Status">
            <div className="sd-status">
              <ol className="sd-steps" aria-label="Store Day progress">
                {steps.map((s, i) => (
                  <li
                    key={s.label}
                    className={`sd-step ${s.state}`}
                    aria-current={s.state === 'active' ? 'step' : undefined}
                  >
                    <span className="sd-step-n">{s.state === 'done' ? '✓' : i + 1}</span>
                    <span>
                      <b>{s.label}</b>
                      <small>{s.note}</small>
                    </span>
                  </li>
                ))}
              </ol>
              {readiness && (
                <dl className="sd-ready" aria-label="Store Day readiness">
                  {(
                    [
                      [
                        'Sales audit',
                        readiness.audit.overall_status ??
                          (readiness.audit.state === 'Clean' &&
                          readiness.ingest.state === 'Complete' &&
                          readiness.audit.signed_off
                            ? 'Sales Audit Complete'
                            : 'Sales Audit In Progress'),
                        'Overall store-day audit lifecycle',
                      ],
                      ['Data', readiness.ingest.state, readiness.ingest.detail],
                      [
                        'Audit',
                        readiness.audit.resa_status ?? readiness.audit.state,
                        readiness.audit.detail,
                      ],
                      [
                        'Export',
                        readiness.export.state,
                        readiness.export.records
                          ? `${readiness.export.records} record(s), ${readiness.export.acknowledged} acknowledged`
                          : 'Nothing exported for this day',
                      ],
                    ] as const
                  ).map(([label, state, note]) => {
                    const action =
                      label === 'Data'
                        ? { text: 'View import log', run: () => selectTab('io') }
                        : label === 'Audit'
                          ? (readiness.audit.missing_transactions ?? 0) > 0
                            ? {
                                text: 'Review missing transactions',
                                run: () => selectTab('missing'),
                              }
                            : readiness.audit.open_cases > 0
                              ? { text: 'Review cases', run: () => selectTab('cases') }
                              : readiness.audit.unexplained_variances > 0
                                ? { text: 'Review totals', run: () => selectTab('totals') }
                                : !readiness.audit.signed_off
                                  ? { text: 'Review sign-off', run: () => selectTab('closure') }
                                  : null
                          : label === 'Export'
                            ? { text: 'View delivery log', run: () => selectTab('io') }
                            : null;
                    return (
                      <div key={label} className="sd-ready-item">
                        <dt>{label}</dt>
                        <dd>
                          <Badge variant={readinessVariant(state)} size="sm">
                            {state}
                          </Badge>
                          <small>{note}</small>
                          {action && (
                            <button type="button" className="link-button" onClick={action.run}>
                              {action.text}
                            </button>
                          )}
                        </dd>
                      </div>
                    );
                  })}
                  <div className="sd-ready-item">
                    <dt>Cases</dt>
                    <dd>
                      <button
                        type="button"
                        className="link-button"
                        onClick={() => selectTab('cases')}
                      >
                        {openCases} open of {cases.length}
                      </button>
                      <small>Raised for this store day</small>
                    </dd>
                  </div>
                </dl>
              )}
              <p className="hint">
                {closed
                  ? 'This day is closed and locked.'
                  : ownRequest
                    ? 'You requested closure; another user must approve it.'
                    : pending
                      ? 'A closure request is waiting for approval.'
                      : 'Re-total after new data arrives, then request closure.'}
              </p>
            </div>
          </Card>

          {attention.length > 0 && (
            <Card title={`Needs attention (${attention.length})`}>
              <ul className="sd-attn" aria-label="Needs attention">
                {attention.map((a) => (
                  <li key={a.key} className={`sd-attn-${a.tone}`}>
                    <span className="sd-attn-mark" aria-hidden>
                      {a.tone === 'error' ? '✕' : '!'}
                    </span>
                    <div>{a.content}</div>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <Card>
            <Tabs
              value={tab}
              onChange={(id) => selectTab(id as DetailTab)}
              items={[
                { id: 'totals', label: 'Totals', count: totals.length },
                { id: 'analysis', label: 'Analysis' },
                { id: 'closure', label: 'Closure', count: closures.length },
                { id: 'cases', label: 'Cases', count: cases.length },
                { id: 'errors', label: 'Error List', count: dayErrors?.total },
                { id: 'history', label: 'History', count: history.length },
                {
                  id: 'io',
                  label: 'Import / Export',
                  count: ioLog.imports.length + ioLog.exports.length,
                },
                {
                  id: 'missing',
                  label: 'Missing transactions',
                  count: gaps.reduce((count, register) => count + register.count, 0),
                },
              ]}
            />

            {tab === 'analysis' && (
              <div className="sd-pane">
                <p className="hint">
                  Review this store day across the summaries used in the sales audit workflow. Store
                  and business date are carried into each view.
                </p>
                <div className="sd-analysis-links">
                  <Link
                    href={`/register-summary?store_id=${encodeURIComponent(storeId)}&business_date=${encodeURIComponent(date)}&source=store-day`}
                  >
                    <strong>Cashier / Register Summary</strong>
                    <span>Transactions, register events, and cash over/short</span>
                  </Link>
                  <Link
                    href={`/item-summary?store_id=${encodeURIComponent(storeId)}&date_from=${encodeURIComponent(date)}&date_to=${encodeURIComponent(date)}`}
                  >
                    <strong>Item Summary</strong>
                    <span>Sold and returned quantities, net sales, and tax</span>
                  </Link>
                  <Link
                    href={`/tender-summary?store_id=${encodeURIComponent(storeId)}&date_from=${encodeURIComponent(date)}&date_to=${encodeURIComponent(date)}`}
                  >
                    <strong>Tender Summary</strong>
                    <span>Payment and refund activity by tender type</span>
                  </Link>
                  <Link
                    href={`/misc-totals?store_id=${encodeURIComponent(storeId)}&date_from=${encodeURIComponent(date)}&date_to=${encodeURIComponent(date)}`}
                  >
                    <strong>Miscellaneous Totals</strong>
                    <span>Review configured store, register, and cashier totals</span>
                  </Link>
                  <Link
                    href={`/ledger?store_id=${encodeURIComponent(storeId)}&business_date=${encodeURIComponent(date)}`}
                  >
                    <strong>GL Journal Preview</strong>
                    <span>Inspect financial postings for this store day</span>
                  </Link>
                  <Link
                    href={`/ach?record_type=Store&store_id=${encodeURIComponent(storeId)}&business_date=${encodeURIComponent(date)}`}
                  >
                    <strong>Store ACH</strong>
                    <span>Review store deposit adjustments for this business day</span>
                  </Link>
                  <Link href={`/ach?record_type=Bank&business_date=${encodeURIComponent(date)}`}>
                    <strong>Bank ACH</strong>
                    <span>Review bank transfer adjustments for this business day</span>
                  </Link>
                </div>
              </div>
            )}

            {tab === 'totals' && (
              <div className="sd-pane">
                <div className="sd-versions">
                  <span className="hint">Totals version</span>
                  <Segmented
                    value={String(version ?? '')}
                    onChange={(v) => load(Number(v))}
                    options={versions.map((v) => ({
                      value: String(v),
                      label: `v${v}${v === day.audit_version ? ' · current' : ''}`,
                    }))}
                  />
                </div>
                {viewingOld && (
                  <Alert
                    tone="info"
                    action={
                      <Button size="sm" onClick={() => load()}>
                        Show current
                      </Button>
                    }
                  >
                    You are looking at version {version}, an earlier calculation. The current
                    version is {day.audit_version}.
                  </Alert>
                )}
                {quality && quality.state !== 'Final' && quality.state !== 'Superseded' && (
                  <Alert tone={quality.state === 'Partial' ? 'error' : 'warning'}>
                    <b>These totals are {quality.state.toLowerCase()}.</b> {quality.detail}.
                  </Alert>
                )}
                <MetricGrid>
                  <MetricTile label="Totals" value={totals.length} />
                  <MetricTile label="Need an explanation" value={unexplained} />
                  <MetricTile
                    label="Out of balance"
                    value={totals.length === 0 ? '-' : outOfBalance.length}
                  />
                  <MetricTile
                    label="Largest variance"
                    value={worst ? money(worst.variance_amount ?? 0, worst.currency) : 'None'}
                  />
                </MetricGrid>
                <Toolbar
                  count={
                    prevTotals
                      ? `Changes compared with v${prevTotals.version}`
                      : versions.length > 1
                        ? undefined
                        : 'First calculation'
                  }
                >
                  <Segmented
                    value={onlyVariance ? 'variance' : 'all'}
                    onChange={(v) => setOnlyVariance(v === 'variance')}
                    options={[
                      { value: 'all', label: `All ${totals.length}` },
                      { value: 'variance', label: `Out of balance ${outOfBalance.length}` },
                    ]}
                  />
                </Toolbar>
                {totals.length === 0 ? (
                  <EmptyState title="No totals for this version">
                    <p className="hint">Re-total the store day to calculate them.</p>
                  </EmptyState>
                ) : shownGroups.length === 0 ? (
                  <EmptyState title="Everything balances in this version" />
                ) : (
                  shownGroups.map((g) => (
                    <section key={g.key} className="sd-group" aria-label={g.title}>
                      <h3>
                        {g.title} <span className="hint">{g.rows.length}</span>
                      </h3>
                      <p className="hint">{g.hint}</p>
                      <DataTable
                        columns={g.compared ? comparedColumns : plainColumns(g.dimension)}
                        rows={g.rows}
                        rowKey={(t) => t.id}
                        rowClassName={(t) => (hasVariance(t) ? 'sd-row-bad' : '')}
                        csvFileName={`${storeId}-${date}-totals-${g.key}-v${version ?? day.audit_version}.csv`}
                        detachTitle={`${storeId} ${date} · ${g.title}`}
                      />
                    </section>
                  ))
                )}
                {explaining && (
                  <div className="sd-section">
                    <h3>
                      Explain the {money(explaining.variance_amount ?? 0, explaining.currency)}{' '}
                      variance on {explaining.name}
                    </h3>
                    <p className="hint">
                      {explaining.outcome === 'Needs case'
                        ? 'This variance is above the case threshold. Give the reason and link the case or correction that resolves it.'
                        : 'This variance is outside tolerance. Give the reason it is acceptable.'}{' '}
                      The explanation stays with the variance while its amount is unchanged.
                    </p>
                    <div className="form-row">
                      <div className="grow">
                        <Field label="Reason">
                          <input
                            value={explainReason}
                            onChange={(e) => setExplainReason(e.target.value)}
                            placeholder="For example: deposit posted the next day"
                          />
                        </Field>
                      </div>
                      <Field label="Case">
                        <select
                          value={explainCase}
                          onChange={(e) => setExplainCase(e.target.value)}
                        >
                          <option value="">None</option>
                          {cases.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.case_number} · {c.case_type}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Correction ID">
                        <input
                          value={explainCorrection}
                          onChange={(e) => setExplainCorrection(e.target.value)}
                          placeholder="Optional"
                        />
                      </Field>
                    </div>
                    <div className="cluster">
                      <Button
                        variant="primary"
                        disabled={
                          busy ||
                          !explainReason.trim() ||
                          (explaining.outcome === 'Needs case' &&
                            !explainCase &&
                            !explainCorrection.trim())
                        }
                        onClick={() =>
                          act(
                            `${base}/variance-explanations`,
                            {
                              total_id: explaining.id,
                              reason: explainReason,
                              case_id: explainCase || null,
                              correction_id: explainCorrection.trim() || null,
                            },
                            () => setExplaining(null),
                            'Variance explained.'
                          )
                        }
                      >
                        Save explanation
                      </Button>
                      <Button onClick={() => setExplaining(null)}>Cancel</Button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {tab === 'closure' && (
              <div className="sd-pane">
                {closed ? (
                  <div className="sd-section">
                    <h3>Reopen this Store Day</h3>
                    <p className="hint">
                      Reopening unlocks the day so it can be re-totaled. A reason is required and is
                      written to the reopen history.
                    </p>
                    {reopenBlockedBy.length > 0 && (
                      <Alert tone="warning">
                        <b>This Store Day cannot be reopened.</b> {reopenBlockedBy.join(' · ')}.
                      </Alert>
                    )}
                    <div className="form-row">
                      <div className="grow">
                        <Field label="Reopen rationale">
                          <input
                            value={rationale}
                            onChange={(e) => setRationale(e.target.value)}
                            placeholder="Why does it need to reopen?"
                          />
                        </Field>
                      </div>
                      <Button
                        disabled={busy || !rationale.trim() || reopenBlockedBy.length > 0}
                        onClick={() => act(`${base}/reopen`, { rationale }, () => setRationale(''))}
                      >
                        Reopen
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="sd-section">
                    {hasCurrentSignoff ? (
                      <Alert tone="success">
                        <b>Totals version {auditSignoff?.signed_version} signed off</b> by{' '}
                        {auditSignoff?.signed_by} on{' '}
                        {auditSignoff?.signed_at ? formatDateTime(auditSignoff.signed_at) : '—'}
                        {auditSignoff?.signoff_rationale && (
                          <div className="hint">{auditSignoff.signoff_rationale}</div>
                        )}
                      </Alert>
                    ) : null}
                    <h3>Request closure</h3>
                    <p className="hint">
                      State the evidence that the totals are right. An auditor signs off first, then
                      a second Finance user approves the closure.
                    </p>
                    <div className="form-row">
                      <div className="grow">
                        <Field label="Evidence basis">
                          <input
                            value={evidenceBasis}
                            onChange={(e) => setEvidenceBasis(e.target.value)}
                            placeholder="For example: variances explained in the linked cases"
                          />
                        </Field>
                      </div>
                      <Button
                        variant="primary"
                        disabled={
                          busy || blockers.length > 0 || !hasCurrentSignoff || !evidenceBasis.trim()
                        }
                        onClick={() =>
                          act(`${base}/closure-requests`, { evidence_basis: evidenceBasis }, () =>
                            setEvidenceBasis('')
                          )
                        }
                      >
                        Request closure
                      </Button>
                    </div>
                    {blockers.length > 0 && (
                      <p className="hint">Resolve the closure blockers above first.</p>
                    )}
                  </div>
                )}

                <div className="sd-section">
                  <h3>Reopen history ({reopens.length})</h3>
                  {reopens.length === 0 ? (
                    <p className="cc-empty">This Store Day has never been reopened.</p>
                  ) : (
                    <DataTable
                      columns={[
                        {
                          key: 'when',
                          header: 'Reopened',
                          nowrap: true,
                          primary: true,
                          csvValue: (r: Reopen) => r.reopened_at,
                          render: (r: Reopen) => formatDateTime(r.reopened_at),
                        },
                        {
                          key: 'by',
                          header: 'By',
                          csvValue: (r: Reopen) => r.reopened_by,
                          render: (r: Reopen) => r.reopened_by,
                        },
                        {
                          key: 'why',
                          header: 'Reason',
                          csvValue: (r: Reopen) => r.reason,
                          render: (r: Reopen) => r.reason,
                        },
                        {
                          key: 'closed',
                          header: 'Had been closed by',
                          csvValue: (r: Reopen) =>
                            r.closed_by
                              ? `${r.closed_by}${r.closed_at ? ` · ${formatDateTime(r.closed_at, { dateStyle: 'medium' })}` : ''}`
                              : '',
                          render: (r: Reopen) =>
                            r.closed_by
                              ? `${r.closed_by}${r.closed_at ? ` · ${formatDateTime(r.closed_at, { dateStyle: 'medium' })}` : ''}`
                              : '—',
                        },
                        {
                          key: 'ver',
                          header: 'Totals version',
                          numeric: true,
                          csvValue: (r: Reopen) => r.audit_version ?? '',
                          render: (r: Reopen) => (r.audit_version ? `v${r.audit_version}` : '—'),
                        },
                      ]}
                      rows={reopens}
                      rowKey={(r: Reopen) => r.id}
                      csvFileName={`${storeId}-${date}-reopen-history.csv`}
                      detachTitle={`${storeId} ${date} · Reopen history`}
                    />
                  )}
                </div>

                <div className="sd-section">
                  <div className="row-between">
                    <h3>Closure requests ({closures.length})</h3>
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={closures.length === 0}
                      onClick={exportClosureRequests}
                    >
                      Export closure history CSV
                    </Button>
                  </div>
                  {closures.length === 0 ? (
                    <p className="cc-empty">No closure requests yet.</p>
                  ) : (
                    closures.map((c) => (
                      <div className="case-note" key={c.id}>
                        <Badge variant={dayStatusVariant(c.status)} size="sm">
                          {c.status}
                        </Badge>{' '}
                        <span>
                          {c.requested_by} · {formatDateTime(c.created_at)}
                        </span>
                        <p>{c.evidence_basis}</p>
                        {c.review_reason && <p>Review: {c.review_reason}</p>}
                        {c.status === 'Pending' && (
                          <div className="form-row">
                            <div className="grow">
                              <Field label="Rejection rationale">
                                <input
                                  value={reviewRationale}
                                  onChange={(e) => setReviewRationale(e.target.value)}
                                  placeholder="Required to reject"
                                />
                              </Field>
                            </div>
                            <Button
                              disabled={busy || !reviewRationale.trim()}
                              onClick={() =>
                                act(
                                  `${base}/closure-requests/${c.id}/reject`,
                                  { rationale: reviewRationale },
                                  () => setReviewRationale('')
                                )
                              }
                            >
                              Reject request
                            </Button>
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}

            {tab === 'cases' && (
              <div className="sd-pane">
                <DataTable
                  columns={[
                    {
                      key: 'case',
                      header: 'Case',
                      primary: true,
                      csvValue: (c) => c.case_number,
                      render: (c) => <CaseLink id={c.id}>{c.case_number}</CaseLink>,
                    },
                    {
                      key: 'type',
                      header: 'Type',
                      csvValue: (c) => c.case_type,
                      render: (c) => c.case_type,
                    },
                    {
                      key: 'status',
                      header: 'Status',
                      csvValue: (c) => c.status,
                      render: (c) => (
                        <Badge variant="secondary" size="sm">
                          {c.status}
                        </Badge>
                      ),
                    },
                    {
                      key: 'exposure',
                      header: 'Exposure',
                      numeric: true,
                      csvValue: (c) => c.exposure,
                      render: (c) => money(c.exposure),
                    },
                  ]}
                  rows={cases}
                  rowKey={(c) => c.id}
                  emptyTitle="No cases for this Store Day"
                  csvFileName={`${storeId}-${date}-cases.csv`}
                  detachTitle={`${storeId} ${date} · Cases`}
                />
              </div>
            )}

            {tab === 'errors' && (
              <div className="sd-pane">
                <p className="hint">
                  Transaction audit exceptions raised for this store day. The list defaults to
                  errors in open cases; use a case link to investigate and resolve them.
                </p>
                <Toolbar count={dayErrors ? `${dayErrors.total} matching errors` : undefined}>
                  <input
                    className="grow"
                    type="search"
                    value={dayErrorsQuery}
                    onChange={(event) => {
                      setDayErrorsQuery(event.target.value);
                      setDayErrorsPage(1);
                    }}
                    placeholder="Search code, family, rule, or case"
                    aria-label="Search Store Day audit errors"
                  />
                  <select
                    aria-label="Filter Store Day audit errors"
                    value={dayErrorsView}
                    onChange={(event) => {
                      setDayErrorsView(event.target.value as 'open' | 'all');
                      setDayErrorsPage(1);
                    }}
                  >
                    <option value="open">Open cases</option>
                    <option value="all">All cases</option>
                  </select>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={dayErrorsLoading || dayErrorsExporting || !dayErrors?.total}
                    onClick={() => void exportDayErrors()}
                  >
                    {dayErrorsExporting ? 'Preparing CSV…' : 'Export matching CSV'}
                  </Button>
                </Toolbar>
                {dayErrorsError && <Alert tone="error">{dayErrorsError}</Alert>}
                <DataTable
                  columns={[
                    {
                      key: 'error',
                      header: 'Audit error',
                      primary: true,
                      csvValue: (item: DayAuditError) =>
                        `${item.code} · ${item.family} · ${item.detection_origin}`,
                      render: (item: DayAuditError) => (
                        <>
                          <Link href={`/audit-errors/${encodeURIComponent(item.code)}`}>
                            {item.code}
                          </Link>
                          <small className="act-sub" style={{ display: 'block' }}>
                            {item.family} · {item.detection_origin}
                          </small>
                        </>
                      ),
                    },
                    {
                      key: 'case',
                      header: 'Case',
                      csvValue: (item: DayAuditError) =>
                        `${item.case_number} (${item.case_status})`,
                      render: (item: DayAuditError) => (
                        <>
                          <CaseLink id={item.case_id}>{item.case_number}</CaseLink>
                          <small className="act-sub" style={{ display: 'block' }}>
                            {item.case_status}
                          </small>
                        </>
                      ),
                    },
                    {
                      key: 'status',
                      header: 'Error status',
                      csvValue: (item: DayAuditError) => item.status,
                      render: (item: DayAuditError) => (
                        <Badge variant={item.status === 'Open' ? 'warning' : 'secondary'} size="sm">
                          {item.status}
                        </Badge>
                      ),
                    },
                    {
                      key: 'severity',
                      header: 'Severity',
                      csvValue: (item: DayAuditError) => item.severity,
                      render: (item: DayAuditError) => item.severity,
                    },
                    {
                      key: 'rule',
                      header: 'Detection rule',
                      csvValue: (item: DayAuditError) =>
                        item.detection_rule_name ?? item.detection_rule_id,
                      render: (item: DayAuditError) =>
                        item.detection_rule_name ?? item.detection_rule_id,
                    },
                    {
                      key: 'amount',
                      header: 'Amount',
                      numeric: true,
                      csvValue: (item: DayAuditError) => item.amount,
                      render: (item: DayAuditError) =>
                        money(item.amount, item.currency ?? undefined),
                    },
                    {
                      key: 'exposure',
                      header: 'Exposure',
                      numeric: true,
                      csvValue: (item: DayAuditError) => item.exposure,
                      render: (item: DayAuditError) =>
                        money(item.exposure, item.currency ?? undefined),
                    },
                    {
                      key: 'close-impact',
                      header: 'Closure impact',
                      csvValue: (item: DayAuditError) =>
                        item.close_blocker_reason
                          ? `${item.close_impact} · ${item.close_blocker_reason}`
                          : item.close_impact,
                      render: (item: DayAuditError) => (
                        <>
                          {item.close_impact}
                          {item.close_blocker_reason && (
                            <small className="act-sub" style={{ display: 'block' }}>
                              {item.close_blocker_reason}
                            </small>
                          )}
                        </>
                      ),
                    },
                    {
                      key: 'created',
                      header: 'Raised',
                      nowrap: true,
                      csvValue: (item: DayAuditError) => item.created_at,
                      render: (item: DayAuditError) => formatDateTime(item.created_at),
                    },
                  ]}
                  rows={dayErrorsLoading ? null : (dayErrors?.items ?? [])}
                  rowKey={(item: DayAuditError) => item.id}
                  emptyTitle="No audit errors for this Store Day"
                  emptyHint="Try All cases to include errors in closed cases."
                  failed={!!dayErrorsError}
                  csvFileName={`${storeId}-${date}-audit-errors-visible.csv`}
                  detachTitle={`${storeId} ${date} · Error List`}
                  footer={
                    dayErrors && (
                      <Pager
                        page={dayErrors.page}
                        pages={dayErrors.pages}
                        total={dayErrors.total}
                        pageSize={dayErrors.page_size}
                        pageSizes={[50]}
                        onPage={setDayErrorsPage}
                        onPageSize={() => {}}
                      />
                    )
                  }
                />
              </div>
            )}

            {tab === 'history' && (
              <div className="sd-pane">
                {history.length === 0 ? (
                  <p className="cc-empty">No history yet.</p>
                ) : (
                  <>
                    <Toolbar count={`${historyPagination.total} events`}>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={history.length === 0}
                        title="Download the audit history events loaded for the current filters."
                        onClick={exportLoadedHistory}
                      >
                        Export loaded history CSV
                      </Button>
                      <input
                        className="grow"
                        type="search"
                        value={historyQ}
                        onChange={(e) => {
                          const q = e.target.value;
                          setHistoryQ(q);
                          void load(undefined, { q, eventType: historyType });
                        }}
                        placeholder="Search history"
                        aria-label="Search history"
                      />
                      <select
                        aria-label="Filter by event"
                        value={historyType}
                        onChange={(e) => {
                          const eventType = e.target.value;
                          setHistoryType(eventType);
                          void load(undefined, { q: historyQ, eventType });
                        }}
                      >
                        <option value="">All events</option>
                        {historyEventTypes.map((t) => (
                          <option key={t} value={t}>
                            {t.replaceAll('_', ' ')}
                          </option>
                        ))}
                      </select>
                    </Toolbar>
                    {history.length === 0 ? (
                      <p className="cc-empty">No events match.</p>
                    ) : (
                      historyByDay.map(([label, items]) => (
                        <Fragment key={label}>
                          <h4 className="sd-day">{label}</h4>
                          <ol className="case-timeline">
                            {items.map((h) => (
                              <li key={h.id}>
                                <time>
                                  {formatDateTime(h.created_at, {
                                    hour: '2-digit',
                                    minute: '2-digit',
                                  })}
                                </time>
                                <div>
                                  <b>{h.event_type.replaceAll('_', ' ')}</b>
                                  <p>{h.description}</p>
                                  <small>{h.actor}</small>
                                </div>
                              </li>
                            ))}
                          </ol>
                        </Fragment>
                      ))
                    )}
                    {historyPagination.page < historyPagination.pages && (
                      <div>
                        <Button onClick={() => void loadMoreHistory()}>
                          Load next{' '}
                          {Math.min(HISTORY_PAGE, historyPagination.total - history.length)} events
                        </Button>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {tab === 'io' && (
              <div className="sd-pane">
                {ioError && <Alert tone="error">{ioError}</Alert>}
                <section>
                  <h3>Import log</h3>
                  <p className="hint">
                    Deliveries contributing source records to this store day. Quarantine counts
                    describe the full delivery.
                  </p>
                  <DataTable
                    columns={[
                      {
                        key: 'source',
                        header: 'Source',
                        primary: true,
                        csvValue: (row) => row.source_system,
                        render: (row) => row.source_system,
                      },
                      {
                        key: 'delivery',
                        header: 'Delivery',
                        csvValue: (row) => row.delivery_id,
                        render: (row) => row.delivery_id,
                      },
                      {
                        key: 'received',
                        header: 'Received',
                        csvValue: (row) => row.received_at,
                        render: (row) => formatDateTime(row.received_at),
                      },
                      {
                        key: 'day_records',
                        header: 'Store-day records',
                        numeric: true,
                        csvValue: (row) => row.store_day_records,
                        render: (row) => formatNumber(row.store_day_records),
                      },
                      {
                        key: 'delivery_records',
                        header: 'Delivery total',
                        numeric: true,
                        csvValue: (row) => row.delivery_records,
                        render: (row) => formatNumber(row.delivery_records),
                      },
                      {
                        key: 'processed',
                        header: 'Processed',
                        numeric: true,
                        csvValue: (row) => row.delivery_processed,
                        render: (row) => formatNumber(row.delivery_processed),
                      },
                      {
                        key: 'quarantined',
                        header: 'Quarantined',
                        numeric: true,
                        csvValue: (row) => row.delivery_quarantined,
                        render: (row) => formatNumber(row.delivery_quarantined),
                      },
                    ]}
                    rows={ioLog.imports}
                    rowKey={(row) => `${row.source_system}:${row.delivery_id}`}
                    emptyTitle="No imported transactions linked to this day"
                    csvFileName={`${storeId}-${date}-import-log.csv`}
                    detachTitle={`${storeId} ${date} · Import log`}
                  />
                </section>
                <section>
                  <h3>Export log</h3>
                  <p className="hint">Outbound batches containing records for this store day.</p>
                  <DataTable
                    columns={[
                      {
                        key: 'destination',
                        header: 'Destination',
                        primary: true,
                        csvValue: (row) => row.destination,
                        render: (row) => row.destination,
                      },
                      {
                        key: 'batch',
                        header: 'Batch',
                        csvValue: (row) => row.batch_id,
                        render: (row) => row.batch_id.slice(0, 8),
                      },
                      {
                        key: 'dataset',
                        header: 'Dataset',
                        csvValue: (row) => row.dataset,
                        render: (row) => row.dataset,
                      },
                      {
                        key: 'status',
                        header: 'Status',
                        csvValue: (row) => row.status,
                        render: (row) => (
                          <Badge
                            variant={
                              row.status === 'Acknowledged'
                                ? 'success'
                                : row.status === 'Failed'
                                  ? 'error'
                                  : 'secondary'
                            }
                          >
                            {row.status}
                          </Badge>
                        ),
                      },
                      {
                        key: 'created',
                        header: 'Created',
                        csvValue: (row) => row.created_at,
                        render: (row) => formatDateTime(row.created_at),
                      },
                      {
                        key: 'day_records',
                        header: 'Store-day records',
                        numeric: true,
                        csvValue: (row) => row.store_day_records,
                        render: (row) => formatNumber(row.store_day_records),
                      },
                      {
                        key: 'batch_records',
                        header: 'Batch total',
                        numeric: true,
                        csvValue: (row) => row.batch_records,
                        render: (row) => formatNumber(row.batch_records),
                      },
                      {
                        key: 'ack',
                        header: 'Acknowledged',
                        csvValue: (row) => row.acked_at ?? row.response_ref ?? '',
                        render: (row) =>
                          row.acked_at ? formatDateTime(row.acked_at) : row.response_ref || '—',
                      },
                      {
                        key: 'error',
                        header: 'Error',
                        csvValue: (row) => row.error ?? '',
                        render: (row) => row.error || '—',
                      },
                      {
                        key: 'sent',
                        header: 'Sent',
                        csvValue: (row) => row.sent_at ?? '',
                        render: (row) => row.sent_at ? formatDateTime(row.sent_at) : '—',
                      },
                      {
                        key: 'retries',
                        header: 'Retries',
                        numeric: true,
                        csvValue: (row) => row.retries,
                        render: (row) => formatNumber(row.retries),
                      },
                    ]}
                    rows={ioLog.exports}
                    rowKey={(row) => row.batch_id}
                    emptyTitle="No exports for this store day"
                    csvFileName={`${storeId}-${date}-export-log.csv`}
                    detachTitle={`${storeId} ${date} · Export log`}
                  />
                </section>
              </div>
            )}

            {tab === 'missing' && (
              <div className="sd-pane">
                <p className="hint">
                  A sequence gap can be a real transaction that did not arrive, or a number the POS
                  generated by mistake. Add details through a governed correction, or dismiss a
                  false gap with an audit reason.
                </p>
                <Toolbar count={`${gaps.length} register group(s) loaded`}>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={gaps.length === 0}
                    title="Download the missing and dismissed transaction numbers currently loaded."
                    onClick={exportLoadedSequenceGaps}
                  >
                    Export loaded gaps CSV
                  </Button>
                </Toolbar>
                {gaps.length === 0 ? (
                  <p className="cc-empty">No missing transaction numbers were found.</p>
                ) : (
                  gaps.map((register) => (
                    <section key={register.register_id}>
                      <h3>
                        Register {register.register_id}{' '}
                        <small className="act-sub">{formatNumber(register.count)} unresolved</small>
                      </h3>
                      {register.missing.length === 0 && register.dismissed.length === 0 ? (
                        <p className="cc-empty">No sequence gaps.</p>
                      ) : (
                        <div className="table-scroll">
                          <table className="data-table">
                            <thead>
                              <tr>
                                <th>Transaction number</th>
                                <th>Status</th>
                                <th>Reason / action</th>
                              </tr>
                            </thead>
                            <tbody>
                              {register.missing.map((number) => {
                                const key = `${register.register_id}:${number}`;
                                const params = new URLSearchParams({
                                  kind: 'MissingTransaction',
                                  store: storeId,
                                  date,
                                  register: register.register_id,
                                  transaction_number: String(number),
                                });
                                return (
                                  <tr key={number}>
                                    <td>{number}</td>
                                    <td>
                                      <Badge variant="warning">Needs review</Badge>
                                    </td>
                                    <td>
                                      <div className="toolbar">
                                        <Link href={`/corrections/new?${params.toString()}`}>
                                          Add transaction details
                                        </Link>
                                        <input
                                          aria-label={`Reason for dismissing ${register.register_id} transaction ${number}`}
                                          maxLength={2000}
                                          placeholder="Reason this number is not expected"
                                          value={gapReasons[key] ?? ''}
                                          onChange={(event) =>
                                            setGapReasons((current) => ({
                                              ...current,
                                              [key]: event.target.value,
                                            }))
                                          }
                                        />
                                        <Button
                                          size="sm"
                                          variant="secondary"
                                          disabled={gapBusy === key || !gapReasons[key]?.trim()}
                                          onClick={() =>
                                            void decideGap(register.register_id, number, 'dismiss')
                                          }
                                        >
                                          {gapBusy === key ? 'Saving…' : 'Mark not a transaction'}
                                        </Button>
                                      </div>
                                    </td>
                                  </tr>
                                );
                              })}
                              {register.dismissed.map((item) => (
                                <tr key={`dismissed-${item.number}`}>
                                  <td>{item.number}</td>
                                  <td>
                                    <Badge variant="secondary">Not a transaction</Badge>
                                  </td>
                                  <td>
                                    {item.reason} · {item.actor} · {formatDateTime(item.decided_at)}{' '}
                                    <Button
                                      size="sm"
                                      variant="secondary"
                                      disabled={
                                        gapBusy === `${register.register_id}:${item.number}`
                                      }
                                      onClick={() =>
                                        void decideGap(register.register_id, item.number, 'restore')
                                      }
                                    >
                                      Restore to review
                                    </Button>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </section>
                  ))
                )}
                {gapPagination.total > 0 && (
                  <Pager
                    page={gapPagination.page}
                    pages={gapPagination.pages}
                    total={gapPagination.total}
                    pageSize={gapPagination.page_size}
                    pageSizes={[50, 100, 200]}
                    onPage={(nextPage) => void loadSequenceGaps(nextPage, gapPagination.page_size)}
                    onPageSize={(nextPageSize) => void loadSequenceGaps(1, nextPageSize)}
                  />
                )}
              </div>
            )}
          </Card>
        </>
      )}
      {removeOpen && day && (
        <Modal
          title="Delete empty Store Day"
          onClose={closeRemoveModal}
          footer={
            <>
              <Button disabled={busy} onClick={closeRemoveModal}>
                Cancel
              </Button>
              <Button
                variant="danger"
                disabled={
                  busy || !removeReason.trim() || removeConfirmation !== `${storeId}/${date}`
                }
                onClick={() => void removeEmptyDay()}
              >
                {busy ? 'Deleting…' : 'Delete Store Day'}
              </Button>
            </>
          }
        >
          <p>
            This removes only an unclosed placeholder with no sales, register activity, cases, cash
            or ACH activity, totals, variance explanations, or closure history. Its deletion reason
            remains in the audit trail.
          </p>
          <Field label="Reason">
            <textarea
              value={removeReason}
              maxLength={2000}
              onChange={(event) => setRemoveReason(event.target.value)}
              autoFocus
            />
          </Field>
          <Field label={`Type ${storeId}/${date} to confirm`}>
            <input
              value={removeConfirmation}
              autoComplete="off"
              onChange={(event) => setRemoveConfirmation(event.target.value)}
            />
          </Field>
        </Modal>
      )}
    </div>
  );
}
