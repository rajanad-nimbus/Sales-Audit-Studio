'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api, CaseDetail, Validation, Workflow, money } from '@/lib/api';

export function CaseDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const [d, setD] = useState<CaseDetail | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => { api.get(`/api/cases/${id}`).then((r) => setD(r.data)).catch(() => setD(null)); }, [id]);
  useEffect(() => { setD(null); setMsg(null); load(); }, [id, load]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setMsg(null);
    try { await fn(); } catch (e: any) { setMsg(e?.response?.data?.detail ?? 'Action failed'); }
    setBusy(false); load(); onChanged();
  };
  const c = d?.case;
  const rec = d?.recommendations.find((r) => r.status === 'Proposed');
  const decide = (decision: string) => run(() => api.post('/api/decisions', {
    case_id: id, recommendation_id: rec!.id, decision_type: 'Finance', decision, actor: 'finance.user', authority_check: 'Passed' }));
  const execute = () => run(async () => {
    const w = ((await api.get(`/api/workflows?case_id=${id}`)).data as Workflow[]).find((x) => x.state === 'Pending');
    if (w) await api.post(`/api/workflows/${w.id}/execute`);
  });
  const verify = () => run(async () => {
    const v = ((await api.get(`/api/validations?case_id=${id}`)).data as Validation[]).find((x) => x.status !== 'Complete');
    if (v) await api.post(`/api/validations/${v.id}/run`);
  });

  return (
    <aside className="ac-drawer" aria-label="Case preview">
      <div className="ac-drawer-head">
        <div><b>{c ? c.case_type : 'Loading…'}</b><small>{c ? `${c.case_number} · ${c.store_id} · ${c.business_date}` : ''}</small></div>
        <button className="hdr-btn" onClick={onClose} aria-label="Close preview">✕</button>
      </div>
      {c && d && (
        <div className="ac-drawer-body">
          <div className="cc-money">
            <div><small>Exception amount</small><b>{money(c.total_exception_amount)}</b></div>
            <div><small>Possible loss</small><b>{money(c.total_exposure)}</b></div>
            <div><small>Status</small><b style={{ fontSize: '.95rem' }}>{c.status}</b></div>
            <div><small>Assigned</small><b style={{ fontSize: '.95rem' }}>{c.assigned_to ?? 'Unassigned'}</b></div>
          </div>
          {msg && <div className="alert alert-warning">{msg}</div>}
          {d.findings[0] && <p className="cc-find">{d.findings[0].conclusion}</p>}
          {d.exceptions.length === 0 && <p className="cc-empty">No exception records are attached to this case.</p>}

          {c.status === 'Open' && d.exceptions.length > 0 && (
            <button className="btn-primary" disabled={busy} onClick={() => run(() => api.post(`/api/cases/${id}/investigate`))}>Run investigation</button>
          )}
          {c.status === 'In Review' && rec && (
            <>
              <div className="cc-conseq">
                <div><small>Proposed</small><span>{rec.disposition_type}, {rec.action_class}</span></div>
                <div><small>What happens</small><span>{rec.expected_workflow}</span></div>
                <div><small>Financial movement</small><span>{Number(rec.financial_impact) === 0 ? 'None' : money(rec.financial_impact)}</span></div>
              </div>
              <div className="cc-actions">
                <button className="btn-primary" disabled={busy} onClick={() => decide('Approved')}>Approve</button>
                <button className="btn-secondary" disabled={busy} onClick={() => decide('Escalated')}>Escalate</button>
              </div>
            </>
          )}
          {c.status === 'In Review' && !rec && <p className="cc-empty">Awaiting a recommendation.</p>}
          {c.status === 'Resolving' && <button className="btn-primary" disabled={busy} onClick={execute}>Carry out action</button>}
          {c.status === 'Pending Validation' && <button className="btn-primary" disabled={busy} onClick={verify}>Verify result</button>}
          <Link href={`/cases/${id}`} className="ac-open">Open full case</Link>
        </div>
      )}
    </aside>
  );
}
