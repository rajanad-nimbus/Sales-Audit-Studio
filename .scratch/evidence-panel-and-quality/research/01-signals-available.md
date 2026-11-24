# Signals already recorded for four quality measures

Read-only survey of `backend/apps` and `backend/integrations/ontology`. Facts only.

Common substrate: `audit_events` (apps/audit/models.py) has event_type, actor, object_type, object_id, case_id, action_description, before_state/after_state (JSON), created_at, seq/hash chain. Written through `nimbus.audit()` (integrations/ontology/nimbus.py). Every row's `after_state` also carries `ontology_context` provenance. There is no separate table for agent runs, only audit rows.

## 1. Wrong act

Exists
- Store-day reopen: `store_days.status = "Reopened"`, `reopened_by`, `reopened_reason` (apps/store_days/models.py). Audit `STORE_DAY_REOPENED` (apps/store_days/closures.py) with after_state {reason, audit_version, closed_at, closed_by}. `closed_by` = "store-day-agent" identifies agent closes that were later reopened. Only the latest reopen sits on the row; history is read from audit events.
- Agent close trail: audit `STORE_DAY_CLOSED`, `STORE_DAY_AUDIT_SIGNED_OFF`, `STORE_DAY_OPENED` (actor store-day-agent, apps/store_days/automation.py); `store_days.closed_by`, `audit_signed_by`; `store_day_closure_requests` (requested_by, reviewed_by, status, evidence_basis).
- Duplicate exclusion: audit `DUPLICATE_SALE_EXCLUDED` (apps/store_days/duplicates.py), actor store-day-agent, object CanonicalTransaction, case_id set, after_state {original_transaction_id, copy/original payment_reference}. Effects: `canonical_transactions.disposition = "Duplicate"`, case status Closed with investigation_status "Auto-Resolved", exceptions Closed.
- Agent-closed store days are not re-closed once `reopened_by` is set (automation.py), which is a behavior, not a metric.
- Other reversals: `ach_adjustments.status = "Reversed"`, `reversed_by`, `reversed_at`, audit `ACH_ADJUSTMENT_REVERSED` (apps/cash). Not agent-driven.
- `RECOMMENDATION_SUPERSEDED` audit and `recommendations.status = "Superseded"` (cases/records.py, agents/investigation_service.py).

Missing
- No case reopen. Cases never go from Closed back to Open (only Rejected decision sets Open before closure). Routes refuse changes to Closed cases ("follow-up review is required"), and no follow-up case link exists.
- No link between a reopened store day and the agent act that closed it, other than comparing `closed_by` in the audit payload. No link from a reopen to excluded duplicates.
- No un-exclude / restore of a transaction whose `disposition = "Duplicate"`; no audit event for it. The excluded transaction records no `excluded_by`/`excluded_at` columns; only the audit event does.
- No counter or flag marking an act as "agent" versus "human" on cases beyond `investigation_status = "Auto-Resolved"` and audit actor.
- No reopen count column (only last reason/by).

## 2. Wrong finding

Exists
- Evidence request (`evidence_requests`, apps/cases/models.py): status (Open/Fulfilled/Rejected), `rejection_reason`, `override_reason`, `override_financial_impact`, override_requested_by/at, override_approved_by/at, fulfilled_by/at, response, reference. Audit: `EVIDENCE_REQUESTED`, `EVIDENCE_PROVIDED`, `EVIDENCE_REQUEST_REJECTED`, `EVIDENCE_OVERRIDE_REQUESTED`, `EVIDENCE_OVERRIDE_APPROVED` (apps/cases/evidence.py). Applying an override sets case Closed with investigation_status "Evidence Overridden".
- Exception-level override fields on `exceptions` (override_reason, requested/approved by/at).
- `findings` (conclusion, finding_type, confidence, supporting_rationale, investigation_id) and `recommendations` (disposition_type, action_class, financial_impact, confidence, status Proposed/Approved/Rejected/Escalated/Superseded), `human_decisions` (recommendation_id, decision Approved/Rejected/Escalated, actor, override_reason holding the rationale, authority_check). Audit `HUMAN_DECISION`, `INVESTIGATION_COMPLETED`, `RECOMMENDATION_SUPERSEDED` (cases/records.py).
- Disagreement can be derived by joining `human_decisions.decision = "Rejected"` (or Escalated) to its `recommendations` row; a rationale is mandatory.
- `evidence_snapshots` (evidence_type, evidence_status, retrieved_at, staleness_threshold) and `cases.evidence_completeness`.
- `policy_evaluations` (outcome Permit/RequireHuman/RequestEvidence, reasons).
- Agent investigator result stored in AGENT_RUN audit after_state (`result`, `tool_calls`, cited ids validated before accept).
- Offline: `backend/evals/run_investigator_evals.py` with `scenarios.json` (scenario-based, not production data).
- Manual match rejection: audit `MANUAL_MATCH_REJECTED`, status Rejected on proposed matches (apps/ingest).

Missing
- `findings` rows have no foreign key to evidence snapshots or tool results (no cited-evidence list on the table); citations live only in AGENT_RUN JSON.
- `human_decisions` does not record whether the recommendation came from the LLM agent or the rules path; `recommendations` has no `source`/`model` column.
- Evidence rejection is not tied to which snapshot or agent finding it contradicted.
- No later-correctness label (finding confirmed/refuted after validation). `validation_obligations.verification_result` exists but is not linked to the finding.
- No recorded reviewer confidence or agreement field; rejection reasons are free text, not categorized.

## 3. Missed escalation

Exists
- `cases.priority` (Critical/High/Normal), `cases.sla_due_at`, index `ix_cases_status_sla`, `snoozed_until`, `assigned_to`, `created_at`, `updated_at`. SLA hours: Critical 4, High 24, Normal 72 (`nimbus.SLA_HOURS`, `sla_due()`).
- Priority set by amount thresholds at case creation (apps/ingest/reconciliation.py: >3000 Critical, >1000 High); editable by finance/admin (cases/routes_lifecycle.py, bulk_actions.py "priority", "snooze", "unsnooze").
- Escalation path: `human_decisions.decision = "Escalated"` sets `recommendations.status` and `cases.status` to "Escalated"; audit `HUMAN_DECISION`. Bulk actions in agents/bulk_actions.py.
- `AUDIT` of priority changes depends on the generic case update audit (not enumerated here).
- Assistant and action center read priority/sla_due_at (assistant/chat.py, agents/action_center.py).

Missing
- No breach record: nothing stores that `sla_due_at` passed, when, or how late a case closed. Closure time is derivable only from audit/updated_at; there is no `closed_at` column on `cases`.
- `sla_due_at` is not recomputed or logged when priority changes in some paths (set at creation; routes_lifecycle sets it on create, confirm on update before relying).
- No escalation timestamp, escalation target, or escalated-by column; only the status and the HUMAN_DECISION event.
- No automatic escalation job or SLA-expiry event; no record of cases that should have escalated but did not.
- No history table for priority (only whatever audit events exist).
- Agents do not set or change priority/SLA (reconciliation engine sets it; investigation agent "cannot" per agent registry).

## 4. Cost and delay

Exists
- Per-run trace in audit `AGENT_RUN` (actor investigation-agent, case_id, written in apps/agents/investigation_service.py), after_state = trace + result + ontology_context. Trace keys: `model`, `via` (claude-agent-sdk on SDK path only), `tool_calls` (list), `input_tokens`, `output_tokens`, and on SDK path `cost_usd` (`total_cost_usd`) and `turns` (`num_turns`); `error` (exception class name) on failure. Failed runs are marked in action_description ("failed") and `result` is None.
- Limits (constants/env, apps/agents/investigator.py): MAX_TURNS 6, MAX_OUTPUT_TOKENS 1200, MAX_COST_USD (env NIMBUS_AGENT_MAX_COST_USD, default 0.50), TIMEOUT_S 90. Exposed by apps/agents/routes.py.
- Aggregation exists in apps/agents/routes.py (about line 515): over recent runs, count, failed, summed input_tokens and output_tokens. Also reads AGENT_RUN events around lines 104, 219, 470.
- Admin settings: `agent_settings` table (key, value JSON, updated_by, updated_at) for investigator_enabled, auto_investigate, model, tools; changes audited.
- Case timing indirectly: `cases.created_at/updated_at`, `evidence_snapshots.retrieved_at`, audit `created_at` per event.

Missing
- No duration or latency field in the trace (no start/end timestamps beyond the audit row's single `created_at`). Elapsed time per run is not recorded; a timeout appears only as `error`.
- Messages API path records no `cost_usd` or `turns` (only token counts); the SDK path records no per-call tokens by turn. Cost is not computed from tokens.
- No cache-read/creation token counts.
- `llm.py` narrate/chat calls (narrative note, assistant chat) record nothing: no tokens, cost or audit event. Only the investigator emits AGENT_RUN.
- Store Day Agent (rules, no LLM) records no run row: sweep results are returned from `run_store_day_agent` but not persisted as run, duration or count; only per-act audit events.
- Aggregation sums tokens only; no cost sum, no duration, no per-case or per-store cost; spend is not persisted outside audit JSON (no indexed column, so queries scan JSON).
- No time-to-first-investigation, time-to-decision or time-to-close fields on `cases` (derivable from audit events only).
- No telemetry library wired in backend code (no prometheus, statsd, sentry or OpenTelemetry usage found under backend/apps or integrations).
