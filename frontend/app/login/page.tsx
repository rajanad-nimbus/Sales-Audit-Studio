'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Logo } from '@/components/Logo';
import { useMe } from '@/components/MeProvider';

const STUDIO = process.env.NEXT_PUBLIC_ONTOLOGY_STUDIO_URL || 'http://localhost:3050';

/** Nimbus has no passwords of its own. People sign in at Ontology Studio, and this screen sends them there. */
export default function LoginPage() {
  const { me, reload } = useMe();
  const router = useRouter();
  const [signedOut, setSignedOut] = useState(false);
  const [redirecting, setRedirecting] = useState(false);

  useEffect(() => { setSignedOut(new URLSearchParams(window.location.search).get('signedout') === '1'); }, []);

  // Once a session exists (signed in here earlier, or in another tab), continue to where the person was headed.
  useEffect(() => {
    if (!me) return;
    let next = '/';
    try { next = sessionStorage.getItem('nimbus_next') || '/'; sessionStorage.removeItem('nimbus_next'); } catch { /* ignore */ }
    router.replace(next.startsWith('/') && !next.startsWith('//') && next !== '/login' ? next : '/');
  }, [me, router]);

  // Ontology Studio and Nimbus share the browser session, so notice a sign-in finished in another tab.
  useEffect(() => {
    const check = () => { if (!document.hidden) reload(); };
    const t = setInterval(check, 4000);
    window.addEventListener('focus', check);
    return () => { clearInterval(t); window.removeEventListener('focus', check); };
  }, [reload]);

  const signIn = () => {
    setRedirecting(true);
    // Ontology Studio only returns to paths on its own site today, so it ignores this. It is sent for when it does not.
    window.location.href = `${STUDIO}/?returnTo=${encodeURIComponent(`${window.location.origin}/`)}`;
  };

  const loading = me === undefined || !!me;
  return (
    <div className="login-page">
      <main className="login-card" aria-labelledby="login-h">
        <div className="login-mark"><Logo size={40} title="Sales Audit Studio" /></div>
        <h1 id="login-h">Sales Audit Studio</h1>
        <p className="login-sub">Agentic financial exception management for retail commerce</p>
        {loading ? <p className="login-note" role="status">{me ? 'Signed in. Opening Sales Audit Studio…' : 'Checking your session…'}</p> : (
          <>
            {signedOut && <div className="alert alert-info" role="status">You have been signed out.</div>}
            <button className="btn-primary btn-lg login-btn" onClick={signIn} disabled={redirecting}>{redirecting ? 'Opening Ontology Studio…' : 'Sign in with Ontology Studio'}</button>
            <p className="login-note">Your account, role and stores are managed in Ontology Studio. After signing in there, come back to Sales Audit Studio and you will be taken straight in.</p>
            <button className="login-link" onClick={reload}>I have already signed in</button>
          </>
        )}
      </main>
      <p className="login-foot">© Nimbus Consulting</p>
    </div>
  );
}
