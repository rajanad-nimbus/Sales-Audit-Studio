'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { useMe } from './MeProvider';

const PERSONAS = [['analyst', 'Sales Auditor'], ['finance', 'Finance Approver'], ['store_manager', 'Store Manager'], ['it', 'IT Support Admin'], ['auditor', 'Internal Audit']] as const;
const ROLE_LABEL: Record<string, string> = { analyst: 'Sales Auditor', finance: 'Finance Approver', store_manager: 'Store Manager', it: 'IT Support Admin', auditor: 'Internal Audit', admin: 'Administrator' };

export { useMe } from './MeProvider';

export function UserMenu() {
  const { me } = useMe();
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState('');
  const [pickStores, setPickStores] = useState(false);
  const [storesText, setStoresText] = useState(() => { try { return localStorage.getItem('nimbus_impersonate_stores') ?? ''; } catch { return ''; } });
  const ontologyStudioUrl = process.env.NEXT_PUBLIC_ONTOLOGY_STUDIO_URL || 'http://localhost:3050';
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  const hasToken = (() => { try { return !!localStorage.getItem('nimbus_token'); } catch { return false; } })();
  const signedOut = me === null;
  const name = me === undefined ? '…' : signedOut ? 'Signed out' : me!.user!;
  const sub = me === undefined ? '' : signedOut ? 'Ontology Studio sign-in required' : me!.impersonating ? `Acting as ${ROLE_LABEL[me!.role]}` : (ROLE_LABEL[me!.role] ?? me!.role) + (me!.store_scoped ? (me!.stores?.length ? ` · ${me!.stores.join(', ')}` : ' · no stores assigned') : '');
  const canImpersonate = !signedOut && me !== undefined && me!.real_role === 'admin';
  const impersonate = (role: string | null, stores?: string) => {
    try {
      role ? localStorage.setItem('nimbus_impersonate', role) : localStorage.removeItem('nimbus_impersonate');
      role === 'store_manager' && stores ? localStorage.setItem('nimbus_impersonate_stores', stores) : localStorage.removeItem('nimbus_impersonate_stores');
    } catch { /* ignore */ }
    window.location.reload();
  };
  const initials = signedOut || me === undefined ? '?' : name.slice(0, 2).toUpperCase();

  const signIn = (e: React.FormEvent) => {
    e.preventDefault();
    if (!token.trim()) return;
    try { localStorage.setItem('nimbus_token', token.trim()); } catch { /* ignore */ }
    window.location.reload();
  };
  const signOut = () => {
    try { localStorage.removeItem('nimbus_token'); } catch { /* ignore */ }
    window.location.reload();
  };

  return (
    <div className="user-menu" ref={box}>
      <button className="user-chip" onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open}>
        <span className="user-avatar">{initials}</span>
        <span className="user-chip-text"><b>{name}</b><small>{sub}</small></span>
      </button>
      {open && (
        <div className="user-pop" role="menu">
          <div className="user-pop-head">
            <span className="user-avatar">{initials}</span>
            <div>
              <b>{name}</b>
              <small>{signedOut ? 'Sign in through Ontology Studio' : `Ontology role: ${me!.role}`}</small>
            </div>
          </div>
          {canImpersonate && (
            <div className="user-pop-imp">
              <small>Impersonate</small>
              {PERSONAS.map(([key, label]) => (
                <button key={key} className={me!.role === key ? 'on' : ''} onClick={() => (key === 'store_manager' ? setPickStores((v) => !v) : impersonate(key))}>{label}</button>
              ))}
              {pickStores && (
                <div className="user-pop-form">
                  <label htmlFor="imp-stores">Which stores? (comma separated)</label>
                  <input id="imp-stores" value={storesText} onChange={(e) => setStoresText(e.target.value)} placeholder="STORE-007, STORE-014" />
                  <small className="hint">A real store manager gets these from Ontology Studio. With none, the store manager sees nothing.</small>
                  <button className="btn-primary btn-small" onClick={() => impersonate('store_manager', storesText.split(',').map((x) => x.trim()).filter(Boolean).join(','))}>Act as Store Manager</button>
                </div>
              )}
              {me!.impersonating && <button onClick={() => impersonate(null)}>Stop impersonating</button>}
            </div>
          )}
          <Link href="/settings" className="user-pop-link" onClick={() => setOpen(false)}>Configuration</Link>
          {signedOut && <a className="user-pop-link" href={ontologyStudioUrl}>Open Ontology Studio sign-in</a>}
          {(signedOut || !hasToken) && (
            <form onSubmit={signIn} className="user-pop-form">
              <label htmlFor="tok">Ontology Studio bearer token</label>
              <input id="tok" type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Paste a JWT for API access" autoComplete="off" />
              <button type="submit" className="btn-primary btn-small" disabled={!token.trim()}>Sign in</button>
            </form>
          )}
          {hasToken && <button className="user-pop-out" onClick={signOut}>Sign out</button>}
        </div>
      )}
    </div>
  );
}
