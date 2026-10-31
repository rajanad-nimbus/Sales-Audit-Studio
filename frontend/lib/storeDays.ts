import type { BadgeVariant } from '@/components/ui';

export interface StoreDay {
  store_id: string;
  business_date: string;
  status: string;
  audit_version: number;
  totals_stale: boolean;
}
export interface Total {
  id: string;
  name: string;
  level: string;
  dimension: string | null;
  calculated_amount: string;
  declared_amount: string | null;
  variance_amount: string | null;
  currency: string;
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
