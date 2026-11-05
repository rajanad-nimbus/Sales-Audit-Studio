import { beforeEach, describe, expect, it } from 'vitest';
import { changes, readSeen, rowMark, snapshot, writeSeen } from '@/lib/commandCenterSeen';

const at = new Date('2026-11-03T09:00:00Z');
const base = snapshot(
  {
    counts: { sla_breached: 2, needs_decision: 5, at_risk: 3, unassigned: 4, open_total: 20 },
    queue: [
      { id: 'a', sla_state: 'at_risk' },
      { id: 'b', sla_state: 'breached' },
    ],
    failedExports: [{ id: 'x1' }],
    feeds: [
      { source_system: 'POS', state: 'Current' },
      { source_system: 'Shopify', state: 'Stale' },
    ],
    close: { business_date: '2026-11-02', closed: 10 },
  },
  at
);

describe('changes since the last visit', () => {
  it('reports nothing when nothing moved', () => {
    expect(changes(base, { ...base })).toEqual([]);
  });

  it('puts what got worse first, and says what got better', () => {
    const now = snapshot({
      counts: { sla_breached: 5, needs_decision: 3, at_risk: 3, unassigned: 4, open_total: 21 },
      queue: [],
      failedExports: [{ id: 'x2' }],
      feeds: [
        { source_system: 'POS', state: 'Stale' },
        { source_system: 'Shopify', state: 'Current' },
      ],
      close: { business_date: '2026-11-02', closed: 14 },
    });
    expect(changes(base, now).map((c) => [c.text, c.tone])).toEqual([
      ['3 more cases past SLA', 'worse'],
      ['1 more open case', 'worse'],
      ['1 export failed', 'worse'],
      ['POS fell behind', 'worse'],
      ['2 fewer cases waiting for a decision', 'better'],
      ['1 failed export cleared', 'better'],
      ['Shopify caught up', 'better'],
      ['4 store days closed', 'better'],
    ]);
  });

  it('does not compare closings across different business dates', () => {
    const now = { ...base, close: { date: '2026-11-03', closed: 1 } };
    expect(changes(base, now)).toEqual([]);
  });

  it('skips counters this role cannot see', () => {
    const now = { ...base, counts: {} };
    expect(changes(base, now)).toEqual([]);
  });
});

describe('row marks', () => {
  it('marks rows new to the queue and rows that have just breached', () => {
    expect(rowMark(base, 'c', 'ok')).toBe('new');
    expect(rowMark(base, 'a', 'breached')).toBe('breached');
    expect(rowMark(base, 'b', 'breached')).toBeNull();
    expect(rowMark(null, 'c', 'ok')).toBeNull();
  });
});

describe('storage', () => {
  beforeEach(() => localStorage.clear());

  it('keeps one picture per user and ignores anything unreadable', () => {
    writeSeen('ana', base);
    expect(readSeen('ana')).toEqual(base);
    expect(readSeen('ben')).toBeNull();
    localStorage.setItem('nimbus_cc_seen:ana', 'not json');
    expect(readSeen('ana')).toBeNull();
  });
});
