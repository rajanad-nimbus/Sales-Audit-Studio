'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

export interface Screen { key: string; path: string; label: string; group: string; allowed: boolean; roles: string[] }
export interface Me { user: string | null; role: string; role_label?: string; real_role?: string; impersonating?: boolean; screens: Screen[];
  store_scoped?: boolean; stores?: string[] | null }   // stores: null = every store; a list (maybe empty) = only those


interface Ctx { me: Me | null | undefined; reload: () => void }
const MeContext = createContext<Ctx>({ me: undefined, reload: () => {} });

/** One /api/me for the whole app. The server decides which screens the role may open; nothing else is hard-coded here. */
export function MeProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const load = useCallback(() => { api.get('/api/me').then((r) => setMe(r.data)).catch(() => setMe(null)); }, []);
  useEffect(() => { load(); }, [load]);
  const value = useMemo(() => ({ me, reload: load }), [me, load]);
  return <MeContext.Provider value={value}>{children}</MeContext.Provider>;
}

export const useMe = () => useContext(MeContext);

/** The screen that owns a path: the longest registered path that is a prefix of it. */
export function screenFor(screens: Screen[] | undefined, pathname: string): Screen | undefined {
  return [...(screens ?? [])].sort((a, b) => b.path.length - a.path.length)
    .find((s) => (s.path === '/' ? pathname === '/' : pathname === s.path || pathname.startsWith(s.path + '/')));
}

export function useCanOpen() {
  const { me } = useMe();
  return useCallback((path: string) => {
    if (!me) return false;
    return !!screenFor(me.screens, path)?.allowed;
  }, [me]);
}

/** A link to a case, or plain text when this role has no access to Cases (so nobody is sent to a page they will be refused). */
export function CaseLink({ id, children }: { id: string; children: React.ReactNode }) {
  const canOpen = useCanOpen();
  return canOpen('/cases') ? <Link href={`/cases/${id}`}>{children}</Link> : <span>{children}</span>;
}

/** Blocks a screen the role may not open, before it mounts or fetches anything. The API refuses the same calls independently. */
export function AccessGuard({ children }: { children: React.ReactNode }) {
  const { me, reload } = useMe();
  const pathname = usePathname();
  const router = useRouter();
  const screen = me ? screenFor(me.screens, pathname) : undefined;
  const firstAllowed = me?.screens.find((s) => s.allowed && s.group !== 'Configuration') ?? me?.screens.find((s) => s.allowed);
  const blocked = !!me && !!screen && !screen.allowed;

  useEffect(() => { if (blocked && pathname === '/' && firstAllowed) router.replace(firstAllowed.path); }, [blocked, pathname, firstAllowed, router]);

  if (me === undefined) return <div className="loading" role="status">Checking your access</div>;
  if (me === null) {
    return (
      <div className="empty-state" role="alert"><b>You are not signed in</b>
        <p>Sign in through Ontology Studio, then reload. If you are signed in, the service may be unreachable.</p>
        <button className="btn-secondary btn-sm" onClick={reload}>Try again</button></div>
    );
  }
  if (blocked) {
    return (
      <div className="empty-state" role="alert">
        <b>You don&apos;t have access to {screen!.label}</b>
        <p>You are signed in as <b>{me.role_label ?? me.role}</b>{me.impersonating ? ' (impersonating)' : ''}. This screen is for: {screen!.roles.length ? screen!.roles.join(', ') : 'administrators'}.</p>
        {firstAllowed && <Link href={firstAllowed.path} className="btn-primary btn-sm" style={{ display: 'inline-flex', textDecoration: 'none' }}>Go to {firstAllowed.label}</Link>}
      </div>
    );
  }
  return <>{children}</>;
}
