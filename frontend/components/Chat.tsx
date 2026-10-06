'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { api } from '@/lib/api';

/* ---------- types ---------- */
interface Source { type: 'case' | 'ontology'; label: string; href: string | null }
interface Msg { id: string; role: 'user' | 'assistant'; content: string; ts: string; sources?: Source[]; followups?: string[]; via?: 'rules' | 'llm' }
interface Ctx { type: 'portfolio' | 'case'; caseId?: string; label?: string }
interface Session { id: string; title: string; context: Ctx; messages: Msg[]; updatedAt: string }
type Mode = 'docked' | 'floating';
type Persona = '' | 'finance' | 'it';

interface Chat { open: boolean; toggle: () => void; setOpen: (v: boolean) => void; mode: Mode; setMode: (m: Mode) => void;
  minimized: boolean; setMinimized: (v: boolean) => void; caseId: string | null; setCaseId: (id: string | null) => void }
const ChatCtx = createContext<Chat>({ open: false, toggle: () => {}, setOpen: () => {}, mode: 'docked', setMode: () => {}, minimized: false,
  setMinimized: () => {}, caseId: null, setCaseId: () => {} });
export const useChat = () => useContext(ChatCtx);

const KEY = 'nimbus_assistant_v2';
const store = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } };
const read = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const uid = (p: string) => `${p}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

const greeting = (ctx: Ctx) => ctx.type === 'case'
  ? `Hi, I'm the **Nimbus Assistant**, grounded in **${ctx.label ?? 'this case'}**. I can explain why it happened, its exposure, evidence and policy decision, and what to do next.`
  : `Hi, I'm the **Nimbus Assistant**, grounded in **all cases**. I can explain what needs a decision, exposure, evidence, policy, connectors and the daily batch. I can't approve or change anything.`;
const newSession = (ctx: Ctx): Session => ({ id: uid('s'), title: 'New conversation', context: ctx, updatedAt: new Date().toISOString(),
  messages: [{ id: uid('m'), role: 'assistant', content: greeting(ctx), ts: new Date().toISOString() }] });

/* ---------- provider ---------- */
export function ChatProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpenState] = useState(false);
  const [mode, setModeState] = useState<Mode>('floating');
  const [minimized, setMinimized] = useState(false);
  const [caseId, setCaseId] = useState<string | null>(null);
  useEffect(() => {
    setOpenState(read('nimbus_chat_open') === '1');
    setModeState(read('nimbus_chat_mode') === 'docked' ? 'docked' : 'floating');
  }, []);
  const setOpen = useCallback((v: boolean) => { setOpenState(v); store('nimbus_chat_open', v ? '1' : '0'); if (v) setMinimized(false); }, []);
  const toggle = useCallback(() => setOpenState((o) => { store('nimbus_chat_open', o ? '0' : '1'); if (!o) setMinimized(false); return !o; }), []);
  const setMode = useCallback((m: Mode) => { setModeState(m); store('nimbus_chat_mode', m); setMinimized(false); }, []);
  return <ChatCtx.Provider value={{ open, toggle, setOpen, mode, setMode, minimized, setMinimized, caseId, setCaseId }}>{children}</ChatCtx.Provider>;
}

/* ---------- icons ---------- */
const PATHS: Record<string, string> = {
  sparkles: 'M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8L12 3zM19 15l.8 2.2 2.2.8-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15z',
  send: 'M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z', plus: 'M12 5v14M5 12h14', history: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2',
  pin: 'M12 17v5M9 3h6l-1 6 3 3v2H7v-2l3-3-1-6z', minus: 'M5 12h14', x: 'M18 6L6 18M6 6l12 12', copy: 'M9 9h10v12H9zM5 15V3h10',
  check: 'M5 13l4 4L19 7', refresh: 'M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5',
  up: 'M7 10v11H3V10h4zm0 0l4-8c1.5 0 2.5 1 2.5 2.5V8H20a2 2 0 0 1 2 2.3l-1.2 8A2 2 0 0 1 18.8 20H7',
  down: 'M17 14V3h4v11h-4zm0 0l-4 8c-1.500 0-2.500-1-2.500-2.500V16H4a2 2 0 0 1-2-2.300l1.200-8A2 2 0 0 1 5.200 4H17',
  trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3', down2: 'M12 5v14M5 12l7 7 7-7', lock: 'M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.300-4.300', chevron: 'M6 9l6 6 6-6', globe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18',
  folder: 'M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7z', back: 'M19 12H5M12 19l-7-7 7-7', chat: 'M21 12a8 8 0 0 1-11.600 7.100L4 20l1-4.600A8 8 0 1 1 21 12z',
  doc: 'M7 3h8l4 4v14H7zM15 3v4h4M10 12h6M10 16h6',
};
const Icon = ({ n, s = 15 }: { n: string; s?: number }) => (
  <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d={PATHS[n]} /></svg>
);

/* ---------- tiny, safe markdown (no HTML injection) ---------- */
function inline(text: string, key: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0, i = 0, m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith('**')) out.push(<strong key={`${key}b${i}`}>{t.slice(2, -2)}</strong>);
    else if (t.startsWith('`')) out.push(<code key={`${key}c${i}`}>{t.slice(1, -1)}</code>);
    else { const mm = /\[([^\]]+)\]\(([^)]+)\)/.exec(t)!; out.push(/^\/(?!\/)/.test(mm[2]) ? <Link key={`${key}l${i}`} href={mm[2]}>{mm[1]}</Link> : mm[1]); }
    last = m.index + t.length; i++;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const Tag = list.ordered ? 'ol' : 'ul';
    blocks.push(<Tag key={`l${blocks.length}`}>{list.items.map((it, j) => <li key={j}>{inline(it, `li${blocks.length}${j}`)}</li>)}</Tag>);
    list = null;
  };
  text.split('\n').forEach((raw, idx) => {
    const line = raw.trimEnd();
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line), num = /^\s*\d+[.)]\s+(.*)$/.exec(line), head = /^#{1,4}\s+(.*)$/.exec(line);
    if (bullet || num) {
      const ordered = !!num;
      if (list && list.ordered !== ordered) flush();
      list = list ?? { ordered, items: [] };
      list.items.push((bullet ?? num)![1]);
    } else {
      flush();
      if (head) blocks.push(<p key={idx}><strong>{inline(head[1], `h${idx}`)}</strong></p>);
      else if (line.trim()) blocks.push(<p key={idx}>{inline(line, `p${idx}`)}</p>);
    }
  });
  flush();
  return <div className="as-md">{blocks}</div>;
}

const CAPABILITIES = [
  ['Cases & exposure', 'What needs a decision, the largest exposure, SLA status.'],
  ['Evidence & findings', 'Why a case happened and how complete its evidence is.'],
  ['Policy & decisions', 'What the policy gate decided and what approving would do.'],
  ['Operations', 'Connector health, the daily batch and recent activity.'],
];
const PERSONAS: { v: Persona; l: string }[] = [{ v: '', l: 'General' }, { v: 'finance', l: 'Finance' }, { v: 'it', l: 'IT Operations' }];

/* ---------- panel ---------- */
export function ChatPanel() {
  const { open, setOpen, mode, setMode, minimized, setMinimized, caseId, setCaseId } = useChat();
  const pathname = usePathname();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string>('');
  const [view, setView] = useState<'chat' | 'history'>('chat');
  const [search, setSearch] = useState('');
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [persona, setPersona] = useState<Persona>('');
  const [caseLabel, setCaseLabel] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [fb, setFb] = useState<Record<string, 'up' | 'down'>>({});
  const [away, setAway] = useState(false);
  const [ctxMenu, setCtxMenu] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  const loaded = useRef(false);

  useEffect(() => {
    try {
      const saved = JSON.parse(read(KEY) || 'null') as { sessions: Session[]; active: string } | null;
      if (saved?.sessions?.length) { setSessions(saved.sessions); setActiveId(saved.active || saved.sessions[0].id); }
      else { const s = newSession({ type: 'portfolio' }); setSessions([s]); setActiveId(s.id); }
    } catch { const s = newSession({ type: 'portfolio' }); setSessions([s]); setActiveId(s.id); }
    setPersona((read('nimbus_persona') as Persona) || '');
    try { setFb(JSON.parse(read('nimbus_feedback') || '{}')); } catch { /* ignore */ }
    loaded.current = true;
  }, []);
  useEffect(() => { if (loaded.current) store(KEY, JSON.stringify({ sessions: sessions.slice(0, 30), active: activeId })); }, [sessions, activeId]);

  useEffect(() => { const m = pathname.match(/^\/cases\/([^/]+)/); if (m) setCaseId(m[1]); else if (pathname !== '/') setCaseId(null); }, [pathname, setCaseId]);
  useEffect(() => {
    if (!caseId) { setCaseLabel(null); return; }
    api.get(`/api/cases/${caseId}`).then((r) => setCaseLabel(`${r.data.case.case_number} · ${r.data.case.case_type}`)).catch(() => setCaseLabel(null));
  }, [caseId]);

  const active = sessions.find((s) => s.id === activeId) ?? sessions[0];
  const locked = !!active && active.messages.some((m) => m.role === 'user');
  const effective: Ctx = locked || !active ? active?.context ?? { type: 'portfolio' } : caseId && caseLabel ? { type: 'case', caseId, label: caseLabel } : { type: 'portfolio' };

  // keep the greeting in an untouched conversation aligned with the live context
  useEffect(() => {
    if (!active || locked) return;
    const g = greeting(effective);
    if (active.messages[0]?.content !== g || active.context.caseId !== effective.caseId) {
      setSessions((ss) => ss.map((s) => s.id === active.id ? { ...s, context: effective, messages: [{ ...s.messages[0], content: g }] } : s));
    }
  }, [effective.type, effective.caseId, effective.label, locked, active?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const update = (id: string, fn: (s: Session) => Session) => setSessions((ss) => ss.map((s) => (s.id === id ? fn(s) : s)));
  useEffect(() => { if (open && !minimized && view === 'chat') end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [active?.messages.length, busy, open, minimized, view]);
  useEffect(() => { if (ta.current) { ta.current.style.height = 'auto'; ta.current.style.height = Math.min(ta.current.scrollHeight, 120) + 'px'; } }, [input]);

  const ask = async (text: string, base?: Session) => {
    const session = base ?? active;
    const message = text.trim();
    if (!message || busy || !session) return;
    const ctx = session.messages.some((m) => m.role === 'user') ? session.context : effective;
    const user: Msg = { id: uid('u'), role: 'user', content: message, ts: new Date().toISOString() };
    const history = session.messages.slice(-10).map((m) => ({ role: m.role, content: m.content }));
    update(session.id, (s) => ({ ...s, context: ctx, updatedAt: user.ts, title: s.title === 'New conversation' ? message.slice(0, 36) + (message.length > 36 ? '…' : '') : s.title, messages: [...s.messages, user] }));
    setInput(''); setBusy(true);
    try {
      const { data } = await api.post('/api/chat', { message, case_id: ctx.type === 'case' ? ctx.caseId : null, persona, history });
      const a: Msg = { id: uid('a'), role: 'assistant', content: data.answer, ts: new Date().toISOString(), sources: data.sources, followups: data.followups, via: data.source };
      update(session.id, (s) => ({ ...s, updatedAt: a.ts, messages: [...s.messages, a] }));
    } catch (e: any) {
      const msg = e?.response?.status === 401 ? 'Please sign in to use the assistant.' : 'I could not reach the backend. Try again in a moment.';
      update(session.id, (s) => ({ ...s, messages: [...s.messages, { id: uid('a'), role: 'assistant', content: msg, ts: new Date().toISOString() }] }));
    }
    setBusy(false);
  };

  const regenerate = () => {
    if (!active || busy) return;
    const idx = [...active.messages].map((m) => m.role).lastIndexOf('user');
    if (idx < 0) return;
    const q = active.messages[idx].content;
    const trimmed: Session = { ...active, messages: active.messages.slice(0, idx) };
    update(active.id, () => trimmed);
    ask(q, trimmed);
  };
  const startNew = () => { const s = newSession(caseId && caseLabel ? { type: 'case', caseId, label: caseLabel } : { type: 'portfolio' }); setSessions((ss) => [s, ...ss]); setActiveId(s.id); setView('chat'); setTimeout(() => ta.current?.focus(), 50); };
  const remove = (id: string) => {
    setSessions((ss) => { const n = ss.filter((s) => s.id !== id); if (!n.length) { const s = newSession({ type: 'portfolio' }); setActiveId(s.id); return [s]; } if (id === activeId) setActiveId(n[0].id); return n; });
  };
  const copy = (id: string, t: string) => { navigator.clipboard?.writeText(t); setCopied(id); setTimeout(() => setCopied(null), 1500); };
  const exportChat = () => active && copy('export', active.messages.map((m) => `### ${m.role === 'user' ? 'You' : 'Nimbus Assistant'} (${new Date(m.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })})\n\n${m.content}\n`).join('\n---\n\n'));
  const rate = (id: string, v: 'up' | 'down') => { const n = { ...fb, [id]: fb[id] === v ? undefined : v } as Record<string, 'up' | 'down'>; setFb(n); store('nimbus_feedback', JSON.stringify(n)); };
  const choosePersona = (p: Persona) => { setPersona(p); store('nimbus_persona', p); };
  const chooseCtx = (c: Ctx) => { if (!active || locked) return; setCtxMenu(false); update(active.id, (s) => ({ ...s, context: c, messages: [{ ...s.messages[0], content: greeting(c) }] })); };

  const shown = useMemo(() => sessions.filter((s) => !search || s.title.toLowerCase().includes(search.toLowerCase()) || s.messages.some((m) => m.content.toLowerCase().includes(search.toLowerCase()))), [sessions, search]);

  if (!active) return null;
  if (!open) {
    return <button className="as-launcher" onClick={() => { setMode('floating'); setOpen(true); }} aria-label="Open Nimbus Assistant" title="Nimbus Assistant"><Icon n="sparkles" s={22} /></button>;
  }

  const lastAssistant = [...active.messages].reverse().find((m) => m.role === 'assistant');
  const onlyGreeting = active.messages.length <= 1;
  const chips = onlyGreeting
    ? (effective.type === 'case' ? ['Why did this happen?', 'What is the exposure?', 'What should I do next?'] : ['What needs my decision?', 'Give me a summary', 'Which connectors are unhealthy?'])
    : lastAssistant?.followups ?? [];

  return (
    <aside className={`as-panel ${mode}`} aria-label="Nimbus Assistant">
      <div className="as-head">
        <div className="as-title"><span className="as-logo"><Icon n="sparkles" s={16} /></span><div><b>Nimbus Assistant</b><small>Grounded in your data · read-only</small></div></div>
        <div className="as-actions">
          <button onClick={() => setView(view === 'history' ? 'chat' : 'history')} title="Conversations" aria-label="Conversations"><Icon n="history" /></button>
          <button onClick={startNew} title="New conversation" aria-label="New conversation"><Icon n="plus" /></button>
          <button onClick={exportChat} title="Copy conversation as Markdown" aria-label="Copy conversation"><Icon n={copied === 'export' ? 'check' : 'doc'} /></button>
          <button onClick={() => setMode(mode === 'docked' ? 'floating' : 'docked')} title={mode === 'docked' ? 'Unpin (float)' : 'Pin as sidebar'} aria-label="Toggle docking" className={mode === 'docked' ? 'on' : ''}><Icon n="pin" /></button>
          <button onClick={() => setOpen(false)} title="Close" aria-label="Close"><Icon n="x" /></button>
        </div>
      </div>

      {view === 'history' ? (
        <div className="as-history">
          <div className="as-search"><Icon n="search" s={14} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search conversations" aria-label="Search conversations" /></div>
          <button className="btn-primary btn-small" onClick={startNew}><Icon n="plus" s={13} /> New conversation</button>
          <div className="as-sessions">
            {shown.length === 0 && <p className="cc-empty">No conversations match.</p>}
            {shown.map((s) => (
              <div key={s.id} role="button" tabIndex={0} className={`as-session${s.id === activeId ? ' on' : ''}`} onClick={() => { setActiveId(s.id); setView('chat'); }} onKeyDown={(e) => e.key === 'Enter' && (setActiveId(s.id), setView('chat'))}>
                <div><b>{s.title}</b><small>{s.context.type === 'case' ? s.context.label : 'All cases'} · {s.messages.filter((m) => m.role === 'user').length} question(s) · {new Date(s.updatedAt).toLocaleDateString()}</small>
                  <em>{s.messages[s.messages.length - 1]?.content.replace(/[*`#]/g, '').slice(0, 70)}</em></div>
                <button onClick={(e) => { e.stopPropagation(); remove(s.id); }} aria-label={`Delete ${s.title}`}><Icon n="trash" s={14} /></button>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <>
          <div className="as-ctxbar">
            <div className="as-ctxwrap">
              <button className={`as-chip${locked ? ' locked' : ''}`} disabled={locked} onClick={() => setCtxMenu(!ctxMenu)}
                title={locked ? 'Context is locked for this conversation. Start a new one to change it.' : 'Choose what this chat is about (locks when you send a message)'}>
                <Icon n={locked ? 'lock' : effective.type === 'case' ? 'folder' : 'globe'} s={12} />
                <span>{effective.type === 'case' ? effective.label : 'All cases'}</span>{!locked && <Icon n="chevron" s={11} />}
              </button>
              {ctxMenu && !locked && (
                <div className="as-menu">
                  <small>Chat context</small>
                  <button onClick={() => chooseCtx({ type: 'portfolio' })}><Icon n="globe" s={13} /> All cases</button>
                  {caseId && caseLabel && <button onClick={() => chooseCtx({ type: 'case', caseId, label: caseLabel })}><Icon n="folder" s={13} /> {caseLabel}</button>}
                  {!caseId && <em>Select a case to chat about it.</em>}
                </div>
              )}
            </div>
            <select className="as-persona" value={persona} onChange={(e) => choosePersona(e.target.value as Persona)} aria-label="Perspective">
              {PERSONAS.map((p) => <option key={p.v} value={p.v}>{p.l}</option>)}
            </select>
          </div>

          <div className="as-body" ref={scroller} onScroll={(e) => { const t = e.currentTarget; setAway(t.scrollHeight - t.scrollTop - t.clientHeight > 150); }}>
            {active.messages.map((m, i) => m.role === 'user' ? (
              <div key={m.id} className="as-msg user"><div className="as-meta">You</div><div className="as-bubble">{m.content}</div></div>
            ) : (
              <div key={m.id} className="as-msg bot">
                <span className="as-avatar"><Icon n="sparkles" s={14} /></span>
                <div className="as-content">
                  <div className="as-meta"><b>Nimbus Assistant</b><span>{new Date(m.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>{m.via === 'llm' && <span className="as-ai">AI-drafted</span>}</div>
                  <Markdown text={m.content} />
                  {m.sources && m.sources.length > 0 && (
                    <div className="as-sources">{m.sources.map((s, j) => s.href ? <Link key={j} href={s.href}>{s.label}</Link> : <span key={j}>{s.label}</span>)}</div>
                  )}
                  {i > 0 && (
                    <div className="as-tools">
                      <button onClick={() => copy(m.id, m.content)} title="Copy" aria-label="Copy"><Icon n={copied === m.id ? 'check' : 'copy'} s={13} /></button>
                      {m.id === lastAssistant?.id && <button onClick={regenerate} title="Regenerate" aria-label="Regenerate"><Icon n="refresh" s={13} /></button>}
                      <i />
                      <button className={fb[m.id] === 'up' ? 'on' : ''} onClick={() => rate(m.id, 'up')} title="Helpful" aria-label="Helpful"><Icon n="up" s={13} /></button>
                      <button className={fb[m.id] === 'down' ? 'on' : ''} onClick={() => rate(m.id, 'down')} title="Not helpful" aria-label="Not helpful"><Icon n="down" s={13} /></button>
                    </div>
                  )}
                </div>
              </div>
            ))}
            {onlyGreeting && (
              <div className="as-caps">{CAPABILITIES.map(([t, d]) => <div key={t}><b>{t}</b><span>{d}</span></div>)}</div>
            )}
            {busy && <div className="as-msg bot"><span className="as-avatar"><Icon n="sparkles" s={14} /></span><div className="as-content"><span className="as-think">Thinking…</span></div></div>}
            <div ref={end} />
          </div>
          {away && <button className="as-down" onClick={() => end.current?.scrollIntoView({ behavior: 'smooth' })} aria-label="Scroll to latest"><Icon n="down2" s={15} /></button>}

          {chips.length > 0 && !busy && <div className="as-chips">{chips.slice(0, 3).map((c) => <button key={c} onClick={() => ask(c)}>{c}</button>)}</div>}
          <form className="as-composer" onSubmit={(e) => { e.preventDefault(); ask(input); }}>
            <textarea ref={ta} rows={1} value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about cases, evidence, policy…" aria-label="Message"
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input); } }} />
            <button type="submit" className="btn-primary btn-small" disabled={busy || !input.trim()}><Icon n="send" s={13} /> Send</button>
          </form>
          <div className="as-hint">Enter to send · Shift+Enter for a new line</div>
        </>
      )}
    </aside>
  );
}
