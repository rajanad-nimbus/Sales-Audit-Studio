import axios from 'axios';
import { notifyDataChanged } from './sync';

export const api = axios.create({ baseURL: process.env.NEXT_PUBLIC_API_URL, withCredentials: true });

api.interceptors.request.use((config) => {
  try {
    const token = localStorage.getItem('nimbus_token');
    if (token) config.headers.Authorization = `Bearer ${token}`;
    const as = localStorage.getItem('nimbus_impersonate');
    if (as) config.headers['X-Impersonate-Role'] = as;
    // An admin acting as a store manager chooses which stores to simulate; a real store manager's stores come from Ontology Studio.
    const stores = localStorage.getItem('nimbus_impersonate_stores');
    if (as === 'store_manager' && stores) config.headers['X-Impersonate-Stores'] = stores;
  } catch { /* storage unavailable */ }
  return config;
});

// Any successful write means other screens' numbers may be stale.
api.interceptors.response.use((res) => {
  const m = (res.config.method ?? 'get').toLowerCase();
  if (m !== 'get' && !(res.config.url ?? '').includes('/api/chat')) notifyDataChanged();
  return res;
});

export interface Case {
  id: string;
  case_number: string;
  status: string;
  case_type: string;
  business_date: string;
  store_id: string;
  total_exception_amount: string;
  total_exposure: string;
  investigation_status: string;
  evidence_completeness: number;
  assigned_to: string | null;
  priority: string;
  sla_due_at: string | null;
  snoozed_until: string | null;
  created_at: string;
}

export interface NimbusException {
  id: string;
  exception_type: string;
  exception_family: string;
  source_system: string;
  exception_amount: string;
  estimated_exposure: string;
  severity: string;
  close_impact: string;
  status: string;
}

export interface Finding {
  id: string;
  conclusion: string;
  finding_type: string;
  confidence: string;
}

export interface Recommendation {
  id: string;
  disposition_type: string;
  action_class: string;
  expected_workflow: string;
  financial_impact: string;
  confidence: string;
  status: string;
}

export interface Decision {
  id: string;
  decision: string;
  decision_type: string;
  actor: string;
  authority_check: string;
  override_reason: string | null;
  created_at: string;
}

export interface ReconciliationRecord {
  transaction_id: string; source_system: string; source_record_id: string | null;
  transaction_type: string; business_date: string; event_timestamp: string; amount: string;
  currency: string; payment_reference: string | null; settlement_reference: string | null;
  settlement_date: string; reconciliation_status: string; source_lineage: string;
}

export interface ReconciliationDetail {
  linked: boolean; message: string; sources: string[]; records: ReconciliationRecord[];
}

export interface CaseDetail {
  case: Case;
  exceptions: NimbusException[];
  findings: Finding[];
  recommendations: Recommendation[];
  decisions: Decision[];
}

export interface AuditEvent {
  id: string;
  event_type: string;
  actor: string;
  object_type: string;
  case_id: string | null;
  action_description: string;
  created_at: string;
}

export const money = (v: string | number) =>
  Number(v).toLocaleString('en-US', { style: 'currency', currency: 'USD' });

export interface Workflow {
  id: string;
  case_id: string;
  workflow_type: string;
  state: string;
  current_step: string;
}

export interface Validation {
  id: string;
  case_id: string;
  workflow_id: string;
  status: string;
  expected_observation: string;
  verification_result: string | null;
}

export interface PolicyEval {
  id: string;
  outcome: string;
  rule_version: string;
  reasons: { reasons: string[] };
}

export interface Connector {
  id: string;
  name: string;
  state: string;
  mode: string;
  last_error: string | null;
}

export interface ACRow {
  id: string; case_number: string; case_type: string; store_id: string; business_date: string;
  status: string; priority: string; amount: string; exposure: string; evidence: number; investigation_status: string;
  assigned_to: string | null; sla_due_at: string | null; sla_state: 'breached' | 'at_risk' | 'ok' | 'none';
  snoozed_until: string | null; age_hours: number;
}
export interface BatchStatus {
  freshness: 'fresh' | 'stale' | 'never'; data_as_of: string | null; hours_since_success?: number;
  last_success?: { finished_at: string; business_date: string; transactions_loaded: number; cases_created: number } | null;
  last_failed?: boolean;
}
export const compact = (n: number) => new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(n);

export interface ExportDest { id: string; name: string; kind: 'webhook' | 'file'; format: 'json' | 'csv'; enabled: boolean; config: { url?: string } }
export interface ExportBatchRow {
  id: string; destination: string; dataset: string; status: 'Pending' | 'Sent' | 'Acknowledged' | 'Failed' | 'Cancelled'; record_count: number;
  payload_hash: string | null; created_by: string; created_at: string; sent_at: string | null; acked_at: string | null;
  response_code: number | null; response_ref: string | null; error: string | null; retries: number; backposted_count: number;
}
export interface AuditRow {
  seq: number; id: string; created_at: string; event_type: string; actor: string; object_type: string; object_id: string;
  case_id: string | null; description: string; hash: string; prev_hash: string | null;
}
export interface EvidenceItem { id: string; evidence_type: string; status: string; source_system: string; retrieved_at: string; source_record_ids: string[] }
export interface TimelineItem { id: string; seq: number; event_type: string; actor: string; description: string; created_at: string }
