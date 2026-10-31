'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { useMe } from './MeProvider';
import { Button } from '@/components/ui';
import { useStores } from '@/lib/useStores';

const PERSONAS = [
  ['analyst', 'Sales Auditor'],
  ['finance', 'Finance Approver'],
  ['store_manager', 'Store Manager'],
  ['it', 'IT Support Admin'],
  ['auditor', 'Internal Audit'],
] as const;
const ROLE_LABEL: Record<string, string> = {
  analyst: 'Sales Auditor',
  finance: 'Finance Approver',
  store_manager: 'Store Manager',
  it: 'IT Support Admin',
  auditor: 'Internal Audit',
  admin: 'Administrator',
};

export { useMe } from './MeProvider';

export function UserMenu() {
  const { me } = useMe();
  const [open, setOpen] = useState(false);
  const [pickStores, setPickStores] = useState(false);
  const [storesText, setStoresText] = useState(() => {
    try {
      return localStorage.getItem('nimbus_impersonate_stores') ?? '';
    } catch {
      return '';
    }
  });
  const box = useRef<HTMLDivElement>(null);
  const knownStores = useStores();
  const chosenStores = storesText
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  const signedOut = me === null;
  const name =
    me === undefined ? '…' : signedOut ? 'Signed out' : me!.name || me!.user || 'Unknown user';
  const sub =
    me === undefined
      ? ''
      : signedOut
        ? 'Ontology Studio sign-in required'
        : me!.impersonating
          ? `Acting as ${ROLE_LABEL[me!.role]}`
          : (ROLE_LABEL[me!.role] ?? me!.role) +
            (me!.store_scoped
              ? me!.stores?.length
                ? ` · ${me!.stores.join(', ')}`
                : ' · no stores assigned'
              : '');
  const canImpersonate = !signedOut && me !== undefined && me!.real_role === 'admin';
  const impersonate = (role: string | null, stores?: string) => {
    try {
      role
        ? localStorage.setItem('nimbus_impersonate', role)
        : localStorage.removeItem('nimbus_impersonate');
      role === 'store_manager' && stores
        ? localStorage.setItem('nimbus_impersonate_stores', stores)
        : localStorage.removeItem('nimbus_impersonate_stores');
    } catch {
      /* ignore */
    }
    window.location.reload();
  };
  const initials =
    signedOut || me === undefined
      ? '?'
      : (
          name
            .split(/[\s._-]+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((w) => w[0])
            .join('') || name.slice(0, 1)
        ).toUpperCase();

  const [signingOut, setSigningOut] = useState(false);
  const signOut = async () => {
    setSigningOut(true);
    // Ends the Ontology Studio session cookie (shared by both apps), then clears what this browser remembers.
    try {
      await api.post('/api/auth/logout');
    } catch {
      /* already signed out, or the service is down: still leave */
    }
    try {
      ['nimbus_impersonate', 'nimbus_impersonate_stores', 'nimbus_next', 'nimbus_token'].forEach(
        (k) => {
          localStorage.removeItem(k);
          sessionStorage.removeItem(k);
        }
      );
    } catch {
      /* ignore */
    }
    window.location.assign('/login?signedout=1');
  };

  return (
    <div className="user-menu" ref={box}>
      <button
        className="user-chip"
        onClick={() => setOpen(!open)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="user-avatar">{initials}</span>
        <span className="user-chip-text">
          <b>{name}</b>
          <small>{sub}</small>
        </span>
      </button>
      {open && (
        <div className="user-pop" role="menu">
          <div className="user-pop-head">
            <span className="user-avatar">{initials}</span>
            <div>
              <b>{name}</b>
              <small>
                {signedOut
                  ? 'Sign in through Ontology Studio'
                  : `${me!.user ?? ''}${me!.user ? ' · ' : ''}${ROLE_LABEL[me!.role] ?? me!.role}`}
              </small>
            </div>
          </div>
          {canImpersonate && (
            <div className="user-pop-imp">
              <small>Impersonate</small>
              {PERSONAS.map(([key, label]) => (
                <button
                  key={key}
                  className={me!.role === key ? 'on' : ''}
                  onClick={() =>
                    key === 'store_manager' ? setPickStores((v) => !v) : impersonate(key)
                  }
                >
                  {label}
                </button>
              ))}
              {pickStores && (
                <div className="user-pop-form">
                  <label>Which stores?</label>
                  <div className="imp-stores" role="group" aria-label="Stores">
                    {knownStores.length === 0 && <small className="hint">No stores yet.</small>}
                    {knownStores.map((s) => (
                      <label key={s} className="imp-store">
                        <input
                          type="checkbox"
                          checked={chosenStores.includes(s)}
                          onChange={(e) =>
                            setStoresText(
                              (e.target.checked
                                ? [...chosenStores, s]
                                : chosenStores.filter((x) => x !== s)
                              ).join(',')
                            )
                          }
                        />
                        {s}
                      </label>
                    ))}
                  </div>
                  <small className="hint">
                    A real store manager gets these from Ontology Studio. With none, the store
                    manager sees nothing.
                  </small>
                  <Button
                    variant="primary"
                    size="sm"

                    onClick={() =>
                      impersonate(
                        'store_manager',
                        storesText
                          .split(',')
                          .map((x) => x.trim())
                          .filter(Boolean)
                          .join(',')
                      )
                    }
                  >
                    Act as Store Manager
                  </Button>
                </div>
              )}
              {me!.impersonating && (
                <button onClick={() => impersonate(null)}>Stop impersonating</button>
              )}
            </div>
          )}
          <Link href="/settings" className="user-pop-link" onClick={() => setOpen(false)}>
            Configuration
          </Link>
          {signedOut ? (
            <Link href="/login" className="user-pop-link" onClick={() => setOpen(false)}>
              Sign in
            </Link>
          ) : (
            <button className="user-pop-out" onClick={signOut} disabled={signingOut}>
              {signingOut ? 'Signing out…' : 'Sign out'}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
