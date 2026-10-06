'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useDataChanged } from '@/lib/sync';

export type AgentMode = { state: 'llm'; model: string } | { state: 'off' } | { state: 'no_key' } | { state: 'unknown' };

/** Is the investigation agent actually running on an LLM right now? Reads the same config the Agents tab shows. */
export function useAgentMode(): AgentMode {
  const [mode, setMode] = useState<AgentMode>({ state: 'unknown' });
  const load = () => {
    api.get('/api/agent/config').then(({ data }) => {
      if (!data.settings.investigator_enabled) setMode({ state: 'off' });
      else if (!data.llm_key_configured) setMode({ state: 'no_key' });
      else setMode({ state: 'llm', model: data.settings.model });
    }).catch(() => setMode({ state: 'unknown' }));
  };
  useEffect(load, []);
  useDataChanged(load);
  return mode;
}

/** Honest status line: warns when investigations are NOT LLM-driven, so nobody assumes they are. */
export function AgentModeNotice({ mode, compact = false }: { mode: AgentMode; compact?: boolean }) {
  if (mode.state === 'unknown') return null;
  if (mode.state === 'llm') {
    return compact ? <span className="badge badge-sm badge-info" title="Investigations run on an LLM agent">LLM agent · {mode.model}</span> : null;
  }
  const text = mode.state === 'off'
    ? 'The LLM investigation agent is turned off, so investigations use deterministic checks only. An administrator can turn it on under Agent Activity → Agents & configuration.'
    : 'No Claude credentials are set on the server, so investigations fall back to deterministic checks. Set CLAUDE_CODE_OAUTH_TOKEN (or ANTHROPIC_API_KEY) and restart the backend to make the agent LLM-driven.';
  if (compact) return <span className="badge badge-sm badge-warning" title={text}>{mode.state === 'off' ? 'LLM agent off · deterministic checks' : 'No Claude credentials · deterministic checks'}</span>;
  return <div className="alert alert-warning" role="status">{text}</div>;
}
