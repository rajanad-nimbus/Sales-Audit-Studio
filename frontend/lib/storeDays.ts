import type { BadgeVariant } from '@/components/ui';

export interface Readiness {
  ingest: {
    state: string;
    transactions: number;
    detail: string;
    day_close?: { state: string; events: number; markers: number; required?: boolean };
  };
  audit: {
    state: string;
    resa_status?: string;
    overall_status?: string;
    open_cases: number;
    unexplained_variances: number;
    missing_transactions?: number;
    detail: string;
    signed_off?: boolean;
    signed_version?: number | null;
    signed_by?: string | null;
    signed_at?: string | null;
    signoff_rationale?: string | null;
  };
  export: { state: string; records: number; pending: number; acknowledged: number };
  ready_to_close: boolean;
}
export interface StoreDay {
  store_id: string;
  business_date: string;
  status: string;
  audit_version: number;
  totals_stale: boolean;
  closed_by?: string | null;
  readiness?: Readiness;
}
export interface Explanation {
  id: string;
  audit_version: number;
  rollup_key?: string | null;
  variance_amount: string;
  outcome: string;
  reason: string;
  case_id: string | null;
  correction_id: string | null;
  explained_by: string;
  created_at: string;
}
export interface Reopen {
  id: string;
  reopened_by: string;
  reopened_at: string;
  reason: string;
  closed_by: string | null;
  closed_at: string | null;
  audit_version: number | null;
}
export interface Total {
  id: string;
  name: string;
  level: string;
  dimension: string | null;
  rollup_key?: string | null;
  calculated_amount: string;
  declared_amount: string | null;
  variance_amount: string | null;
  currency: string;
  outcome?: string | null;
  resolved?: boolean;
  tolerance?: {
    within_amount: string;
    case_amount: string;
    rule_id: string | null;
    total_name: string | null;
    location_trait: string | null;
  };
  explanation?: Explanation | null;
  data_quality?: string;
}

export function formatRollupKey(key?: string | null): string {
  if (!key) return '';
  try {
    return (JSON.parse(key) as [string, string, unknown][])
      .map(([realm, field, value]) => `${realm}.${field}: ${value ?? '—'}`)
      .join(' · ');
  } catch {
    return key;
  }
}
export interface DayCase {
  id: string;
  case_number: string;
  case_type: string;
  status: string;
  exposure: string;
}
export interface Closure {
  id: string;
  requested_by: string;
  evidence_basis: string;
  status: string;
  reviewed_by: string | null;
  review_reason: string | null;
  created_at: string;
}
export interface EvidenceOverride {
  case_id: string;
  case_number: string;
  requirement: string;
  status: string;
  reason: string | null;
  financial_impact: string;
  requested_by: string | null;
  approved_by: string | null;
}
export interface HistoryItem {
  id: string;
  event_type: string;
  actor: string;
  description: string;
  created_at: string;
}

export const dayStatusVariant = (s: string): BadgeVariant =>
  s === 'Closed'
    ? 'success'
    : s === 'Pending'
      ? 'warning'
      : s === 'Rejected'
        ? 'error'
        : 'secondary';
export const apiError = (e: unknown, fallback: string) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
export const dayPath = (d: { store_id: string; business_date: string }) =>
  `/store-days/${encodeURIComponent(d.store_id)}/${d.business_date}`;
export const dayApi = (d: { store_id: string; business_date: string }) =>
  `/api/store-days/${d.store_id}/${d.business_date}`;
export const dayKey = (d: { store_id: string; business_date: string }) =>
  `${d.store_id}|${d.business_date}`;

/** Readiness states that are done; anything else needs attention. */
const GOOD = new Set(['Complete', 'Clean', 'Final', 'Exported', 'Audited', 'Sales Audit Complete']);
const NEUTRAL = new Set(['Not exported', 'In progress', 'Not totaled', 'Totaled']);
export const readinessVariant = (state: string): BadgeVariant =>
  GOOD.has(state)
    ? 'success'
    : NEUTRAL.has(state)
      ? 'secondary'
      : state === 'Incomplete' || state === 'Needs attention'
        ? 'error'
        : 'warning';
export const outcomeVariant = (
  outcome: string | null | undefined,
  resolved?: boolean
): BadgeVariant =>
  !outcome || outcome === 'Balanced' || outcome === 'Within tolerance'
    ? 'success'
    : resolved
      ? 'info'
      : outcome === 'Needs case'
        ? 'error'
        : 'warning';
export const qualityVariant = (q: string | undefined): BadgeVariant =>
  q === 'Final'
    ? 'success'
    : q === 'Partial'
      ? 'error'
      : q === 'Superseded'
        ? 'secondary'
        : 'warning';

/** What an expanded row of the Store Days list shows (GET .../brief). */
export interface StoreDayBrief {
  store_day: StoreDay;
  readiness: Readiness;
  blockers: string[];
  variances: {
    with_variance: number;
    unexplained: number;
    top_unexplained: {
      name: string;
      level: string;
      dimension: string | null;
      rollup_key?: string | null;
      variance: string;
      currency: string;
      outcome: string | null;
    }[];
  };
  cases: {
    open: number;
    top: {
      id: string;
      case_number: string;
      case_type: string;
      status: string;
      exposure: string;
      currency: string | null;
    }[];
  };
  registers_with_gaps: number;
}
