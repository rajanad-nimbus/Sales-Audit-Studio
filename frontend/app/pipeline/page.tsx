'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api, BatchStatus } from '@/lib/api';
import { SkeletonRows, useToast } from '@/components/Feedback';

interface Stats {
  source_records: Record<string, number>;
  transactions: Record<string, number>;
  quarantined: { id: string; source_system: string; source_record_id: string; reason: string }[];
}
interface Run { id: string; business_date: string; status: string; trigger: string; started_at: string; finished_at: string | null; transactions_loaded: number; matched_pairs: number; cases_created: number; error: string | null }
interface Recon { transactions_examined: number; matched_pairs: number; exceptions: Record<string, number>; cases_created: number }
interface Delivery { source_system: string; delivery_id: string; received_at: string; records: number; processed: number; quarantined: number; last_error: string | null }
interface FeedHealth { profile_id: string; name: string; source_system: string; schedule: string | null; last_received_at: string | null; age_hours: number | null; expected_within_hours: number | null; state: 'fresh' | 'overdue' | 'never' | 'unknown' | 'disabled' }
type Tab = 'runs' | 'feeds' | 'quarantine' | 'demo';

const sum = (o: Record<string, number> = {}) => Object.values(o).reduce((a, b) => a + b, 0);
const n = (v: number) => v.toLocaleString();
const when = (iso: string) => new Date(iso).toLocaleString();
const apiError = (e: unknown, fallback: string) => (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
const runTone = (s: string) => (s === 'Succeeded' ? 'badge-success' : s === 'Failed' ? 'badge-error' : 'badge-warning');
const feedTone = (s: FeedHealth['state']) => (s === 'fresh' ? 'badge-success' : s === 'overdue' ? 'badge-error' : s === 'never' ? 'badge-warning' : 'badge-secondary');
const ago = (h: number | null | undefined) => (h == null ? '' : h < 1 ? 'less than an hour ago' : h < 48 ? `${Math.round(h)}h ago` : `${Math.round(h / 24)}d ago`);

export default function PipelinePage() {
  const toast = useToast();
  const [stats, setStats] = useState<Stats | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [bstatus, setBstatus] = useState<BatchStatus | null>(null);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [feedHealth, setFeedHealth] = useState<FeedHealth[]>([]);
  const [ingestRes, setIngestRes] = useState<Record<string, Record<string, number>> | null>(null);
  const [recon, setRecon] = useState<Recon | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [confirmClr, setConfirmClr] = useState(false);
  const [tab, setTab] = useState<Tab>('runs');

  const load = useCallback(async () => {
    try {
      const [st, r, b, d, f] = await Promise.all([api.get('/api/ingest/stats'), api.get('/api/batch/runs?limit=10'), api.get('/api/batch/status'), api.get('/api/ingest/deliveries?limit=10'), api.get('/api/ingest/feed-health')]);
      setStats(st.data); setRuns(r.data); setBstatus(b.data); setDeliveries(d.data); setFeedHealth(f.data); setError(null);
    } catch { setError('Could not load pipeline status. Check that the backend is running and your role is Finance or IT.'); }
    finally { setLoaded(true); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const h = window.location.hash.slice(1) as Tab;
    if (['runs', 'feeds', 'quarantine', 'demo'].includes(h)) setTab(h);
  }, []);
  const pick = (t: Tab) => { setTab(t); history.replaceState(null, '', `#${t}`); };

  const run = async (name: string, fn: () => Promise<string>) => {
    setBusy(name);
    try { toast(await fn(), 'success'); } catch (e) { toast(apiError(e, 'Step failed'), 'error'); }
    setBusy(null); load();
  };
  const runBatch = () => run('batch', async () => {
    const { data } = await api.post('/api/batch/run', {});
    return `Batch for ${data.business_date}: ${data.status}. ${n(data.transactions_loaded)} transactions loaded, ${n(data.cases_created)} cases raised.`;
  });

  const tx = stats?.transactions ?? {};
  const quarantined = stats?.quarantined ?? [];
  const overdue = feedHealth.filter((f) => f.state === 'overdue' || f.state === 'never').length;
  const flow = [
    { label: 'Source records', value: sum(stats?.source_records), sub: 'received from configured or simulated source systems', href: '' },
    { label: 'Normalized', value: stats?.source_records?.Processed ?? 0, sub: 'archived as canonical transactions', href: '' },
    { label: 'Matched', value: tx.Matched ?? 0, sub: 'reconciled with no exception', href: '' },
    { label: 'Exceptions', value: tx.Exception ?? 0, sub: 'became cases', href: '/cases' },
  ];

  // One line that answers "is my data OK?"
  let health: { tone: 'success' | 'warning' | 'error'; title: string; body: string };
  if (!bstatus) health = { tone: 'warning', title: 'Checking pipeline…', body: '' };
  else if (bstatus.freshness === 'never') health = { tone: 'warning', title: 'No data loaded yet', body: 'Run the nightly batch to load the demo feed and raise cases.' };
  else if (bstatus.last_failed) health = { tone: 'error', title: 'The last batch failed', body: 'Open Batch runs to see the error, then run the batch again.' };
  else if (bstatus.freshness === 'stale') health = { tone: 'warning', title: 'Data is stale', body: `The last successful batch finished ${ago(bstatus.hours_since_success)}. Data as of ${bstatus.data_as_of ?? 'unknown'}.` };
  else health = { tone: 'success', title: 'Data is current', body: `Data as of ${bstatus.data_as_of ?? 'unknown'}.${bstatus.last_success ? ` Last batch loaded ${n(bstatus.last_success.transactions_loaded)} transactions and raised ${n(bstatus.last_success.cases_created)} cases.` : ''}` };

  const tabs: [Tab, string, number | null, boolean][] = [
    ['runs', 'Batch runs', runs.length, false],
    ['feeds', 'Source feeds', feedHealth.length, overdue > 0],
    ['quarantine', 'Quarantine', quarantined.length, quarantined.length > 0],
    ['demo', 'Demo tools', null, false],
  ];

  return (
    <div className="page-container">
      <div className="page-header">
        <div><h1>Data Pipeline</h1><p className="page-sub">Ingest, archive, normalize, reconcile, then cases. Transactions arrive once a day.</p></div>
        <div className="cc-controls">
          <button className="btn-secondary" onClick={load} disabled={!!busy}>Refresh</button>
          <button className="btn-primary" disabled={!!busy} onClick={runBatch}>{busy === 'batch' ? 'Running batch…' : 'Run nightly batch'}</button>
        </div>
      </div>
      {error && <div className="alert alert-error" role="alert">{error}<button className="btn-secondary btn-sm alert-close" onClick={load}>Retry</button></div>}

      {!loaded ? <SkeletonRows rows={3} label="Loading pipeline" /> : (
        <>
          <div className={`pl-health ${health.tone}`} role="status">
            <b>{health.title}</b><span>{health.body}</span>
            <span className="pl-chips">
              {overdue > 0 && <button onClick={() => pick('feeds')}>{overdue} feed{overdue > 1 ? 's' : ''} overdue</button>}
              {quarantined.length > 0 && <button onClick={() => pick('quarantine')}>{n(quarantined.length)} quarantined</button>}
            </span>
          </div>

          <ol className="cc-pipeline pl-flow" aria-label="Pipeline stages">
            {flow.map((f, i) => {
              const body = <><b>{n(f.value)}</b><small>{f.label}</small><small className="pl-sub">{f.sub}</small></>;
              return (
                <li key={f.label} className="cc-stage has">
                  {f.href && f.value > 0 ? <Link href={f.href} className="pl-stage-link">{body}</Link> : body}
                  {i < flow.length - 1 && <em aria-hidden>›</em>}
                </li>
              );
            })}
          </ol>

          <div className="case-tabs" role="tablist" aria-label="Pipeline sections">
            {tabs.map(([k, label, count, hot]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => pick(k)}>
                {label}{count !== null && <span className={hot ? 'pl-hot' : ''}>{n(count)}</span>}
              </button>
            ))}
          </div>

          {tab === 'runs' && (
            <section className="cc-card">
              <p className="hint" style={{ marginBottom: '.75rem' }}>Cases appear when a batch finishes. In production a schedule runs <code>python batch_job.py --date YYYY-MM-DD</code> (or <code>POST /api/batch/run</code>) after the extract lands.</p>
              {runs.length === 0 ? <div className="empty-state"><b>No batch runs yet</b><p>Use “Run nightly batch” above to load the demo feed.</p></div> : (
                <div className="table-scroll"><table className="data-table">
                  <thead><tr><th>Business date</th><th>Status</th><th>Started</th><th className="num">Loaded</th><th className="num">Matched</th><th className="num">Cases</th><th>Trigger</th></tr></thead>
                  <tbody>{runs.map((r) => (
                    <tr key={r.id}><td className="font-medium">{r.business_date}</td>
                      <td><span className={`badge badge-sm ${runTone(r.status)}`}>{r.status}</span>{r.error && <small className="pl-err">{r.error}</small>}</td>
                      <td className="nowrap">{when(r.started_at)}</td><td className="num">{n(r.transactions_loaded)}</td>
                      <td className="num">{n(r.matched_pairs)}</td><td className="num">{n(r.cases_created)}</td><td>{r.trigger}</td></tr>
                  ))}</tbody></table></div>
              )}
            </section>
          )}

          {tab === 'feeds' && (
            <div className="case-panel">
              <section className="cc-card">
                <h3>Feed freshness</h3>
                <p className="hint" style={{ marginBottom: '.6rem' }}>Each source feed is expected on a schedule. Edit schedules and mappings under <Link href="/settings#integrations">Settings → Integrations</Link>.</p>
                {feedHealth.length === 0 ? <p className="cc-empty">No source-feed profiles are configured.</p> : (
                  <div className="table-scroll"><table className="data-table">
                    <thead><tr><th>Profile</th><th>Schedule</th><th>Last receipt</th><th>Freshness</th></tr></thead>
                    <tbody>{feedHealth.map((f) => (
                      <tr key={f.profile_id}><td className="font-medium">{f.name}<small className="act-sub"> {f.source_system}</small></td><td>{f.schedule || 'Not set'}</td>
                        <td>{f.last_received_at ? <>{when(f.last_received_at)}<small className="act-sub"> {ago(f.age_hours)}</small></> : '—'}</td>
                        <td><span className={`badge badge-sm ${feedTone(f.state)}`}>{f.state}</span></td></tr>
                    ))}</tbody></table></div>
                )}
              </section>
              <section className="cc-card">
                <h3>Recent deliveries</h3>
                <p className="hint" style={{ marginBottom: '.6rem' }}>Deliveries are archived read-only before normalization.</p>
                {deliveries.length === 0 ? <p className="cc-empty">No source deliveries received yet.</p> : (
                  <div className="table-scroll"><table className="data-table">
                    <thead><tr><th>Source</th><th>Delivery</th><th>Received</th><th className="num">Records</th><th className="num">Processed</th><th className="num">Quarantined</th><th>Latest error</th></tr></thead>
                    <tbody>{deliveries.map((d) => (
                      <tr key={`${d.source_system}-${d.delivery_id}`}><td className="font-medium">{d.source_system}</td><td>{d.delivery_id}</td><td className="nowrap">{when(d.received_at)}</td>
                        <td className="num">{n(d.records)}</td><td className="num">{n(d.processed)}</td>
                        <td className="num">{d.quarantined > 0 ? <span className="badge badge-sm badge-warning">{n(d.quarantined)}</span> : 0}</td><td>{d.last_error || '—'}</td></tr>
                    ))}</tbody></table></div>
                )}
              </section>
            </div>
          )}

          {tab === 'quarantine' && (
            <section className="cc-card">
              <p className="hint" style={{ marginBottom: '.75rem' }}>Records that failed validation are held here instead of being loaded. Fix the source and re-deliver them.</p>
              {quarantined.length === 0 ? <div className="empty-state"><b>Nothing in quarantine</b><p>Every received record passed validation.</p></div> : (
                <div className="table-scroll"><table className="data-table">
                  <thead><tr><th>Source</th><th>Record</th><th>Reason</th></tr></thead>
                  <tbody>{quarantined.map((q) => <tr key={q.id}><td className="font-medium">{q.source_system}</td><td>{q.source_record_id}</td><td>{q.reason}</td></tr>)}</tbody></table></div>
              )}
            </section>
          )}

          {tab === 'demo' && (
            <div className="case-panel">
              <div className="alert alert-warning">Demo tools create or delete sample data. They are for testing and do not run in production.</div>
              <div className="case-panel case-two">
                <section className="cc-card">
                  <h3>Step by step</h3>
                  <p className="hint" style={{ marginBottom: '.6rem' }}>Run the nightly batch does both steps at once. Use these to see each step on its own.</p>
                  <div className="cc-actions">
                    <button className="btn-secondary" disabled={!!busy} onClick={() => run('feed', async () => { const { data } = await api.post('/api/ingest/simulate'); setIngestRes(data); return '1. Synthetic day ingested.'; })}>
                      {busy === 'feed' ? 'Ingesting…' : '1. Ingest synthetic day'}</button>
                    <button className="btn-secondary" disabled={!!busy} onClick={() => run('recon', async () => { const { data } = await api.post('/api/reconcile'); setRecon(data); return `2. Reconciled: ${n(data.cases_created)} cases raised.`; })}>
                      {busy === 'recon' ? 'Reconciling…' : '2. Run reconciliation'}</button>
                  </div>
                  {ingestRes && <div className="sd-section"><h3>Last ingestion</h3>{Object.entries(ingestRes).map(([src, r]) => (
                    <div key={src} className="cc-run"><b>{src}</b><span>{r.received} received · {r.archived} archived · {r.duplicates_skipped} duplicate skipped · {r.quarantined} quarantined</span></div>))}</div>}
                  {recon && <div className="sd-section"><h3>Last reconciliation</h3>
                    <div className="cc-run"><span>Examined {n(recon.transactions_examined)} · matched pairs {n(recon.matched_pairs)}</span><b>{n(recon.cases_created)} cases</b></div>
                    {Object.entries(recon.exceptions).map(([k, v]) => <div key={k} className="cc-run"><span>{k.replace(/_/g, ' ')}</span><b>{v}</b></div>)}
                    {recon.cases_created > 0 && <p style={{ margin: '.6rem 0 0' }}><Link href="/">Open the Command Center →</Link></p>}</div>}
                </section>
                <section className="cc-card">
                  <h3>Volume test</h3>
                  <p className="hint" style={{ marginBottom: '.6rem' }}>Adds 50,000 synthetic cases to test Action Center performance. They have no exceptions, so they cannot be investigated.</p>
                  <div className="cc-actions">
                    <button className="btn-secondary" disabled={!!busy} onClick={() => run('vol', async () => { const { data } = await api.post('/api/action-center/scale-demo?cases=50000'); return `Loaded 50,000 synthetic cases (${n(data.total_cases)} total).`; })}>
                      {busy === 'vol' ? 'Loading…' : 'Load 50k synthetic cases'}</button>
                    {confirmClr ? (
                      <span className="confirm-inline">Delete all synthetic cases?
                        <button className="btn-danger btn-small" disabled={!!busy} onClick={() => { setConfirmClr(false); run('clr', async () => { const { data } = await api.delete('/api/action-center/scale-demo'); return `Removed ${n(data.deleted)} synthetic cases.`; }); }}>Yes, remove</button>
                        <button className="btn-secondary btn-small" onClick={() => setConfirmClr(false)}>Cancel</button>
                      </span>
                    ) : <button className="btn-secondary" disabled={!!busy} onClick={() => setConfirmClr(true)}>Remove synthetic cases</button>}
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
