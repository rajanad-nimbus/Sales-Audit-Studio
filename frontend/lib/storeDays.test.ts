import { describe, expect, it } from 'vitest';
import { dayPath, outcomeVariant, qualityVariant, readinessVariant } from '@/lib/storeDays';

describe('readinessVariant', () => {
  it('marks finished states as success and trouble as error', () => {
    for (const state of ['Complete', 'Clean', 'Final', 'Exported']) {
      expect(readinessVariant(state)).toBe('success');
    }
    expect(readinessVariant('Incomplete')).toBe('error');
    expect(readinessVariant('Needs attention')).toBe('error');
  });
  it('treats not-yet states as neutral and anything else as a warning', () => {
    expect(readinessVariant('Not exported')).toBe('secondary');
    expect(readinessVariant('In progress')).toBe('secondary');
    expect(readinessVariant('Out of date')).toBe('warning');
    expect(readinessVariant('No control total')).toBe('warning');
  });
});

describe('outcomeVariant', () => {
  it('shows balanced and within-tolerance variances as fine', () => {
    expect(outcomeVariant('Balanced')).toBe('success');
    expect(outcomeVariant('Within tolerance')).toBe('success');
    expect(outcomeVariant(null)).toBe('success');
  });
  it('shows an explained variance as info, and an unexplained one by how serious it is', () => {
    expect(outcomeVariant('Needs reason', true)).toBe('info');
    expect(outcomeVariant('Needs reason', false)).toBe('warning');
    expect(outcomeVariant('Needs case', false)).toBe('error');
  });
});

describe('qualityVariant', () => {
  it('flags partial data as an error and unconfirmed data as a warning', () => {
    expect(qualityVariant('Final')).toBe('success');
    expect(qualityVariant('Partial')).toBe('error');
    expect(qualityVariant('Superseded')).toBe('secondary');
    expect(qualityVariant('Provisional')).toBe('warning');
    expect(qualityVariant('Stale')).toBe('warning');
  });
});

describe('dayPath', () => {
  it('encodes the store so unusual ids stay in one path segment', () => {
    expect(dayPath({ store_id: 'S 1/2', business_date: '2026-10-01' })).toBe(
      '/store-days/S%201%2F2/2026-10-01'
    );
  });
});
