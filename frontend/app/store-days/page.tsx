'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api, money } from '@/lib/api';
import { SkeletonRows, useToast } from '@/components/Feedback';
import { StoreScopeNote } from '@/components/StoreScope';
import { CaseLink, useMe } from '@/components/MeProvider';

interface StoreDay { store_id: string; business_date: string; status: string; audit_version: number }
interface Total { id: string; name: string; level: string; dimension: string | null; calculated_amount: string; declared_amount: string | null; variance_amount: string | null }
interface DayCase { id: string; case_number: string; case_type: string; status: string; exposure: string }
interface Closure { id: string; requested_by: string; evidence_basis: string; status: string; reviewed_by: string | null; review_reason: string | null; created_at: string }
interface EvidenceOverride { case_id: string; case_number: string; requirement: string; status: string; reason: string | null; financial_impact: string; requested_by: string | null; approved_by: string | null }
interface HistoryItem { id: string; event_type: string; actor: string; description: string; created_at: string }

const tone = (s: string) => (s === 'Closed' ? 'badge-success' : s === 'Pending' ? 'badge-warning' : s === 'Rejected' ? 'badge-error' : 'badge-secondary');
const apiError = (e: unknown, fallback: string) => (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
const base = (d: { store_id: string; business_date: string }) => `/api/store-days/${d.store_id}/${d.business_date}`;

export default function StoreDaysPage() {
  const toast = useToast();
  const { me } = useMe();
  const canOpen = !!me && (me.role === 'it' || me.role === 'admin');
  const [days, setDays] = useState<StoreDay[]>([]);
  const [stores, setStores] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<StoreDay | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [totals, setTotals] = useState<Total[]>([]);
  const [versions, setVersions] = useState<number[]>([]);
  const [version, setVersion] = useState<number | null>(null);
  const [blockers, setBlockers] = useState<string[]>([]);
  const [cases, setCases] = useState<DayCase[]>([]);
  const [closures, setClosures] = useState<Closure[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [overrides, setOverrides] = useState<EvidenceOverride[]>([]);
  const today = new Date().toISOString().slice(0, 10);
  const [openStore, setOpenStore] = useState('');
  const [openDate, setOpenDate] = useState(today);
  const [rationale, setRationale] = useState('');
  const [reviewRationale, setReviewRationale] = useState('');
  const [evidenceBasis, setEvidenceBasis] = useState('');
  const [busy, setBusy] = useState(false);
  const [statusFilter, setStatusFilter] = useState('');
  const [q, setQ] = useState('');

  const load = useCallback(async (storeId?: string) => {
    try {
      const [d, s] = await Promise.all([api.get('/api/store-days', { params: storeId ? { store_id: storeId } : {} }), api.get('/api/store-days/stores')]);
      setDays(d.data); setStores(s.data); setError(null);
    } catch { setError('Unable to load Store Days. Sign in with a Finance or IT role.'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const open = async (day: StoreDay, v?: number) => {
    setSelected(day); setDetailLoading(true);
    try {
      const { data } = await api.get(base(day), { params: v ? { version: v } : {} });
      setTotals(data.totals); setVersions(data.versions); setVersion(data.selected_version); setBlockers(data.close_blockers);
      setCases(data.cases); setClosures(data.closure_requests); setHistory(data.history); setOverrides(data.evidence_overrides ?? []);
    } catch (e) { toast(apiError(e, 'Could not open this Store Day.'), 'error'); }
    finally { setDetailLoading(false); }
  };

  const act = async (path: string, body?: object, done?: () => void) => {
    setBusy(true);
    try {
      const { data } = await api.post(path, body);
      toast(data.transactions !== undefined ? `Re-totaled ${data.transactions} transaction(s); ${data.totals_created} totals created.` : `Store Day is now ${data.status}.`, 'success');
      done?.();
      await load();
      if (data.store_day) await open(data.store_day); else if (selected) await open({ ...selected, ...data });
    } catch (e) { toast(apiError(e, 'Action failed'), 'error'); }
    finally { setBusy(false); }
  };

  const statuses = Array.from(new Set(days.map((d) => d.status))).sort();
  const shownDays = days
    .filter((d) => (!statusFilter || d.status === statusFilter) && (!q.trim() || `${d.store_id} ${d.business_date}`.toLowerCase().includes(q.trim().toLowerCase())))
    .sort((a, b) => b.business_date.localeCompare(a.business_date) || a.store_id.localeCompare(b.store_id));
  const openDay = async () => {
    setBusy(true);
    try {
      const { data } = await api.post('/api/store-days', { store_id: openStore, business_date: openDate });
      toast(`Opened ${data.store_id} · ${data.business_date}. Re-total it once its data has loaded.`, 'success');
      await load(); await open(data);
    } catch (e) { toast(apiError(e, 'Could not open the Store Day.'), 'error'); }
    finally { setBusy(false); }
  };
  const pending = closures.some((c) => c.status === 'Pending');
  const closed = selected?.status === 'Closed';

  return (
    <div className="page-container">
      <div className="page-header">
        <div><h1>Store Days &amp; Totals</h1><p className="page-sub">Versioned balancing totals for the controlled store-day audit lifecycle.</p></div>
      </div>
      <StoreScopeNote />
      {error && <div className="alert alert-error" role="alert">{error}<button className="btn-secondary btn-sm alert-close" onClick={() => load()}>Retry</button></div>}

      <div className="cc-card">
        <h3>Open a Store Day</h3>
        {canOpen ? (
          <>
            <p className="hint" style={{ marginBottom: '.6rem' }}>A Store Day is the audit unit for one store and one business date. Open it here, then Re-total it once its data has loaded.</p>
            <div className="form-row">
              <label className="field">Store
                <select value={openStore} onChange={(e) => setOpenStore(e.target.value)}>
                  <option value="">Select a store</option>{stores.map((s) => <option key={s}>{s}</option>)}
                </select></label>
              <label className="field">Business date<input type="date" value={openDate} max={today} onChange={(e) => setOpenDate(e.target.value)} /></label>
              <button className="btn-primary" disabled={busy || !openStore || !openDate} onClick={openDay}>{busy ? 'Opening…' : 'Open Store Day'}</button>
            </div>
          </>
        ) : (
          <p className="hint">Only an IT administrator can open a Store Day. Ask IT to open the store and date you need, then re-total it here.</p>
        )}
      </div>

      <div className="sd-split">
        <div className="cc-card">
          <h3>Store Day queue <span className="cc-count">{shownDays.length}</span></h3>
          {days.length > 0 && (
            <div className="toolbar" style={{ marginBottom: '.6rem' }}>
              <input className="grow" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search store or date" aria-label="Search Store Days" />
              <div className="cc-seg" style={{ flexWrap: 'wrap' }}>
                <button className={!statusFilter ? 'on' : ''} onClick={() => setStatusFilter('')}>All {days.length}</button>
                {statuses.map((s) => <button key={s} className={statusFilter === s ? 'on' : ''} onClick={() => setStatusFilter(s)}>{s} {days.filter((d) => d.status === s).length}</button>)}
              </div>
            </div>
          )}
          {loading ? <SkeletonRows rows={5} /> : days.length === 0
            ? <div className="empty-state"><b>No Store Days yet</b><p>{canOpen ? 'Pick a store and date above and open a Store Day.' : 'An IT administrator needs to open one.'}</p></div>
            : shownDays.length === 0 ? <p className="cc-empty">No Store Days match this filter.</p>
            : <div className="table-container"><table className="data-table">
              <thead><tr><th>Store</th><th>Date</th><th>Status</th><th className="num">Ver.</th></tr></thead>
              <tbody>{shownDays.map((d) => {
                const on = selected?.store_id === d.store_id && selected.business_date === d.business_date;
                return (
                  <tr key={`${d.store_id}-${d.business_date}`} className={`row-link ${on ? 'selected' : ''}`} tabIndex={0} aria-selected={on}
                    onClick={() => open(d)} onKeyDown={(e) => { if (e.key === 'Enter') open(d); }}>
                    <td className="font-medium">{d.store_id}</td><td>{d.business_date}</td>
                    <td><span className={`badge badge-sm ${tone(d.status)}`}>{d.status}</span></td><td className="num">v{d.audit_version}</td>
                  </tr>
                );
              })}</tbody></table></div>}
        </div>

        <div className="cc-card">
          {!selected ? <div className="cc-placeholder"><b>Select a Store Day</b><p>Totals, closure requests and history appear here.</p></div> : (
            <>
              <div className="cc-head">
                <div><h2>{selected.store_id} · {selected.business_date}</h2>
                  <small>Current totals version {selected.audit_version}</small></div>
                <span className={`badge badge-md ${tone(selected.status)}`}>{selected.status}</span>
              </div>
              {detailLoading ? <SkeletonRows rows={6} /> : <>
                {blockers.length > 0 && <div className="alert alert-error sd-section"><span><b>Closure blockers:</b> {blockers.join(' · ')}</span></div>}
                {overrides.length > 0 && (
                  <div className="alert alert-warning sd-section" style={{ flexDirection: 'column', alignItems: 'flex-start' }}>
                    <b>{overrides.length} evidence gap{overrides.length > 1 ? 's' : ''} {overrides.some((o) => o.status === 'Override Pending') ? 'overridden or awaiting approval' : 'overridden'} on this day</b>
                    {overrides.map((o) => (
                      <span key={`${o.case_id}-${o.requirement}`}><CaseLink id={o.case_id}>{o.case_number}</CaseLink> · {o.requirement} · {o.status === 'Override Pending' ? `awaiting a second approver (requested by ${o.requested_by})` : `approved by ${o.approved_by}`} · impact {money(o.financial_impact)}{o.reason ? ` · ${o.reason}` : ''}</span>
                    ))}
                  </div>
                )}

                <div className="sd-bar sd-section">
                  <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: '.5rem' }}>Version
                    <select value={version ?? ''} onChange={(e) => open(selected, Number(e.target.value))}>
                      {versions.map((v) => <option key={v} value={v}>v{v}{v === selected.audit_version ? ' (current)' : ''}</option>)}
                    </select></label>
                  <button className="btn-secondary btn-sm" disabled={busy || closed} onClick={() => act(`${base(selected)}/retotal`)}>Re-total</button>
                  {closed ? (
                    <div className="form-row">
                      <input value={rationale} onChange={(e) => setRationale(e.target.value)} placeholder="Reopen rationale (required)" aria-label="Reopen rationale" />
                      <button className="btn-secondary btn-sm" disabled={busy || !rationale.trim()} onClick={() => act(`${base(selected)}/reopen`, { rationale }, () => setRationale(''))}>Reopen</button>
                    </div>
                  ) : (
                    <button className="btn-primary btn-sm" disabled={busy || blockers.length > 0 || !pending}
                      title={!pending ? 'A pending closure request is required first' : blockers.length ? 'Resolve the blockers first' : ''}
                      onClick={() => act(`${base(selected)}/close`)}>Approve &amp; close</button>
                  )}
                </div>

                {!closed && (
                  <div className="cc-askrow sd-section">
                    <input value={evidenceBasis} onChange={(e) => setEvidenceBasis(e.target.value)} placeholder="Closure evidence basis" aria-label="Closure evidence basis" />
                    <button className="btn-secondary btn-sm" disabled={busy || blockers.length > 0 || !evidenceBasis.trim()}
                      onClick={() => act(`${base(selected)}/closure-requests`, { evidence_basis: evidenceBasis }, () => setEvidenceBasis(''))}>Request closure</button>
                  </div>
                )}

                <div className="sd-section"><h3>Totals</h3>
                  {totals.length === 0 ? <p className="cc-empty">No totals for this version.</p> : (
                    <div className="table-container"><table className="data-table">
                      <thead><tr><th>Total</th><th>Level</th><th>Dimension</th><th className="num">Calculated</th><th className="num">Declared</th><th className="num">Variance</th></tr></thead>
                      <tbody>{totals.map((t) => {
                        const bad = t.variance_amount !== null && Number(t.variance_amount) !== 0;
                        return (<tr key={t.id}><td className="font-medium">{t.name}</td><td>{t.level}</td><td>{t.dimension || '—'}</td>
                          <td className="num">{money(t.calculated_amount)}</td><td className="num">{t.declared_amount === null ? '—' : money(t.declared_amount)}</td>
                          <td className={`num ${bad ? 'sd-neg' : ''}`}>{t.variance_amount === null ? '—' : money(t.variance_amount)}</td></tr>);
                      })}</tbody></table></div>)}
                </div>

                <div className="sd-section"><h3>Closure requests</h3>
                  {closures.length === 0 ? <p className="cc-empty">No closure requests yet.</p> : closures.map((c) => (
                    <div className="case-note" key={c.id}>
                      <span className={`badge badge-sm ${tone(c.status)}`}>{c.status}</span> <span>{c.requested_by} · {new Date(c.created_at).toLocaleString()}</span>
                      <p>{c.evidence_basis}</p>{c.review_reason && <p>Review: {c.review_reason}</p>}
                      {c.status === 'Pending' && (
                        <div className="cc-askrow">
                          <input value={reviewRationale} onChange={(e) => setReviewRationale(e.target.value)} placeholder="Rejection rationale (required)" aria-label="Rejection rationale" />
                          <button className="btn-secondary btn-sm" disabled={busy || !reviewRationale.trim()}
                            onClick={() => act(`${base(selected)}/closure-requests/${c.id}/reject`, { rationale: reviewRationale }, () => setReviewRationale(''))}>Reject request</button>
                        </div>)}
                    </div>))}
                </div>

                <div className="sd-section"><h3>Linked cases</h3>
                  {cases.length === 0 ? <p className="cc-empty">No cases for this Store Day.</p> : (
                    <ul className="summary-list">{cases.map((c) => (
                      <li key={c.id}><CaseLink id={c.id}>{c.case_number}</CaseLink> · {c.case_type} · {c.status} · {money(c.exposure)}</li>))}</ul>)}
                </div>

                <div className="sd-section"><h3>History</h3>
                  {history.length === 0 ? <p className="cc-empty">No history yet.</p> : (
                    <ol className="case-timeline">{history.map((h) => (
                      <li key={h.id}><time>{new Date(h.created_at).toLocaleString()}</time>
                        <div><b>{h.event_type.replaceAll('_', ' ')}</b><p>{h.description}</p><small>{h.actor}</small></div></li>))}</ol>)}
                </div>
              </>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
