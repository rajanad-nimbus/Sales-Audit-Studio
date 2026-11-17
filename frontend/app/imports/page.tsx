'use client';

import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { api, BatchStatus } from '@/lib/api';
import { SkeletonRows, useToast } from '@/components/Feedback';
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  Field,
  PageHeader,
  PageNav,
  Toolbar,
} from '@/components/ui';
import { useMe } from '@/components/MeProvider';
import { ShopifySyncCard } from '@/components/ShopifySyncCard';
import type { Source } from '@/lib/sources';
import { formatDateTime, formatNumber } from '@/lib/preferences';
import { downloadBlob } from '@/lib/csv';

interface Stats {
  source_records: Record<string, number>;
  transactions: Record<string, number>;
}
interface Run {
  id: string;
  business_date: string;
  status: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  transactions_loaded: number;
  matched_pairs: number;
  cases_created: number;
  error: string | null;
  store_day_open_status: 'Succeeded' | 'Skipped' | 'Failed' | null;
  store_days_opened: number;
  store_days_already_open: number;
  store_day_open_error: string | null;
}
interface Recon {
  transactions_examined: number;
  matched_pairs: number;
  exceptions: Record<string, number>;
  cases_created: number;
  store_days?: StoreDayRunSummary;
}
interface StoreDayRunSummary {
  opened: number;
  totaled: number;
  closed: number;
  needs_review: number;
  failed: number;
  automatic_close?: boolean;
}
interface Delivery {
  source_system: string;
  delivery_id: string;
  received_at: string;
  records: number;
  processed: number;
  quarantined: number;
  last_error: string | null;
}
interface QuarantineRecord {
  id: string;
  source_system: string;
  delivery_id: string;
  source_record_id: string;
  reason: string;
  received_at: string;
}
interface FeedHealth {
  profile_id: string;
  name: string;
  source_system: string;
  schedule: string | null;
  last_received_at: string | null;
  age_hours: number | null;
  expected_within_hours: number | null;
  state: 'fresh' | 'overdue' | 'never' | 'unknown' | 'disabled';
}
interface SourceProfile {
  id: string;
  name: string;
  source_system: string;
  column_mapping: Record<string, string>;
  enabled: boolean;
}
interface IngestContract {
  transaction_columns: string[];
  pos_control_columns: string[];
}
interface ManualUploadResult {
  format: 'csv' | 'rtlog';
  source_system: string;
  delivery_id: string;
  records_received: number;
  records_archived: number;
  duplicates_skipped: number;
  quarantined: number;
  transactions_created: number;
  register_events_created: number;
  register_events_quarantined: number;
  not_mapped: number;
  problems: string[];
  automatic_audit: AutomaticAuditResult | null;
}
interface AutomaticAuditResult {
  enabled: boolean;
  runs: {
    business_date: string;
    stores: number;
    store_ids: string[];
    transactions_examined?: number;
    cases_created?: number;
    store_days?: StoreDayRunSummary;
    error?: string;
  }[];
}
interface IngestStats {
  received: number;
  archived: number;
  duplicates_skipped: number;
  quarantined: number;
  canonical_created: number;
}
interface CsvUploadResponse extends IngestStats {
  source_system: string;
  delivery_id: string;
  automatic_audit: AutomaticAuditResult | null;
}
interface RtlogUploadResponse {
  source_system: string;
  delivery_id: string;
  store_id: string;
  business_date: string;
  transactions: number;
  sales_and_returns: IngestStats;
  register_events: {
    received: number;
    created: number;
    duplicates_skipped: number;
    quarantined: number;
  };
  not_mapped: { transaction_number: string; type: string }[];
  problems: string[];
  automatic_audit: AutomaticAuditResult | null;
}
interface UploadPreview {
  format: 'csv' | 'rtlog';
  records_received: number;
  valid: number;
  quarantined: number;
  stores: string[];
  business_dates: string[];
  errors: string[];
  warnings: string[];
}
interface CsvPreviewResponse {
  source_system: string;
  records_received: number;
  valid: number;
  quarantined: number;
  errors: { row: number; reason: string }[];
  errors_truncated: boolean;
  stores: string[];
  business_dates: string[];
}
interface RtlogPreviewResponse {
  store_id: string | null;
  business_date: string | null;
  transactions: number;
  problems: string[];
  ingestable_records: number;
  valid_records: number;
  quarantined_records: number;
  not_mapped: number;
  validation_errors: { record: number; reason: string }[];
  validation_errors_truncated: boolean;
}
type SourceSwitch = 'enabled' | 'in_daily_batch' | 'manual_sync' | 'require_day_close';
type Tab = 'sources' | 'upload' | 'runs' | 'feeds' | 'quarantine' | 'demo';
const SOURCE_NOTES: Record<string, string> = {
  POS: 'Till sales and returns',
  POSControl: 'Register control totals',
  Processor: 'Card captures and refunds',
  Bank: 'Bank deposits',
  ERP: 'General-ledger postings',
  Shopify: 'Online orders and refunds, pulled from Shopify',
};

const sum = (o: Record<string, number> = {}) => Object.values(o).reduce((a, b) => a + b, 0);
const n = (v: number) => formatNumber(v);
const when = (iso: string) => formatDateTime(iso);
const apiError = (e: unknown, fallback: string) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
const runTone = (s: string) =>
  s === 'Succeeded' ? 'badge-success' : s === 'Failed' ? 'badge-error' : 'badge-warning';
const feedTone = (s: FeedHealth['state']) =>
  s === 'fresh'
    ? 'badge-success'
    : s === 'overdue'
      ? 'badge-error'
      : s === 'never'
        ? 'badge-warning'
        : 'badge-secondary';
const ago = (h: number | null | undefined) =>
  h == null
    ? ''
    : h < 1
      ? 'less than an hour ago'
      : h < 48
        ? `${Math.round(h)}h ago`
        : `${Math.round(h / 24)}d ago`;

export default function ImportsPage() {
  const toast = useToast();
  const { me } = useMe();
  const canManage = !!me && (me.role === 'it' || me.role === 'admin');
  const canReconcile = !!me && ['it', 'finance', 'admin'].includes(me.role);
  const [sources, setSources] = useState<Source[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runStart, setRunStart] = useState('');
  const [runEnd, setRunEnd] = useState('');
  const [runStatus, setRunStatus] = useState('');
  const [runPage, setRunPage] = useState(1);
  const [runPageSize, setRunPageSize] = useState(50);
  const [runHasMore, setRunHasMore] = useState(false);
  const [runHistoryLoading, setRunHistoryLoading] = useState(false);
  const [runHistoryExporting, setRunHistoryExporting] = useState(false);
  const [runHistoryError, setRunHistoryError] = useState('');
  const [runHistoryNotice, setRunHistoryNotice] = useState('');
  const [quarantineRows, setQuarantineRows] = useState<QuarantineRecord[]>([]);
  const [quarantineSource, setQuarantineSource] = useState('');
  const [quarantineDeliveryId, setQuarantineDeliveryId] = useState('');
  const [quarantineStart, setQuarantineStart] = useState('');
  const [quarantineEnd, setQuarantineEnd] = useState('');
  const [quarantineSearchDraft, setQuarantineSearchDraft] = useState('');
  const [quarantineQuery, setQuarantineQuery] = useState('');
  const [quarantinePage, setQuarantinePage] = useState(1);
  const [quarantinePageSize, setQuarantinePageSize] = useState(50);
  const [quarantineHasMore, setQuarantineHasMore] = useState(false);
  const [quarantineLoading, setQuarantineLoading] = useState(false);
  const [quarantineExporting, setQuarantineExporting] = useState(false);
  const [quarantineError, setQuarantineError] = useState('');
  const [quarantineNotice, setQuarantineNotice] = useState('');
  const [bstatus, setBstatus] = useState<BatchStatus | null>(null);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [deliverySource, setDeliverySource] = useState('');
  const [deliveryStart, setDeliveryStart] = useState('');
  const [deliveryEnd, setDeliveryEnd] = useState('');
  const [deliverySearchDraft, setDeliverySearchDraft] = useState('');
  const [deliveryQuery, setDeliveryQuery] = useState('');
  const [deliveryPage, setDeliveryPage] = useState(1);
  const [deliveryPageSize, setDeliveryPageSize] = useState(50);
  const [deliveryHasMore, setDeliveryHasMore] = useState(false);
  const [deliveryLoading, setDeliveryLoading] = useState(false);
  const [deliveryExporting, setDeliveryExporting] = useState(false);
  const [deliveryError, setDeliveryError] = useState('');
  const [deliveryNotice, setDeliveryNotice] = useState('');
  const [feedHealth, setFeedHealth] = useState<FeedHealth[]>([]);
  const [ingestRes, setIngestRes] = useState<Record<string, Record<string, number>> | null>(null);
  const [recon, setRecon] = useState<Recon | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [confirmClr, setConfirmClr] = useState(false);
  const [tab, setTab] = useState<Tab>('sources');
  const [uploadFormat, setUploadFormat] = useState<'csv' | 'rtlog'>('csv');
  const [uploadSource, setUploadSource] = useState('POS');
  const [uploadCurrency, setUploadCurrency] = useState('USD');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const [uploadDeliveryId, setUploadDeliveryId] = useState('');
  const [uploadPreview, setUploadPreview] = useState<UploadPreview | null>(null);
  const [uploadProfileId, setUploadProfileId] = useState('');
  const [uploadProfiles, setUploadProfiles] = useState<SourceProfile[]>([]);
  const [ingestContract, setIngestContract] = useState<IngestContract | null>(null);
  const [uploadResult, setUploadResult] = useState<ManualUploadResult | null>(null);
  const [uploadError, setUploadError] = useState('');
  const [uploadBusy, setUploadBusy] = useState(false);
  const [reconcileDate, setReconcileDate] = useState(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  });
  const [manualRecon, setManualRecon] = useState<Recon | null>(null);
  const [reconcileBusy, setReconcileBusy] = useState(false);
  const [reconcileError, setReconcileError] = useState('');

  const load = useCallback(async () => {
    try {
      const [st, b, f, src] = await Promise.all([
        api.get('/api/ingest/stats'),
        api.get('/api/batch/status'),
        api.get('/api/ingest/feed-health'),
        api.get('/api/ingest/sources'),
      ]);
      setSources(src.data);
      setStats(st.data);
      setBstatus(b.data);
      setFeedHealth(f.data);
      setError(null);
    } catch {
      setError(
        'Could not load import status. Check that the backend is running and your role is Finance or IT.'
      );
    } finally {
      setLoaded(true);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const loadRunHistory = useCallback(async () => {
    setRunHistoryLoading(true);
    setRunHistoryError('');
    try {
      const { data } = await api.get('/api/batch/runs/history', {
        params: {
          start: runStart || undefined,
          end: runEnd || undefined,
          status: runStatus || undefined,
          page: runPage,
          page_size: runPageSize,
        },
      });
      setRuns(data.items);
      setRunHasMore(data.has_more);
    } catch (e) {
      setRunHistoryError(apiError(e, 'Could not load batch run history.'));
    } finally {
      setRunHistoryLoading(false);
    }
  }, [runStart, runEnd, runStatus, runPage, runPageSize]);
  useEffect(() => {
    if (tab === 'runs') loadRunHistory();
  }, [tab, loadRunHistory]);
  const loadQuarantine = useCallback(async () => {
    setQuarantineLoading(true);
    setQuarantineError('');
    try {
      const { data } = await api.get('/api/ingest/quarantine', {
        params: {
          source_system: quarantineSource || undefined,
          delivery_id: quarantineDeliveryId || undefined,
          query: quarantineQuery || undefined,
          start: quarantineStart || undefined,
          end: quarantineEnd || undefined,
          page: quarantinePage,
          page_size: quarantinePageSize,
        },
      });
      setQuarantineRows(data.items);
      setQuarantineHasMore(data.has_more);
    } catch (e) {
      setQuarantineError(apiError(e, 'Could not load quarantined records.'));
    } finally {
      setQuarantineLoading(false);
    }
  }, [
    quarantineSource,
    quarantineDeliveryId,
    quarantineQuery,
    quarantineStart,
    quarantineEnd,
    quarantinePage,
    quarantinePageSize,
  ]);
  useEffect(() => {
    if (tab === 'quarantine') loadQuarantine();
  }, [tab, loadQuarantine]);
  const loadDeliveryHistory = useCallback(async () => {
    setDeliveryLoading(true);
    setDeliveryError('');
    try {
      const { data } = await api.get('/api/ingest/deliveries/history', {
        params: {
          source_system: deliverySource || undefined,
          query: deliveryQuery || undefined,
          start: deliveryStart || undefined,
          end: deliveryEnd || undefined,
          page: deliveryPage,
          page_size: deliveryPageSize,
        },
      });
      setDeliveries(data.items);
      setDeliveryHasMore(data.has_more);
    } catch (e) {
      setDeliveryError(apiError(e, 'Could not load source delivery history.'));
    } finally {
      setDeliveryLoading(false);
    }
  }, [deliverySource, deliveryQuery, deliveryStart, deliveryEnd, deliveryPage, deliveryPageSize]);
  useEffect(() => {
    if (tab === 'feeds') loadDeliveryHistory();
  }, [tab, loadDeliveryHistory]);
  const downloadDeliveryHistory = async () => {
    setDeliveryExporting(true);
    setDeliveryError('');
    setDeliveryNotice('');
    try {
      const { data, headers } = await api.get('/api/ingest/deliveries/history.csv', {
        params: {
          source_system: deliverySource || undefined,
          query: deliveryQuery || undefined,
          start: deliveryStart || undefined,
          end: deliveryEnd || undefined,
        },
        responseType: 'blob',
      });
      downloadBlob('source-deliveries.csv', data);
      if (headers?.['x-delivery-history-truncated'] === 'true') {
        setDeliveryNotice(
          'The CSV reached 50,000 deliveries. Narrow the source, delivery ID, or date filters to export the full result.'
        );
      }
    } catch (e) {
      setDeliveryError(apiError(e, 'Could not export source delivery history.'));
    } finally {
      setDeliveryExporting(false);
    }
  };
  const downloadQuarantine = async () => {
    setQuarantineExporting(true);
    setQuarantineError('');
    setQuarantineNotice('');
    try {
      const { data, headers } = await api.get('/api/ingest/quarantine.csv', {
        params: {
          source_system: quarantineSource || undefined,
          delivery_id: quarantineDeliveryId || undefined,
          query: quarantineQuery || undefined,
          start: quarantineStart || undefined,
          end: quarantineEnd || undefined,
        },
        responseType: 'blob',
      });
      downloadBlob('quarantine-records.csv', data);
      if (headers?.['x-quarantine-truncated'] === 'true') {
        setQuarantineNotice(
          'The CSV reached 50,000 rows. Narrow the source, delivery, search, or date filters to export the full result.'
        );
      }
    } catch (e) {
      setQuarantineError(apiError(e, 'Could not export quarantined records.'));
    } finally {
      setQuarantineExporting(false);
    }
  };
  const downloadRunHistory = async () => {
    setRunHistoryExporting(true);
    setRunHistoryError('');
    setRunHistoryNotice('');
    try {
      const { data, headers } = await api.get('/api/batch/runs/history.csv', {
        params: {
          start: runStart || undefined,
          end: runEnd || undefined,
          status: runStatus || undefined,
        },
        responseType: 'blob',
      });
      downloadBlob('batch-runs.csv', data);
      if (headers?.['x-batch-history-truncated'] === 'true') {
        setRunHistoryNotice(
          'The CSV reached 50,000 rows. Narrow the date or status filters to export the full history.'
        );
      }
    } catch (e) {
      setRunHistoryError(apiError(e, 'Could not export batch run history.'));
    } finally {
      setRunHistoryExporting(false);
    }
  };
  useEffect(() => {
    if (tab !== 'upload' || !canManage) return;
    let current = true;
    Promise.all([
      api.get<SourceProfile[]>('/api/ingest/profiles'),
      api.get<IngestContract>('/api/ingest/contracts'),
    ])
      .then(([profileResponse, contractResponse]) => {
        if (!current) return;
        setUploadProfiles(profileResponse.data.filter((profile) => profile.enabled));
        setIngestContract(contractResponse.data);
        setUploadError('');
      })
      .catch((e: unknown) => {
        if (current) setUploadError(apiError(e, 'Could not load upload profiles and contracts.'));
      });
    return () => {
      current = false;
    };
  }, [canManage, tab]);
  useEffect(() => {
    const h = window.location.hash.slice(1) as Tab;
    if (['sources', 'upload', 'runs', 'feeds', 'quarantine', 'demo'].includes(h)) setTab(h);
  }, []);
  const pick = (t: Tab) => {
    setTab(t);
    history.replaceState(null, '', `#${t}`);
  };
  const reviewDeliveryQuarantine = (delivery: Delivery) => {
    setQuarantineSource(delivery.source_system);
    setQuarantineDeliveryId(delivery.delivery_id);
    setQuarantinePage(1);
    setQuarantineSearchDraft('');
    setQuarantineQuery('');
    pick('quarantine');
  };

  const run = async (name: string, fn: () => Promise<string>) => {
    setBusy(name);
    try {
      toast(await fn(), 'success');
    } catch (e) {
      toast(apiError(e, 'Step failed'), 'error');
    }
    setBusy(null);
    load();
    if (tab === 'runs') loadRunHistory();
  };
  const setSwitch = async (source: Source, field: SourceSwitch, value: boolean) => {
    try {
      const { data } = await api.put(`/api/ingest/sources/${source.source_system}`, {
        [field]: value,
      });
      setSources((all) => all.map((s) => (s.source_system === data.source_system ? data : s)));
      toast(`${source.source_system} updated.`, 'success');
    } catch (e) {
      toast(apiError(e, 'Could not change this source.'), 'error');
    }
  };
  const uploadManualFile = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!uploadFile) {
      setUploadError('Choose a file to upload.');
      return;
    }
    const source = sources.find((item) => item.source_system === uploadSource);
    if (!source?.enabled || !source.manual_sync) {
      setUploadError(`${uploadSource} is not enabled for manual upload. Check Sources settings.`);
      return;
    }
    if (uploadFile.size > 24 * 1024 * 1024) {
      setUploadError('The file exceeds the 24 MB upload limit.');
      return;
    }
    setUploadBusy(true);
    setUploadError('');
    try {
      const content = await uploadFile.text();
      if (!uploadPreview) {
        setUploadResult(null);
        const deliveryId = `MANUAL-${Date.now()}-${crypto.randomUUID()}`;
        setUploadDeliveryId(deliveryId);
        if (uploadFormat === 'rtlog') {
          if (uploadSource !== 'POS') throw new Error('RTLOG uploads are only for POS.');
          const { data } = await api.post<RtlogPreviewResponse>('/api/ingest/pos-log/profile', {
            format: 'rtlog',
            delivery_id: deliveryId,
            content,
            currency: uploadCurrency,
          });
          setUploadPreview({
            format: 'rtlog',
            records_received: data.ingestable_records,
            valid: data.valid_records,
            quarantined: data.quarantined_records,
            stores: data.store_id ? [data.store_id] : [],
            business_dates: data.business_date ? [data.business_date] : [],
            errors: data.validation_errors
              .map((item) => `Mapped record ${item.record}: ${item.reason}`)
              .concat(data.validation_errors_truncated ? ['More validation errors omitted.'] : []),
            warnings: [
              ...data.problems,
              ...(data.not_mapped
                ? [
                    `${data.not_mapped} RTLOG transactions have no usable item lines and will not create sale records.`,
                  ]
                : []),
            ],
          });
        } else {
          const { data } = await api.post<CsvPreviewResponse>('/api/ingest/csv/preview', {
            source_system: uploadSource,
            delivery_id: deliveryId,
            csv_data: content,
            profile_id: uploadProfileId || null,
          });
          setUploadPreview({
            format: 'csv',
            records_received: data.records_received,
            valid: data.valid,
            quarantined: data.quarantined,
            stores: data.stores,
            business_dates: data.business_dates,
            errors: data.errors
              .map((item) => `Row ${item.row}: ${item.reason}`)
              .concat(data.errors_truncated ? ['More validation errors omitted.'] : []),
            warnings: [],
          });
        }
        toast('File preview is ready. Review it before ingesting.', 'success');
        return;
      }
      const deliveryId = uploadDeliveryId;
      if (!deliveryId) throw new Error('Review the file again before ingesting.');
      if (uploadFormat === 'rtlog') {
        if (uploadSource !== 'POS') throw new Error('RTLOG uploads are only for POS.');
        const { data } = await api.post<RtlogUploadResponse>('/api/ingest/pos-log', {
          format: 'rtlog',
          delivery_id: deliveryId,
          content,
          currency: uploadCurrency,
        });
        setReconcileDate(data.business_date);
        setUploadResult({
          format: 'rtlog',
          source_system: data.source_system,
          delivery_id: data.delivery_id,
          records_received: data.transactions,
          records_archived: data.sales_and_returns.archived,
          duplicates_skipped: data.sales_and_returns.duplicates_skipped,
          quarantined: data.sales_and_returns.quarantined,
          transactions_created: data.sales_and_returns.canonical_created,
          register_events_created: data.register_events.created,
          register_events_quarantined: data.register_events.quarantined,
          not_mapped: data.not_mapped.length,
          problems: data.problems,
          automatic_audit: data.automatic_audit,
        });
      } else {
        const { data } = await api.post<CsvUploadResponse>('/api/ingest/csv', {
          source_system: uploadSource,
          delivery_id: deliveryId,
          csv_data: content,
          profile_id: uploadProfileId || null,
        });
        setUploadResult({
          format: 'csv',
          source_system: data.source_system,
          delivery_id: data.delivery_id,
          records_received: data.received,
          records_archived: data.archived,
          duplicates_skipped: data.duplicates_skipped,
          quarantined: data.quarantined,
          transactions_created: data.canonical_created,
          register_events_created: 0,
          register_events_quarantined: 0,
          not_mapped: 0,
          problems: [],
          automatic_audit: data.automatic_audit,
        });
      }
      setUploadFile(null);
      if (uploadInputRef.current) uploadInputRef.current.value = '';
      setUploadPreview(null);
      setUploadDeliveryId('');
      toast('File uploaded and archived.', 'success');
      await load();
    } catch (e: unknown) {
      setUploadError(apiError(e, 'The file could not be uploaded.'));
    } finally {
      setUploadBusy(false);
    }
  };
  const runManualReconciliation = async (date = reconcileDate, storeIds?: string[]) => {
    if (!date) {
      setReconcileError('Choose the business date to audit.');
      return;
    }
    setReconcileDate(date);
    setReconcileBusy(true);
    setReconcileError('');
    setManualRecon(null);
    try {
      const params = new URLSearchParams({ business_date: date });
      storeIds?.forEach((storeId) => params.append('store_ids', storeId));
      const { data } = await api.post<Recon>(`/api/reconcile?${params.toString()}`, null);
      setManualRecon(data);
      setUploadResult((current) => {
        if (!current?.automatic_audit?.enabled) return current;
        return {
          ...current,
          automatic_audit: {
            ...current.automatic_audit,
            runs: current.automatic_audit.runs.map((run) =>
              run.business_date === date
                ? {
                    ...run,
                    error: undefined,
                    transactions_examined: data.transactions_examined,
                    cases_created: data.cases_created,
                    store_days: data.store_days,
                  }
                : run
            ),
          },
        };
      });
      toast(`Reconciliation completed for ${date}.`, 'success');
      await load();
    } catch (e: unknown) {
      setReconcileError(apiError(e, 'Reconciliation could not be completed.'));
    } finally {
      setReconcileBusy(false);
    }
  };
  const runBatch = () =>
    run('batch', async () => {
      const { data } = await api.post('/api/batch/run', {});
      return `Batch for ${data.business_date}: ${data.status}. ${n(data.transactions_loaded)} transactions loaded, ${n(data.cases_created)} cases raised.`;
    });

  const tx = stats?.transactions ?? {};
  const quarantineCount = stats?.source_records?.Quarantined ?? 0;
  const overdue = feedHealth.filter((f) => f.state === 'overdue' || f.state === 'never').length;
  const flow = [
    {
      label: 'Source records',
      value: sum(stats?.source_records),
      sub: 'received from configured or simulated source systems',
      href: '',
    },
    {
      label: 'Normalized',
      value: stats?.source_records?.Processed ?? 0,
      sub: 'archived as canonical transactions',
      href: '',
    },
    { label: 'Matched', value: tx.Matched ?? 0, sub: 'reconciled with no exception', href: '' },
    { label: 'Exceptions', value: tx.Exception ?? 0, sub: 'became cases', href: '/cases' },
  ];

  // One line that answers "is my data OK?"
  let health: { tone: 'success' | 'warning' | 'error'; title: string; body: string };
  if (!bstatus) health = { tone: 'warning', title: 'Checking imports…', body: '' };
  else if (bstatus.freshness === 'never')
    health = {
      tone: 'warning',
      title: 'No data loaded yet',
      body: 'Run the nightly batch to load the demo feed and raise cases.',
    };
  else if (bstatus.last_failed)
    health = {
      tone: 'error',
      title: 'The last batch failed',
      body: 'Open Batch runs to see the error, then run the batch again.',
    };
  else if (bstatus.freshness === 'stale')
    health = {
      tone: 'warning',
      title: 'Data is stale',
      body: `The last successful batch finished ${ago(bstatus.hours_since_success)}. Data as of ${bstatus.data_as_of ?? 'unknown'}.`,
    };
  else
    health = {
      tone: 'success',
      title: 'Data is current',
      body: `Data as of ${bstatus.data_as_of ?? 'unknown'}.${bstatus.last_success ? ` Last batch loaded ${n(bstatus.last_success.transactions_loaded)} transactions and raised ${n(bstatus.last_success.cases_created)} cases.` : ''}`,
    };

  const tabs: [Tab, string, number | null, boolean][] = [
    ['sources', 'Sources', sources.filter((s) => s.enabled).length, false],
    ['upload', 'Manual upload', null, false],
    ['runs', 'Batch runs', null, false],
    ['feeds', 'Source feeds', feedHealth.length, overdue > 0],
    ['quarantine', 'Quarantine', quarantineCount, quarantineCount > 0],
    ['demo', 'Demo tools', null, false],
  ];

  return (
    <div className="page-container">
      <PageHeader
        title="Imports"
        subtitle="Ingest, archive, normalize, reconcile, then cases. Transactions arrive once a day."
        actions={
          <>
            <Button onClick={load} disabled={!!busy}>
              Refresh
            </Button>
            <Button variant="primary" disabled={!!busy} onClick={runBatch}>
              {busy === 'batch' ? 'Running batch…' : 'Run nightly batch'}
            </Button>
          </>
        }
      />
      {error && (
        <Alert
          tone="error"
          action={
            <Button size="sm" className="alert-close" onClick={load}>
              Retry
            </Button>
          }
        >
          {error}
        </Alert>
      )}

      {!loaded ? (
        <SkeletonRows rows={3} label="Loading imports" />
      ) : (
        <>
          <div className={`pl-health ${health.tone}`} role="status">
            <b>{health.title}</b>
            <span>{health.body}</span>
            <span className="pl-chips">
              {overdue > 0 && (
                <button onClick={() => pick('feeds')}>
                  {overdue} feed{overdue > 1 ? 's' : ''} overdue
                </button>
              )}
              {quarantineCount > 0 && (
                <button onClick={() => pick('quarantine')}>{n(quarantineCount)} quarantined</button>
              )}
            </span>
          </div>

          <ol className="cc-pipeline pl-flow" aria-label="Import stages">
            {flow.map((f, i) => {
              const body = (
                <>
                  <b>{n(f.value)}</b>
                  <small>{f.label}</small>
                  <small className="pl-sub">{f.sub}</small>
                </>
              );
              return (
                <li key={f.label} className="cc-stage has">
                  {f.href && f.value > 0 ? (
                    <Link href={f.href} className="pl-stage-link">
                      {body}
                    </Link>
                  ) : (
                    body
                  )}
                  {i < flow.length - 1 && <em aria-hidden>›</em>}
                </li>
              );
            })}
          </ol>

          <div className="case-tabs" role="tablist" aria-label="Import sections">
            {tabs.map(([k, label, count, hot]) => (
              <button
                key={k}
                role="tab"
                aria-selected={tab === k}
                className={tab === k ? 'on' : ''}
                onClick={() => pick(k)}
              >
                {label}
                {count !== null && <span className={hot ? 'pl-hot' : ''}>{n(count)}</span>}
              </button>
            ))}
          </div>

          {tab === 'sources' && (
            <div className="case-panel">
              <Card title="Sources">
                <p className="hint">
                  Choose which sources Nimbus accepts and how each one brings data in. A source can
                  join the nightly batch, be synced or uploaded by hand, or both. Changes apply
                  straight away and are written to the audit trail.
                  {!canManage && ' Only an IT administrator can change them.'} Requiring POS Day
                  Close makes a store day incomplete until its close marker arrives.
                </p>
                <DataTable
                  columns={[
                    {
                      key: 'source',
                      header: 'Source',
                      primary: true,
                      render: (s) => (
                        <>
                          {s.source_system}
                          <small className="act-sub" style={{ display: 'block' }}>
                            {SOURCE_NOTES[s.source_system]}
                          </small>
                        </>
                      ),
                    },
                    {
                      key: 'state',
                      header: 'Status',
                      render: (s) =>
                        !s.enabled ? (
                          <Badge variant="secondary" size="sm">
                            Off
                          </Badge>
                        ) : s.configured === false ? (
                          <Badge variant="warning" size="sm">
                            Not connected
                          </Badge>
                        ) : (
                          <Badge variant="success" size="sm">
                            On
                          </Badge>
                        ),
                    },
                    ...(
                      [
                        ['enabled', 'Accept data'],
                        ['in_daily_batch', 'Nightly batch'],
                        ['manual_sync', 'Manual sync / upload'],
                      ] as [SourceSwitch, string][]
                    ).map(([field, header]) => ({
                      key: field,
                      header,
                      render: (s: Source) => (
                        <input
                          type="checkbox"
                          role="switch"
                          aria-label={`${header} for ${s.source_system}`}
                          checked={s[field]}
                          disabled={!canManage || (field !== 'enabled' && !s.enabled)}
                          onChange={(e) => setSwitch(s, field, e.target.checked)}
                        />
                      ),
                    })),
                    {
                      key: 'day-close',
                      header: 'Require Day Close',
                      render: (s: Source) =>
                        s.source_system === 'POS' ? (
                          <input
                            type="checkbox"
                            role="switch"
                            aria-label="Require POS Day Close marker"
                            checked={s.require_day_close}
                            disabled={!canManage || !s.enabled}
                            onChange={(e) => setSwitch(s, 'require_day_close', e.target.checked)}
                          />
                        ) : (
                          '—'
                        ),
                    },
                    {
                      key: 'last',
                      header: 'Last delivery',
                      nowrap: true,
                      render: (s) => (s.last_received_at ? when(s.last_received_at) : 'Never'),
                    },
                    {
                      key: 'held',
                      header: 'Quarantined',
                      numeric: true,
                      render: (s) => n(s.quarantined),
                    },
                  ]}
                  rows={sources}
                  rowKey={(s) => s.source_system}
                />
              </Card>
              {sources
                .filter((s) => s.source_system === 'Shopify')
                .map((s) => (
                  <ShopifySyncCard
                    key={s.source_system}
                    source={s}
                    canManage={canManage}
                    onUpdated={(u) =>
                      setSources((all) =>
                        all.map((x) => (x.source_system === u.source_system ? u : x))
                      )
                    }
                    onSynced={load}
                  />
                ))}
            </div>
          )}

          {tab === 'upload' && (
            <div className="case-panel">
              <Card title="Manual source upload">
                <p className="hint">
                  Upload an Oracle RTLOG file for POS, or a CSV extract for another enabled source.
                  Files are validated, archived, and deduplicated using the same ingestion pipeline
                  as delivered feeds. Uploads are limited to 24 MB.
                </p>
                {!canManage && (
                  <Alert tone="warning">Only an IT administrator can upload source files.</Alert>
                )}
                {uploadError && <Alert tone="error">{uploadError}</Alert>}
                {canManage && (
                  <form onSubmit={uploadManualFile} className="stack stack-md">
                    <div className="form-row">
                      <label className="field">
                        <span>File format</span>
                        <select
                          value={uploadFormat}
                          onChange={(event) => {
                            const next = event.target.value as 'csv' | 'rtlog';
                            setUploadFormat(next);
                            setUploadPreview(null);
                            setUploadDeliveryId('');
                            if (next === 'rtlog') setUploadSource('POS');
                          }}
                        >
                          <option value="csv">CSV source extract</option>
                          <option value="rtlog">Oracle Retail RTLOG</option>
                        </select>
                      </label>
                      <label className="field">
                        <span>Source system</span>
                        <select
                          value={uploadSource}
                          disabled={uploadFormat === 'rtlog'}
                          onChange={(event) => {
                            setUploadSource(event.target.value);
                            setUploadProfileId('');
                            setUploadPreview(null);
                            setUploadDeliveryId('');
                          }}
                        >
                          {sources
                            .filter((source) => source.source_system !== 'Shopify')
                            .map((source) => (
                              <option key={source.source_system} value={source.source_system}>
                                {source.source_system}
                                {!source.enabled
                                  ? ' · switched off'
                                  : !source.manual_sync
                                    ? ' · manual upload off'
                                    : ''}
                              </option>
                            ))}
                        </select>
                      </label>
                      {uploadFormat === 'rtlog' && (
                        <label className="field">
                          <span>Currency</span>
                          <input
                            required
                            minLength={3}
                            maxLength={3}
                            value={uploadCurrency}
                            onChange={(event) => {
                              setUploadCurrency(event.target.value.toUpperCase());
                              setUploadPreview(null);
                              setUploadDeliveryId('');
                            }}
                          />
                        </label>
                      )}
                      {uploadFormat === 'csv' && (
                        <label className="field">
                          <span>Feed profile (optional)</span>
                          <select
                            value={uploadProfileId}
                            onChange={(event) => {
                              setUploadProfileId(event.target.value);
                              setUploadPreview(null);
                              setUploadDeliveryId('');
                            }}
                          >
                            <option value="">Use CSV headers as canonical columns</option>
                            {uploadProfiles
                              .filter((profile) => profile.source_system === uploadSource)
                              .map((profile) => (
                                <option key={profile.id} value={profile.id}>
                                  {profile.name}
                                </option>
                              ))}
                          </select>
                        </label>
                      )}
                      <label className="field">
                        <span>{uploadFormat === 'rtlog' ? 'RTLOG file' : 'CSV file'}</span>
                        <input
                          ref={uploadInputRef}
                          required
                          type="file"
                          accept={uploadFormat === 'rtlog' ? '.dat,.rtlog,.txt' : '.csv,text/csv'}
                          onChange={(event) => {
                            const next = event.target.files?.[0] ?? null;
                            setUploadFile(next);
                            setUploadPreview(null);
                            setUploadDeliveryId('');
                            setUploadError(
                              next && next.size > 24 * 1024 * 1024
                                ? 'The file exceeds the 24 MB upload limit.'
                                : ''
                            );
                          }}
                        />
                      </label>
                    </div>
                    {uploadFormat === 'csv' && ingestContract && (
                      <p className="hint">
                        Expected canonical columns for {uploadSource}:{' '}
                        <code>
                          {(uploadSource === 'POSControl'
                            ? ingestContract.pos_control_columns
                            : ingestContract.transaction_columns
                          ).join(', ')}
                        </code>
                        . A feed profile maps source headers to these fields.
                      </p>
                    )}
                    {sources.find((source) => source.source_system === uploadSource)?.enabled ===
                      false && (
                      <Alert tone="warning">This source is switched off in Sources settings.</Alert>
                    )}
                    {sources.find((source) => source.source_system === uploadSource)
                      ?.manual_sync === false && (
                      <Alert tone="warning">
                        Manual upload is switched off for this source in Sources settings.
                      </Alert>
                    )}
                    {uploadPreview && (
                      <Card title="Review before ingesting">
                        <p className="hint">
                          {uploadPreview.format === 'rtlog' ? 'RTLOG' : 'CSV'} preview for{' '}
                          <strong>{uploadSource}</strong>: {n(uploadPreview.records_received)}{' '}
                          records; {n(uploadPreview.valid)} valid and {n(uploadPreview.quarantined)}{' '}
                          expected to be quarantined.
                        </p>
                        {uploadPreview.stores.length > 0 && (
                          <p className="hint">Stores: {uploadPreview.stores.join(', ')}</p>
                        )}
                        {uploadPreview.business_dates.length > 0 && (
                          <p className="hint">
                            Business dates: {uploadPreview.business_dates.join(', ')}
                          </p>
                        )}
                        {uploadPreview.errors.length > 0 && (
                          <Alert tone="warning">{uploadPreview.errors.join(' · ')}</Alert>
                        )}
                        {uploadPreview.warnings.length > 0 && (
                          <Alert tone="warning">
                            {uploadPreview.warnings.slice(0, 10).join(' · ')}
                          </Alert>
                        )}
                      </Card>
                    )}
                    <Button
                      variant="primary"
                      type="submit"
                      disabled={
                        uploadBusy ||
                        !uploadFile ||
                        !sources.find((source) => source.source_system === uploadSource)?.enabled ||
                        !sources.find((source) => source.source_system === uploadSource)
                          ?.manual_sync
                      }
                    >
                      {uploadBusy
                        ? uploadPreview
                          ? 'Ingesting…'
                          : 'Preparing preview…'
                        : uploadPreview
                          ? 'Confirm and ingest'
                          : 'Review file'}
                    </Button>
                  </form>
                )}
              </Card>
              {uploadResult && (
                <Card title="Upload result">
                  <p className="hint">
                    Delivery <code>{uploadResult.delivery_id}</code> · {uploadResult.source_system}
                  </p>
                  <div className="cc-run">
                    <span>
                      {n(uploadResult.records_received)} records ·{' '}
                      {n(uploadResult.records_archived)} archived ·{' '}
                      {n(uploadResult.duplicates_skipped)} duplicates skipped ·{' '}
                      {n(uploadResult.quarantined)} quarantined
                    </span>
                    <b>{n(uploadResult.transactions_created)} transactions</b>
                  </div>
                  {uploadResult.format === 'rtlog' && (
                    <p className="hint">
                      {n(uploadResult.register_events_created)} register events created;{' '}
                      {n(uploadResult.register_events_quarantined)} quarantined;{' '}
                      {n(uploadResult.not_mapped)} transaction records were not mapped.
                    </p>
                  )}
                  {uploadResult.problems.length > 0 && (
                    <Alert tone="warning">
                      {uploadResult.problems.slice(0, 10).join(' · ')}
                      {uploadResult.problems.length > 10 && ' · More parser warnings omitted.'}
                    </Alert>
                  )}
                  {uploadResult.automatic_audit?.enabled && (
                    <div className="stack stack-sm">
                      <b>Automatic audit after import</b>
                      {uploadResult.automatic_audit.runs.map((auditRun) => (
                        <div
                          className="stack stack-sm"
                          key={`${auditRun.business_date}:${auditRun.stores}`}
                        >
                          <p className="hint">
                            {auditRun.business_date} · {n(auditRun.stores)} store(s) ·{' '}
                            {auditRun.error
                              ? auditRun.error
                              : `${n(auditRun.transactions_examined ?? 0)} transactions checked · ${n(auditRun.cases_created ?? 0)} cases raised`}
                          </p>
                          {auditRun.store_days && (
                            <p className="hint">
                              Store days: {n(auditRun.store_days.opened)} opened ·{' '}
                              {n(auditRun.store_days.totaled)} totaled ·{' '}
                              {n(auditRun.store_days.closed)} closed ·{' '}
                              {n(auditRun.store_days.needs_review)} need review ·{' '}
                              {n(auditRun.store_days.failed)} failed
                            </p>
                          )}
                          {auditRun.error && canReconcile && (
                            <Button
                              size="sm"
                              variant="secondary"
                              disabled={reconcileBusy}
                              onClick={() =>
                                void runManualReconciliation(
                                  auditRun.business_date,
                                  auditRun.store_ids
                                )
                              }
                            >
                              {reconcileBusy
                                ? 'Retrying reconciliation…'
                                : `Retry reconciliation for ${auditRun.business_date}`}
                            </Button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </Card>
              )}
              {canReconcile && (
                <Card title="Audit a business day">
                  <p className="hint">
                    Run reconciliation after all expected POS, control, processor, and ledger files
                    for the day have arrived. This checks the selected date across the stores you
                    can access.
                  </p>
                  {reconcileError && <Alert tone="error">{reconcileError}</Alert>}
                  <form
                    className="toolbar"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void runManualReconciliation();
                    }}
                  >
                    <label className="field">
                      <span>Business date</span>
                      <input
                        required
                        type="date"
                        value={reconcileDate}
                        onChange={(event) => setReconcileDate(event.target.value)}
                      />
                    </label>
                    <Button
                      variant="primary"
                      type="submit"
                      disabled={reconcileBusy || !reconcileDate}
                    >
                      {reconcileBusy ? 'Auditing…' : 'Run reconciliation'}
                    </Button>
                  </form>
                  {manualRecon && (
                    <div className="cc-run" role="status">
                      <span>
                        {n(manualRecon.transactions_examined)} transactions examined ·{' '}
                        {n(manualRecon.matched_pairs)} matched pairs
                        {manualRecon.store_days && (
                          <>
                            {' · '}
                            {n(manualRecon.store_days.opened)} store days opened ·{' '}
                            {n(manualRecon.store_days.totaled)} totaled ·{' '}
                            {n(manualRecon.store_days.closed)} closed ·{' '}
                            {n(manualRecon.store_days.needs_review)} need review
                          </>
                        )}
                      </span>
                      <b>{n(manualRecon.cases_created)} cases raised</b>
                    </div>
                  )}
                </Card>
              )}
            </div>
          )}

          {tab === 'runs' && (
            <section className="cc-card">
              <p className="hint" style={{ marginBottom: '.75rem' }}>
                Cases appear when a batch finishes. In production a schedule runs{' '}
                <code>
                  PYTHONPATH=backend python -m apps.operations.batch_job --date YYYY-MM-DD
                </code>{' '}
                (or <code>POST /api/batch/run</code>) after the extract lands.
              </p>
              <Toolbar>
                <input
                  type="date"
                  aria-label="Batch business date from"
                  value={runStart}
                  max={runEnd || undefined}
                  onChange={(event) => {
                    setRunStart(event.target.value);
                    setRunPage(1);
                  }}
                />
                <input
                  type="date"
                  aria-label="Batch business date to"
                  value={runEnd}
                  min={runStart || undefined}
                  onChange={(event) => {
                    setRunEnd(event.target.value);
                    setRunPage(1);
                  }}
                />
                <select
                  aria-label="Batch run status"
                  value={runStatus}
                  onChange={(event) => {
                    setRunStatus(event.target.value);
                    setRunPage(1);
                  }}
                >
                  <option value="">All statuses</option>
                  <option value="Running">Running</option>
                  <option value="Succeeded">Succeeded</option>
                  <option value="Failed">Failed</option>
                </select>
                <Button
                  size="sm"
                  onClick={() => void downloadRunHistory()}
                  disabled={runHistoryExporting || runs.length === 0}
                >
                  {runHistoryExporting ? 'Preparing CSV…' : 'Download filtered CSV'}
                </Button>
              </Toolbar>
              {runHistoryError && <Alert tone="error">{runHistoryError}</Alert>}
              {runHistoryNotice && <Alert tone="warning">{runHistoryNotice}</Alert>}
              {!runHistoryLoading && runs.length === 0 ? (
                <EmptyState title="No batch runs yet">
                  <p>Change the filters or use “Run nightly batch” to create a new run.</p>
                </EmptyState>
              ) : (
                <DataTable
                  columns={[
                    {
                      key: 'c0',
                      header: 'Business date',
                      primary: true,
                      render: (r) => r.business_date,
                    },
                    {
                      key: 'c1',
                      header: 'Status',
                      render: (r) => (
                        <>
                          <span className={`badge badge-sm ${runTone(r.status)}`}>{r.status}</span>
                          {r.error && <small className="pl-err">{r.error}</small>}
                        </>
                      ),
                    },
                    {
                      key: 'c2',
                      header: 'Started',
                      nowrap: true,
                      render: (r) => when(r.started_at),
                    },
                    {
                      key: 'c3',
                      header: 'Loaded',
                      numeric: true,
                      render: (r) => n(r.transactions_loaded),
                    },
                    {
                      key: 'c4',
                      header: 'Matched',
                      numeric: true,
                      render: (r) => n(r.matched_pairs),
                    },
                    {
                      key: 'c5',
                      header: 'Cases',
                      numeric: true,
                      render: (r) => n(r.cases_created),
                    },
                    { key: 'c6', header: 'Trigger', render: (r) => r.trigger },
                    {
                      key: 'c7',
                      header: 'Today’s Store Days',
                      render: (r) =>
                        r.store_day_open_status ? (
                          <>
                            <span className={`badge badge-sm ${runTone(r.store_day_open_status)}`}>
                              {r.store_day_open_status}
                            </span>
                            <small className="act-sub">
                              {r.store_day_open_status === 'Succeeded'
                                ? `${n(r.store_days_opened)} opened · ${n(r.store_days_already_open)} already open`
                                : r.store_day_open_error}
                            </small>
                          </>
                        ) : (
                          'Not run'
                        ),
                    },
                  ]}
                  rows={runHistoryLoading ? null : runs}
                  rowKey={(r) => String(r.id)}
                  footer={
                    <PageNav
                      page={runPage}
                      hasMore={runHasMore}
                      pageSize={runPageSize}
                      onPageSize={(size) => {
                        setRunPageSize(size);
                        setRunPage(1);
                      }}
                      onPage={setRunPage}
                    />
                  }
                />
              )}
            </section>
          )}

          {tab === 'feeds' && (
            <div className="case-panel">
              <section className="cc-card">
                <h3>Feed freshness</h3>
                <p className="hint" style={{ marginBottom: '.6rem' }}>
                  Each source feed is expected on a schedule. Edit schedules and mappings under{' '}
                  <Link href="/settings#integrations">Settings → Integrations</Link>.
                </p>
                {feedHealth.length === 0 ? (
                  <p className="cc-empty">No source-feed profiles are configured.</p>
                ) : (
                  <DataTable
                    columns={[
                      {
                        key: 'c0',
                        header: 'Profile',
                        primary: true,
                        render: (f) => (
                          <>
                            {f.name}
                            <small className="act-sub"> {f.source_system}</small>
                          </>
                        ),
                      },
                      { key: 'c1', header: 'Schedule', render: (f) => f.schedule || 'Not set' },
                      {
                        key: 'c2',
                        header: 'Last receipt',
                        render: (f) => (
                          <>
                            {f.last_received_at ? (
                              <>
                                {when(f.last_received_at)}
                                <small className="act-sub"> {ago(f.age_hours)}</small>
                              </>
                            ) : (
                              '—'
                            )}
                          </>
                        ),
                      },
                      {
                        key: 'c3',
                        header: 'Freshness',
                        render: (f) => (
                          <>
                            <span className={`badge badge-sm ${feedTone(f.state)}`}>{f.state}</span>
                          </>
                        ),
                      },
                    ]}
                    rows={feedHealth}
                    rowKey={(f) => String(f.profile_id)}
                  />
                )}
              </section>
              <section className="cc-card">
                <h3>Source delivery history</h3>
                <p className="hint" style={{ marginBottom: '.6rem' }}>
                  Deliveries are archived read-only before normalization. Search and export the full
                  matching delivery history.
                </p>
                <Toolbar>
                  <select
                    aria-label="Delivery source"
                    value={deliverySource}
                    onChange={(event) => {
                      setDeliverySource(event.target.value);
                      setDeliveryPage(1);
                    }}
                  >
                    <option value="">All sources</option>
                    {sources.map((source) => (
                      <option key={source.source_system} value={source.source_system}>
                        {source.source_system}
                      </option>
                    ))}
                  </select>
                  <input
                    type="date"
                    aria-label="Delivery received from"
                    value={deliveryStart}
                    max={deliveryEnd || undefined}
                    onChange={(event) => {
                      setDeliveryStart(event.target.value);
                      setDeliveryPage(1);
                    }}
                  />
                  <input
                    type="date"
                    aria-label="Delivery received to"
                    value={deliveryEnd}
                    min={deliveryStart || undefined}
                    onChange={(event) => {
                      setDeliveryEnd(event.target.value);
                      setDeliveryPage(1);
                    }}
                  />
                  <input
                    type="search"
                    aria-label="Search delivery IDs"
                    placeholder="Delivery ID"
                    value={deliverySearchDraft}
                    onChange={(event) => setDeliverySearchDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        setDeliveryQuery(deliverySearchDraft.trim());
                        setDeliveryPage(1);
                      }
                    }}
                  />
                  <Button
                    size="sm"
                    onClick={() => {
                      setDeliveryQuery(deliverySearchDraft.trim());
                      setDeliveryPage(1);
                    }}
                  >
                    Search
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => void downloadDeliveryHistory()}
                    disabled={deliveryExporting || deliveries.length === 0}
                  >
                    {deliveryExporting ? 'Preparing CSV…' : 'Download filtered CSV'}
                  </Button>
                </Toolbar>
                {deliveryError && <Alert tone="error">{deliveryError}</Alert>}
                {deliveryNotice && <Alert tone="warning">{deliveryNotice}</Alert>}
                {!deliveryLoading && deliveries.length === 0 ? (
                  <EmptyState title="No source deliveries match">
                    <p>Change the source, date range, or delivery ID search.</p>
                  </EmptyState>
                ) : (
                  <DataTable
                    columns={[
                      {
                        key: 'source',
                        header: 'Source',
                        primary: true,
                        render: (delivery) => delivery.source_system,
                      },
                      {
                        key: 'delivery',
                        header: 'Delivery',
                        render: (delivery) => delivery.delivery_id,
                      },
                      {
                        key: 'received',
                        header: 'Received',
                        nowrap: true,
                        render: (delivery) => when(delivery.received_at),
                      },
                      {
                        key: 'records',
                        header: 'Records',
                        numeric: true,
                        render: (delivery) => n(delivery.records),
                      },
                      {
                        key: 'processed',
                        header: 'Processed',
                        numeric: true,
                        render: (delivery) => n(delivery.processed),
                      },
                      {
                        key: 'quarantined',
                        header: 'Quarantined',
                        numeric: true,
                        render: (delivery) =>
                          delivery.quarantined > 0 ? (
                            <Button
                              size="sm"
                              variant="secondary"
                              aria-label={`Review ${delivery.quarantined} quarantined records from ${delivery.delivery_id}`}
                              onClick={() => reviewDeliveryQuarantine(delivery)}
                            >
                              {n(delivery.quarantined)} review
                            </Button>
                          ) : (
                            0
                          ),
                      },
                      {
                        key: 'error',
                        header: 'Latest error',
                        render: (delivery) => delivery.last_error || '—',
                      },
                    ]}
                    rows={deliveryLoading ? null : deliveries}
                    rowKey={(delivery) => `${delivery.source_system}-${delivery.delivery_id}`}
                    footer={
                      <PageNav
                        page={deliveryPage}
                        hasMore={deliveryHasMore}
                        pageSize={deliveryPageSize}
                        onPageSize={(size) => {
                          setDeliveryPageSize(size);
                          setDeliveryPage(1);
                        }}
                        onPage={setDeliveryPage}
                      />
                    }
                  />
                )}
              </section>
            </div>
          )}

          {tab === 'quarantine' && (
            <section className="cc-card">
              <p className="hint" style={{ marginBottom: '.75rem' }}>
                Records that failed validation are held here instead of being loaded. Fix the source
                and re-deliver them.
              </p>
              <Toolbar count={`${n(quarantineCount)} quarantined records`}>
                <select
                  aria-label="Quarantine source"
                  value={quarantineSource}
                  onChange={(event) => {
                    setQuarantineSource(event.target.value);
                    setQuarantinePage(1);
                  }}
                >
                  <option value="">All sources</option>
                  {sources.map((source) => (
                    <option key={source.source_system} value={source.source_system}>
                      {source.source_system}
                    </option>
                  ))}
                </select>
                <input
                  type="search"
                  aria-label="Filter by delivery ID"
                  placeholder="Delivery ID"
                  value={quarantineDeliveryId}
                  onChange={(event) => {
                    setQuarantineDeliveryId(event.target.value);
                    setQuarantinePage(1);
                  }}
                />
                <input
                  type="date"
                  aria-label="Quarantine received from"
                  value={quarantineStart}
                  max={quarantineEnd || undefined}
                  onChange={(event) => {
                    setQuarantineStart(event.target.value);
                    setQuarantinePage(1);
                  }}
                />
                <input
                  type="date"
                  aria-label="Quarantine received to"
                  value={quarantineEnd}
                  min={quarantineStart || undefined}
                  onChange={(event) => {
                    setQuarantineEnd(event.target.value);
                    setQuarantinePage(1);
                  }}
                />
                <input
                  type="search"
                  aria-label="Search quarantined record ID or reason"
                  placeholder="Record ID or reason"
                  value={quarantineSearchDraft}
                  onChange={(event) => setQuarantineSearchDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      setQuarantineQuery(quarantineSearchDraft.trim());
                      setQuarantinePage(1);
                    }
                  }}
                />
                <Button
                  size="sm"
                  onClick={() => {
                    setQuarantineQuery(quarantineSearchDraft.trim());
                    setQuarantinePage(1);
                  }}
                >
                  Search
                </Button>
                <Button
                  size="sm"
                  onClick={() => void downloadQuarantine()}
                  disabled={quarantineExporting || quarantineRows.length === 0}
                >
                  {quarantineExporting ? 'Preparing CSV…' : 'Download filtered CSV'}
                </Button>
              </Toolbar>
              {quarantineError && <Alert tone="error">{quarantineError}</Alert>}
              {quarantineNotice && <Alert tone="warning">{quarantineNotice}</Alert>}
              {!quarantineLoading && quarantineRows.length === 0 ? (
                <EmptyState title="Nothing in quarantine">
                  <p>Try changing the source, delivery ID, date range, or search phrase.</p>
                </EmptyState>
              ) : (
                <DataTable
                  columns={[
                    {
                      key: 'c0',
                      header: 'Source',
                      primary: true,
                      render: (q) => q.source_system,
                    },
                    { key: 'c1', header: 'Delivery', render: (q) => q.delivery_id },
                    { key: 'c2', header: 'Record', render: (q) => q.source_record_id },
                    {
                      key: 'c3',
                      header: 'Received',
                      nowrap: true,
                      render: (q) => when(q.received_at),
                    },
                    { key: 'c4', header: 'Reason', render: (q) => q.reason },
                  ]}
                  rows={quarantineLoading ? null : quarantineRows}
                  rowKey={(q) => String(q.id)}
                  footer={
                    <PageNav
                      page={quarantinePage}
                      hasMore={quarantineHasMore}
                      pageSize={quarantinePageSize}
                      onPageSize={(size) => {
                        setQuarantinePageSize(size);
                        setQuarantinePage(1);
                      }}
                      onPage={setQuarantinePage}
                    />
                  }
                />
              )}
            </section>
          )}

          {tab === 'demo' && (
            <div className="case-panel">
              <Alert tone="warning">
                Demo tools create or delete sample data. They are for testing and do not run in
                production.
              </Alert>
              <div className="case-panel case-two">
                <section className="cc-card">
                  <h3>Step by step</h3>
                  <p className="hint" style={{ marginBottom: '.6rem' }}>
                    Run the nightly batch does both steps at once. Use these to see each step on its
                    own.
                  </p>
                  <div className="cc-actions">
                    <Button
                      disabled={!!busy}
                      onClick={() =>
                        run('feed', async () => {
                          const { data } = await api.post('/api/ingest/simulate');
                          setIngestRes(data);
                          return '1. Synthetic day ingested.';
                        })
                      }
                    >
                      {busy === 'feed' ? 'Ingesting…' : '1. Ingest synthetic day'}
                    </Button>
                    <Button
                      disabled={!!busy}
                      onClick={() =>
                        run('recon', async () => {
                          const { data } = await api.post('/api/reconcile');
                          setRecon(data);
                          return `2. Reconciled: ${n(data.cases_created)} cases raised.`;
                        })
                      }
                    >
                      {busy === 'recon' ? 'Reconciling…' : '2. Run reconciliation'}
                    </Button>
                  </div>
                  {ingestRes && (
                    <div className="sd-section">
                      <h3>Last ingestion</h3>
                      {Object.entries(ingestRes).map(([src, r]) => (
                        <div key={src} className="cc-run">
                          <b>{src}</b>
                          <span>
                            {r.received} received · {r.archived} archived · {r.duplicates_skipped}{' '}
                            duplicate skipped · {r.quarantined} quarantined
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                  {recon && (
                    <div className="sd-section">
                      <h3>Last reconciliation</h3>
                      <div className="cc-run">
                        <span>
                          Examined {n(recon.transactions_examined)} · matched pairs{' '}
                          {n(recon.matched_pairs)}
                        </span>
                        <b>{n(recon.cases_created)} cases</b>
                      </div>
                      {Object.entries(recon.exceptions).map(([k, v]) => (
                        <div key={k} className="cc-run">
                          <span>{k.replace(/_/g, ' ')}</span>
                          <b>{v}</b>
                        </div>
                      ))}
                      {recon.cases_created > 0 && (
                        <p style={{ margin: '.6rem 0 0' }}>
                          <Link href="/">Open the Command Center →</Link>
                        </p>
                      )}
                    </div>
                  )}
                </section>
                <section className="cc-card">
                  <h3>Volume test</h3>
                  <p className="hint" style={{ marginBottom: '.6rem' }}>
                    Adds 50,000 synthetic cases to test Action Center performance. They have no
                    exceptions, so they cannot be investigated.
                  </p>
                  <div className="cc-actions">
                    <Button
                      disabled={!!busy}
                      onClick={() =>
                        run('vol', async () => {
                          const { data } = await api.post(
                            '/api/action-center/scale-demo?cases=50000'
                          );
                          return `Loaded 50,000 synthetic cases (${n(data.total_cases)} total).`;
                        })
                      }
                    >
                      {busy === 'vol' ? 'Loading…' : 'Load 50k synthetic cases'}
                    </Button>
                    {confirmClr ? (
                      <span className="confirm-inline">
                        Delete all synthetic cases?
                        <Button
                          variant="danger"
                          size="sm"

                          disabled={!!busy}
                          onClick={() => {
                            setConfirmClr(false);
                            run('clr', async () => {
                              const { data } = await api.delete('/api/action-center/scale-demo');
                              return `Removed ${n(data.deleted)} synthetic cases.`;
                            });
                          }}
                        >
                          Yes, remove
                        </Button>
                        <Button
                          size="sm"

                          onClick={() => setConfirmClr(false)}
                        >
                          Cancel
                        </Button>
                      </span>
                    ) : (
                      <Button disabled={!!busy} onClick={() => setConfirmClr(true)}>
                        Remove synthetic cases
                      </Button>
                    )}
                  </div>
                </section>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
