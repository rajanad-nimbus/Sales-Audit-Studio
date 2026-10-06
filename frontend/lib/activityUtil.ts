// Shared by Agent Activity and the Agents configuration page.
// Every automated actor the backend writes to the audit trail. Anything else is a person.
export const GROUPS = [
  { key: 'reconciliation', label: 'Reconciliation', actors: ['reconciliation-engine'] },
  { key: 'investigation', label: 'Investigation', actors: ['investigation-agent'] },
  { key: 'policy', label: 'Policy', actors: ['policy-engine'] },
  { key: 'orchestrator', label: 'Orchestrator', actors: ['orchestrator'] },
  { key: 'resolution', label: 'Resolution', actors: ['resolution-agent'] },
  { key: 'validation', label: 'Validation', actors: ['validation-agent'] },
  { key: 'connectors', label: 'Connectors & batch', actors: ['batch-scheduler', 'shopify-connector', 'shopify-webhook', 'export-service'] },
];
export const SYSTEM_ACTORS = GROUPS.flatMap((g) => g.actors);
export const groupOf = (actor: string) =>
  GROUPS.find((g) => g.actors.some((a) => actor.startsWith(a)))?.key ?? (actor === 'system' ? 'system' : 'people');
export const FILTERS = [{ key: 'all', label: 'All' }, ...GROUPS, { key: 'people', label: 'People' }];

export const pretty = (s: string) => s.replace(/[-_.]/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());
export const tone = (type: string) =>
  /FAIL|ERROR|REJECT|BREACH|QUARANTIN/i.test(type) ? 'badge-error'
    : /COMPLETE|CLOSED|APPROVED|VALIDATED|SUCCEED|RESOLVED/i.test(type) ? 'badge-success'
    : /CREATED|RAISED|RECEIVED/i.test(type) ? 'badge-info' : 'badge-secondary';

export const isAlert = (type: string) => /FAIL|ERROR|REJECT|BREACH|QUARANTIN/i.test(type);
export const initials = (actor: string) => pretty(actor).split(' ').filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();
export const groupLabel = (actor: string) => FILTERS.find((f) => f.key === groupOf(actor))?.label ?? (actor === 'system' ? 'System' : 'People');

export const ago = (iso: string) => {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 5 ? 'just now' : s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : new Date(iso).toLocaleDateString();
};
export const dayLabel = (iso: string) => {
  const d = new Date(iso), t = new Date();
  const diff = Math.round((new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime() - new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) / 86400000);
  return diff === 0 ? 'Today' : diff === 1 ? 'Yesterday' : d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
};

