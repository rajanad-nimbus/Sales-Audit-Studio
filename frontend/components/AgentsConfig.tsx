'use client';

import { Fragment, useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { SkeletonRows, useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { AgentModeNotice, useAgentMode } from '@/components/AgentMode';
import { ago, groupOf, pretty } from '@/lib/activityUtil';

interface AgentCfg { actor: string; name: string; kind: string; enabled: boolean; role: string; can: string[]; cannot: string[]; model: string | null; tools: string[]; mode_note?: string; limits: Record<string, any>; events_total: number; events_24h: number; last_active: string | null }
interface AgentSettings { investigator_enabled: boolean; auto_investigate: boolean; model: string; tools: string[] }
interface AgentConfig { settings: AgentSettings; options: { models: string[]; tools: string[] }; llm_key_configured: boolean; settings_history: { at: string; by: string; setting: string; before: any; after: any }[]; agents: AgentCfg[]; human_in_loop: string; agent_runs: { recent: number; failed: number; input_tokens: number; output_tokens: number } }

/** The agents, what each may and may not do, and the admin-controlled agent settings. */
export function AgentsConfig() {
  const { me } = useMe();
  const toast = useToast();
  const mode = useAgentMode();
  const [cfg, setCfg] = useState<AgentConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isAdmin = me?.role === 'admin' && !me?.impersonating;
  const [draft, setDraft] = useState<AgentSettings | null>(null);

  const load = useCallback(async () => {
    try { setCfg((await api.get('/api/agent/config')).data); setError(null); }
    catch { setError('Could not load the agent configuration.'); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (cfg && !draft) setDraft(cfg.settings); }, [cfg, draft]);
  const dirty = !!(cfg && draft && JSON.stringify(cfg.settings) !== JSON.stringify(draft));
  const save = async () => {
    if (!draft) return;
    try { await api.put('/api/agent/settings', draft); toast('Agent settings saved and audited', 'success'); setDraft(null); load(); }
    catch (e: any) { toast(e?.response?.data?.detail ?? 'Could not save settings', 'error'); }
  };

  if (error) return <div className="alert alert-error" role="alert">{error}<button className="btn-secondary btn-sm alert-close" onClick={load}>Retry</button></div>;
  if (!cfg) return <SkeletonRows rows={6} label="Loading agent configuration" />;
  return (
    <>
      <AgentModeNotice mode={mode} />
      {cfg && (
        <div className="ac-tiles">
          <div className="ac-tile"><small>Automated events (24h)</small><b>{cfg.agents.reduce((n, a) => n + a.events_24h, 0)}</b><em>across {cfg.agents.length} agents</em></div>
          <div className="ac-tile"><small>LLM investigation runs</small><b>{cfg.agent_runs.recent}</b><em>{cfg.agent_runs.failed} failed · {(cfg.agent_runs.input_tokens + cfg.agent_runs.output_tokens).toLocaleString()} tokens</em></div>
          <div className="ac-tile"><small>Autonomy</small><b>Off</b><em>every action needs a person</em></div>
        </div>
      )}
        <div className="agent-grid">
          {cfg && draft && (
            <section className="agent-card" style={{ gridColumn: '1 / -1' }}>
              <header><h3>Agent settings</h3><span className="badge badge-sm badge-secondary">{isAdmin ? 'Admin: editable' : 'View only'}</span></header>
              <p>{isAdmin ? 'Changes apply to the next investigation and are written to the audit trail with your name.' : me?.impersonating ? 'Stop impersonating to edit.' : 'Only an administrator can change these settings.'} Autonomy limits, policy thresholds and roles are not editable here.</p>
              {!cfg.llm_key_configured && <p className="act-sub">No Claude credentials (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY) are configured on the server, so the LLM agent cannot run whatever is selected.</p>}
              <fieldset disabled={!isAdmin} style={{ border: 0, padding: 0, margin: 0, display: 'grid', gap: '.6rem' }}>
                <label><input type="checkbox" checked={draft.investigator_enabled} onChange={(e) => setDraft({ ...draft, investigator_enabled: e.target.checked })} /> Use the LLM Investigation Agent</label>
                <label><input type="checkbox" checked={draft.auto_investigate} onChange={(e) => setDraft({ ...draft, auto_investigate: e.target.checked })} /> Investigate new cases automatically after each batch</label>
                <label>Model <select value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })}>{cfg.options.models.map((m) => <option key={m}>{m}</option>)}</select></label>
                <div>Tools the agent may use:{cfg.options.tools.map((t) => (
                  <label key={t} style={{ display: 'block' }}><input type="checkbox" checked={draft.tools.includes(t)}
                    onChange={(e) => setDraft({ ...draft, tools: e.target.checked ? [...draft.tools, t] : draft.tools.filter((x) => x !== t) })} /> {t}</label>))}</div>
              </fieldset>
              {isAdmin && <div className="cc-actions"><button className="btn-primary btn-small" disabled={!dirty || draft.tools.length === 0} onClick={save}>Save changes</button>
                <button className="btn-secondary btn-small" disabled={!dirty} onClick={() => setDraft(cfg.settings)}>Reset</button></div>}
              {cfg.settings_history.length > 0 && <details><summary>Recent changes</summary><ul>{cfg.settings_history.map((h, i) => <li key={i}>{ago(h.at)} · {h.by} set {h.setting} to {JSON.stringify(h.after)} (was {JSON.stringify(h.before)})</li>)}</ul></details>}
            </section>
          )}
          {cfg ? cfg.agents.map((a) => (
            <article key={a.actor} className="agent-card">
              <header>
                <h3>{a.name}</h3>
                <span className={`badge badge-sm ${a.kind.startsWith('LLM') ? 'badge-info' : 'badge-secondary'}`}>{a.kind}</span>
              </header>
              <p>{a.role}</p>
              {a.mode_note && <p className="act-sub">{a.mode_note}</p>}
              <dl>
                <dt>Model</dt><dd>{a.model ?? 'None (deterministic)'}</dd>
                <dt>Tools</dt><dd>{a.tools.length ? a.tools.join(', ') : 'None'}</dd>
                <dt>Activity</dt><dd>{a.events_24h} in 24h · {a.events_total} total{a.last_active ? ` · last ${ago(a.last_active)}` : ''}</dd>
                {Object.entries(a.limits).map(([k, v]) => <Fragment key={k}><dt>{pretty(k)}</dt><dd>{String(v)}</dd></Fragment>)}
              </dl>
              <div className="agent-can"><b>Can</b><ul>{a.can.map((x) => <li key={x}>{x}</li>)}</ul></div>
              <div className="agent-cannot"><b>Cannot</b><ul>{a.cannot.map((x) => <li key={x}>{x}</li>)}</ul></div>
              <Link className="ac-link" href={`/activity?agent=${groupOf(a.actor)}`}>See its activity</Link>
            </article>
          )) : <div className="loading">Loading agent configuration</div>}
          {cfg && <p className="ac-hint" style={{ gridColumn: '1 / -1' }}>{cfg.human_in_loop} Configuration is read from the running service; secrets are never shown.</p>}
        </div>
    </>
  );
}
