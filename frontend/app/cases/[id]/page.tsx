'use client';

import { Fragment, use, useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { SkeletonRows, useToast } from '@/components/Feedback';
import { PriorityBadge, StatusBadge } from '@/components/CaseTable';
import { AgentModeNotice, useAgentMode } from '@/components/AgentMode';
import { useMe } from '@/components/UserMenu';
import { api, CaseDetail, EvidenceItem, TimelineItem, Workflow, Validation, PolicyEval, ReconciliationDetail, money } from '@/lib/api';

type Tab = 'overview' | 'records' | 'activity';
interface Collab {
  notes: { id: string; author: string; body: string; created_at: string }[];
  evidence_requests: { id: string; requirement: string; requested_from: string; status: string; created_at: string;
    response?: string | null; reference?: string | null; fulfilled_by?: string | null; fulfilled_at?: string | null;
    rejection_reason?: string | null; override_reason?: string | null; override_financial_impact?: string | null;
    override_requested_by?: string | null; override_requested_at?: string | null; override_approved_by?: string | null; override_approved_at?: string | null }[];
}

const STAGES = ['Open', 'In Investigation', 'In Review', 'Resolving', 'Pending Validation', 'Closed'];
const STAGE_LABEL: Record<string, string> = { 'In Investigation': 'Investigating', 'In Review': 'Decision', 'Pending Validation': 'Validating', Resolving: 'Acting' };
const sevTone = (s: string) => (s === 'Critical' || s === 'High' ? 'badge-error' : s === 'Medium' ? 'badge-warning' : 'badge-secondary');
const outcomeTone = (s: string) => (/allow|pass|approv|within/i.test(s) ? 'badge-success' : /block|deny|reject|fail|breach/i.test(s) ? 'badge-error' : 'badge-warning');
const pretty = (s: string) => s.replace(/_/g, ' ');
const apiError = (e: unknown, fallback: string) => (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
const when = (iso: string) => new Date(iso).toLocaleString();

function slaInfo(due: string | null) {
  if (!due) return { text: 'No SLA', tone: '' };
  const h = Math.round((new Date(due).getTime() - Date.now()) / 3600000);
  if (h < 0) return { text: `${-h}h overdue`, tone: 'bad' };
  return { text: h < 48 ? `${h}h left` : `${Math.round(h / 24)}d left`, tone: h < 8 ? 'warn' : '' };
}

export default function CaseDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const toast = useToast();
  const { me } = useMe();
  const agentMode = useAgentMode();
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [validations, setValidations] = useState<Validation[]>([]);
  const [policies, setPolicies] = useState<PolicyEval[]>([]);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [reconciliation, setReconciliation] = useState<ReconciliationDetail | null>(null);
  const [collab, setCollab] = useState<Collab>({ notes: [], evidence_requests: [] });
  const [tab, setTab] = useState<Tab>('overview');
  const [busy, setBusy] = useState(false);
  const [rationale, setRationale] = useState('');
  const [rationaleError, setRationaleError] = useState(false);
  const [note, setNote] = useState('');
  const [requirement, setRequirement] = useState('');
  const [requestedFrom, setRequestedFrom] = useState('');
  const [matchIds, setMatchIds] = useState<string[]>([]);
  const [matchRationale, setMatchRationale] = useState('');
  const [provideFor, setProvideFor] = useState<string | null>(null);   // requirement being answered
  const [provideNote, setProvideNote] = useState('');
  const [provideRef, setProvideRef] = useState('');

  const load = useCallback(async () => {
    try {
      const [d, w, v, p, e, t, c, r] = await Promise.all([
        api.get(`/api/cases/${id}`), api.get(`/api/workflows?case_id=${id}`), api.get(`/api/validations?case_id=${id}`),
        api.get(`/api/policy-evaluations?case_id=${id}`), api.get(`/api/cases/${id}/evidence`), api.get(`/api/cases/${id}/timeline`),
        api.get(`/api/cases/${id}/collaboration`), api.get(`/api/cases/${id}/reconciliation`),
      ]);
      setDetail(d.data); setWorkflows(w.data); setValidations(v.data); setPolicies(p.data); setEvidence(e.data);
      setTimeline(t.data); setCollab(c.data); setReconciliation(r.data); setError(null);
    } catch { setError('Case not found'); }
  }, [id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const h = window.location.hash.slice(1) as Tab;
    if (h === 'records' || h === 'activity') setTab(h);
  }, []);
  const pickTab = (t: Tab) => { setTab(t); history.replaceState(null, '', `#${t}`); };

  // One place for every mutation: busy lock, success toast, error toast, refresh.
  const act = async (fn: () => Promise<unknown>, success: string): Promise<boolean> => {
    setBusy(true);
    try { await fn(); toast(success, 'success'); await load(); return true; }
    catch (e) { toast(apiError(e, 'That did not work.'), 'error'); return false; }
    finally { setBusy(false); }
  };

  const decide = async (recId: string, decision: 'Approved' | 'Escalated') => {
    if (!rationale.trim()) { setRationaleError(true); document.getElementById('decision-rationale')?.focus(); return; }
    const ok = await act(() => api.post('/api/decisions', {
      case_id: id, recommendation_id: recId, decision_type: 'Finance', decision, actor: 'finance.user', authority_check: 'Pending', rationale,
    }), decision === 'Approved' ? 'Recommendation approved.' : 'Escalated for review.');
    if (ok) setRationale('');
  };
  const investigate = () => act(() => api.post(`/api/cases/${id}/investigate`), 'Investigation finished.');
  const execute = (wfId: string) => act(() => api.post(`/api/workflows/${wfId}/execute`), 'Action executed.');
  const validate = (vId: string) => act(() => api.post(`/api/validations/${vId}/run`), 'Validation finished.');
  const addNote = async () => { if (note.trim() && await act(() => api.post(`/api/cases/${id}/notes`, { body: note }), 'Note added.')) setNote(''); };
  const requestEvidence = async () => {
    if (!requirement.trim() || !requestedFrom.trim()) return;
    if (await act(() => api.post(`/api/cases/${id}/evidence-requests`, { requirement, requested_from: requestedFrom }), 'Evidence requested.')) { setRequirement(''); setRequestedFrom(''); }
  };
  // Reject / override are inline forms keyed by evidence request id.
  const [rejectFor, setRejectFor] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [overrideFor, setOverrideFor] = useState<string | null>(null);
  const [overrideReason, setOverrideReason] = useState('');
  const [overrideImpact, setOverrideImpact] = useState('0');
  const impactValue = Number(overrideImpact);
  const impactOk = overrideImpact.trim() !== '' && Number.isFinite(impactValue) && impactValue >= 0;
  const reject = async () => {
    if (!rejectFor || !rejectReason.trim()) return;
    if (await act(() => api.post(`/api/cases/${id}/evidence-requests/${rejectFor}/reject`, { rationale: rejectReason }), 'Evidence request rejected.')) { setRejectFor(null); setRejectReason(''); }
  };
  const submitOverride = async () => {
    if (!overrideFor || !overrideReason.trim() || !impactOk) return;
    setBusy(true);
    try {
      const { data } = await api.post(`/api/cases/${id}/evidence-requests/${overrideFor}/override`, { rationale: overrideReason, financial_impact: impactValue });
      toast(data.requires_second_approver ? 'Override sent to a second Finance approver.' : 'Evidence gap overridden. The case is closed as Evidence Overridden.', 'success');
      setOverrideFor(null); setOverrideReason(''); setOverrideImpact('0');
      await load();
    } catch (e) { toast(apiError(e, 'Could not record the override.'), 'error'); }
    finally { setBusy(false); }
  };
  const approveOverride = (reqId: string) => act(() => api.post(`/api/cases/${id}/evidence-requests/${reqId}/override/approve`), 'Override approved. The case is closed as Evidence Overridden.');
  const startProvide = (requirement: string) => { setProvideFor(requirement); setProvideNote(''); setProvideRef(''); };
  const provide = async () => {
    if (!provideFor || !provideNote.trim()) return;
    setBusy(true);
    try {
      const { data } = await api.post(`/api/cases/${id}/evidence/provide`, { requirement: provideFor, note: provideNote, reference: provideRef || null });
      toast(data.reevaluated ? `Evidence recorded. Case re-evaluated: ${data.evidence_completeness}% complete.`
        : data.counts_toward_completeness ? 'Evidence recorded. Re-run the investigation to update the case.'
        : 'Recorded, but this is not a requirement for this exception type, so completeness is unchanged.', 'success');
      setProvideFor(null); setProvideNote(''); setProvideRef('');
      await load();
    } catch (e) { toast(apiError(e, 'Could not record the evidence.'), 'error'); }
    finally { setBusy(false); }
  };
  const proposeMatch = async () => {
    if (matchIds.length < 2 || !matchRationale.trim()) { toast('Select at least two records and give a rationale.', 'error'); return; }
    if (await act(() => api.post('/api/reconciliation/matches', { transaction_ids: matchIds, rationale: matchRationale }), 'Manual match proposed.')) { setMatchIds([]); setMatchRationale(''); }
  };

  if (error) return <div className="empty-state"><b>{error}</b><p>It may have been removed or you may lack access.</p><Link href="/cases">Back to cases</Link></div>;
  if (!detail) return <SkeletonRows rows={8} label="Loading case" />;

  const { case: c, exceptions, findings, recommendations, decisions } = detail;
  const stageIndex = Math.max(0, STAGES.indexOf(c.status));
  const closed = c.status === 'Closed';
  const sla = slaInfo(c.sla_due_at);
  const canInvestigate = !['Resolving', 'Pending Validation', 'Closed'].includes(c.status) && exceptions.length > 0;
  const proposed = recommendations.filter((r) => r.status === 'Proposed');
  const earlier = recommendations.filter((r) => r.status !== 'Proposed');
  const pendingWf = workflows.filter((w) => w.state === 'Pending');
  const openVal = validations.filter((v) => v.status !== 'Complete');

  return (
    <div className="case-workspace">
      <div className="case-hero">
        <div>
          <a href="/cases" className="case-back" onClick={(e) => { if (window.history.length > 1) { e.preventDefault(); router.back(); } }}>← Back</a>
          <h1>{c.case_type}</h1>
          <p>{c.case_number} · Store {c.store_id} · Business date {c.business_date}</p>
        </div>
        <div className="case-hero-side">
          <PriorityBadge p={c.priority} /><StatusBadge s={c.status} />
          {canInvestigate && findings.length > 0 && <button className="btn-secondary btn-sm" disabled={busy} onClick={investigate}>Re-run investigation</button>}
        </div>
      </div>

      <ol className="case-stepper" aria-label="Case progress">
        {STAGES.map((s, i) => (
          <li key={s} className={i < stageIndex ? 'done' : i === stageIndex ? 'current' : ''} aria-current={i === stageIndex ? 'step' : undefined}>
            <i>{i < stageIndex ? '✓' : i + 1}</i><span>{STAGE_LABEL[s] ?? s}</span>
          </li>
        ))}
      </ol>

      <div className="case-summary">
        <div><small>Exception amount</small><b>{money(c.total_exception_amount)}</b></div>
        <div><small>Estimated exposure</small><b>{money(c.total_exposure)}</b></div>
        <div><small>Evidence complete</small><b>{c.evidence_completeness}%</b>
          <span className="cc-bar" aria-hidden><div style={{ width: `${c.evidence_completeness}%` }} /></span></div>
        <div className={sla.tone}><small>SLA · Owner</small><b>{sla.text}</b><em>{c.assigned_to || 'Unassigned'}</em></div>
      </div>

      {/* The one thing to do next */}
      <section className={`next-step ${closed ? 'is-done' : ''}`} aria-labelledby="next-step-h">
        <h2 id="next-step-h">{closed ? 'Case closed' : 'Next step'}</h2>
        {closed ? <p>{c.investigation_status === 'Evidence Overridden' ? 'Closed with an evidence override. The gap was not fulfilled; the override and who approved it are under Activity & notes.' : 'This case is closed. Everything below is the record of what happened.'}</p>
          : proposed.length > 0 ? (
            <>
              {c.investigation_status === 'Awaiting Evidence' && (
                <div className="alert alert-warning" role="status">Evidence is missing, so this case cannot be approved on its merits yet. Provide it under Records &amp; evidence and the case re-evaluates automatically.
                  <button className="btn-secondary btn-sm alert-close" onClick={() => pickTab('records')}>Go to evidence</button></div>
              )}
              <p>Review the recommendation, explain your decision, then approve or escalate.</p>
              {proposed.map((r) => (
                <div key={r.id} className="next-rec">
                  <div>
                    <b>{pretty(r.disposition_type)}</b> <span className="badge badge-sm badge-secondary">{pretty(r.action_class)}</span>
                    <p>Confidence {r.confidence}{r.financial_impact ? ` · Impact ${money(r.financial_impact)}` : ''}</p>
                    {r.expected_workflow && <p>On approval: {r.expected_workflow}</p>}
                  </div>
                  <div className="action-buttons">
                    <button className="btn-primary" disabled={busy} onClick={() => decide(r.id, 'Approved')}>Approve</button>
                    <button className="btn-secondary" disabled={busy} onClick={() => decide(r.id, 'Escalated')}>Escalate</button>
                  </div>
                </div>
              ))}
              <label className="field">Decision rationale <span className="hint">Required. Recorded in the audit trail.</span>
                <textarea id="decision-rationale" rows={3} value={rationale} aria-invalid={rationaleError}
                  onChange={(e) => { setRationale(e.target.value); setRationaleError(false); }}
                  placeholder="Why is this the right call? Cite the evidence." style={rationaleError ? { borderColor: 'var(--error)' } : undefined} />
              </label>
              {rationaleError && <p className="next-err" role="alert">Add a rationale before approving or escalating.</p>}
            </>
          ) : pendingWf.length > 0 ? (
            pendingWf.map((w) => (
              <div key={w.id} className="next-rec">
                <div><b>Execute {w.workflow_type} workflow</b><p>Approved. Waiting on you to run: {w.current_step}.</p></div>
                <button className="btn-primary" disabled={busy} onClick={() => execute(w.id)}>Execute action</button>
              </div>
            ))
          ) : openVal.length > 0 ? (
            openVal.map((v) => (
              <div key={v.id} className="next-rec">
                <div><b>Validate the outcome</b><p>Expected: {v.expected_observation}</p></div>
                <button className="btn-primary" disabled={busy} onClick={() => validate(v.id)}>Run validation</button>
              </div>
            ))
          ) : exceptions.length === 0 ? (
            <p>This case has no exceptions, so there is nothing to investigate. It is probably a synthetic demo case.</p>
          ) : canInvestigate && findings.length === 0 ? (
            <div className="next-rec">
              <div><b>Run the investigation</b><p>The agent will gather evidence, find the cause and propose a recommendation.</p>
                <p style={{ marginTop: '.4rem' }}><AgentModeNotice mode={agentMode} compact /></p></div>
              <button className="btn-primary" disabled={busy} onClick={investigate}>{busy ? 'Investigating…' : 'Run investigation'}</button>
            </div>
          ) : <p>Nothing needs your action right now. The case is waiting on the system or on requested evidence.</p>}
      </section>

      <div className="case-tabs" role="tablist" aria-label="Case sections">
        {([['overview', 'Overview', exceptions.length], ['records', 'Records & evidence', (reconciliation?.records.length ?? 0) + evidence.length], ['activity', 'Activity & notes', timeline.length]] as [Tab, string, number][]).map(([k, label, n]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => pickTab(k)}>{label}<span>{n}</span></button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="case-panel">
          <section className="info-card case-section">
            <h2>What happened</h2>
            {exceptions.length === 0 ? <p className="cc-empty">No exceptions recorded.</p> : (
              <div className="table-scroll"><table className="data-table">
                <thead><tr><th>Exception</th><th>Source</th><th className="num">Amount</th><th>Severity</th></tr></thead>
                <tbody>{exceptions.map((x) => (
                  <tr key={x.id}><td className="font-medium">{pretty(x.exception_type)}<small className="act-sub"> {pretty(x.exception_family)}</small></td>
                    <td>{x.source_system}</td><td className="num">{money(x.exception_amount)}</td><td><span className={`badge badge-sm ${sevTone(x.severity)}`}>{x.severity}</span></td></tr>
                ))}</tbody></table></div>
            )}
          </section>

          <section className="info-card case-section">
            <h2>Why it happened</h2>
            {findings.length === 0 ? <p className="cc-empty">No findings yet. Run the investigation to find the cause.</p> : (
              <ul className="summary-list">{findings.map((f) => (
                <li key={f.id}>{f.conclusion} <span className="badge badge-sm badge-success">{f.confidence} confidence</span></li>
              ))}</ul>
            )}
          </section>

          <section className="info-card case-section">
            <h2>Policy check</h2>
            {policies.length === 0 ? <p className="cc-empty">No policy evaluation yet.</p> : policies.map((x) => (
              <div key={x.id} className="indicator">
                <span className={`badge badge-sm ${outcomeTone(x.outcome)}`}>{x.outcome}</span>
                <span>{x.reasons.reasons.join('; ')}</span>
              </div>
            ))}
          </section>

          {(decisions.length > 0 || earlier.length > 0 || workflows.length > 0 || validations.length > 0) && (
            <section className="info-card case-section">
              <h2>Decisions &amp; follow-through</h2>
              {earlier.map((r) => (
                <div key={r.id} className="indicator"><div style={{ flex: 1 }}><b>{pretty(r.disposition_type)}</b><p>{pretty(r.action_class)} · confidence {r.confidence}</p></div>
                  <span className="badge badge-sm badge-secondary">{r.status}</span></div>
              ))}
              {decisions.map((d) => (
                <div key={d.id} className="indicator"><div style={{ flex: 1 }}><b>{d.decision}</b> by {d.actor}<p>{d.override_reason || 'No rationale recorded'} · {when(d.created_at)}</p></div>
                  <span className={`badge badge-sm ${d.authority_check === 'Passed' ? 'badge-success' : 'badge-secondary'}`}>Authority: {d.authority_check}</span></div>
              ))}
              {workflows.map((w) => (
                <div key={w.id} className="indicator"><div style={{ flex: 1 }}><b>{w.workflow_type} workflow</b><p>{w.current_step}</p></div>
                  <span className={`badge badge-sm ${w.state === 'Pending' ? 'badge-warning' : 'badge-success'}`}>{w.state}</span></div>
              ))}
              {validations.map((v) => (
                <div key={v.id} className="indicator"><div style={{ flex: 1 }}><b>Validation</b><p>{v.expected_observation}{v.verification_result ? ` · Result: ${v.verification_result}` : ''}</p></div>
                  <span className={`badge badge-sm ${v.status === 'Complete' ? 'badge-success' : 'badge-warning'}`}>{v.status}</span></div>
              ))}
            </section>
          )}
        </div>
      )}

      {tab === 'records' && (
        <div className="case-panel">
          <section className="info-card case-section">
            <h2>Source records</h2>
            {reconciliation && <p className="case-help">{reconciliation.message}</p>}
            <p className="hint"><Link href={`/transactions?store_id=${encodeURIComponent(c.store_id)}&date_from=${c.business_date}&date_to=${c.business_date}`}>View all transactions for this store day →</Link></p>
            {!reconciliation ? <SkeletonRows rows={3} /> : reconciliation.records.length === 0 ? <p className="cc-empty">No reconciled source records are available.</p> : (
              <>
                <div className="table-scroll"><table className="data-table">
                  <thead><tr><th aria-label="Select for manual match" /><th>Source</th><th>Record</th><th>Type</th><th className="num">Amount</th><th>Payment / settlement key</th><th>Match</th></tr></thead>
                  <tbody>{reconciliation.records.map((r) => (
                    <tr key={r.transaction_id}>
                      <td><input type="checkbox" aria-label={`Select ${r.source_record_id ?? r.transaction_id} for manual match`} checked={matchIds.includes(r.transaction_id)}
                        onChange={() => setMatchIds((ids) => ids.includes(r.transaction_id) ? ids.filter((x) => x !== r.transaction_id) : [...ids, r.transaction_id])} /></td>
                      <td>{r.source_system}</td><td><Link href={`/transactions/${r.transaction_id}`}>{r.source_record_id || 'Open transaction'}</Link></td><td>{r.transaction_type}</td><td className="num">{money(r.amount)}</td>
                      <td>{r.payment_reference || r.settlement_reference || '—'}</td>
                      <td><span className={`badge badge-sm ${/match/i.test(r.reconciliation_status) && !/un/i.test(r.reconciliation_status) ? 'badge-success' : 'badge-warning'}`}>{r.reconciliation_status}</span></td>
                    </tr>
                  ))}</tbody></table></div>
                <details className="case-manual" open={matchIds.length > 0}>
                  <summary>Match records manually{matchIds.length > 0 ? ` (${matchIds.length} selected)` : ''}</summary>
                  <p className="hint">Tick two or more records above that belong together, explain why, then propose the match.</p>
                  <div className="cc-askrow">
                    <input value={matchRationale} onChange={(e) => setMatchRationale(e.target.value)} placeholder="Why do these records match?" aria-label="Manual match rationale" />
                    <button className="btn-secondary" disabled={busy || matchIds.length < 2 || !matchRationale.trim()} onClick={proposeMatch}>Propose match</button>
                  </div>
                </details>
              </>
            )}
          </section>

          <section className="info-card case-section">
            <h2>Evidence</h2>
            {evidence.length === 0 ? <p className="cc-empty">No evidence has been retrieved yet. Request it from the Activity tab.</p> : (
              <div className="table-scroll"><table className="data-table">
                <thead><tr><th>Requirement</th><th>Status</th><th>Source</th><th>Retrieved</th><th className="num">Linked records</th><th /></tr></thead>
                <tbody>{evidence.map((e) => (
                  <Fragment key={e.id}>
                    <tr><td className="font-medium">{pretty(e.evidence_type)}</td>
                      <td><span className={`badge badge-sm ${/complete|retrieved|available|ok/i.test(e.status) ? 'badge-success' : 'badge-warning'}`}>{e.status}</span></td>
                      <td>{e.source_system}</td><td className="nowrap">{when(e.retrieved_at)}</td><td className="num">{e.source_record_ids.length}</td>
                      <td>{e.status === 'Unavailable' && !closed && <button className="btn-secondary btn-sm" onClick={() => startProvide(e.evidence_type)}>Provide evidence</button>}</td></tr>
                    {provideFor === e.evidence_type && (
                      <tr><td colSpan={6}>
                        <div className="case-manual" style={{ margin: '.25rem 0' }}>
                          <b>Provide evidence for {pretty(e.evidence_type)}</b>
                          <p className="hint">No ingested record can supply this automatically. Say what you checked or attach a reference. It is audited under your name.</p>
                          <div className="form-row">
                            <label className="field grow">What you checked or supplied<input value={provideNote} onChange={(ev) => setProvideNote(ev.target.value)} placeholder="For example: confirmed with the store manager on the phone" autoFocus /></label>
                            <label className="field">Reference (optional)<input value={provideRef} onChange={(ev) => setProvideRef(ev.target.value)} placeholder="Ticket, document or system id" /></label>
                            <button className="btn-primary" disabled={busy || !provideNote.trim()} onClick={provide}>Record evidence</button>
                            <button className="btn-secondary" onClick={() => setProvideFor(null)}>Cancel</button>
                          </div>
                        </div>
                      </td></tr>
                    )}
                  </Fragment>
                ))}</tbody></table></div>
            )}
          </section>
        </div>
      )}

      {tab === 'activity' && (
        <div className="case-panel case-two">
          <section className="info-card case-section">
            <h2>Timeline</h2>
            {timeline.length === 0 ? <p className="cc-empty">No case events recorded.</p> : (
              <ol className="case-timeline">{timeline.map((ev) => (
                <li key={ev.id}><time>{when(ev.created_at)}</time>
                  <div><b>{pretty(ev.event_type)}</b><p>{ev.description}</p><small>{ev.actor} · #{ev.seq}</small></div></li>
              ))}</ol>
            )}
          </section>

          <div className="case-stack">
            <section className="info-card case-section">
              <h2>Notes</h2>
              <div className="cc-askrow">
                <input value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') addNote(); }} placeholder="Add an investigation note" aria-label="Note" />
                <button className="btn-primary" disabled={busy || !note.trim()} onClick={addNote}>Add</button>
              </div>
              {collab.notes.length === 0 ? <p className="cc-empty" style={{ marginTop: '.6rem' }}>No notes yet.</p>
                : collab.notes.map((n) => <div key={n.id} className="case-note"><b>{n.author}</b><span>{when(n.created_at)}</span><p>{n.body}</p></div>)}
            </section>

            <section className="info-card case-section">
              <h2>Request evidence</h2>
              <div className="case-request">
                <input value={requirement} onChange={(e) => setRequirement(e.target.value)} placeholder="What evidence is needed?" aria-label="Evidence requirement" />
                <input value={requestedFrom} onChange={(e) => setRequestedFrom(e.target.value)} placeholder="Who should provide it?" aria-label="Requested from" />
                <button className="btn-secondary" disabled={busy || !requirement.trim() || !requestedFrom.trim()} onClick={requestEvidence}>Request</button>
              </div>
              {collab.evidence_requests.map((r) => {
                const tone = r.status === 'Fulfilled' ? 'badge-success' : r.status === 'Rejected' ? 'badge-error' : r.status === 'Overridden' ? 'badge-info' : 'badge-warning';
                const mine = !!me && (me.user ?? '') === (r.override_requested_by ?? '');
                const actionable = !closed && (r.status === 'Open' || r.status === 'Rejected');
                return (
                  <div key={r.id} className="case-note"><b>{r.requirement}</b><span>{r.requested_from} · {when(r.created_at)}</span>
                    <p><span className={`badge badge-sm ${tone}`}>{r.status}</span></p>
                    {r.status === 'Fulfilled' && <p>{r.response}{r.reference ? ` · Ref: ${r.reference}` : ''}<small className="act-sub" style={{ display: 'block' }}>Provided by {r.fulfilled_by}{r.fulfilled_at ? ` · ${when(r.fulfilled_at)}` : ''}</small></p>}
                    {r.status === 'Rejected' && <p>Rejected: {r.rejection_reason}</p>}
                    {(r.status === 'Overridden' || r.status === 'Override Pending') && (
                      <p>Override: {r.override_reason}<small className="act-sub" style={{ display: 'block' }}>
                        Financial impact {money(r.override_financial_impact ?? 0)} · requested by {r.override_requested_by}{r.override_requested_at ? ` · ${when(r.override_requested_at)}` : ''}
                        {r.override_approved_by ? ` · approved by ${r.override_approved_by}${r.override_approved_at ? ` · ${when(r.override_approved_at)}` : ''}` : ''}</small></p>
                    )}
                    {r.status === 'Override Pending' && !closed && (
                      mine ? <p className="hint">Waiting for a different Finance approver. You requested this override, so you cannot approve it.</p>
                        : <button className="btn-primary btn-sm" disabled={busy} onClick={() => approveOverride(r.id)}>Approve override</button>
                    )}
                    {actionable && provideFor !== r.requirement && rejectFor !== r.id && overrideFor !== r.id && (
                      <div className="action-buttons" style={{ marginTop: '.4rem' }}>
                        {r.status === 'Open' && <button className="btn-secondary btn-sm" onClick={() => startProvide(r.requirement)}>Provide evidence</button>}
                        {r.status === 'Open' && <button className="btn-secondary btn-sm" onClick={() => { setRejectFor(r.id); setRejectReason(''); setOverrideFor(null); }}>Reject</button>}
                        <button className="btn-secondary btn-sm" onClick={() => { setOverrideFor(r.id); setOverrideReason(''); setOverrideImpact('0'); setRejectFor(null); }}>Override</button>
                      </div>
                    )}
                    {r.status === 'Open' && provideFor === r.requirement && (
                      <div className="form-row" style={{ marginTop: '.5rem' }}>
                        <label className="field grow">What you checked or supplied<input value={provideNote} onChange={(ev) => setProvideNote(ev.target.value)} autoFocus /></label>
                        <label className="field">Reference (optional)<input value={provideRef} onChange={(ev) => setProvideRef(ev.target.value)} /></label>
                        <button className="btn-primary btn-sm" disabled={busy || !provideNote.trim()} onClick={provide}>Record</button>
                        <button className="btn-secondary btn-sm" onClick={() => setProvideFor(null)}>Cancel</button>
                      </div>)}
                    {rejectFor === r.id && (
                      <div className="form-row" style={{ marginTop: '.5rem' }}>
                        <label className="field grow">Why is this evidence unavailable? (required)<input value={rejectReason} onChange={(ev) => setRejectReason(ev.target.value)} autoFocus /></label>
                        <button className="btn-primary btn-sm" disabled={busy || !rejectReason.trim()} onClick={reject}>Reject request</button>
                        <button className="btn-secondary btn-sm" onClick={() => setRejectFor(null)}>Cancel</button>
                      </div>)}
                    {overrideFor === r.id && (
                      <div style={{ marginTop: '.5rem' }}>
                        <p className="hint">Closes the case without this evidence. It stays marked “Evidence Overridden” and is audited. Above the autonomy limit a different Finance approver must confirm.</p>
                        <div className="form-row">
                          <label className="field grow">Rationale (required)<input value={overrideReason} onChange={(ev) => setOverrideReason(ev.target.value)} autoFocus /></label>
                          <label className="field">Financial impact ($)<input inputMode="decimal" value={overrideImpact} onChange={(ev) => setOverrideImpact(ev.target.value)} aria-invalid={!impactOk} style={!impactOk ? { borderColor: 'var(--error)', width: '9rem' } : { width: '9rem' }} /></label>
                          <button className="btn-primary btn-sm" disabled={busy || !overrideReason.trim() || !impactOk} onClick={submitOverride}>Override</button>
                          <button className="btn-secondary btn-sm" onClick={() => setOverrideFor(null)}>Cancel</button>
                        </div>
                        {!impactOk && <p className="next-err" role="alert">Enter a number of 0 or more.</p>}
                      </div>)}
                  </div>
                );
              })}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
