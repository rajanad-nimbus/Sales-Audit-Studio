'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useMe } from '@/components/MeProvider';
import { PageHeader } from '@/components/ui';

const HINTS: Record<string, string> = {
  settings: 'Theme and your account',
  'settings-integrations': 'Shopify, source feeds, matching tolerance, go-live checks',
  'settings-controls': 'Totals, audit rules and GL mapping',
  'settings-close': 'Variance tolerances and closing the books',
  'settings-retention': 'How long each dataset is kept',
  'settings-agents': 'The AI investigator and what each agent may do',
  'settings-destinations': 'Where exports are delivered',
  'settings-access': 'Which role can open which screen',
};

export default function ConfigurationLayout({ children }: { children: React.ReactNode }) {
  const { me } = useMe();
  const pathname = usePathname();
  const items = (me?.screens ?? []).filter((s) => s.group === 'Configuration' && s.allowed);
  const active = (path: string) =>
    path === '/settings'
      ? pathname === '/settings'
      : pathname === path || pathname.startsWith(path + '/');
  return (
    <div className="page-container">
      <PageHeader
        title="Configuration"
        subtitle="Everything that sets how Nimbus behaves. You see the sections your role may open."
      />
      <div className="cfg-layout">
        <nav className="cfg-nav" aria-label="Configuration sections">
          {items.map((s) => (
            <Link
              key={s.key}
              href={s.path}
              className={`cfg-link${active(s.path) ? ' on' : ''}`}
              aria-current={active(s.path) ? 'page' : undefined}
            >
              <b>{s.label}</b>
              <small>{HINTS[s.key]}</small>
            </Link>
          ))}
        </nav>
        <div className="cfg-body">{children}</div>
      </div>
    </div>
  );
}
