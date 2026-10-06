'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useLive } from '@/lib/sync';
import { useCanOpen } from '@/components/MeProvider';
import { api, AuditEvent } from '@/lib/api';

type Level = 'info' | 'success' | 'warning';
interface Note { id: string; title: string; body: string; level: Level; caseId: string | null; at: string }

const TITLES: Record<string, [string, Level]> = {
  CASE_CREATED: ['New case', 'info'],
  INVESTIGATION_COMPLETED: ['Ready for decision', 'warning'],
  POLICY_EVALUATED: ['Policy evaluated', 'info'],
  HUMAN_DECISION: ['Decision recorded', 'info'],
  WORKFLOW_STARTED: ['Workflow started', 'info'],
  ACTION_EXECUTED: ['Action executed', 'info'],
  VALIDATION_VERIFIED: ['Verified and closed', 'success'],
  VALIDATION_WAITING: ['Validation waiting', 'warning'],
};

const toNote = (e: AuditEvent): Note => {
  const [title, level] = TITLES[e.event_type] ?? [e.event_type.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase()), 'info'];
  return { id: e.id, title, body: e.action_description, level, caseId: e.case_id, at: e.created_at };
};

const ago = (iso: string) => {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 60 ? 'just now' : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
};

const SEEN = 'nimbus_notif_seen';

export function NotificationBell() {
  const router = useRouter();
  const canOpen = useCanOpen();
  const [notes, setNotes] = useState<Note[]>([]);
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState<string>('');
  const [toasts, setToasts] = useState<Note[]>([]);
  const known = useRef<Set<string> | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let s = '';
    try { s = localStorage.getItem(SEEN) || ''; } catch { /* ignore */ }
    if (!s) { s = new Date().toISOString(); try { localStorage.setItem(SEEN, s); } catch { /* ignore */ } }
    setSeen(s);
  }, []);

  const poll = useCallback(async () => {
    try {
      const list = ((await api.get('/api/audit?limit=30')).data as AuditEvent[]).map(toNote);
      setNotes(list);
      if (known.current === null) { known.current = new Set(list.map((n) => n.id)); return; }
      const fresh = list.filter((n) => !known.current!.has(n.id));
      fresh.forEach((n) => known.current!.add(n.id));
      if (fresh.length) {
        setToasts((t) => [...fresh.slice(0, 3).reverse(), ...t].slice(0, 3));
        fresh.slice(0, 3).forEach((n) => setTimeout(() => setToasts((t) => t.filter((x) => x.id !== n.id)), 6000));
      }
    } catch { /* offline: keep last list */ }
  }, []);

  useLive(poll, 10000);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  const unread = notes.filter((n) => n.at > seen).length;
  const markAll = () => {
    const now = new Date().toISOString();
    setSeen(now);
    try { localStorage.setItem(SEEN, now); } catch { /* ignore */ }
  };
  const go = (n: Note) => { setOpen(false); setToasts((t) => t.filter((x) => x.id !== n.id)); if (n.caseId && canOpen('/cases')) router.push(`/cases/${n.caseId}`); };

  return (
    <>
      <div className="notif" ref={box}>
        <button className={`hdr-btn icon${open ? ' on' : ''}`} onClick={() => setOpen(!open)} aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6ZM10 19a2 2 0 0 0 4 0" />
          </svg>
          {unread > 0 && <span className="notif-badge">{unread > 9 ? '9+' : unread}</span>}
        </button>
        {open && (
          <div className="notif-menu" role="menu">
            <div className="notif-head"><b>Notifications</b><button onClick={markAll} disabled={unread === 0}>Mark all read</button></div>
            <div className="notif-list">
              {notes.length === 0 && <p className="cc-empty" style={{ padding: '1rem' }}>Nothing yet.</p>}
              {notes.map((n) => (
                <button key={n.id} className={`notif-item${n.at > seen ? ' unread' : ''}`} onClick={() => go(n)}>
                  <i className={`lvl ${n.level}`} />
                  <div><b>{n.title}</b><small>{n.body}</small></div>
                  <time>{ago(n.at)}</time>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="toasts" aria-live="polite">
        {toasts.map((n) => (
          <button key={n.id} className={`toast ${n.level}`} onClick={() => go(n)}>
            <b>{n.title}</b><small>{n.body}</small>
          </button>
        ))}
      </div>
    </>
  );
}
