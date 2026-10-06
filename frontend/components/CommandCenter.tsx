'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { SkeletonRows } from '@/components/Feedback';
import { useDataChanged } from '@/lib/sync';
import { useCanOpen, useMe } from '@/components/MeProvider';
import { useChat } from '@/components/Chat';
import { PriorityBadge, StatusBadge, Evidence } from '@/components/CaseTable';
import { api, ACRow, Case, CaseDetail, Connector, Validation, Workflow, money } from '@/lib/api';

type Persona = 'finance' | 'it';

const STAGES = [
  { key: 'detected', label: 'Detected' },
  { key: 'investigating', label: 'Investigating' },
  { key: 'decision', label: 'Ready for decision' },
  { key: 'acting', label: 'Acting' },
  { key: 'validating', label: 'Validating' },
  { key: 'closed', label: 'Closed' },
];

const stageOf = (c: Case) => {
  if (c.status === 'Closed') return 'closed';
  if (c.status === 'Pending Validation') return 'validating';
  if (c.status === 'Resolving') return 'acting';
  if (c.status === 'In Review') return 'decision';
  if (c.status === 'In Investigation') return 'investigating';
  return 'detected';
};

export function CommandCenter() {
  const router = useRouter();
  // The view follows who you are acting as (use Impersonate in the user menu); there is no separate switch.
  const { me } = useMe();
  const canOpen = useCanOpen();
  const persona: Persona = me?.role === 'it' ? 'it' : 'finance';
  const [items, setItems] = useState<Record<string, Case[]>>({ decision: [], progress: [], done: [], stage: [] });
  const [totals, setTotals] = useState<Record<string, number>>({});
  const [tab, setTab] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [kpi, setKpi] = useState({ amount: 0, exposure: 0, decision: 0, closed: 0 });
  const [daily, setDaily] = useState({ prepared: 0, awaitingEvidence: 0 });
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [validations, setValidations] = useState<Validation[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [stageFilter, setStageFilter] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<string | null>(null);
  const { setCaseId } = useChat();
  useEffect(() => { setCaseId(selected); }, [selected, setCaseId]);

  const STATUS_OF: Record<string, string> = { detected: 'Open', investigating: 'In Investigation', decision: 'In Review', acting: 'Resolving', validating: 'Pending Validation', closed: 'Closed' };
  const toCase = (r: ACRow) => ({ id: r.id, case_type: r.case_type, case_number: r.case_number, store_id: r.store_id,
    total_exception_amount: r.amount, evidence_completeness: r.evidence, investigation_status: r.investigation_status, priority: r.priority, status: r.status } as unknown as Case);

  const refresh = useCallback(async () => {
    const tot: Record<string, number> = {};
    const top = async (query: string, key: string) => {
      const { data } = await api.get(`/api/action-center/cases?${query}&page_size=5`);
      tot[key] = data.total;
      return (data.items as ACRow[]).map(toCase);
    };
    try {
      const stageQ = stageFilter ? `status=${encodeURIComponent(STATUS_OF[stageFilter])}&stage=&sort=exposure&dir=desc` : '';
      const [sm, fc, d, p, dn, st, k, w, v] = await Promise.all([
        api.get('/api/action-center/summary'), api.get('/api/action-center/facets?stage='),
        top('stage=decision&sort=exposure&dir=desc', 'decision'), top('stage=progress&sort=sla&dir=asc', 'progress'), top('status=Closed&sort=age&dir=asc', 'done'),
        stageFilter ? top(stageQ, 'stage') : Promise.resolve([] as Case[]),
        api.get('/api/connectors'), api.get('/api/workflows'), api.get('/api/validations'),
      ]);
      const byStatus = Object.fromEntries((fc.data.status as { value: string; count: number }[]).map((x) => [x.value, x.count]));
      setCounts(Object.fromEntries(Object.entries(STATUS_OF).map(([key, status]) => [key, byStatus[status] ?? 0])));
      setKpi({ amount: Number(sm.data.open_amount), exposure: Number(sm.data.open_exposure), decision: sm.data.needs_decision, closed: sm.data.closed });
      setDaily({ prepared: sm.data.agent_prepared ?? 0, awaitingEvidence: sm.data.agent_waiting_evidence ?? 0 });
      setItems({ decision: d, progress: p, done: dn, stage: st }); setTotals(tot);
      setConnectors(k.data); setWorkflows(w.data); setValidations(v.data);
      setError(null); setLoaded(true); setTick((t) => t + 1);
    } catch { setError('Backend unreachable. Retrying.'); }
  }, [stageFilter]);

  useDataChanged(() => { refresh(); });
  useEffect(() => {
    refresh();
    const tick = () => { if (!document.hidden) refresh(); };
    const t = setInterval(tick, 15000);
    document.addEventListener('visibilitychange', tick);
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', tick); };
  }, [refresh]);

  useEffect(() => {
    setAnswer(null);
    if (!selected) { setDetail(null); return; }
    api.get(`/api/cases/${selected}`).then((r) => setDetail(r.data)).catch(() => setDetail(null));
  }, [selected, tick]);

  const act = async (fn: () => Promise<unknown>) => {
    try { setNotice(null); await fn(); }
    catch (e: any) { setNotice(e?.response?.data?.detail ?? 'Action failed'); }
    refresh();
  };

  const decide = (c: Case, recId: string, decision: string) => act(() => api.post('/api/decisions', {
    case_id: c.id, recommendation_id: recId, decision_type: persona === 'finance' ? 'Finance' : 'IT',
    decision, actor: persona === 'finance' ? 'finance.user' : 'it.operator', authority_check: 'Passed',
  }));

  const ask = () => {
    if (!detail) { setAnswer('Select a case first. Answers come from the selected case only.'); return; }
    const { case: c, findings, recommendations, exceptions } = detail;
    const q = question.toLowerCase();
    const rec = recommendations[0];
    if (/why|cause|root|finding/.test(q))
      setAnswer(findings.length ? `${findings[0].conclusion} (${findings[0].confidence} confidence)` : 'No finding yet. Run an investigation first.');
    else if (/exposure|loss|amount|money|impact/.test(q))
      setAnswer(`Exception amount ${money(c.total_exception_amount)}; estimated loss exposure ${money(c.total_exposure)}.${exceptions[0] ? ` Close impact: ${exceptions[0].close_impact}.` : ''}`);
    else if (/next|status|what now|should/.test(q))
      setAnswer(rec ? `Stage: ${stageOf(c)}. Recommendation: ${rec.disposition_type} via ${rec.action_class} (${rec.status}). ${rec.expected_workflow}.` : `Stage: ${stageOf(c)}. Investigation is the next step.`);
    else if (/evidence/.test(q))
      setAnswer(`Evidence completeness is ${c.evidence_completeness}%, computed from the required evidence.`);
    else setAnswer('Ask about the cause ("why"), money ("exposure"), next steps ("what next") or evidence. I cannot approve or execute anything.');
  };

  const amount = kpi.amount;
  const exposure = kpi.exposure;
  const queues = stageFilter
    ? [{ title: STAGES.find((x) => x.key === stageFilter)!.label, key: 'stage', items: items.stage }]
    : persona === 'finance'
      ? [{ title: 'Ready for your decision', key: 'decision', items: items.decision }, { title: 'In progress', key: 'progress', items: items.progress }, { title: 'Recently closed', key: 'done', items: items.done }]
      : [{ title: 'Needs intervention', key: 'progress', items: items.progress }, { title: 'Awaiting decision', key: 'decision', items: items.decision }, { title: 'Resolved', key: 'done', items: items.done }];

  const sc = detail?.case;
  const selWf = workflows.filter((w) => w.case_id === selected);
  const selVal = validations.filter((v) => v.case_id === selected);
  const rec = detail?.recommendations.find((r) => r.status === 'Proposed');
  const steps = ['Detected', 'Evidence', 'Recommendation', 'Decision', 'Action', 'Validation', 'Closed'];
  const stepIdx = !sc ? 0 : { detected: 0, investigating: 1, decision: 3, acting: 4, validating: 5, closed: 6 }[stageOf(sc)]!;

  return (
    <div className="cc">
      <div className="cc-top">
        <div>
          <h1>Command Center</h1>
          <span className="cc-live"><i className="dot on" />Case data updates with the nightly batch; actions show immediately</span>
        </div>
      </div>

      {error && <div className="alert alert-error" role="alert">{error}<button className="btn-secondary btn-sm alert-close" onClick={refresh}>Retry now</button></div>}
      {notice && <div className="alert alert-warning" role="alert">{notice}<button className="alert-close" aria-label="Dismiss" onClick={() => setNotice(null)}>×</button></div>}

      <section className="cc-card" style={{ marginBottom: '1rem' }}>
        <div className="cc-head"><div><h2>Today’s operating plan</h2><p className="page-sub">Start with data readiness, then balance Store Days, then review only the cases Nimbus has prepared for you.</p></div></div>
        <div className="cc-pipeline" aria-label="Daily operating flow">
          <Link className="cc-stage has" href="/pipeline"><b>1</b><small>Confirm data arrived</small><small className="pl-sub">POS and simulated feeds</small></Link>
          <Link className="cc-stage has" href="/store-days"><b>2</b><small>Balance Store Days</small><small className="pl-sub">Re-total and resolve blockers</small></Link>
          <button className="cc-stage has" onClick={() => setStageFilter('decision')}><b>{daily.prepared}</b><small>Agent prepared</small><small className="pl-sub">Ready for your review</small></button>
          <Link className="cc-stage has" href="/actions"><b>{kpi.decision}</b><small>Your decisions</small><small className="pl-sub">Approve, reject, or escalate</small></Link>
        </div>
        {daily.awaitingEvidence > 0 && <p className="hint" style={{ marginTop: '.7rem' }}>Nimbus is waiting for evidence on {daily.awaitingEvidence} case{daily.awaitingEvidence === 1 ? '' : 's'}; open <Link href="/actions">Action Center</Link> to assign or request it.</p>}
        <p className="hint" style={{ marginTop: '.45rem' }}>Nimbus can ingest, reconcile, collect linked evidence, and propose a disposition. People retain control of corrections, approvals, closures, and financial decisions.</p>
      </section>

      <div className="cc-kpis">
        <div className="cc-kpi"><span>Open exception amount</span><b>{loaded ? money(amount) : <i className="skeleton" />}</b></div>
        <div className="cc-kpi"><span>Estimated loss exposure</span><b>{loaded ? money(exposure) : <i className="skeleton" />}</b></div>
        <div className="cc-kpi"><span>Awaiting your decision</span><b>{loaded ? kpi.decision.toLocaleString() : <i className="skeleton" />}</b></div>
        <div className="cc-kpi"><span>Closed</span><b>{loaded ? kpi.closed.toLocaleString() : <i className="skeleton" />}</b></div>
      </div>

      <div className="cc-pipeline">
        {STAGES.map((s) => (
          <button key={s.key} className={`cc-stage ${stageFilter === s.key ? 'on' : ''}`}
            onClick={() => setStageFilter(stageFilter === s.key ? null : s.key)}>
            <b>{(counts[s.key] ?? 0).toLocaleString()}</b><small>{s.label}</small>
          </button>
        ))}
      </div>

      {stageFilter && (
        <div className="cc-filter-note">Showing stage: <b>{STAGES.find((x) => x.key === stageFilter)?.label}</b>
          <button className="btn-secondary btn-sm" onClick={() => setStageFilter(null)}>Clear filter</button></div>
      )}

      <div className="cc-queues">
          {persona === 'it' && (
            <div className="cc-card">
              <h3>Connector health</h3>
              {connectors.map((k) => (
                <div key={k.id} className={`cc-conn ${k.state === 'Healthy' ? 'ok' : 'warn'}`}>
                  <i className="dot on" />
                  <div><b>{k.name}</b><small>{k.state} · {k.mode}</small></div>
                  <div className="cc-mini">
                    <button onClick={() => act(() => api.post(`/api/connectors/${k.id}/retry`))}>Retry</button>
                    <button onClick={() => act(() => api.post(`/api/connectors/${k.id}/${k.state === 'Paused' ? 'resume' : 'pause'}`))}>
                      {k.state === 'Paused' ? 'Resume' : 'Pause'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
          {(() => {
            const q = queues[Math.min(tab, queues.length - 1)];
            const total = totals[q.key] ?? q.items.length;
            return (
              <div className="cc-queue-table">
                <div className="cc-tabs" role="tablist">
                  {queues.map((x, n) => (
                    <button key={x.key} role="tab" aria-selected={x === q} className={x === q ? 'on' : ''} onClick={() => setTab(n)}>
                      {x.title}<span>{(totals[x.key] ?? x.items.length).toLocaleString()}</span>
                    </button>
                  ))}
                </div>
                {q.items.length === 0 && (loaded
                  ? <div className="empty-state"><b>Nothing here{stageFilter ? ' for this stage' : ''}</b>
                      <p>{stageFilter ? 'Try another stage or clear the filter.' : 'Cases appear after the nightly batch runs.'}</p>
                      {!stageFilter && <Link href="/pipeline">Open Data Pipeline</Link>}</div>
                  : <SkeletonRows rows={5} label="Loading cases" />)}
                {q.items.length > 0 && <div className="table-container"><table className="data-table">
                  <thead><tr><th>Case</th><th>Type</th><th>Store</th><th className="num">Exception</th><th className="num">Evidence</th><th>Agent status</th><th>Priority</th><th>Status</th></tr></thead>
                  <tbody>{q.items.map((c) => (
                    <tr key={c.id} className={canOpen('/cases') ? 'row-link' : ''} onClick={() => { if (canOpen('/cases')) router.push(`/cases/${c.id}`); }} tabIndex={canOpen('/cases') ? 0 : -1}
                      onKeyDown={(e) => { if (canOpen('/cases') && (e.key === 'Enter' || e.key === ' ')) router.push(`/cases/${c.id}`); }}>
                      <td className="font-medium">{c.case_number}</td><td>{c.case_type}</td><td>{c.store_id}</td>
                      <td className="num">{money(c.total_exception_amount)}</td><td className="num"><Evidence pct={c.evidence_completeness} /></td><td><small>{({ 'Ready for Decision': 'Prepared', 'Awaiting Evidence': 'Needs evidence', 'In Investigation': 'Working' } as Record<string,string>)[(c as any).investigation_status] ?? 'Not started'}</small></td>
                      <td><PriorityBadge p={c.priority} /></td>
                      <td><StatusBadge s={c.status} /></td>
                    </tr>
                  ))}</tbody>
                </table></div>}
                {total > q.items.length && <Link className="ac-open" href="/actions">See all {total.toLocaleString()} in Action Center</Link>}
              </div>
            );
          })()}
      </div>
    </div>
  );
}
