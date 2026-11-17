'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { formatDateTime } from '@/lib/preferences';
import { useToast } from '@/components/Feedback';
import { Alert, Badge, Button, Card, DataTable, Field, PageHeader, Tabs } from '@/components/ui';

type Ref = {
  id: string;
  category: string;
  code: string;
  name: string;
  active: boolean;
  attributes: Record<string, unknown> | null;
  updated_at: string;
};
type SyncResult = {
  status?: string;
  stores: number;
  items: number;
  created: number;
  updated: number;
  deactivated: number;
  skipped_manual: number;
  duplicates: number;
};
type FoundationPage = {
  items: Ref[];
  total: number;
  category_counts: Record<string, number>;
};
type ImportPreview = {
  valid: boolean;
  preview_token?: string;
  run_id?: string;
  errors: { row: number; message: string }[];
  rows: { category: string; code: string; name: string; active: boolean; action: string }[];
  counts: Record<string, number>;
};
type ImportRun = {
  id: string;
  process_name: string;
  file_name: string;
  categories: string[];
  status: string;
  rows_total: number;
  created: number;
  updated: number;
  unchanged: number;
  errors: { row: number; message: string }[];
  error: string | null;
  uploaded_by: string;
  started_at: string;
  finished_at: string | null;
};
type ImportRunPage = { items: ImportRun[]; total: number; offset: number; limit: number };
const IMPORT_RUN_PAGE_SIZE = 30;

/** Each tab shows one or more reference categories. Synced tabs are read-only: Shopify is the source. */
const GROUPS = [
  {
    id: 'stores',
    label: 'Stores',
    cats: ['Store'],
    synced: true,
    blurb: 'Your shop and its locations, synced from Shopify.',
  },
  {
    id: 'products',
    label: 'Products',
    cats: ['Item'],
    synced: true,
    blurb: 'Every product variant in the Shopify catalog, keyed by SKU.',
  },
  {
    id: 'calendar',
    label: 'Calendar',
    cats: ['BusinessCalendar'],
    synced: false,
    blurb: 'Business calendar entries: trading days, holidays and closures.',
  },
  {
    id: 'tenders',
    label: 'Tenders',
    cats: ['Tender'],
    synced: false,
    blurb: 'Payment types accepted at the till.',
  },
  {
    id: 'registers',
    label: 'Registers & cashiers',
    cats: ['Register', 'Cashier'],
    synced: false,
    blurb: 'The tills and the people who use them.',
  },
  {
    id: 'accounts',
    label: 'Accounts & tax',
    cats: ['BankAccount', 'GLAccount', 'TaxCode'],
    synced: false,
    blurb: 'Bank and general-ledger accounts, and tax codes.',
  },
] as const;

const PAGE_SIZE = 200;
const LABELS: Record<string, string> = {
  Store: 'Store',
  Item: 'Product',
  BusinessCalendar: 'Calendar',
  Tender: 'Tender',
  Register: 'Register',
  Cashier: 'Cashier',
  BankAccount: 'Bank account',
  GLAccount: 'GL account',
  TaxCode: 'Tax code',
};
const apiError = (e: unknown, fallback: string) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
const sourceOf = (r: Ref) => (r.attributes?.source as string | undefined) ?? 'Manual';

export function FoundationData() {
  const toast = useToast();
  const [refs, setRefs] = useState<Ref[] | null>(null);
  const [total, setTotal] = useState(0);
  const [categoryCounts, setCategoryCounts] = useState<Record<string, number>>({});
  const [page, setPage] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<string>(GROUPS[0].id);
  const [query, setQuery] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [synced, setSynced] = useState<SyncResult | null>(null);
  const [form, setForm] = useState({ category: '', code: '', name: '' });
  const [importPreview, setImportPreview] = useState<{
    fileName: string;
    processName: string;
    csvText: string;
    result: ImportPreview;
  } | null>(null);
  const [importing, setImporting] = useState(false);
  const [importRuns, setImportRuns] = useState<ImportRun[] | null>(null);
  const [importRunTotal, setImportRunTotal] = useState(0);
  const [importRunQuery, setImportRunQuery] = useState('');
  const [importRunStatus, setImportRunStatus] = useState('');
  const [importRunOffset, setImportRunOffset] = useState(0);
  const [linkedProcessId, setLinkedProcessId] = useState('');
  const [importRunsLoading, setImportRunsLoading] = useState(false);
  const [importRunsExporting, setImportRunsExporting] = useState(false);
  const [selectedImportRun, setSelectedImportRun] = useState<ImportRun | null>(null);
  const [processName, setProcessName] = useState('');
  const importFile = useRef<HTMLInputElement>(null);
  const selectedImportRunRef = useRef<HTMLDivElement>(null);
  const importRunsRequest = useRef(0);

  const group = GROUPS.find((g) => g.id === tab) ?? GROUPS[0];
  const category = form.category || group.cats[0];
  const load = useCallback(() => {
    setPage(0);
    setRefresh((current) => current + 1);
  }, []);

  const loadImportRuns = useCallback(async () => {
    const requestId = ++importRunsRequest.current;
    setImportRunsLoading(true);
    try {
      const { data } = await api.get<ImportRunPage>('/api/foundation/import/runs', {
        params: {
          q: importRunQuery.trim() || undefined,
          status: importRunStatus || undefined,
          offset: importRunOffset,
          limit: IMPORT_RUN_PAGE_SIZE,
        },
      });
      if (requestId !== importRunsRequest.current) return;
      setImportRuns(data.items);
      setImportRunTotal(data.total);
    } catch {
      if (requestId === importRunsRequest.current) setImportRuns(null);
    } finally {
      if (requestId === importRunsRequest.current) setImportRunsLoading(false);
    }
  }, [importRunOffset, importRunQuery, importRunStatus]);

  useEffect(() => {
    const syncProcessFromUrl = () => {
      const processId = new URLSearchParams(window.location.search).get('process_id')?.trim() ?? '';
      setLinkedProcessId(processId);
      setImportRunQuery(processId);
      setImportRunOffset(0);
      setSelectedImportRun(null);
    };
    syncProcessFromUrl();
    window.addEventListener('popstate', syncProcessFromUrl);
    return () => window.removeEventListener('popstate', syncProcessFromUrl);
  }, []);

  useEffect(() => {
    if (!linkedProcessId || !importRuns) return;
    const match = importRuns.find((run) => run.id === linkedProcessId);
    if (match) setSelectedImportRun(match);
  }, [linkedProcessId, importRuns]);

  useEffect(() => {
    if (selectedImportRun) selectedImportRunRef.current?.scrollIntoView({ block: 'nearest' });
  }, [selectedImportRun]);

  const openImportRun = (run: ImportRun) => {
    setSelectedImportRun(run);
    setLinkedProcessId(run.id);
    setImportRunQuery(run.id);
    setImportRunOffset(0);
    const url = new URL(window.location.href);
    url.searchParams.set('process_id', run.id);
    window.history.pushState({}, '', url);
  };

  const clearLinkedProcess = () => {
    if (!linkedProcessId) return;
    setLinkedProcessId('');
    setSelectedImportRun(null);
    const url = new URL(window.location.href);
    url.searchParams.delete('process_id');
    window.history.replaceState(null, '', url);
  };

  const closeImportRun = () => {
    setSelectedImportRun(null);
    setLinkedProcessId('');
    setImportRunQuery('');
    setImportRunOffset(0);
    const url = new URL(window.location.href);
    url.searchParams.delete('process_id');
    window.history.pushState({}, '', url);
  };

  const copyImportRunLink = async () => {
    if (!selectedImportRun) return;
    try {
      await navigator.clipboard.writeText(window.location.href);
      toast('Process link copied.', 'success');
    } catch {
      setError('Could not copy the process link. You can copy it from the address bar.');
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => void loadImportRuns(), 180);
    return () => window.clearTimeout(timer);
  }, [loadImportRuns]);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      if (page === 0) setRefs(null);
      else setLoadingMore(true);
      try {
        const { data } = await api.get<FoundationPage>('/api/foundation', {
          params: {
            categories: group.cats.join(','),
            q: query.trim() || undefined,
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          },
        });
        if (cancelled) return;
        setRefs((current) => (page === 0 ? data.items : [...(current ?? []), ...data.items]));
        setTotal(data.total);
        setCategoryCounts(data.category_counts);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(apiError(e, 'Could not load foundation data.'));
      } finally {
        if (!cancelled) setLoadingMore(false);
      }
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [group, page, query, refresh]);

  const countOf = (cats: readonly string[]) =>
    cats.reduce((sum, current) => sum + (categoryCounts[current] ?? 0), 0);

  const pick = (id: string) => {
    setTab(id);
    setQuery('');
    setPage(0);
    setForm({ category: '', code: '', name: '' });
    setImportPreview(null);
  };
  const downloadTemplate = () => {
    const content = 'category,code,name,active,attributes_json\r\n';
    const url = URL.createObjectURL(new Blob([content], { type: 'text/csv;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `foundation-${group.id}-template.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  const downloadImportRuns = async () => {
    setImportRunsExporting(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (importRunQuery.trim()) params.set('q', importRunQuery.trim());
      if (importRunStatus) params.set('status', importRunStatus);
      const { data, headers } = await api.get(`/api/foundation/import/runs.csv?${params}`, {
        responseType: 'blob',
      });
      downloadBlob('foundation-import-runs.csv', data);
      if (headers?.['x-foundation-import-truncated'] === 'true') {
        setError(
          'The process history export reached its 50,000 row limit. Narrow the filters to export the remaining matches.'
        );
      }
    } catch (e) {
      setError(apiError(e, 'Could not export import process history.'));
    } finally {
      setImportRunsExporting(false);
    }
  };
  const previewImport = async (file?: File) => {
    if (!file) return;
    setError(null);
    setImporting(true);
    try {
      const csvText = await file.text();
      const selectedProcessName = processName.trim() || file.name;
      const { data } = await api.post<ImportPreview>('/api/foundation/import/preview', {
        csv_text: csvText,
        categories: [...group.cats],
        file_name: file.name,
        process_name: selectedProcessName,
      });
      setImportPreview({
        fileName: file.name,
        processName: selectedProcessName,
        csvText,
        result: data,
      });
      void loadImportRuns();
    } catch (e) {
      setError(apiError(e, 'Could not preview this CSV file.'));
    } finally {
      setImporting(false);
      if (importFile.current) importFile.current.value = '';
    }
  };
  const applyImport = async () => {
    if (
      !importPreview?.result.valid ||
      !importPreview.result.preview_token ||
      !importPreview.result.run_id
    )
      return;
    setImporting(true);
    setError(null);
    try {
      const { data } = await api.post('/api/foundation/import', {
        csv_text: importPreview.csvText,
        categories: [...group.cats],
        preview_token: importPreview.result.preview_token,
        run_id: importPreview.result.run_id,
        file_name: importPreview.fileName,
        process_name: importPreview.processName,
      });
      setImportPreview(null);
      load();
      void loadImportRuns();
      toast(
        `Imported reference data: ${data.created} created, ${data.updated} updated, ${data.unchanged} unchanged.`,
        'success'
      );
    } catch (e) {
      if ((e as { response?: { status?: number } })?.response?.status === 409) {
        setImportPreview(null);
        void loadImportRuns();
      }
      setError(apiError(e, 'Could not import this CSV file.'));
    } finally {
      setImporting(false);
    }
  };
  const syncShopify = async () => {
    setSyncing(true);
    setError(null);
    try {
      const { data } = await api.post('/api/ingest/shopify/sync-reference');
      setSynced(data);
      load();
      toast(
        data.status === 'superseded'
          ? 'A newer Shopify sync has already been applied.'
          : 'Shopify stores and products synced.',
        'success'
      );
    } catch (e) {
      setError(apiError(e, 'Shopify sync failed. Check the connection in Configuration.'));
    } finally {
      setSyncing(false);
    }
  };
  const add = async () => {
    try {
      await api.post('/api/foundation', {
        category,
        code: form.code,
        name: form.name,
        active: true,
        attributes: {},
      });
      setForm({ ...form, code: '', name: '' });
      load();
      toast('Saved.', 'success');
    } catch (e) {
      setError(apiError(e, 'Could not save.'));
    }
  };

  return (
    <div className="page-container">
      <PageHeader
        title="Foundation data"
        subtitle="The stores, products, calendar and other reference data that audits and totals rely on."
      />
      <Tabs
        value={tab}
        onChange={pick}
        items={GROUPS.map((g) => ({ id: g.id, label: g.label, count: countOf(g.cats) }))}
      />
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      {group.synced && synced && (
        <Alert tone="success" onDismiss={() => setSynced(null)}>
          Synced {synced.stores} store(s) and {synced.items} product variant(s): {synced.created}{' '}
          new, {synced.updated} changed, {synced.deactivated} no longer in Shopify
          {synced.skipped_manual > 0 && `, ${synced.skipped_manual} left alone (entered by hand)`}
          {synced.duplicates > 0 && `, ${synced.duplicates} skipped (duplicate SKU)`}.
        </Alert>
      )}
      <Card title={group.label}>
        <div className="foundation-body">
          <p className="hint">{group.blurb}</p>
          <div className="form-row">
            <span className="hint">
              {query.trim()
                ? `${total} records match this search.`
                : `${total} records in this tab.`}
            </span>
          </div>
          {group.synced ? (
            <div className="form-row">
              <Button variant="primary" onClick={syncShopify} disabled={syncing}>
                {syncing ? 'Syncing…' : 'Sync from Shopify'}
              </Button>
              <span className="hint">
                Read-only here. Records you add elsewhere are never overwritten, and items removed
                from Shopify are marked inactive.
              </span>
            </div>
          ) : (
            <div className="form-row">
              {group.cats.length > 1 && (
                <Field label="Type">
                  <select
                    value={category}
                    onChange={(e) => setForm({ ...form, category: e.target.value })}
                  >
                    {group.cats.map((c) => (
                      <option key={c} value={c}>
                        {LABELS[c]}
                      </option>
                    ))}
                  </select>
                </Field>
              )}
              <Field label="Code">
                <input
                  value={form.code}
                  onChange={(e) => setForm({ ...form, code: e.target.value })}
                />
              </Field>
              <div className="grow">
                <Field label="Name">
                  <input
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </Field>
              </div>
              <Button
                variant="primary"
                disabled={!form.code.trim() || !form.name.trim()}
                onClick={add}
              >
                Add
              </Button>
              <Button variant="secondary" onClick={downloadTemplate}>
                Download CSV template
              </Button>
              <Field label="Process name">
                <input
                  value={processName}
                  maxLength={100}
                  onChange={(event) => setProcessName(event.target.value)}
                  placeholder="Defaults to the file name"
                />
              </Field>
              <input
                ref={importFile}
                type="file"
                accept=".csv,text/csv"
                hidden
                onChange={(event) => void previewImport(event.target.files?.[0])}
              />
              <Button
                variant="secondary"
                disabled={importing}
                onClick={() => importFile.current?.click()}
              >
                {importing ? 'Reading CSV…' : 'Import CSV'}
              </Button>
            </div>
          )}
          {countOf(group.cats) > 8 && (
            <input
              aria-label={`Search ${group.label}`}
              placeholder={`Search ${group.label.toLowerCase()} by code or name`}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPage(0);
              }}
            />
          )}
          <DataTable
            columns={[
              { key: 'code', header: 'Code', primary: true, render: (r) => r.code },
              { key: 'name', header: 'Name', render: (r) => r.name },
              ...(group.cats.length > 1
                ? [{ key: 'type', header: 'Type', render: (r: Ref) => LABELS[r.category] }]
                : []),
              {
                key: 'source',
                header: 'Source',
                render: (r) => (
                  <Badge variant={sourceOf(r) === 'Shopify' ? 'info' : 'secondary'} size="sm">
                    {sourceOf(r)}
                  </Badge>
                ),
              },
              {
                key: 'status',
                header: 'Status',
                render: (r) => (
                  <Badge variant={r.active ? 'success' : 'secondary'} size="sm">
                    {r.active ? 'Active' : 'Inactive'}
                  </Badge>
                ),
              },
              {
                key: 'updated',
                header: 'Updated',
                nowrap: true,
                render: (r) => formatDateTime(r.updated_at, { dateStyle: 'medium' }),
              },
            ]}
            rows={refs}
            rowKey={(r) => r.id}
            emptyTitle={`No ${group.label.toLowerCase()} yet`}
            emptyHint={
              group.synced ? 'Use "Sync from Shopify" to load them.' : 'Add the first one above.'
            }
          />
          {refs && refs.length < total && (
            <p className="hint">
              Showing {refs.length} of {total} matching record(s).
              <Button
                variant="secondary"
                onClick={() => setPage((current) => current + 1)}
                disabled={loadingMore}
              >
                {loadingMore ? 'Loading…' : 'Load more'}
              </Button>
            </p>
          )}
        </div>
      </Card>
      {importPreview && (
        <Card title={`Import preview · ${importPreview.fileName}`}>
          {importPreview.result.valid ? (
            <>
              <p className="hint">
                Review the changes below. The import is applied as one operation; any validation or
                database conflict leaves all records unchanged.
              </p>
              <div className="toolbar-count">
                {importPreview.result.counts.create ?? 0} create ·{' '}
                {importPreview.result.counts.update ?? 0} update ·{' '}
                {importPreview.result.counts.unchanged ?? 0} unchanged
              </div>
              <div className="table-scroll" style={{ maxHeight: '24rem', overflowY: 'auto' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Action</th>
                      <th>Type</th>
                      <th>Code</th>
                      <th>Name</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {importPreview.result.rows.map((row) => (
                      <tr key={`${row.category}:${row.code}`}>
                        <td>{row.action}</td>
                        <td>{LABELS[row.category] ?? row.category}</td>
                        <td>{row.code}</td>
                        <td>{row.name}</td>
                        <td>{row.active ? 'Active' : 'Inactive'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="row-actions" style={{ marginTop: '1rem' }}>
                <Button disabled={importing} onClick={() => void applyImport()}>
                  {importing ? 'Importing…' : 'Apply import'}
                </Button>
                <Button
                  variant="secondary"
                  disabled={importing}
                  onClick={() => setImportPreview(null)}
                >
                  Cancel
                </Button>
              </div>
            </>
          ) : (
            <>
              <Alert tone="error">
                The file has validation issues. Fix these rows and upload it again.
              </Alert>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Row</th>
                      <th>Issue</th>
                    </tr>
                  </thead>
                  <tbody>
                    {importPreview.result.errors.map((item, index) => (
                      <tr key={`${item.row}:${index}`}>
                        <td>{item.row}</td>
                        <td>{item.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Button variant="secondary" onClick={() => setImportPreview(null)}>
                Close preview
              </Button>
            </>
          )}
        </Card>
      )}
      {importRuns !== null && (
        <Card title="CSV import history">
          <p className="hint">
            Named processes retain their upload status and validation issues for Finance and IT.
          </p>
          <div className="form-row">
            <Field label="Find process">
              <input
                value={importRunQuery}
                maxLength={100}
                placeholder="Process, file, uploader or process ID"
                onChange={(event) => {
                  clearLinkedProcess();
                  setImportRunQuery(event.target.value);
                  setImportRunOffset(0);
                }}
              />
            </Field>
            <Field label="Status">
              <select
                value={importRunStatus}
                onChange={(event) => {
                  clearLinkedProcess();
                  setImportRunStatus(event.target.value);
                  setImportRunOffset(0);
                }}
              >
                <option value="">All statuses</option>
                {['Previewed', 'Validation failed', 'Succeeded', 'Stale', 'Failed'].map(
                  (status) => (
                    <option key={status} value={status}>
                      {status}
                    </option>
                  )
                )}
              </select>
            </Field>
            <Button
              variant="secondary"
              disabled={!importRunTotal || importRunsExporting}
              onClick={() => void downloadImportRuns()}
            >
              {importRunsExporting ? 'Preparing CSV…' : 'Download all matching processes'}
            </Button>
            <span className="hint">
              {importRunsLoading
                ? 'Updating process history…'
                : `${importRunTotal} process(es) match.`}
            </span>
          </div>
          {linkedProcessId && !importRunsLoading && importRunTotal === 0 && (
            <Alert tone="warning">No import process matches this link.</Alert>
          )}
          <DataTable
            columns={[
              { key: 'process', header: 'Process', primary: true, render: (r) => r.process_name },
              { key: 'file', header: 'File', render: (r) => r.file_name },
              {
                key: 'categories',
                header: 'Data types',
                render: (r) => r.categories.map((value) => LABELS[value] ?? value).join(', '),
              },
              {
                key: 'status',
                header: 'Status',
                render: (r) => (
                  <Badge
                    variant={
                      r.status === 'Succeeded'
                        ? 'success'
                        : ['Failed', 'Validation failed'].includes(r.status)
                          ? 'error'
                          : r.status === 'Stale'
                            ? 'warning'
                            : 'secondary'
                    }
                    size="sm"
                  >
                    {r.status}
                  </Badge>
                ),
              },
              {
                key: 'rows',
                header: 'Rows',
                numeric: true,
                render: (r) =>
                  r.status === 'Succeeded'
                    ? `${r.created} created · ${r.updated} updated · ${r.unchanged} unchanged`
                    : r.rows_total,
              },
              {
                key: 'issue',
                header: 'Issues',
                render: (r) => (
                  <div className="stack stack-xs">
                    {r.error && <span>{r.error}</span>}
                    {r.errors.length > 0 && (
                      <Button variant="secondary" onClick={() => openImportRun(r)}>
                        Review {r.errors.length} row issue{r.errors.length === 1 ? '' : 's'}
                      </Button>
                    )}
                    {!r.error && r.errors.length === 0 && '—'}
                  </div>
                ),
              },
              {
                key: 'started',
                header: 'Started',
                nowrap: true,
                render: (r) => formatDateTime(r.started_at),
              },
              { key: 'by', header: 'Uploaded by', render: (r) => r.uploaded_by },
              {
                key: 'review',
                header: 'Process detail',
                render: (r) => (
                  <Button
                    variant="secondary"
                    aria-label={`Open import process ${r.process_name}`}
                    onClick={() => openImportRun(r)}
                  >
                    Open process
                  </Button>
                ),
              },
            ]}
            rows={importRunsLoading ? null : importRuns}
            rowKey={(r) => r.id}
            emptyTitle="No CSV imports yet"
            emptyHint="No processes match these filters. Previewing a file creates a process record with its result and validation issues."
          />
          <div className="row-actions" style={{ marginTop: '1rem' }}>
            <Button
              variant="secondary"
              disabled={importRunsLoading || importRunOffset === 0}
              onClick={() =>
                setImportRunOffset((current) => Math.max(0, current - IMPORT_RUN_PAGE_SIZE))
              }
            >
              Previous
            </Button>
            <span className="hint">
              {importRunTotal === 0
                ? 'No matching processes'
                : `Showing ${importRunOffset + 1}–${Math.min(importRunOffset + IMPORT_RUN_PAGE_SIZE, importRunTotal)} of ${importRunTotal}`}
            </span>
            <Button
              variant="secondary"
              disabled={
                importRunsLoading || importRunOffset + IMPORT_RUN_PAGE_SIZE >= importRunTotal
              }
              onClick={() => setImportRunOffset((current) => current + IMPORT_RUN_PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </Card>
      )}
      {selectedImportRun && (
        <div ref={selectedImportRunRef} id="import-process">
          <Card title={`Import process · ${selectedImportRun.process_name}`}>
            <p className="hint">
              {selectedImportRun.file_name} ·{' '}
              {selectedImportRun.categories.map((value) => LABELS[value] ?? value).join(', ')}
            </p>
            <div className="cluster" style={{ marginBlock: 'var(--space-sm)' }}>
              <Badge
                variant={
                  selectedImportRun.status === 'Succeeded'
                    ? 'success'
                    : ['Failed', 'Validation failed'].includes(selectedImportRun.status)
                      ? 'error'
                      : selectedImportRun.status === 'Stale'
                        ? 'warning'
                        : 'secondary'
                }
              >
                {selectedImportRun.status}
              </Badge>
              <span className="hint">Process ID: {selectedImportRun.id}</span>
              <span className="hint">{selectedImportRun.rows_total} row(s) received</span>
              <span className="hint">
                Started {formatDateTime(selectedImportRun.started_at)} ·{' '}
                {selectedImportRun.uploaded_by}
              </span>
              {selectedImportRun.finished_at && (
                <span className="hint">
                  Finished {formatDateTime(selectedImportRun.finished_at)}
                </span>
              )}
              <span className="hint">
                {selectedImportRun.created} created · {selectedImportRun.updated} updated ·{' '}
                {selectedImportRun.unchanged} unchanged
              </span>
            </div>
            {selectedImportRun.error && <Alert tone="error">{selectedImportRun.error}</Alert>}
            {selectedImportRun.errors.length > 0 ? (
              <div className="table-scroll" style={{ maxHeight: '24rem', overflowY: 'auto' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Row</th>
                      <th>Issue</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedImportRun.errors.map((issue, index) => (
                      <tr key={`${issue.row}:${index}`}>
                        <td>{issue.row}</td>
                        <td>{issue.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="hint">No row-level validation issues were recorded for this process.</p>
            )}
            <div className="row-actions">
              <Button variant="secondary" onClick={() => void copyImportRunLink()}>
                Copy process link
              </Button>
              <Button variant="secondary" onClick={closeImportRun}>
                Close process
              </Button>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
