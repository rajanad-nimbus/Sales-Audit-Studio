'use client';

import { Fragment, useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api, AuditRow } from '@/lib/api';
import { CopyButton, SkeletonRows, useToast } from '@/components/Feedback';
import { CaseLink } from '@/components/MeProvider';

interface Verify { checked: number; intact: boolean; broken_events: number; first_broken_seq: number | null; head_hash: string; head_seq: number }

export default function AuditPage() {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [f, setF] = useState({ event_type: '', actor: '', q: '', date_from: '', date_to: '' });
  const [v, setV] = useState<Verify | null>(null);
  const [checking, setChecking] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const toast = useToast();

  const params = (extra: Record<string, string | number> = {}) => {
    const p = new URLSearchParams();
    Object.entries({ ...f, ...extra }).forEach(([k, val]) => { if (val !== '') p.set(k, String(val)); });
    return p.toString();
  };
  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/api/audit/page?${params({ page, page_size: 50 })}`);
      setRows(data.items); setTotal(data.total); setPages(data.pages); setTypes(data.event_types); setError(null);
    } catch { setError('Could not load the audit trail.'); }
    finally { setLoaded(true); }
  }, [f, page]);
  const verify = useCallback(async () => {
    setChecking(true);
    try { setV((await api.get('/api/audit/verify')).data); } catch { setV(null); }
    setChecking(false);
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { verify(); }, [verify]);
  useEffect(() => { setPage(1); }, [f]);

  const download = async (format: 'csv' | 'json') => {
    try {
      const { data } = await api.get(`/api/audit/export?${params({ format })}`, { responseType: 'blob' });
      const a = document.createElement('a'); const url = URL.createObjectURL(data); a.href = url; a.download = `audit-trail.${format}`; a.click();
      URL.revokeObjectURL(url); toast(`Exported audit-trail.${format} (filters applied).`, 'success');
    } catch { toast('Export failed.', 'error'); }
  };
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const preset = (days: number | null) => set(days === null ? { date_from: '', date_to: '' } : { date_from: iso(new Date(Date.now() - days * 86400000)), date_to: iso(new Date()) });
  const activePreset = !f.date_from && !f.date_to ? null : f.date_to === iso(new Date()) ? Math.round((Date.now() - new Date(f.date_from).getTime()) / 86400000) : -1;
  const hasFilter = Object.values(f).some(Boolean);
  const set = (patch: Partial<typeof f>) => setF((x) => ({ ...x, ...patch }));

  return (
    <div className="page-container">
      <div className="page-header">
        <div><h1>Audit Trail</h1><span className="cc-live">Append-only. Each event is chained to the one before it.</span></div>
        <div className="cc-controls">
          <button className="btn-secondary btn-small" onClick={() => download('csv')}>Export CSV</button>
          <button className="btn-secondary btn-small" onClick={() => download('json')}>Export JSON</button>
        </div>
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      <div className={`ex-integrity ${v ? (v.intact ? 'ok' : 'bad') : ''}`}>
        {!v ? 'Checking integrity…' : v.intact ? (
          <><b>Chain intact.</b> {v.checked.toLocaleString()} events verified. Head #{v.head_seq}: <code>{v.head_hash.slice(0, 16)}…</code></>
        ) : (
          <><b>Integrity problem.</b> {v.broken_events} event(s) do not match their recorded hash, starting at #{v.first_broken_seq}. Treat the trail as compromised and investigate.</>
        )}
        <button className="ac-link" onClick={verify} disabled={checking}>{checking ? 'Verifying…' : 'Verify now'}</button>
      </div>

      <div className="ac-filters">
        <input className="ac-search" placeholder="Search descriptions" value={f.q} onChange={(e) => set({ q: e.target.value })} aria-label="Search" />
        <select value={f.event_type} onChange={(e) => set({ event_type: e.target.value })} aria-label="Event type">
          <option value="">Any event</option>{types.map((t) => <option key={t} value={t}>{t.replace(/_/g, ' ').toLowerCase()}</option>)}
        </select>
        <input placeholder="Actor" value={f.actor} onChange={(e) => set({ actor: e.target.value })} aria-label="Actor" style={{ width: 150 }} />
        <span className="date-presets" role="group" aria-label="Date range">
          {([['Any time', null], ['Today', 0], ['7 days', 7], ['30 days', 30]] as [string, number | null][]).map(([label, d]) => (
            <button key={label} className={activePreset === d ? 'on' : ''} onClick={() => preset(d)}>{label}</button>
          ))}
        </span>
        <input type="date" value={f.date_from} max={f.date_to || undefined} onChange={(e) => set({ date_from: e.target.value })} aria-label="From date" title="From date" />
        <input type="date" value={f.date_to} min={f.date_from || undefined} onChange={(e) => set({ date_to: e.target.value })} aria-label="To date" title="To date" />
        {hasFilter && <button className="btn-secondary btn-sm" onClick={() => setF({ event_type: '', actor: '', q: '', date_from: '', date_to: '' })}>Clear filters</button>}
      </div>

      {!loaded ? <SkeletonRows rows={8} label="Loading audit trail" /> : <div className="table-container">
        <table className="data-table">
          <thead><tr><th>#</th><th>Time</th><th>Event</th><th>Actor</th><th>What happened</th><th>Case</th><th>Hash</th></tr></thead>
          <tbody>
            {rows.map((e) => (
              <Fragment key={e.id}>
                <tr className={`ac-row ${open === e.id ? 'open' : ''}`} tabIndex={0} aria-expanded={open === e.id} onKeyDown={(k) => { if (k.key === 'Enter') setOpen(open === e.id ? null : e.id); }} onClick={() => setOpen(open === e.id ? null : e.id)}>
                  <td className="text-sm">{e.seq}</td>
                  <td className="text-sm nowrap">{new Date(e.created_at).toLocaleString()}</td>
                  <td><span className={`badge badge-sm ${/FAIL|ERROR|REJECT|BREACH|QUARANTIN/i.test(e.event_type) ? 'badge-error' : /APPROV|CLOSED|COMPLETE|VALIDAT|RESOLV/i.test(e.event_type) ? 'badge-success' : /CREATED|RAISED|RECEIVED|EXPORT/i.test(e.event_type) ? 'badge-info' : 'badge-secondary'}`}>{e.event_type.replace(/_/g, ' ').toLowerCase()}</span></td><td>{e.actor}</td><td>{e.description}</td>
                  <td onClick={(x) => x.stopPropagation()}>{e.case_id ? <CaseLink id={e.case_id}>Open</CaseLink> : '-'}</td>
                  <td><code>{e.hash?.slice(0, 8)}</code></td>
                </tr>
                {open === e.id && (
                  <tr><td colSpan={7} className="ex-detail">
                    <div>Hash <code>{e.hash}</code> <CopyButton value={e.hash ?? ''} /></div><div>Previous <code>{e.prev_hash ?? 'None (first entry)'}</code></div>
                    <div>Object {e.object_type} <code>{e.object_id}</code></div>
                  </td></tr>
                )}
              </Fragment>
            ))}
            {rows.length === 0 && <tr><td colSpan={7} className="cc-empty empty-row">No events match.</td></tr>}
          </tbody>
        </table>
        <div className="ac-pager pager-sticky">
          <span>{total.toLocaleString()} event(s)</span><span className="ac-spacer" />
          <button className="btn-secondary btn-small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <span>Page {page} of {pages.toLocaleString()}</span>
          <button className="btn-secondary btn-small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      </div>}
    </div>
  );
}
