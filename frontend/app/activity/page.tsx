'use client';

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { api, AuditEvent } from '@/lib/api';

interface Evt extends AuditEvent { after?: any }
import { useDataChanged } from '@/lib/sync';
import { CopyButton, SkeletonRows, useToast } from '@/components/Feedback';
import { AgentModeNotice, useAgentMode } from '@/components/AgentMode';
import { CaseLink, useCanOpen } from '@/components/MeProvider';

import { GROUPS, SYSTEM_ACTORS, groupOf, FILTERS, pretty, tone, isAlert, initials, groupLabel, ago, dayLabel } from '@/lib/activityUtil';

export default function ActivityPage() {
  const [latest, setLatest] = useState<Evt[]>([]);
  const [older, setOlder] = useState<Evt[]>([]);
  const [total, setTotal] = useState(0);
  const [nextPage, setNextPage] = useState(2);
  const [pages, setPages] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const [onlyAlerts, setOnlyAlerts] = useState(false);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const known = useRef<Set<string> | null>(null);
  const agentMode = useAgentMode();
  const canOpen = useCanOpen();
  const events = useMemo(() => {
    const seen = new Set<string>(); const out: Evt[] = [];
    for (const e of [...latest, ...older]) if (!seen.has(e.id)) { seen.add(e.id); out.push(e); }
    return out;
  }, [latest, older]);
  const [open, setOpen] = useState<string | null>(null);
  const toast = useToast();
  const [loaded, setLoaded] = useState(false);
  const [agent, setAgent] = useState('all');
  useEffect(() => { const g = new URLSearchParams(window.location.search).get('agent'); if (g) setAgent(g); }, []);
  const [q, setQ] = useState('');
  const [live, setLive] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [, setNow] = useState(0);

  const load = useCallback(async () => {
    try {
      const ev = await api.get('/api/audit/page?page_size=200');
      const items: Evt[] = ev.data.items.map((e: any) => ({ ...e, action_description: e.description }));
      setLatest(items); setTotal(ev.data.total); setPages(ev.data.pages);
      // Briefly highlight events that arrived since the last refresh (not on first load).
      const ids = new Set(items.map((e) => e.id));
      if (known.current) {
        const added = items.filter((e) => !known.current!.has(e.id)).map((e) => e.id);
        if (added.length) { setFresh(new Set(added)); setTimeout(() => setFresh(new Set()), 4000); }
      }
      known.current = new Set([...(known.current ?? []), ...ids]);
      setError(null);
    }
    catch { setError('Could not load activity. Showing the last data received.'); }
    finally { setLoaded(true); setNow((n) => n + 1); }
  }, []);
  const loadOlder = async () => {
    setLoadingMore(true);
    try {
      const { data } = await api.get(`/api/audit/page?page_size=200&page=${nextPage}`);
      setOlder((o) => [...o, ...data.items.map((e: any) => ({ ...e, action_description: e.description }))]);
      setNextPage((n) => n + 1);
    } catch { toast('Could not load older events.', 'error'); }
    finally { setLoadingMore(false); }
  };
  // Keep "5m ago" labels honest between refreshes.
  useEffect(() => { const t = setInterval(() => setNow((n) => n + 1), 30000); return () => clearInterval(t); }, []);
  useDataChanged(() => { load(); });
  useEffect(() => {
    load();
    if (!live) return;
    const tick = () => { if (!document.hidden) load(); };
    const t = setInterval(tick, 5000);
    document.addEventListener('visibilitychange', tick);
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', tick); };
  }, [load, live]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: events.length };
    for (const e of events) { const k = groupOf(e.actor); c[k] = (c[k] ?? 0) + 1; }
    return c;
  }, [events]);

  const alertCount = useMemo(() => events.filter((e) => isAlert(e.event_type)).length, [events]);
  const today = useMemo(() => events.filter((e) => dayLabel(e.created_at) === 'Today'), [events]);
  const todayAuto = today.filter((e) => groupOf(e.actor) !== 'people').length;
  const filtered = agent !== 'all' || !!q.trim() || onlyAlerts;
  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    return events.filter((e) => (agent === 'all' || groupOf(e.actor) === agent)
      && (!onlyAlerts || isAlert(e.event_type))
      && (!t || `${e.actor} ${e.event_type} ${e.action_description}`.toLowerCase().includes(t)));
  }, [events, agent, q, onlyAlerts]);

  const groups = useMemo(() => {
    const out: { day: string; items: Evt[] }[] = [];
    for (const e of shown) {
      const day = dayLabel(e.created_at);
      if (out.at(-1)?.day === day) out.at(-1)!.items.push(e); else out.push({ day, items: [e] });
    }
    return out;
  }, [shown]);

  const last = events[0];
  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1>Agent Activity</h1>
          <p className="page-sub">Everything agents, connectors and people did, newest first. Read-only audit trail.</p>
        </div>
        <div className="cc-controls">
          <span className="cc-live"><i className={`dot ${live ? 'on' : ''}`} />{live ? 'Live' : 'Paused'}{last ? ` · last event ${ago(last.created_at)}` : ''}</span>
          {canOpen('/settings/agents') && <Link className="btn-secondary btn-sm" href="/settings/agents">Agent settings</Link>}
          <button className="btn-secondary btn-sm" onClick={() => setLive((v) => !v)}>{live ? 'Pause' : 'Resume'}</button>
          <button className="btn-secondary btn-sm" onClick={load}>Refresh</button>
        </div>
      </div>
      {error && <div className="alert alert-error" role="alert">{error}</div>}
      <AgentModeNotice mode={agentMode} />


      {loaded && (
        <div className="ac-tiles">
          <div className="ac-tile"><small>Events today</small><b>{today.length.toLocaleString()}</b><em>{todayAuto.toLocaleString()} automated · {(today.length - todayAuto).toLocaleString()} by people</em></div>
          <button className={`ac-tile ${alertCount ? 'bad' : ''}`} onClick={() => setOnlyAlerts((v) => !v)} aria-pressed={onlyAlerts} disabled={alertCount === 0 && !onlyAlerts}>
            <small>Needs attention</small><b>{alertCount.toLocaleString()}</b><em>{alertCount ? (onlyAlerts ? 'Showing only these. Click to show all' : 'Failures and rejections. Click to filter') : 'No failures in the loaded window'}</em></button>
          <div className="ac-tile"><small>Last event</small><b style={{ fontSize: '1.15rem' }}>{last ? ago(last.created_at) : '—'}</b><em>{last ? `${pretty(last.actor)}` : 'Nothing recorded yet'}</em></div>
        </div>
      )}


      <>

      <div className="toolbar">
        <div className="cc-seg" style={{ flexWrap: 'wrap' }}>
          {FILTERS.map((a) => (
            <button key={a.key} className={agent === a.key ? 'on' : ''} onClick={() => setAgent(a.key)}>{a.label} {counts[a.key] ?? 0}</button>
          ))}
        </div>
        <input className="grow" type="search" placeholder="Search activity" aria-label="Search activity" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className={`btn-secondary btn-sm ${onlyAlerts ? 'act-on' : ''}`} aria-pressed={onlyAlerts} onClick={() => setOnlyAlerts((v) => !v)}>Needs attention{alertCount ? ` ${alertCount}` : ''}</button>
        {filtered && <button className="btn-secondary btn-sm" onClick={() => { setAgent('all'); setQ(''); setOnlyAlerts(false); }}>Clear filters</button>}
        <span className="toolbar-count">{shown.length.toLocaleString()} of {total.toLocaleString()} events{events.length < total ? ` (${events.length.toLocaleString()} loaded)` : ''}</span>
      </div>

      {!loaded ? <SkeletonRows rows={8} label="Loading activity" />
        : shown.length === 0 ? <div className="empty-state"><b>No activity</b><p>{events.length ? 'Nothing matches these filters in the loaded events.' : 'Activity appears once the daily batch or an agent runs.'}</p>
            {events.length > 0 && filtered && <button className="btn-secondary btn-sm" onClick={() => { setAgent('all'); setQ(''); setOnlyAlerts(false); }}>Clear filters</button>}
            {events.length > 0 && filtered && events.length < total && <button className="btn-secondary btn-sm" disabled={loadingMore} onClick={loadOlder} style={{ marginLeft: '.5rem' }}>Search older events</button>}</div>
        : groups.map((g) => (
          <section key={g.day}>
            <h3 className="act-day">{g.day}<span className="cc-count">{g.items.length}</span></h3>
            <div className="table-container">
              <table className="data-table">
                <thead><tr><th style={{ width: '7rem' }}>When</th><th style={{ width: '14rem' }}>Actor</th><th>What happened</th><th style={{ width: '5rem' }}>Case</th></tr></thead>
                <tbody>
                  {g.items.map((e) => (
                    <Fragment key={e.id}>
                    <tr className={`ac-row ${open === e.id ? 'open' : ''} ${fresh.has(e.id) ? 'act-new' : ''}`} tabIndex={0} aria-expanded={open === e.id}
                      onClick={() => setOpen(open === e.id ? null : e.id)} onKeyDown={(k) => { if (k.key === 'Enter') setOpen(open === e.id ? null : e.id); }}>
                      <td className="text-sm nowrap" title={new Date(e.created_at).toLocaleString()}>{ago(e.created_at)}</td>
                      <td><span className="act-actor"><i className={`act-av ${groupOf(e.actor) === 'people' ? 'person' : ''}`} aria-hidden>{initials(e.actor)}</i>
                        <span><span className="font-medium">{pretty(e.actor)}</span><small className="act-sub" style={{ display: 'block' }}>{groupLabel(e.actor)}</small></span></span></td>
                      <td><span className={`badge badge-sm ${tone(e.event_type)}`}>{pretty(e.event_type)}</span> <span className="act-desc">{e.action_description}</span></td>
                      <td onClick={(x) => x.stopPropagation()}>{e.case_id ? <CaseLink id={e.case_id}>Open</CaseLink> : <span className="act-sub">-</span>}</td>
                    </tr>
                    {open === e.id && (
                      <tr><td colSpan={4}>
                        <div className="act-detail">
                          {e.after?.result ? (<>
                            <b>{e.after.result.summary}</b>
                            <ul>{e.after.result.hypotheses?.map((h: any, i: number) => <li key={i}><b>{h.cause}</b>: {h.verdict} ({h.cited_ids?.length ?? 0} cited). {h.reasoning}</li>)}</ul>
                            <small>Model {e.after.model}{e.after.via ? ` via ${e.after.via}` : ''} · {e.after.tool_calls?.length ?? 0} tool calls · {(e.after.input_tokens + e.after.output_tokens).toLocaleString()} tokens{typeof e.after.cost_usd === 'number' ? ` · $${e.after.cost_usd.toFixed(3)}` : ''}{e.after.result.rejected_citations?.length ? ` · ${e.after.result.rejected_citations.length} citations rejected` : ''}</small>
                          </>) : e.after ? <pre>{JSON.stringify(e.after, null, 2)}</pre> : <small>No further detail recorded.</small>}
                          <small>Audit event #{(e as any).seq} · {new Date(e.created_at).toLocaleString()}{e.after && <> · <CopyButton value={JSON.stringify(e.after, null, 2)} label="Copy detail" /></>}</small>
                        </div>
                      </td></tr>
                    )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))}

      {loaded && events.length < total && nextPage <= pages && (
        <div className="act-more">
          <span className="hint">Showing the newest {events.length.toLocaleString()} of {total.toLocaleString()} events.</span>
          <button className="btn-secondary" disabled={loadingMore} onClick={loadOlder}>{loadingMore ? 'Loading…' : 'Load older events'}</button>
        </div>
      )}
      </>
    </div>
  );
}
