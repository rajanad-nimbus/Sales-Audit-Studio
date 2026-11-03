import type { Screen } from '@/components/MeProvider';

export interface NavItem {
  label: string;
  href: string;
  icon: string;
  /** Show the count of items waiting for a decision. */
  badge?: boolean;
}
export interface NavSection {
  title: string;
  items: NavItem[];
}

/** The order the groups appear in. The server decides which group each screen is in. */
export const GROUP_ORDER = [
  'Workspace',
  'Store Day Audit',
  'Investigate & Correct',
  'Reports',
  'Integrations',
  'Diagnostics',
];
// Configuration is not in the menu: it opens from the account menu, and its screens still carry
// this group so the Configuration page can list them.
const CONFIGURATION = 'Configuration';

const ICONS: Record<string, string> = {
  '/': 'home',
  '/actions': 'actions',
  '/cases': 'cases',
  '/activity': 'activity',
  '/store-days': 'totals',
  '/transactions': 'transactions',
  '/register-events': 'registerEvents',
  '/cash-office': 'cash',
  '/audit-errors': 'auditErrors',
  '/corrections': 'corrections',
  '/audit': 'audit',
  '/foundation': 'foundation',
  '/imports': 'imports',
  '/exports': 'exports',
};
export const iconFor = (path: string) =>
  path.startsWith('/reports/')
    ? 'reports'
    : path.startsWith('/diagnostics/')
      ? 'diagnostics'
      : (ICONS[path] ?? 'controls');

/**
 * The menu for a role, built from the screens the server says it may open (GET /api/me). The
 * server owns each screen's label, group and order, so the menu cannot disagree with who may open
 * what. Configuration screens are left out of the menu.
 */
export function navSections(screens: Screen[]): NavSection[] {
  const allowed = screens.filter((s) => s.allowed);
  const item = (s: Screen): NavItem => ({
    label: s.label,
    href: s.path,
    icon: iconFor(s.path),
    badge: s.path === '/actions',
  });
  // A group the server adds that is not listed above still shows, before Configuration.
  const extra = [...new Set(allowed.map((s) => s.group))].filter(
    (g) => g !== CONFIGURATION && !GROUP_ORDER.includes(g)
  );
  const sections = [...GROUP_ORDER, ...extra]
    .map((title) => ({
      title,
      items: allowed.filter((s) => s.group === title).map(item),
    }))
    .filter((section) => section.items.length > 0);
  return sections;
}
