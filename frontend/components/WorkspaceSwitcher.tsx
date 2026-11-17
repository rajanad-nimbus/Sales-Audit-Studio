'use client';

import { useEffect, useMemo, useRef } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { MENU_ONLY, navSections } from '@/lib/nav';
import { screenFor, useMe } from '@/components/MeProvider';

export function WorkspaceSwitcher() {
  const pathname = usePathname();
  const { me } = useMe();
  const details = useRef<HTMLDetailsElement>(null);
  const sections = useMemo(() => navSections(me?.screens ?? [], MENU_ONLY), [me]);
  const activeScreen = screenFor(me?.screens, pathname);
  const activeSection = sections.find((section) => section.title === activeScreen?.group);

  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (details.current && !details.current.contains(event.target as Node)) {
        details.current.open = false;
      }
    };
    document.addEventListener('pointerdown', closeOutside);
    return () => document.removeEventListener('pointerdown', closeOutside);
  }, []);

  if (sections.length < 2) return null;

  return (
    <details
      className="workspace-switcher"
      ref={details}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          details.current?.removeAttribute('open');
          details.current?.querySelector<HTMLElement>('summary')?.focus();
        }
      }}
    >
      <summary aria-label="Switch workspace" title="Switch workspace">
        <span className="workspace-switcher-mark" aria-hidden="true">
          <svg viewBox="0 0 20 20" fill="currentColor">
            <rect x="2" y="2" width="6" height="6" rx="1" />
            <rect x="12" y="2" width="6" height="6" rx="1" />
            <rect x="2" y="12" width="6" height="6" rx="1" />
            <rect x="12" y="12" width="6" height="6" rx="1" />
          </svg>
        </span>
        <span className="workspace-switcher-label">
          <small>Workspace</small>
          <b>
            {activeSection?.title ??
              (activeScreen?.group === 'Configuration' ? 'Admin' : 'Choose')}
          </b>
        </span>
        <span className="workspace-switcher-chevron" aria-hidden="true">
          ⌄
        </span>
      </summary>
      <div className="workspace-switcher-menu" aria-label="Available workspaces">
        <p>Switch workspace</p>
        {sections.map((section) => {
          const first = section.items[0];
          const active = section.title === activeSection?.title;
          return (
            <Link
              key={section.title}
              href={first.href}
              aria-current={active ? 'page' : undefined}
              onClick={() => {
                if (details.current) details.current.open = false;
              }}
            >
              <span className="workspace-switcher-item-title">{section.title}</span>
              <span className="workspace-switcher-item-meta">
                {section.items.length} {section.items.length === 1 ? 'screen' : 'screens'} ·{' '}
                {first.label}
              </span>
              {active && <span className="workspace-switcher-current">Current</span>}
            </Link>
          );
        })}
      </div>
    </details>
  );
}
