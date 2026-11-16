'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, ExportBatchRow, ExportDest } from '@/lib/api';
import { SkeletonRows, useToast } from '@/components/Feedback';
import { useDataChanged } from '@/lib/sync';
import Link from 'next/link';
import { useCanOpen } from '@/components/MeProvider';
import { Alert, Button, PageNav, Toolbar } from '@/components/ui';
import { formatDateTime, formatNumber } from '@/lib/preferences';
import { downloadBlob } from '@/lib/csv';

interface TrackRow {
  object_id: string;
  record_ref: string;
  case_number: string;
  store_id: string;
  business_date: string;
  case_status: string;
  state: 'delivered' | 'in_batch' | 'waiting' | 'not_eligible';
  reason: string | null;
  batch_id: string | null;
  batch_status: string | null;
  delivered_at: string | null;
  backpost: boolean;
  backpost_reason: string | null;
}
interface Track {
  kind: 'records' | 'stream';
  summary: Record<string, number>;
  items: TrackRow[];
  total: number;
  page: number;
  pages: number;
}
const STATE_LABEL: Record<TrackRow['state'], string> = {
  delivered: 'Delivered',
  in_batch: 'In a batch',
  waiting: 'Waiting',
  not_eligible: 'Not eligible',
};
const STATE_TONE: Record<TrackRow['state'], string> = {
  delivered: 'badge-success',
  in_batch: 'badge-error',
  waiting: 'badge-warning',
  not_eligible: 'badge-secondary',
};

const DATASETS = [
  {
    key: 'resolutions',
    label: 'Resolutions',
    note: 'Closed, validated cases with decision and disposition',
  },
  {
    key: 'adjustments',
    label: 'Adjustments',
    note: 'Approved financial adjustments (journal lines for ERP/GL)',
  },
  {
    key: 'audited_totals',
    label: 'Audited totals',
    note: 'Current versioned totals from closed, reconciled store days',
  },
  {
    key: 'audited_transactions',
    label: 'Audited transactions',
    note: 'Transaction and item details after transaction checks or Store Day closure',
  },
  {
    key: 'transaction_corrections',
    label: 'Transaction corrections',
    note: 'Approved transaction corrections, with reviewer and rationale',
  },
  { key: 'audit', label: 'Audit trail', note: 'Audit events since the last delivery, with hashes' },
];
const tone = (s: string) =>
  s === 'Acknowledged'
    ? 'badge-success'
    : s === 'Failed'
      ? 'badge-error'
      : s === 'Sent'
        ? 'badge-primary'
        : 'badge-secondary';

export default function ExportsPage() {
  const [dests, setDests] = useState<ExportDest[]>([]);
  const [batches, setBatches] = useState<ExportBatchRow[]>([]);
  const [batchDestination, setBatchDestination] = useState('');
  const [batchDataset, setBatchDataset] = useState('');
  const [batchStatus, setBatchStatus] = useState('');
  const [batchStart, setBatchStart] = useState('');
  const [batchEnd, setBatchEnd] = useState('');
  const [batchSearchDraft, setBatchSearchDraft] = useState('');
  const [batchQuery, setBatchQuery] = useState('');
  const [batchPage, setBatchPage] = useState(1);
  const [batchPageSize, setBatchPageSize] = useState(50);
  const [batchHasMore, setBatchHasMore] = useState(false);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchExporting, setBatchExporting] = useState(false);
  const [destId, setDestId] = useState('');
  const [dataset, setDataset] = useState('resolutions');
  const [preview, setPreview] = useState<{
    count: number;
    sample: any[];
    waiting: { current: number; backposted: number; business_dates: string[] };
  } | null>(null);
  const [scope, setScope] = useState<'all' | 'current' | 'backposts'>('all');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [track, setTrack] = useState<Track | null>(null);
  const [trackState, setTrackState] = useState('');
  const [trackQ, setTrackQ] = useState('');
  const [trackQd, setTrackQd] = useState('');
  const [trackPage, setTrackPage] = useState(1);
  const [trackExporting, setTrackExporting] = useState(false);
  const [cancelFor, setCancelFor] = useState<string | null>(null);
  const [resolutionEvidence, setResolutionEvidence] = useState('');
  const [recoverFor, setRecoverFor] = useState<string | null>(null);
  const [recoveryEvidence, setRecoveryEvidence] = useState('');
  const [workerStopped, setWorkerStopped] = useState(false);
  const [ackFor, setAckFor] = useState<string | null>(null);
  const [ackRef, setAckRef] = useState('');
  const toast = useToast();
  const canOpen = useCanOpen();

  const load = useCallback(async () => {
    try {
      const { data: destinations } = await api.get('/api/exports/destinations');
      setDests(destinations);
      setDestId((cur) => cur || destinations.find((x: ExportDest) => x.enabled)?.id || '');
      setErr(null);
    } catch (e: any) {
      setErr(
        e?.response?.status === 403 ? 'Your role cannot view exports.' : 'Could not load exports.'
      );
    } finally {
      setLoaded(true);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const loadBatchHistory = useCallback(async () => {
    setBatchLoading(true);
    try {
      const { data } = await api.get('/api/exports/batches/history', {
        params: {
          destination_id: batchDestination || undefined,
          dataset: batchDataset || undefined,
          status: batchStatus || undefined,
          start: batchStart || undefined,
          end: batchEnd || undefined,
          q: batchQuery || undefined,
          page: batchPage,
          page_size: batchPageSize,
        },
      });
      setBatches(data.items);
      setBatchHasMore(data.has_more);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? 'Could not load export history.');
    } finally {
      setBatchLoading(false);
    }
  }, [
    batchDestination,
    batchDataset,
    batchStatus,
    batchStart,
    batchEnd,
    batchQuery,
    batchPage,
    batchPageSize,
  ]);
  useEffect(() => {
    loadBatchHistory();
  }, [loadBatchHistory]);
  const downloadBatchHistory = async () => {
    setBatchExporting(true);
    setErr(null);
    try {
      const { data, headers } = await api.get('/api/exports/batches/history.csv', {
        params: {
          destination_id: batchDestination || undefined,
          dataset: batchDataset || undefined,
          status: batchStatus || undefined,
          start: batchStart || undefined,
          end: batchEnd || undefined,
          q: batchQuery || undefined,
        },
        responseType: 'blob',
      });
      downloadBlob('export-history.csv', data);
      if (headers?.['x-export-history-truncated'] === 'true') {
        setErr('The CSV reached 50,000 batches. Narrow the filters to export the full history.');
      }
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? 'Could not export batch history.');
    } finally {
      setBatchExporting(false);
    }
  };
  const loadTrack = useCallback(async () => {
    if (!destId) {
      setTrack(null);
      return;
    }
    try {
      const { data } = await api.get('/api/exports/tracking', {
        params: {
          destination_id: destId,
          dataset,
          state: trackState,
          q: trackQd,
          page: trackPage,
          page_size: 25,
        },
      });
      setTrack(data);
    } catch {
      setTrack(null);
    }
  }, [destId, dataset, trackState, trackQd, trackPage]);
  useEffect(() => {
    loadTrack();
  }, [loadTrack]);
  useEffect(() => {
    const t = setTimeout(() => setTrackQd(trackQ), 300);
    return () => clearTimeout(t);
  }, [trackQ]);
  useEffect(() => {
    setTrackPage(1);
  }, [destId, dataset, trackState, trackQd]);
  useDataChanged(() => {
    loadTrack();
  });
  useEffect(() => {
    setPreview(null);
  }, [destId, dataset, scope]);
  useEffect(() => {
    if (dataset === 'audit') setScope('all');
  }, [dataset]);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? 'That did not work.');
    }
    setBusy(false);
    load();
    loadTrack();
    loadBatchHistory();
  };
  const runExport = (dry: boolean) =>
    act(async () => {
      const { data } = await api.post('/api/exports/run', {
        destination_id: destId,
        dataset,
        dry_run: dry,
        scope,
      });
      if (dry) setPreview({ count: data.count, sample: data.sample, waiting: data.waiting });
      else
        toast(
          data.message ?? `Export ${data.status.toLowerCase()}: ${data.record_count} record(s).`,
          'success'
        );
    });
  const download = async (b: ExportBatchRow) => {
    const { data } = await api.get(`/api/exports/batches/${b.id}/payload`, {
      responseType: 'blob',
    });
    const a = document.createElement('a');
    const url = URL.createObjectURL(data);
    a.href = url;
    a.download = `${b.dataset}-${b.id.slice(0, 8)}`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const downloadTracking = async () => {
    if (!destId) return;
    setTrackExporting(true);
    setErr(null);
    try {
      const { data, headers } = await api.get('/api/exports/tracking.csv', {
        params: {
          destination_id: destId,
          dataset,
          state: trackState,
          q: trackQd,
        },
        responseType: 'blob',
      });
      downloadBlob(`export-tracking-${dataset}.csv`, data);
      if (headers?.['x-tracking-truncated'] === 'true') {
        setErr(
          'The tracking CSV reached 50,000 rows. Narrow the state or search filter to export more precisely.'
        );
      }
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? 'Could not download export tracking.');
    } finally {
      setTrackExporting(false);
    }
  };

  const enabled = dests.filter((d) => d.enabled);
  const dest = dests.find((d) => d.id === destId);
  const ds = DATASETS.find((d) => d.key === dataset)!;

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1>Exports</h1>
          <p className="page-sub">Send results to downstream systems. Every export is audited.</p>
        </div>
      </div>
      {err && (
        <Alert
          tone="error"
          action={
            <Button size="sm" className="alert-close" onClick={load}>
              Retry
            </Button>
          }
        >
          {err}
        </Alert>
      )}

      <section className="cc-card">
        <h3>Run an export</h3>
        <p className="hint" style={{ marginBottom: '.6rem' }}>
          1. Choose what to send
        </p>
        <div className="seg-cards" role="radiogroup" aria-label="Dataset">
          {DATASETS.map((d) => (
            <button
              key={d.key}
              role="radio"
              aria-checked={dataset === d.key}
              className={`seg-card ${dataset === d.key ? 'on' : ''}`}
              onClick={() => setDataset(d.key)}
            >
              <b>{d.label}</b>
              <small>{d.note}</small>
            </button>
          ))}
        </div>
        <p className="hint" style={{ margin: '.9rem 0 .5rem' }}>
          2. Choose where to send it
        </p>
        {enabled.length === 0 ? (
          <Alert tone="warning">
            No enabled destination. Add or enable one in Configuration → Export destinations.
          </Alert>
        ) : (
          <div className="form-row">
            <label className="field">
              Destination
              <select value={destId} onChange={(e) => setDestId(e.target.value)}>
                {enabled.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({d.kind === 'webhook' ? 'webhook' : 'file'}, {d.format.toUpperCase()})
                  </option>
                ))}
              </select>
            </label>
            <button
              className="btn-secondary"
              disabled={busy || !destId}
              onClick={() => runExport(true)}
            >
              Preview
            </button>
            <button
              className="btn-primary"
              disabled={busy || !destId || (preview !== null && preview.count === 0)}
              title={preview?.count === 0 ? 'Nothing to export' : ''}
              onClick={() => runExport(false)}
            >
              {busy ? 'Working…' : `Export ${ds.label.toLowerCase()}`}
            </button>
          </div>
        )}
        {dataset !== 'audit' && enabled.length > 0 && (
          <div style={{ marginTop: '.75rem' }}>
            <span className="hint">Which records</span>
            <div
              className="cc-seg"
              role="radiogroup"
              aria-label="Which records to export"
              style={{ marginLeft: '.5rem' }}
            >
              {(
                [
                  ['all', 'Everything waiting'],
                  ['current', 'Current day only'],
                  ['backposts', 'Back-posts only'],
                ] as const
              ).map(([k, label]) => (
                <button
                  key={k}
                  role="radio"
                  aria-checked={scope === k}
                  className={scope === k ? 'on' : ''}
                  onClick={() => setScope(k)}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="hint" style={{ marginTop: '.4rem' }}>
              A <b>back-post</b> is a record that resolved after its business day ended, or after
              that day was already exported. Each one carries its original{' '}
              <code>business_date</code>, <code>backpost</code> and a reason, so the receiving
              ledger posts it to the right day or period and not to today.
            </p>
          </div>
        )}
        <p className="hint" style={{ marginTop: '.6rem' }}>
          Only records not yet delivered to {dest ? <b>{dest.name}</b> : 'this destination'} are
          included, up to 5,000 per batch. Preview first to see what will be sent.
        </p>
        {preview && (
          <div className="sd-section">
            {preview.count === 0 ? (
              <div className="empty-state">
                <b>Nothing to export</b>
                <p>
                  Everything in {ds.label.toLowerCase()} has already been delivered to this
                  destination.
                </p>
              </div>
            ) : (
              <>
                <b>{formatNumber(preview.count)} record(s) ready.</b>
                {preview.waiting && dataset !== 'audit' && (
                  <span className="hint">
                    {' '}
                    Waiting overall: {formatNumber(preview.waiting.current)} current ·{' '}
                    {formatNumber(preview.waiting.backposted)} back-posted
                    {preview.waiting.backposted > 0
                      ? ` (business dates ${preview.waiting.business_dates.join(', ')})`
                      : ''}
                    .
                  </span>
                )}
                <span className="hint"> Showing a sample.</span>
                <pre className="ex-pre">{JSON.stringify(preview.sample, null, 1)}</pre>
              </>
            )}
          </div>
        )}
      </section>

      <section className="cc-card" aria-labelledby="track-h">
        <h3 id="track-h">
          What has been exported{' '}
          <small>
            · {ds.label} to {dest?.name ?? 'the selected destination'}
          </small>
        </h3>
        {!destId ? (
          <p className="cc-empty">
            Choose an enabled destination above to see what has and has not been sent to it.
          </p>
        ) : !track ? (
          <SkeletonRows rows={3} label="Loading export tracking" />
        ) : track.kind === 'stream' ? (
          <>
            <div className="ac-tiles">
              <div className="ac-tile">
                <small>Exported through</small>
                <b>#{formatNumber(track.summary.last_exported_seq)}</b>
                <em>of {formatNumber(track.summary.head_seq)} audit events</em>
              </div>
              <div className="ac-tile">
                <small>Waiting</small>
                <b>{formatNumber(track.summary.waiting_events)}</b>
                <em>events not yet delivered</em>
              </div>
            </div>
            <Button size="sm" onClick={() => void downloadTracking()} disabled={trackExporting}>
              {trackExporting ? 'Preparing CSV…' : 'Download stream status CSV'}
            </Button>
          </>
        ) : (
          <>
            <div className="ac-tiles">
              {(
                [
                  ['delivered', 'Delivered', 'Sent to this destination'],
                  ['in_batch', 'In a batch', 'Reserved by a failed or in-progress batch'],
                  ['waiting', 'Waiting', null],
                  ['not_eligible', 'Not eligible', 'Not exported, with the reason'],
                ] as const
              ).map(([k, label, hint]) => (
                <button
                  key={k}
                  className={`ac-tile ${k === 'in_batch' && track.summary.in_batch > 0 ? 'bad' : ''}`}
                  aria-pressed={trackState === k}
                  onClick={() => setTrackState(trackState === k ? '' : k)}
                >
                  <small>{label}</small>
                  <b>{formatNumber(track.summary[k] ?? 0)}</b>
                  <em>
                    {k === 'waiting'
                      ? `${formatNumber(track.summary.waiting_backposts ?? 0)} are back-posts`
                      : hint}
                  </em>
                </button>
              ))}
            </div>
            <div className="toolbar" style={{ margin: '.75rem 0' }}>
              <input
                className="grow"
                type="search"
                value={trackQ}
                onChange={(e) => setTrackQ(e.target.value)}
                placeholder="Search record, store or date"
                aria-label="Search export tracking"
              />
              {(trackState || trackQ) && (
                <button
                  className="btn-secondary btn-sm"
                  onClick={() => {
                    setTrackState('');
                    setTrackQ('');
                  }}
                >
                  Clear filters
                </button>
              )}
              <span className="toolbar-count">{formatNumber(track.total)} record(s)</span>
              <Button
                size="sm"
                onClick={() => void downloadTracking()}
                disabled={trackExporting || track.total === 0}
              >
                {trackExporting ? 'Preparing CSV…' : 'Download matching CSV'}
              </Button>
            </div>
            {track.items.length === 0 ? (
              <p className="cc-empty">No records match.</p>
            ) : (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Record</th>
                      <th>Store</th>
                      <th>Business date</th>
                      <th>State</th>
                      <th>Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {track.items.map((r) => (
                      <tr key={r.object_id}>
                        <td className="font-medium">
                          {dataset === 'audited_transactions' ? (
                            <Link href={`/transactions/${encodeURIComponent(r.object_id)}`}>
                              {r.record_ref}
                            </Link>
                          ) : dataset === 'audited_totals' ? (
                            <Link
                              href={`/store-days/${encodeURIComponent(r.store_id)}/${encodeURIComponent(r.business_date)}`}
                            >
                              {r.record_ref}
                            </Link>
                          ) : (
                            r.record_ref
                          )}
                        </td>
                        <td>{r.store_id}</td>
                        <td>{r.business_date}</td>
                        <td>
                          <span className={`badge badge-sm ${STATE_TONE[r.state]}`}>
                            {STATE_LABEL[r.state]}
                          </span>
                          {r.backpost && (
                            <span
                              className="badge badge-sm badge-info"
                              style={{ marginLeft: '.35rem' }}
                              title={r.backpost_reason ?? ''}
                            >
                              Back-post
                            </span>
                          )}
                        </td>
                        <td>
                          {r.state === 'delivered'
                            ? `Batch ${r.batch_id?.slice(0, 8)}${r.batch_status === 'Acknowledged' ? ' · acknowledged' : ' · awaiting acknowledgment'}${r.delivered_at ? ` · ${formatDateTime(r.delivered_at)}` : ''}`
                            : r.state === 'in_batch'
                              ? `Batch ${r.batch_id?.slice(0, 8)} · ${r.reason}`
                              : r.state === 'waiting'
                                ? (r.backpost_reason ?? 'Ready for the next export')
                                : r.reason}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {track.pages > 1 && (
              <div className="ac-pager">
                <span>
                  Page {track.page} of {formatNumber(track.pages)}
                </span>
                <span className="ac-spacer" />
                <button
                  className="btn-secondary btn-small"
                  disabled={track.page <= 1}
                  onClick={() => setTrackPage(track.page - 1)}
                >
                  Previous
                </button>
                <button
                  className="btn-secondary btn-small"
                  disabled={track.page >= track.pages}
                  onClick={() => setTrackPage(track.page + 1)}
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </section>

      <section>
        <h2 className="act-day">Export history</h2>
        <Toolbar>
          <select
            aria-label="Export history destination"
            value={batchDestination}
            onChange={(event) => {
              setBatchDestination(event.target.value);
              setBatchPage(1);
            }}
          >
            <option value="">All destinations</option>
            {dests.map((destination) => (
              <option key={destination.id} value={destination.id}>
                {destination.name}
              </option>
            ))}
          </select>
          <select
            aria-label="Export history dataset"
            value={batchDataset}
            onChange={(event) => {
              setBatchDataset(event.target.value);
              setBatchPage(1);
            }}
          >
            <option value="">All datasets</option>
            {DATASETS.map((item) => (
              <option key={item.key} value={item.key}>
                {item.label}
              </option>
            ))}
          </select>
          <select
            aria-label="Export history status"
            value={batchStatus}
            onChange={(event) => {
              setBatchStatus(event.target.value);
              setBatchPage(1);
            }}
          >
            <option value="">All statuses</option>
            {['Pending', 'Sent', 'Acknowledged', 'Failed'].map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
          <input
            type="date"
            aria-label="Export created from"
            value={batchStart}
            max={batchEnd || undefined}
            onChange={(event) => {
              setBatchStart(event.target.value);
              setBatchPage(1);
            }}
          />
          <input
            type="date"
            aria-label="Export created to"
            value={batchEnd}
            min={batchStart || undefined}
            onChange={(event) => {
              setBatchEnd(event.target.value);
              setBatchPage(1);
            }}
          />
          <input
            type="search"
            aria-label="Search export reference, batch ID, or error"
            placeholder="Batch ID, reference, or error"
            value={batchSearchDraft}
            onChange={(event) => setBatchSearchDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                setBatchQuery(batchSearchDraft.trim());
                setBatchPage(1);
              }
            }}
          />
          <Button
            size="sm"
            onClick={() => {
              setBatchQuery(batchSearchDraft.trim());
              setBatchPage(1);
            }}
          >
            Search
          </Button>
          <Button
            size="sm"
            onClick={() => void downloadBatchHistory()}
            disabled={batchExporting || batches.length === 0}
          >
            {batchExporting ? 'Preparing CSV…' : 'Download filtered CSV'}
          </Button>
        </Toolbar>
        {!loaded ? (
          <SkeletonRows rows={5} label="Loading exports" />
        ) : batchLoading ? (
          <SkeletonRows rows={5} label="Loading export history" />
        ) : batches.length === 0 ? (
          <div className="empty-state">
            <b>No exports yet</b>
            <p>
              Change the filters or preview a dataset above, then export it. Each delivery appears
              here with its status and fingerprint.
            </p>
          </div>
        ) : (
          <>
            <div className="table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Destination</th>
                    <th>Dataset</th>
                    <th className="num">Records</th>
                    <th>Status</th>
                    <th>Reference</th>
                    <th>Fingerprint</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {batches.map((b) => (
                    <tr key={b.id}>
                      <td className="text-sm nowrap">{formatDateTime(b.created_at)}</td>
                      <td className="font-medium">{b.destination}</td>
                      <td>{b.dataset}</td>
                      <td className="num">
                        {formatNumber(b.record_count)}
                        {b.backposted_count > 0 && (
                          <small className="act-sub" style={{ display: 'block' }}>
                            {formatNumber(b.backposted_count)} back-posted
                          </small>
                        )}
                      </td>
                      <td>
                        <span className={`badge badge-sm ${tone(b.status)}`}>{b.status}</span>
                        {b.error && (
                          <small style={{ display: 'block', color: 'var(--error)' }}>
                            {b.error}
                          </small>
                        )}
                      </td>
                      <td
                        className="text-sm"
                        style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }}
                      >
                        {b.response_ref ?? '-'}
                      </td>
                      <td>
                        <code title={b.payload_hash ?? ''}>
                          {b.payload_hash?.slice(0, 10) ?? '-'}
                        </code>
                      </td>
                      <td>
                        <div className="action-buttons">
                          <Button size="sm" onClick={() => download(b)}>
                            Download
                          </Button>
                          {b.status === 'Pending' &&
                            (recoverFor === b.id ? (
                              <span className="confirm-inline">
                                Verify the delivery worker has stopped, then check the downstream
                                outcome.
                                <input
                                  value={recoveryEvidence}
                                  onChange={(e) => setRecoveryEvidence(e.target.value)}
                                  placeholder="Evidence worker stopped"
                                  aria-label="Evidence worker stopped"
                                />
                                <label>
                                  <input
                                    type="checkbox"
                                    checked={workerStopped}
                                    onChange={(e) => setWorkerStopped(e.target.checked)}
                                  />{' '}
                                  Worker stopped
                                </label>
                                <button
                                  className="btn-small btn-danger"
                                  disabled={busy || !workerStopped || !recoveryEvidence.trim()}
                                  onClick={() =>
                                    act(async () => {
                                      await api.post(`/api/exports/batches/${b.id}/recover`, {
                                        worker_stopped: workerStopped,
                                        evidence: recoveryEvidence,
                                      });
                                      setRecoverFor(null);
                                      toast(
                                        'Attempt recovered. Reconcile the downstream outcome.',
                                        'success'
                                      );
                                    })
                                  }
                                >
                                  Recover interrupted attempt
                                </button>
                                <button
                                  className="btn-small btn-secondary"
                                  onClick={() => setRecoverFor(null)}
                                >
                                  Keep
                                </button>
                              </span>
                            ) : (
                              <button
                                className="btn-small btn-secondary"
                                disabled={busy}
                                onClick={() => {
                                  setRecoveryEvidence('');
                                  setWorkerStopped(false);
                                  setRecoverFor(b.id);
                                }}
                              >
                                Recover interrupted attempt
                              </button>
                            ))}
                          {b.status === 'Failed' && (
                            <button
                              className="btn-small btn-primary"
                              disabled={busy}
                              onClick={() =>
                                act(async () => {
                                  await api.post(`/api/exports/batches/${b.id}/retry`);
                                  toast('Retry sent.', 'success');
                                })
                              }
                            >
                              Retry
                            </button>
                          )}
                          {b.status === 'Failed' &&
                            (cancelFor === b.id ? (
                              <span className="confirm-inline">
                                Confirm the downstream outcome before resolving this batch.
                                <input
                                  value={resolutionEvidence}
                                  onChange={(e) => setResolutionEvidence(e.target.value)}
                                  placeholder="Evidence checked with receiver"
                                  aria-label="Evidence of downstream outcome"
                                />
                                <button
                                  className="btn-small btn-danger"
                                  disabled={busy || !resolutionEvidence.trim()}
                                  onClick={() =>
                                    act(async () => {
                                      const { data } = await api.post(
                                        `/api/exports/batches/${b.id}/reconcile`,
                                        { outcome: 'not_received', evidence: resolutionEvidence }
                                      );
                                      setCancelFor(null);
                                      setResolutionEvidence('');
                                      toast(
                                        `Batch cancelled. ${data.released} record(s) released.`,
                                        'success'
                                      );
                                    })
                                  }
                                >
                                  Not received: release
                                </button>
                                <button
                                  className="btn-small btn-primary"
                                  disabled={busy || !resolutionEvidence.trim()}
                                  onClick={() =>
                                    act(async () => {
                                      await api.post(`/api/exports/batches/${b.id}/reconcile`, {
                                        outcome: 'received',
                                        evidence: resolutionEvidence,
                                      });
                                      setCancelFor(null);
                                      setResolutionEvidence('');
                                      toast('Batch marked received.', 'success');
                                    })
                                  }
                                >
                                  Received: mark delivered
                                </button>
                                <button
                                  className="btn-small btn-secondary"
                                  onClick={() => setCancelFor(null)}
                                >
                                  Keep
                                </button>
                              </span>
                            ) : (
                              <button
                                className="btn-small btn-secondary"
                                disabled={busy}
                                onClick={() => {
                                  setResolutionEvidence('');
                                  setCancelFor(b.id);
                                }}
                              >
                                Reconcile outcome
                              </button>
                            ))}
                          {b.status === 'Sent' &&
                            (ackFor === b.id ? (
                              <span className="confirm-inline">
                                <input
                                  value={ackRef}
                                  onChange={(e) => setAckRef(e.target.value)}
                                  placeholder="Downstream reference (optional)"
                                  aria-label="Downstream reference"
                                  autoFocus
                                />
                                <button
                                  className="btn-small btn-primary"
                                  disabled={busy}
                                  onClick={() =>
                                    act(async () => {
                                      await api.post(`/api/exports/batches/${b.id}/ack`, {
                                        reference: ackRef,
                                      });
                                      setAckFor(null);
                                      setAckRef('');
                                      toast('Marked acknowledged.', 'success');
                                    })
                                  }
                                >
                                  Confirm
                                </button>
                                <button
                                  className="btn-small btn-secondary"
                                  onClick={() => setAckFor(null)}
                                >
                                  Cancel
                                </button>
                              </span>
                            ) : (
                              <button
                                className="btn-small btn-primary"
                                disabled={busy}
                                onClick={() => {
                                  setAckFor(b.id);
                                  setAckRef('');
                                }}
                              >
                                Mark acknowledged
                              </button>
                            ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <PageNav
              page={batchPage}
              hasMore={batchHasMore}
              pageSize={batchPageSize}
              onPageSize={(size) => {
                setBatchPageSize(size);
                setBatchPage(1);
              }}
              onPage={setBatchPage}
            />
          </>
        )}
      </section>

      <p className="hint">
        Destinations are managed in{' '}
        {canOpen('/settings/destinations') ? (
          <Link href="/settings/destinations">Configuration → Export destinations</Link>
        ) : (
          'Configuration'
        )}
        .
      </p>
    </div>
  );
}
