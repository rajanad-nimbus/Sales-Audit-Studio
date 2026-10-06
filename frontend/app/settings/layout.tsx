'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useMe } from '@/components/MeProvider';

const HINTS: Record<string, string> = {
  settings: 'Appearance and your account',
  'settings-integrations': 'Source feeds, Shopify, Ontology policy, tolerance, readiness',
  'settings-controls': 'Stores, tenders, totals, audit rules, GL mapping',
  'settings-retention': 'How long each dataset is kept',
  'settings-agents': 'The LLM agent and what each agent may do',
  'settings-destinations': 'Where exports are delivered',
  'settings-access': 'Which role can open which screen',
};

export default function ConfigurationLayout({ children }: { children: React.ReactNode }) {
  const { me } = useMe();
  const pathname = usePathname();
  const items = (me?.screens ?? []).filter((s) => s.group === 'Configuration' && s.allowed);
  const active = (path: string) => (path === '/settings' ? pathname === '/settings' : pathname === path || pathname.startsWith(path + '/'));
  return (
    <div className="page-container">
      <div className="page-header"><div><h1>Configuration</h1><p className="page-sub">Everything that sets how Nimbus behaves, in one place. You see the sections your role may open.</p></div></div>
      <div className="cfg-layout">
        <nav className="cfg-nav" aria-label="Configuration sections">
          {items.map((s) => (
            <Link key={s.key} href={s.path} className={`cfg-link${active(s.path) ? ' on' : ''}`} aria-current={active(s.path) ? 'page' : undefined}>
              <b>{s.label}</b><small>{HINTS[s.key]}</small>
            </Link>
          ))}
        </nav>
        <div className="cfg-body">{children}</div>
      </div>
    </div>
  );
}
