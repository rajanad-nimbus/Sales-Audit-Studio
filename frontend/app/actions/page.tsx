'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ACRow, BatchStatus, compact, money } from '@/lib/api';
import { useRouter } from 'next/navigation';
import { useDataChanged } from '@/lib/sync';

interface F { q: string; stage: string; case_type: string; store: string; priority: string; assignee: string; sla: string; min_amount: string; date_from: string; date_to: string; snoozed: string }
const EMPTY: F = { q: '', stage: 'open', case_type: '', store: '', priority: '', assignee: '', sla: '', min_amount: '', date_from: '', date_to: '', snoozed: '' };
type Facets = Record<'status' | 'priority' | 'case_type' | 'store', { value: string; count: number }[]>;
interface Summary { needs_decision: number; sla_breached: number; at_risk: number; unassigned: number; mine: number; snoozed: number; open_total: number; open_exposure: string; stores_affected: number; closed: number; straight_through_rate: number | null; by_day: { date: string; count: number }[] }
interface Group { key: string; count: number; amount: string; exposure: string; breached: number; oldest_hours: number }

const PRESETS: { name: string; f: Partial<F>; sort?: string; dir?: string }[] = [
  { name: 'Needs decision', f: { stage: 'decision' }, sort: 'exposure', dir: 'desc' },
  { name: 'SLA breached', f: { sla: 'breached' }, sort: 'sla', dir: 'asc' },
  { name: 'Unassigned', f: { assignee: 'unassigned' }, sort: 'exposure', dir: 'desc' },
  { name: 'My queue', f: { assignee: 'me' }, sort: 'sla', dir: 'asc' },
  { name: 'High exposure', f: { min_amount: '1000', stage: 'decision' }, sort: 'exposure', dir: 'desc' },
  { name: 'Snoozed', f: { snoozed: 'only' }, sort: 'sla', dir: 'asc' },
];
const GROUPS = [['', 'None'], ['case_type', 'Type'], ['store', 'Store'], ['day', 'Day'], ['priority', 'Priority'], ['assignee', 'Assignee']];
const FILTER_FOR_GROUP: Record<string, keyof F> = { case_type: 'case_type', store: 'store', priority: 'priority', assignee: 'assignee', day: 'date_from' };
const ACTIONS: { id: string; label: string; params?: object; danger?: boolean }[] = [
  { id: 'assign', label: 'Assign to me', params: { assignee: 'me' } },
  { id: 'investigate', label: 'Investigate' },
  { id: 'approve', label: 'Approve' },
  { id: 'escalate', label: 'Escalate' },
  { id: 'advance', label: 'Advance' },
  { id: 'snooze', label: 'Snooze 24h', params: { hours: 24 } },
  { id: 'unassign', label: 'Unassign' },
];

const qs = (f: F, extra: Record<string, string | number> = {}) => {
  const p = new URLSearchParams();
  Object.entries({ ...f, ...extra }).forEach(([k, v]) => { if (v !== '' && v !== undefined) p.set(k, String(v)); });
  return p.toString();
};
const slaText = (r: ACRow) => {
  if (r.sla_state === 'none' || !r.sla_due_at) return '-';
  const h = Math.round((new Date(r.sla_due_at).getTime() - Date.now()) / 3600000);
  return h < 0 ? `${-h}h over` : `${h}h left`;
};

export default function ActionCenter() {
  const [f, setF] = useState<F>(EMPTY);
  const skipReset = useRef(1); // effect runs to ignore: the mount, plus one more if a saved view is restored
  const [restored, setRestored] = useState(false);
  useEffect(() => {
    // Restore the last view (so Back from a case lands in the same list); an explicit ?q= wins.
    try {
      const saved = JSON.parse(sessionStorage.getItem('nimbus_actions_view') || 'null');
      if (saved) { skipReset.current += 1; setF({ ...EMPTY, ...saved.f }); if (saved.sort) setSort(saved.sort); setGroupBy(saved.groupBy ?? ''); setPageSize(saved.pageSize ?? 50); setPage(saved.page ?? 1); }
    } catch { /* ignore */ }
    const q = new URLSearchParams(window.location.search).get('q'); if (q) setF((x) => ({ ...x, q }));
    setRestored(true);
  }, []);
  const [sort, setSort] = useState({ sort: 'sla', dir: 'asc' });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [groupBy, setGroupBy] = useState('');
  const [rows, setRows] = useState<ACRow[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [groups, setGroups] = useState<Group[]>([]);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [batch, setBatch] = useState<BatchStatus | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [allMatching, setAllMatching] = useState(false);
  const [cursor, setCursor] = useState(0);
  const router = useRouter();
  const openCase = (id: string) => router.push(`/cases/${id}`);
  const [confirm, setConfirm] = useState<{ id: string; label: string; params?: object; ids?: string[] } | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [proposed, setProposed] = useState<{ case_id: string; case_number: string; amount: string }[]>([]);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [views, setViews] = useState<{ name: string; f: F; sort: typeof sort }[]>([]);
  const seq = useRef(0);

  useEffect(() => { try { setViews(JSON.parse(localStorage.getItem('nimbus_views') || '[]')); } catch { /* ignore */ } }, []);
  const persistViews = (v: typeof views) => { setViews(v); try { localStorage.setItem('nimbus_views', JSON.stringify(v)); } catch { /* ignore */ } };

  const load = useCallback(async () => {
    const mine = ++seq.current;
    setLoading(true);
    try {
      const calls: Promise<any>[] = [api.get(`/api/action-center/facets?${qs(f)}`), api.get('/api/action-center/summary'), api.get('/api/batch/status')];
      if (groupBy) calls.push(api.get(`/api/action-center/groups?${qs(f, { by: groupBy })}`));
      else calls.push(api.get(`/api/action-center/cases?${qs(f, { ...sort, page, page_size: pageSize })}`));
      const [fc, sm, bs, main] = await Promise.all(calls);
      if (mine !== seq.current) return;
      setFacets(fc.data); setSummary(sm.data); setBatch(bs.data);
      if (groupBy) setGroups(main.data);
      else { setRows(main.data.items); setTotal(main.data.total); setPages(main.data.pages); }
      setError(null);
      // Agent-nominated cases; finance/admin only, so a 403 just means no banner.
      api.get('/api/agent/eligible').then((r) => { if (mine === seq.current) setProposed(r.data.cases); }).catch(() => setProposed([]));
    } catch (e: any) {
      if (mine === seq.current) setError(e?.response?.status === 401 ? 'Please sign in to use the Action Center.' : e?.response?.status === 403 ? 'Your role cannot view the Action Center.' : 'Could not load data.');
    }
    if (mine === seq.current) setLoading(false);
  }, [f, sort, page, pageSize, groupBy]);

  useEffect(() => { load(); }, [load]);
  useDataChanged(() => { load(); });
  useEffect(() => {
    const tick = () => { if (!document.hidden) load(); };
    const t = setInterval(tick, 30000);
    document.addEventListener('visibilitychange', tick);
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', tick); };
  }, [load]);
  useEffect(() => {
    if (skipReset.current > 0) { skipReset.current -= 1; return; }
    setPage(1); setSelected(new Set()); setAllMatching(false); setCursor(0);
  }, [f, groupBy, pageSize]);
  useEffect(() => {
    if (!restored) return;
    try { sessionStorage.setItem('nimbus_actions_view', JSON.stringify({ f, sort, groupBy, pageSize, page })); } catch { /* ignore */ }
  }, [restored, f, sort, groupBy, pageSize, page]);

  const set = (patch: Partial<F>) => setF((x) => ({ ...x, ...patch }));
  const applyPreset = (p: (typeof PRESETS)[number]) => { setF({ ...EMPTY, ...p.f }); setSort({ sort: p.sort ?? 'sla', dir: p.dir ?? 'asc' }); setGroupBy(''); };
  const advCount = (['case_type', 'store', 'priority', 'sla', 'min_amount', 'date_from', 'date_to'] as (keyof F)[]).filter((k) => f[k] !== '').length;
  const chips = (Object.keys(f) as (keyof F)[]).filter((k) => f[k] !== EMPTY[k]);
  const toggleSort = (key: string) => setSort((s) => s.sort === key ? { sort: key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { sort: key, dir: key === 'sla' || key === 'store' || key === 'type' ? 'asc' : 'desc' });

  const pageIds = rows.map((r) => r.id);
  const allOnPage = pageIds.length > 0 && pageIds.every((i) => selected.has(i));
  const count = allMatching ? total : selected.size;
  const selectedExposure = useMemo(() => rows.filter((r) => selected.has(r.id)).reduce((s, r) => s + Number(r.exposure), 0), [rows, selected]);

  const runBulk = async () => {
    if (!confirm) return;
    try {
      const body: any = { action: confirm.id, params: confirm.params ?? {} };
      if (allMatching) body.select_all = Object.fromEntries(Object.entries(f).filter(([, v]) => v !== ''));
      else body.ids = confirm.ids ?? [...selected];
      const { data } = await api.post('/api/action-center/bulk', body);
      setResult(data); if (!confirm.ids) { setSelected(new Set()); setAllMatching(false); }
    } catch (e: any) { setResult({ error: e?.response?.data?.detail ?? 'Bulk action failed' }); }
    setConfirm(null); load();
  };

  const download = async () => {
    const { data } = await api.get(`/api/action-center/export.csv?${qs(f, sort)}`, { responseType: 'blob' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(data); a.download = 'action-center.csv'; a.click();
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(t.tagName) || confirm || groupBy) return;
      if (e.key === 'j') setCursor((c) => Math.min(c + 1, rows.length - 1));
      else if (e.key === 'k') setCursor((c) => Math.max(c - 1, 0));
      else if (e.key === 'x' && rows[cursor]) setSelected((s) => { const n = new Set(s); n.has(rows[cursor].id) ? n.delete(rows[cursor].id) : n.add(rows[cursor].id); return n; });
      else if (e.key === 'Enter' && rows[cursor]) openCase(rows[cursor].id);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [rows, cursor, confirm, groupBy]);

  const tile = (label: string, value: string | number, hint: string, onClick?: () => void, tone?: string) => (
    <button className={`ac-tile ${tone ?? ''}`} onClick={onClick} disabled={!onClick}><small>{label}</small><b>{value}</b><em>{hint}</em></button>
  );
  const sortHead = (label: string, key: string) => (
    <th><button className="ac-th" onClick={() => toggleSort(key)}>{label}{sort.sort === key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}</button></th>
  );

  return (
    <div className="ac">
      <div className="ac-main">
        <div className="cc-top">
          <div><h1>Action Center</h1>
            <span className="cc-live">{loading ? 'Updating…' : `${compact(total || 0)} matching`} {batch?.data_as_of && `· data as of ${batch.data_as_of}`}</span></div>
          <div className="cc-controls">
            <button className="btn-secondary btn-small" onClick={load}>Refresh</button>
            <button className="btn-secondary btn-small" onClick={download}>Export CSV</button>
          </div>
        </div>

        {batch && batch.freshness !== 'fresh' && (
          <div className="alert alert-warning">
            {batch.freshness === 'never' ? 'The daily transaction batch has not run yet. Run it from Data Pipeline.' :
              `The last successful batch finished ${Math.round(batch.hours_since_success ?? 0)}h ago. Cases may be out of date.`}
          </div>
        )}
        {batch?.last_failed && <div className="alert alert-error">The most recent batch run failed. Check Data Pipeline for details.</div>}
        {error && <div className="alert alert-error">{error}</div>}

        {summary && (
          <div className="ac-tiles">
            {tile('Needs decision', compact(summary.needs_decision), 'ready for you', () => applyPreset(PRESETS[0]))}
            {tile('SLA breached', compact(summary.sla_breached), `${compact(summary.at_risk)} at risk`, () => applyPreset(PRESETS[1]), summary.sla_breached ? 'bad' : '')}
            {tile('Unassigned', compact(summary.unassigned), 'no owner', () => applyPreset(PRESETS[2]))}
            {tile('My queue', compact(summary.mine), 'assigned to me', () => applyPreset(PRESETS[3]))}
            {tile('Open exposure', '$' + compact(Number(summary.open_exposure)), `${compact(summary.open_total)} open · ${summary.stores_affected} stores`)}
            {tile('Straight-through', summary.straight_through_rate == null ? '-' : `${summary.straight_through_rate}%`, 'closed without manual decision')}
          </div>
        )}

        <div className="ac-presets">
          {PRESETS.map((p) => <button key={p.name} className="ac-chip" onClick={() => applyPreset(p)}>{p.name}</button>)}
          {views.map((v, i) => (
            <span key={v.name + i} className="ac-chip saved">
              <button onClick={() => { setF(v.f); setSort(v.sort); setGroupBy(''); }}>{v.name}</button>
              <button aria-label={`Delete view ${v.name}`} onClick={() => persistViews(views.filter((_, j) => j !== i))}>✕</button>
            </span>
          ))}
          <button className="ac-chip add" onClick={() => { const name = window.prompt('Name this view'); if (name) persistViews([...views, { name, f, sort }]); }}>+ Save view</button>
        </div>

        <div className="ac-filters">
          <input className="ac-search" placeholder="Search case, store or type" value={f.q} onChange={(e) => set({ q: e.target.value })} aria-label="Search" />
          <select value={f.stage} onChange={(e) => set({ stage: e.target.value })} aria-label="Stage">
            <option value="">Any stage (incl. done)</option><option value="open">Open work</option><option value="decision">Needs decision</option><option value="progress">In progress</option><option value="done">Done</option>
          </select>
          <select value={f.assignee} onChange={(e) => set({ assignee: e.target.value })} aria-label="Assignee">
            <option value="">Anyone</option><option value="me">Me</option><option value="unassigned">Unassigned</option>
          </select>
          <button className={`btn-secondary btn-small ac-more${moreOpen ? ' on' : ''}`} onClick={() => setMoreOpen(!moreOpen)} aria-expanded={moreOpen}>
            Filters{advCount > 0 ? ` (${advCount})` : ''} {moreOpen ? '▴' : '▾'}
          </button>
          <div className="cc-seg" aria-label="Group by">
            {GROUPS.map(([k, l]) => <button key={k} className={groupBy === k ? 'on' : ''} onClick={() => setGroupBy(k)}>{k === '' ? 'No grouping' : l}</button>)}
          </div>
        </div>
        {moreOpen && (
          <div className="ac-filters ac-adv">
          <select value={f.case_type} onChange={(e) => set({ case_type: e.target.value })} aria-label="Type">
            <option value="">Any type</option>{facets?.case_type.map((x) => <option key={x.value} value={x.value}>{x.value} ({compact(x.count)})</option>)}
          </select>
          <select value={f.store} onChange={(e) => set({ store: e.target.value })} aria-label="Store">
            <option value="">Any store</option>{facets?.store.map((x) => <option key={x.value} value={x.value}>{x.value} ({compact(x.count)})</option>)}
          </select>
          <select value={f.priority} onChange={(e) => set({ priority: e.target.value })} aria-label="Priority">
            <option value="">Any priority</option>{facets?.priority.map((x) => <option key={x.value} value={x.value}>{x.value} ({compact(x.count)})</option>)}
          </select>
          <select value={f.sla} onChange={(e) => set({ sla: e.target.value })} aria-label="SLA">
            <option value="">Any SLA</option><option value="breached">Breached</option><option value="at_risk">At risk (8h)</option>
          </select>
          <input type="number" min={0} placeholder="Min $" value={f.min_amount} onChange={(e) => set({ min_amount: e.target.value })} aria-label="Minimum amount" style={{ width: 90 }} />
          <input type="date" value={f.date_from} onChange={(e) => set({ date_from: e.target.value })} aria-label="From date" />
          <input type="date" value={f.date_to} onChange={(e) => set({ date_to: e.target.value })} aria-label="To date" />
            <button className="ac-link" onClick={() => set({ case_type: '', store: '', priority: '', sla: '', min_amount: '', date_from: '', date_to: '' })}>Reset these</button>
          </div>
        )}
        {chips.length > 0 && (
          <div className="ac-active">
            {chips.map((k) => <button key={k} className="ac-chip" onClick={() => set({ [k]: EMPTY[k] })}>{k.replace('_', ' ')}: {f[k]} ✕</button>)}
            <button className="ac-link" onClick={() => setF(EMPTY)}>Clear all</button>
          </div>
        )}

        {result && (
          <div className={`alert ${result.error || result.skipped_count ? 'alert-warning' : ''}`}>
            {result.error ? result.error : (
              <>
                <b>{result.action}</b>: {result.ok} of {result.requested} done{result.skipped_count ? `, ${result.skipped_count} skipped` : ''}.
                {result.truncated && ` Limited to ${result.requested} per run (${result.limits?.agent_actions} for agent actions, ${result.limits?.bulk} otherwise). Run again for the rest.`}
                {result.skipped?.length > 0 && <details><summary>Why were some skipped?</summary><ul>{[...new Set(result.skipped.map((s: any) => s.reason))].map((r: any) => <li key={r}>{r} ({result.skipped.filter((s: any) => s.reason === r).length})</li>)}</ul></details>}
              </>
            )}
            <button className="ac-link" style={{ marginLeft: '.75rem' }} onClick={() => setResult(null)}>Dismiss</button>
          </div>
        )}

        {proposed.length > 0 && (
          <div className="ac-bulk" style={{ position: 'static' }}>
            <b>{proposed.length} case{proposed.length === 1 ? '' : 's'} proposed by the agent</b>
            <span>· timing only, no financial movement, evidence complete, findings cited</span>
            <details><summary>Review list</summary>
              <ul>{proposed.map((c) => <li key={c.case_id}><button className="ac-link" onClick={() => openCase(c.case_id)}>{c.case_number}</button> {money(c.amount)}</li>)}</ul>
            </details>
            <span className="ac-spacer" />
            <button className="btn-primary btn-small" onClick={() => setConfirm({ id: 'approve', label: 'Approve agent-proposed cases', ids: proposed.map((c) => c.case_id) })}>Approve {proposed.length}</button>
          </div>
        )}

        {count > 0 && (
          <div className="ac-bulk">
            <b>{compact(count)} selected</b>
            {!allMatching && selectedExposure > 0 && <span>· ${compact(selectedExposure)} exposure on this page</span>}
            {!allMatching && allOnPage && total > rows.length && <button className="ac-link" onClick={() => setAllMatching(true)}>Select all {compact(total)} matching</button>}
            {allMatching && <span>· all matching the current filters</span>}
            <span className="ac-spacer" />
            {ACTIONS.map((a) => <button key={a.id} className="btn-secondary btn-small" onClick={() => setConfirm(a)}>{a.label}</button>)}
            <button className="ac-link" onClick={() => { setSelected(new Set()); setAllMatching(false); }}>Clear</button>
          </div>
        )}

        {groupBy ? (
          <div className="table-container">
            <table className="data-table">
              <thead><tr><th>{GROUPS.find((g) => g[0] === groupBy)?.[1]}</th><th>Cases</th><th>Exception amount</th><th>Exposure</th><th>SLA breached</th><th>Oldest</th></tr></thead>
              <tbody>
                {groups.map((g) => (
                  <tr key={g.key} className="ac-row" onClick={() => { const k = FILTER_FOR_GROUP[groupBy]; set({ [k]: g.key === 'Unassigned' ? 'unassigned' : g.key } as Partial<F>); setGroupBy(''); }}>
                    <td className="font-medium">{g.key}</td><td>{g.count.toLocaleString()}</td><td>{money(g.amount)}</td><td>{money(g.exposure)}</td>
                    <td>{g.breached ? <span className="badge badge-sm badge-error">{g.breached.toLocaleString()}</span> : '-'}</td><td>{g.oldest_hours}h</td>
                  </tr>
                ))}
                {groups.length === 0 && <tr><td colSpan={6} className="cc-empty" style={{ padding: '1rem' }}>No groups match.</td></tr>}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="table-container">
            <table className="data-table">
              <thead><tr>
                <th style={{ width: 36 }}><input type="checkbox" checked={allOnPage} onChange={() => setSelected(allOnPage ? new Set() : new Set(pageIds))} aria-label="Select page" /></th>
                <th>Case</th>{sortHead('Type', 'type')}{sortHead('Store', 'store')}{sortHead('Amount', 'amount')}{sortHead('Exposure', 'exposure')}
                <th>Status</th><th>Owner</th>{sortHead('SLA', 'sla')}{sortHead('Age', 'age')}
              </tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.id} className={`ac-row${cursor === i ? ' cursor' : ''}`} onClick={() => openCase(r.id)}>
                    <td onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={selected.has(r.id)} aria-label={`Select ${r.case_number}`}
                      onChange={() => setSelected((s) => { const n = new Set(s); n.has(r.id) ? n.delete(r.id) : n.add(r.id); return n; })} /></td>
                    <td className="font-medium">{r.case_number}{r.priority !== 'Normal' && <span className={`badge badge-sm ${r.priority === 'Critical' ? 'badge-error' : 'badge-secondary'}`} style={{ marginLeft: 6 }}>{r.priority}</span>}</td>
                    <td>{r.case_type}</td><td>{r.store_id}</td><td>{money(r.amount)}</td><td>{money(r.exposure)}</td>
                    <td>{r.status}</td><td>{r.assigned_to ?? '-'}</td>
                    <td className={r.sla_state === 'breached' ? 'text-error' : r.sla_state === 'at_risk' ? 'ac-warn' : ''}>{slaText(r)}</td>
                    <td>{r.age_hours < 48 ? `${r.age_hours}h` : `${Math.round(r.age_hours / 24)}d`}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={10} className="cc-empty empty-row">{loading ? 'Loading…' : 'Nothing matches these filters.'}</td></tr>}
              </tbody>
            </table>
            <div className="ac-pager">
              <span>{total === 0 ? '0' : `${((page - 1) * pageSize + 1).toLocaleString()}–${Math.min(page * pageSize, total).toLocaleString()}`} of {total.toLocaleString()}</span>
              <span className="ac-spacer" />
              <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))} aria-label="Rows per page">{[25, 50, 100].map((n) => <option key={n} value={n}>{n} / page</option>)}</select>
              <button className="btn-secondary btn-small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
              <span>Page {page} of {pages.toLocaleString()}</span>
              <button className="btn-secondary btn-small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </div>
        )}
        <p className="ac-hint">Shortcuts: j / k move, x select, Enter open, Esc close.</p>
      </div>


      {confirm && (
        <div className="ac-modal" role="dialog" aria-modal="true">
          <div className="ac-modal-box">
            <h2>{confirm.label}</h2>
            <p>This applies to <b>{(confirm.ids?.length ?? count).toLocaleString()}</b> case(s){!confirm.ids && allMatching ? ' matching your current filters' : ''}.</p>
            {['approve', 'escalate', 'investigate', 'advance'].includes(confirm.id) && (
              <p className="ac-hint">Agent and decision actions run on at most 200 cases per run. Approvals above $5,000 of financial movement, or without a policy pass, are skipped and listed afterwards.</p>
            )}
            {allMatching && <p className="ac-hint">Bulk actions touch at most 1,000 cases per run.</p>}
            <div className="cc-actions" style={{ justifyContent: 'flex-end' }}>
              <button className="btn-secondary" onClick={() => setConfirm(null)}>Cancel</button>
              <button className="btn-primary" onClick={runBulk}>Confirm</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
