import { describe, expect, it } from 'vitest';
import type { Screen } from '@/components/MeProvider';
import { GROUP_ORDER, iconFor, navSections } from '@/lib/nav';

const screen = (path: string, label: string, group: string, allowed = true): Screen => ({
  key: path,
  path,
  label,
  group,
  allowed,
  roles: [],
});

// The screens the server registers, in the order it lists them.
const SERVER: Screen[] = [
  screen('/', 'Command Center', 'Workspace'),
  screen('/actions', 'Action Center', 'Workspace'),
  screen('/cases', 'Cases', 'Workspace'),
  screen('/store-days', 'Store Days & Totals', 'Store Day Audit'),
  screen('/transactions', 'Transactions', 'Store Day Audit'),
  screen('/foundation', 'Foundation Data', 'Store Day Audit'),
  screen('/audit-errors', 'Audit Errors', 'Investigate & Correct'),
  screen('/audit', 'Audit Trail', 'Investigate & Correct'),
  screen('/reports/variances', 'Total variances', 'Reports'),
  screen('/reports/tax', 'Tax / VAT', 'Reports'),
  screen('/imports', 'Imports', 'Integrations'),
  screen('/exports', 'Exports', 'Integrations'),
  screen('/diagnostics/health', 'System health', 'Diagnostics'),
  screen('/diagnostics/integrity', 'Audit integrity', 'Diagnostics'),
  screen('/activity', 'Agent Activity', 'Diagnostics'),
  screen('/settings', 'General', 'Configuration'),
  screen('/settings/tenders', 'Tender types', 'Configuration'),
];

const titles = (sections: ReturnType<typeof navSections>) => sections.map((s) => s.title);
const labels = (sections: ReturnType<typeof navSections>, title: string) =>
  sections.find((s) => s.title === title)?.items.map((i) => i.label);

describe('navSections', () => {
  it('groups the screens in the agreed order', () => {
    expect(titles(navSections(SERVER))).toEqual([
      'Workspace',
      'Store Day Audit',
      'Investigate & Correct',
      'Integrations',
      'Diagnostics',
    ]);
    // Reports are kept out of the menu (they open by URL), so a group order entry may be unused.
    expect(GROUP_ORDER.filter((g) => titles(navSections(SERVER)).includes(g))).toEqual(
      titles(navSections(SERVER))
    );
  });

  it('puts Imports and Exports together under Integrations', () => {
    expect(labels(navSections(SERVER), 'Integrations')).toEqual(['Imports', 'Exports']);
  });

  it('keeps each screen in the group and order the server gave it', () => {
    const sections = navSections(SERVER);
    expect(labels(sections, 'Store Day Audit')).toEqual([
      'Store Days & Totals',
      'Transactions',
      'Foundation Data',
    ]);
    // Diagnostics pages under /diagnostics/ open by URL; only Agent Activity is in the menu.
    expect(labels(sections, 'Diagnostics')).toEqual(['Agent Activity']);
    expect(labels(sections, 'Reports')).toBeUndefined();
    expect(labels(sections, 'Workspace')).not.toContain('Agent Activity');
  });

  it('leaves Configuration out of the menu', () => {
    const sections = navSections(SERVER);
    expect(titles(sections)).not.toContain('Configuration');
    const everything = sections.flatMap((s) => s.items.map((i) => i.href));
    expect(everything.some((href) => href.startsWith('/settings'))).toBe(false);
  });

  it('shows only the screens the role may open, and drops a group left empty', () => {
    const limited = SERVER.map((s) =>
      ['/store-days', '/activity', '/imports'].includes(s.path)
        ? s
        : { ...s, allowed: false }
    );
    const sections = navSections(limited);
    expect(titles(sections)).toEqual(['Store Day Audit', 'Integrations', 'Diagnostics']);
    expect(labels(sections, 'Diagnostics')).toEqual(['Agent Activity']);
    expect(labels(sections, 'Integrations')).toEqual(['Imports']);
  });

  it('gives a role with no screens an empty menu', () => {
    expect(navSections([])).toEqual([]);
    expect(navSections(SERVER.map((s) => ({ ...s, allowed: false })))).toEqual([]);
  });

  it('still shows a group the server adds that the menu does not know yet', () => {
    const sections = navSections([...SERVER, screen('/new', 'New thing', 'Experiments')]);
    expect(titles(sections).at(-1)).toBe('Experiments');
  });

  it('marks only the Action Center for the waiting-decisions badge', () => {
    const items = navSections(SERVER).flatMap((s) => s.items);
    expect(items.filter((i) => i.badge).map((i) => i.href)).toEqual(['/actions']);
  });
});

describe('iconFor', () => {
  it('uses one icon for every report and for every diagnostics page', () => {
    expect(iconFor('/reports/tax')).toBe('reports');
    expect(iconFor('/reports/tax-corrections')).toBe('reports');
    expect(iconFor('/diagnostics/feeds')).toBe('diagnostics');
  });
  it('has its own icon for Imports, and a default for an unknown screen', () => {
    expect(iconFor('/imports')).toBe('imports');
    expect(iconFor('/exports')).toBe('exports');
    expect(iconFor('/something-new')).toBe('controls');
  });
});

describe('narrowing the menu to a few screens', () => {
  it('keeps only the listed screens the role may open, in their groups', () => {
    const sections = navSections(SERVER, ['/store-days', '/imports']);
    expect(titles(sections)).toEqual(['Store Day Audit', 'Integrations']);
    expect(labels(sections, 'Store Day Audit')).toEqual(['Store Days & Totals']);
    expect(labels(sections, 'Integrations')).toEqual(['Imports']);
  });

  it('never shows a listed screen the role may not open', () => {
    const denied = SERVER.map((s) => (s.path === '/imports' ? { ...s, allowed: false } : s));
    expect(titles(navSections(denied, ['/store-days', '/imports']))).toEqual(['Store Day Audit']);
  });
});
