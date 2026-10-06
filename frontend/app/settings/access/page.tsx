'use client';

import { Fragment, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { SkeletonRows } from '@/components/Feedback';

interface Matrix { roles: { key: string; label: string }[]; screens: { key: string; path: string; label: string; group: string; roles: string[] }[]; you: string }

export default function AccessPage() {
  const [m, setM] = useState<Matrix | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.get('/api/access/matrix').then((r) => setM(r.data)).catch(() => setError('Could not load the access map.')); }, []);
  if (error) return <div className="alert alert-error" role="alert">{error}</div>;
  if (!m) return <SkeletonRows rows={8} label="Loading access map" />;
  const groups = Array.from(new Set(m.screens.map((s) => s.group)));
  return (
    <section className="info-card">
      <h2>Roles &amp; access</h2>
      <p className="hint" style={{ marginBottom: '.75rem' }}>Who can open each screen. The API enforces the same map, so a role without a screen cannot call its data either. Administrators can open everything and can act as another role from the user menu. Your role is highlighted.</p>
      <div className="alert alert-info" style={{ marginBottom: '.75rem' }}><b>Store managers are also limited to their own stores.</b> Which stores a person manages is maintained in Ontology Studio, and Nimbus applies it to every store-level screen and API call. With no store assigned, a store manager sees nothing.</div>
      <div className="table-scroll"><table className="data-table access-matrix">
        <thead><tr><th>Screen</th>{m.roles.map((r) => <th key={r.key} className={`num ${r.key === m.you ? 'you' : ''}`}>{r.label}{r.key === m.you && <small className="act-sub" style={{ display: 'block' }}>You</small>}</th>)}</tr></thead>
        <tbody>{groups.map((g) => (
          <Fragment key={g}>
            <tr className="grp"><td colSpan={m.roles.length + 1}>{g}</td></tr>
            {m.screens.filter((s) => s.group === g).map((s) => (
              <tr key={s.key}><td className="font-medium">{s.label}<small className="act-sub" style={{ display: 'block' }}>{s.path}</small></td>
                {m.roles.map((r) => {
                  const ok = s.roles.includes(r.key);
                  return <td key={r.key} className={`num ${r.key === m.you ? 'you' : ''}`} aria-label={`${r.label}: ${ok ? 'can open' : 'cannot open'} ${s.label}`}>{ok ? <span className="ok">●</span> : <span className="no">–</span>}</td>;
                })}</tr>
            ))}
          </Fragment>
        ))}</tbody></table></div>
    </section>
  );
}
