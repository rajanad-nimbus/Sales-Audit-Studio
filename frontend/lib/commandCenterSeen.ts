/**
 * "What changed since you last looked" for the Command Center.
 *
 * The page remembers, in this browser only, a small picture of what it showed when the person
 * last left it. On the next visit it compares the live numbers with that picture. Nothing here is
 * shared or sent to the server: it is a per-viewer convenience, and an empty or blocked storage
 * simply means no comparison is shown.
 */

export const COUNTERS = [
  'sla_breached',
  'needs_decision',
  'at_risk',
  'unassigned',
  'open_total',
] as const;
export type Counter = (typeof COUNTERS)[number];

/** What the page showed at one moment. */
export interface Seen {
  at: string;
  counts: Partial<Record<Counter, number>>;
  /** Case id to SLA state, for the cases in the work queue. */
  queue: Record<string, string>;
  failedExports: string[];
  feedsBehind: string[];
  close: { date: string; closed: number } | null;
}

/** The parts of the Command Center response this needs. Every part is optional. */
export interface SeenInput {
  counts?: Partial<Record<Counter, number>> | null;
  queue?: { id: string; sla_state: string }[] | null;
  failedExports?: { id: string }[] | null;
  feeds?: { source_system: string; state: string }[] | null;
  close?: { business_date: string; closed: number } | null;
}

export function snapshot(input: SeenInput, at = new Date()): Seen {
  const counts: Partial<Record<Counter, number>> = {};
  for (const k of COUNTERS) {
    const v = input.counts?.[k];
    if (typeof v === 'number') counts[k] = v;
  }
  return {
    at: at.toISOString(),
    counts,
    queue: Object.fromEntries((input.queue ?? []).map((r) => [r.id, r.sla_state])),
    failedExports: (input.failedExports ?? []).map((b) => b.id),
    feedsBehind: (input.feeds ?? [])
      .filter((f) => f.state !== 'Current')
      .map((f) => f.source_system),
    close: input.close ? { date: input.close.business_date, closed: input.close.closed } : null,
  };
}

export type ChangeTone = 'worse' | 'better' | 'neutral';
export interface Change {
  key: string;
  text: string;
  tone: ChangeTone;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** For counters where a higher number is worse. */
const COUNTER_TEXT: Record<Counter, { more: (n: number) => string; fewer: (n: number) => string }> =
  {
    sla_breached: {
      more: (n) => `${plural(n, 'more case')} past SLA`,
      fewer: (n) => `${plural(n, 'fewer case')} past SLA`,
    },
    needs_decision: {
      more: (n) => `${plural(n, 'more case')} waiting for a decision`,
      fewer: (n) => `${plural(n, 'fewer case')} waiting for a decision`,
    },
    at_risk: {
      more: (n) => `${plural(n, 'more case')} due within 8 hours`,
      fewer: (n) => `${plural(n, 'fewer case')} due within 8 hours`,
    },
    unassigned: {
      more: (n) => `${plural(n, 'more unassigned case')}`,
      fewer: (n) => `${plural(n, 'fewer unassigned case')}`,
    },
    open_total: {
      more: (n) => `${plural(n, 'more open case')}`,
      fewer: (n) => `${plural(n, 'fewer open case')}`,
    },
  };

/** The changes between two pictures, worst first. Empty when nothing worth saying changed. */
export function changes(before: Seen, now: Seen): Change[] {
  const out: Change[] = [];
  for (const k of COUNTERS) {
    const a = before.counts[k];
    const b = now.counts[k];
    if (a === undefined || b === undefined || a === b) continue;
    const d = b - a;
    out.push({
      key: k,
      text: d > 0 ? COUNTER_TEXT[k].more(d) : COUNTER_TEXT[k].fewer(-d),
      tone: d > 0 ? 'worse' : 'better',
    });
  }
  const newFailed = now.failedExports.filter((id) => !before.failedExports.includes(id)).length;
  if (newFailed)
    out.push({ key: 'exports', text: `${plural(newFailed, 'export')} failed`, tone: 'worse' });
  const fixed = before.failedExports.filter((id) => !now.failedExports.includes(id)).length;
  if (fixed)
    out.push({
      key: 'exports-fixed',
      text: `${plural(fixed, 'failed export')} cleared`,
      tone: 'better',
    });

  const behind = now.feedsBehind.filter((f) => !before.feedsBehind.includes(f));
  if (behind.length)
    out.push({ key: 'feeds', text: `${behind.join(', ')} fell behind`, tone: 'worse' });
  const recovered = before.feedsBehind.filter((f) => !now.feedsBehind.includes(f));
  if (recovered.length)
    out.push({ key: 'feeds-ok', text: `${recovered.join(', ')} caught up`, tone: 'better' });

  if (before.close && now.close && before.close.date === now.close.date) {
    const d = now.close.closed - before.close.closed;
    if (d > 0) out.push({ key: 'close', text: `${plural(d, 'store day')} closed`, tone: 'better' });
  }
  const order: Record<ChangeTone, number> = { worse: 0, neutral: 1, better: 2 };
  return out.sort((x, y) => order[x.tone] - order[y.tone]);
}

/** How one queue row differs from the last visit: new to the queue, or newly past SLA. */
export function rowMark(
  before: Seen | null,
  id: string,
  slaState: string
): 'new' | 'breached' | null {
  if (!before) return null;
  const was = before.queue[id];
  if (was === undefined) return 'new';
  if (slaState === 'breached' && was !== 'breached') return 'breached';
  return null;
}

const KEY = (user: string) => `nimbus_cc_seen:${user}`;

export function readSeen(user: string): Seen | null {
  try {
    const raw = localStorage.getItem(KEY(user));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Seen;
    return parsed && typeof parsed.at === 'string' && parsed.counts ? parsed : null;
  } catch {
    return null;
  }
}

export function writeSeen(user: string, seen: Seen): void {
  try {
    localStorage.setItem(KEY(user), JSON.stringify(seen));
  } catch {
    /* storage unavailable: the comparison is a convenience */
  }
}
