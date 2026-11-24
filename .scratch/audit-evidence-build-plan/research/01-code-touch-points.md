# Code touch points for the decided audit-evidence capabilities

Read-only survey. Paths are relative to `/Volumes/Cabinet/Projects/ZeTSA`. Latest migration is `0133_workbook_net_retail.py` (next is 0134). No design or estimate is implied.

## Shared facts

- **Ontology is in code.** `backend/integrations/ontology/ontology.py` holds `ONTOLOGY_VERSION = "ontology-v1.0"`, `DEFAULT_EVIDENCE`, `EVIDENCE_SOURCES` and `EXCEPTION_TYPES`. Each type has label, family, `exposure` (zero/amount), `evidence` list, `known_causes` and `disposition`. There is no level, ceiling, class-map or kill-switch field. `get(type)` returns the dict. It is served by `backend/apps/ontology/routes.py` (`/api/ontology`, `/api/ontology/exception-types/{type}`). `ontology_integration.py` is an HTTP client for an external ontology release. `audit_controls.py` parses rules and autonomy limits from that release (`nimbus.policy_limits()`).
- **Rule-raised codes** have no ontology entry. `apps/ingest/reconciliation.py::error_definition` falls back to the `sales_audit_error_definitions` row (`SalesAuditErrorDefinition` in `backend/apps/totals/models.py`). It returns `exposure: "zero"` and `DEFAULT_EVIDENCE`.
- **Cases:** `backend/apps/cases/models.py` has `Case`, `Exception` (as `DBException`), `EvidenceSnapshot`, `EvidenceRequest`, `Finding`, `Recommendation`, `HumanDecision`, `Workflow`, `CaseNote`. `models.py` at the backend root is only 174 lines.
- **Core flow:** `apps/agents/investigation_service.py::run_investigation` gathers records via `evidence_for_requirement`, then `integrations/ontology/nimbus.py::investigate` writes `EvidenceSnapshot` rows, one `Finding`, one `Recommendation` and an `INVESTIGATION_COMPLETED` audit event. `investigator.py` (the LLM agent, SDK or Messages API) only supplies a narrative string. Its trace goes into an `AGENT_RUN` audit event.
- **Audit events** are written via `nimbus.audit(...)`, which adds `ontology_context` provenance. Model: `backend/apps/audit/models.py`. Hash chain: migrations 0004, 0049.
- **Agent settings:** `AgentSetting` key/value table (`apps/agents/models.py`, migration 0022). Keys today: `investigator_enabled`, `auto_investigate`, `model`, `tools`. Validated in `agent_settings.py`, exposed by `apps/agents/routes.py` (`GET /config`, `PUT /settings`).

## Per capability

### 1. Agent findings on evidence snapshots
- **Exists:** `EvidenceSnapshot` (`evidence_data` JSON, `evidence_status` Complete/Unavailable). `Finding` is per case/investigation (conclusion, `finding_type`, confidence, rationale) and is not linked to a snapshot. `investigator.validate(result, seen_ids)` checks cited ids against tool output. `investigator.render` flattens the result to text. `GET /api/cases/{id}/evidence` (`cases/evidence.py::case_evidence`) returns type, status, source, retrieved_at and ids only.
- **Touch:** `investigator.py` (structured per-requirement result), `investigation_service._investigation_narrative`, `nimbus.investigate`, `cases/models.py`, `cases/evidence.py`, `cases/schemas.py`.
- **New:** a verdict (supported/contradicted/inconclusive) and cited ids stored per snapshot. Either `evidence_data` JSON or new columns. There is no migration yet, and no snapshot-to-finding link.
- **Tests:** `apps/agents/tests/test_investigator.py` (883 lines), `test_evidence.py`, `test_investigation_narrative.py`, `apps/cases/tests/test_evidence_pagination.py`, `backend/evals/` (`scenarios.json`, `run_investigator_evals.py`).

### 2. Change-based staleness and regather
- **Exists:** `EvidenceSnapshot.staleness_threshold` (default 14400 s). It is never read: no code uses it. Store-day staleness is separate: `store_days/freshness.py` sets `StoreDay.totals_stale` on re-import/re-total (migration 0036). `run_investigation` is the regather path. It supersedes prior `Proposed` recommendations. `provide_case_evidence` re-runs it when the case is "Awaiting Evidence". `auto_investigate` (`investigation_service.py`) and `bulk_actions._investigate_case` also call it. `nimbus.investigate` appends new snapshots each run and does not mark old ones.
- **Touch:** `store_days/freshness.py` and `store_days/duplicates.py` (change signals), `ingest/auto_audit.py::reconcile_after_import`, `ingest/reconciliation.py`, `investigation_service.py`, `nimbus.investigate`, `cases/models.py`, `cases/evidence.py`.
- **New:** a stale marker on snapshots, a link from a source change to affected cases, a regather trigger, and the "needs review" mark on a recommendation (no such field on `Recommendation`).
- **Tests:** `store_days/tests/test_batched_freshness.py`, `test_ingest_freshness.py`, `test_configuration_freshness.py`, `test_postgres_freshness.py`, `agents/tests/test_evidence.py`.

### 3. Evidence provenance fields
- **Exists:** `evidence_data` holds `retrieved_by` (investigation id), `ontology` version, `source_record_ids`, `canonical_transaction_ids`, `source_records_truncated`. `source_system` and `retrieved_at` are columns. `AGENT_RUN` events carry the trace (model, tool_calls, tokens). `Finding` and `Recommendation` have no producer or model field.
- **Touch:** `nimbus.investigate`, `investigator.py` trace, `cases/models.py`, `cases/evidence.py` response, `frontend/lib/api.ts` (`EvidenceItem`).
- **New:** producer (deterministic vs agent plus model version), tool or query trace per item, source batch/data version. The ontology version is already recorded.
- **Tests:** `agents/tests/test_investigator.py`, `test_evidence.py`.

### 4. Ready rule and conflict block
- **Exists:** "ready" is `complete == len(required)` in `nimbus.investigate`, which sets `investigation_status = "Ready for Decision"` and `status = "In Review"`. `evidence_completeness` is a percent. Human evidence enters via `EvidenceRequest` (`provide_case_evidence`, `provided_evidence`). Decisions are made in `cases/records.py::create_decision` and `policy_evaluation` (`nimbus.evaluate_policy`). `bulk_actions._decide_case` is the bulk path.
- **Touch:** `nimbus.investigate`, `nimbus.evaluate_policy`, `cases/records.py`, `agents/bulk_actions.py`, `agents/routes.py` (`/eligible`), `cases/workflows.py`.
- **New:** contradiction/inconclusive check, conflict detection between sources or human item and finding, a conflict state and its resolution action and event. None exists.
- **Tests:** `agents/tests/test_investigator.py`, `test_bulk_investigation_batching.py`, `test_evidence.py`, `cases/tests/test_decision_actor.py`, `test_validation.py`.

### 5. Agent levels and class map in the ontology
- **Exists:** `EXCEPTION_TYPES` entries have `disposition` (disposition, action class, exposure mode, workflow). Today's only "act" is `store_days/duplicates.py::resolve_duplicate_cases`, called from `store_days/automation.py::settle_day`/`run_store_day_agent` (no ceiling, no level lookup). Hard-coded Shopify branch in `nimbus.investigate` (`SHOPIFY_POSSIBLE_DUPLICATE_ORDER` treated as duplicate). Ontology context fetched by `ontology_integration.context()`.
- **Touch:** `integrations/ontology/ontology.py` (and `ontology_integration.py`/`audit_controls.py` if the class map should come from the release), `apps/ontology/routes.py`, `nimbus.py`, `store_days/duplicates.py`, `store_days/automation.py`, `agents/investigation_service.py`, `agents/agent_tools.py`, `apps/assistant/` (`test_ontology_reference.py` checks ontology text).
- **New:** a level field per type (default collect for unmapped), a ceiling per class, the ceiling check, the 20 added type assignments, and the propose/act gates. The default level is not read anywhere today.
- **Tests:** `integrations/ontology/tests/test_ontology_integration.py`, `store_days/tests/test_store_day_agent.py`, `assistant/tests/test_ontology_reference.py`.

### 6. Kill switch, daily cap, reopen reversal
- **Exists:** `auto_investigate` and `investigator_enabled` settings are global. Store-day reopen: `store_days/closures.py::reopen_store_day` (finance role, reason required, `reopen_guards`, `STORE_DAY_REOPENED` event, status "Reopened"). It does not touch cases or transactions. `duplicates.py` sets `CanonicalTransaction.disposition = "Duplicate"`, closes the case ("Auto-Resolved"), emits `DUPLICATE_SALE_EXCLUDED`, then `invalidate_totals`. There is no case reopen route: `update_case` forbids status changes. `store_days/routes.py` ~line 1235 reads reopen history. Frontend store-day page already lists reopens.
- **Touch:** `agents/agent_settings.py` (+ `AgentSetting` rows or a new table), `agents/routes.py`, `store_days/duplicates.py`, `store_days/closures.py`, `store_days/automation.py`, `cases/routes_lifecycle.py`, `cases/models.py`, `corrections/` (un-exclude/re-total), `frontend/components/AgentsConfig.tsx`, `frontend/app/settings/agents/page.tsx`.
- **New:** per-class switch, daily counter/cap, case reopen action and event, un-exclude, link from reopen to the action it undid, coded reopen reason.
- **Tests:** `store_days/tests/test_store_day_agent.py`, `test_backposting_and_store_days.py`, `agents/tests/test_roles.py`.

### 7. Group-level case dedupe (bank/GL/manifest)
- **Exists:** `ingest/reconciliation.py::create_reconciliation_cases` groups findings by (type, store, date, currency, rule id, rule name) within one run, and always inserts a new `Case`. Bank/GL/manifest findings come from `ingest/controls.py::run_controls`/`run_settlement_controls` (`BANK_VARIANCE`, `MISSING_DEPOSIT`, `GL_VARIANCE`, `MISSING_GL_POSTING`, `MISSING_TRAN_BIG_GAP`, `INCOMPLETE_FEED`, `BALANCING_VARIANCE`). Rule-raised and sequence-gap cases check for an open case elsewhere: `store_days/rule_evaluation.py` and `ingest/sequence.py`. Shopify day controls are in `ingest/shopify/day_controls.py`. Index `0070_exception_case_keyset_index`, `0067_case_creation_transaction_gin_index`, `0087` area is relevant to lookups.
- **Touch:** `ingest/reconciliation.py::create_reconciliation_cases`, `ingest/controls.py`, `ingest/shopify/day_controls.py`; possibly a migration for a lookup index or unique key.
- **New:** open-case lookup and update path for group findings. Update-in-place semantics (amount, exception row, event) do not exist.
- **Tests:** `ingest/tests/test_manifests.py`, `test_settlement_currency.py`, `test_shopify_day_totals.py`, `test_sequence_gaps.py`, `test_line_audit_rules.py`, `test_corrected_reconciliation.py`, `test_reconciliation_store_scope.py`.

### 8. Exposure priority and agent escalation
- **Exists:** priority is computed on raw amount in `create_reconciliation_cases` (`>3000` Critical, `>1000` High) and in `development/routes.py`. `nimbus.SLA_HOURS` and `sla_due`. `Case.total_exposure` and `Exception.estimated_exposure` already exist (exposure `zero` or `amount` from ontology). Priority can be changed by finance in `update_case` and bulk action `priority` (`bulk_actions._apply_simple_action`). `Escalated` decision at `cases/records.py:297`. `action_center.py` (`sla_state`, `summary`, `groups`) and `action_center_filters.py` read priority/SLA.
- **Touch:** `ingest/reconciliation.py`, `nimbus.py`, `cases/routes_lifecycle.py`, `agents/bulk_actions.py`, `agents/action_center*.py`, `cases/records.py`, `cases/models.py`.
- **New:** priority from exposure, agent-triggered escalation (conflict, contradiction, collect-level class), priority history, escalation time/target, SLA breach record.
- **Tests:** `agents/tests/test_action_center_filters.py`, `test_action_center_currency_scope.py`, `test_currency_policy.py`, `cases/tests/test_validation.py`.

### 9. Carry-forward with age limit
- **Exists:** close gate is `store_days/readiness.py::_blocks_close` (counts open cases unless every exception has `close_impact == "No"`) and `store_days/closures.py::close_blocker` (counts all non-Closed cases for the day, ignoring `close_impact`). `close_impact` is set `"Yes" if amount else "No"` in four places (`reconciliation.py`, `rule_evaluation.py`, `cases/records.py`, `development/routes.py`). `export_block_destinations` on the error definition is an export gate, not a close gate. Cases are keyed to a single `business_date`. Next-day opening: `store_days/automation.py::open_following_day`.
- **Touch:** `readiness.py`, `closures.py`, `automation.py` (`ready`, `settle_day`), `exports/eligibility.py`, `cases/models.py`, `apps/command_center/routes.py`.
- **New:** a blocking flag on the error definition, carry-forward state and age counter, business-day age calculation, promotion to blocking. Note the two close gates disagree today.
- **Tests:** `store_days/tests/test_readiness_and_variances.py`, `test_store_day_agent.py`, `test_backposting_and_store_days.py`, `command_center/tests/test_sections.py`.

### 10. Cases for unexplained variances and overdue sources
- **Exists:** variance evaluation in `store_days/variances.py` (`evaluate`, `unresolved`, tolerance and `VarianceExplanation`). It only feeds `comparison_blockers` and `close_blocker`, and creates no case. Explanations via `variance_routes.py::explain_variance`. Late-source detection: `readiness.late_stores`, `missing_data_errors`, `GAP_ERRORS`, `data_quality`; connector health in `system/connectors.py` and migration 0046; manifest tracking in `ingest/manifests.py`; `diagnostics/feeds.py`. `CONNECTOR_TIMEOUT` and `INCOMPLETE_FEED` exist in the ontology. `CONNECTOR_TIMEOUT` is only seeded by `development/routes.py`. No production code raises it. There is no overdue deadline setting. Scheduled sweeps live in `store_days/automation.py::loop/sweep_once`, `operations/batch.py`, `ingest/shopify/scheduler.py`, and `ingest/source_settings.py` (migrations 0050, 0053).
- **Touch:** `store_days/variances.py`, `store_days/automation.py`, `ingest/reconciliation.py` (case creation), `store_days/readiness.py`, `ingest/source_settings.py`, `system/connectors.py`.
- **New:** variance-to-case registration, an overdue check and its deadline setting, delivery evidence for a connector case.
- **Tests:** `store_days/tests/test_readiness_and_variances.py`, `system/tests/test_connectors.py`, `diagnostics/tests/test_diagnostics.py`, `ingest/tests/test_source_settings.py`, `test_source_schedule_audit.py`.

### 11. Per-code override requirement and who-may-override
- **Exists:** evidence override: `cases/evidence.py::override_evidence_request`/`approve_evidence_override` (finance role only). A second approver is needed only when the impact exceeds `limits.autonomy_limit` or the currency differs (`nimbus.policy_limits()`). Exception-level override per code: `audit/error_routes.py` lines ~491-530 (`/{code}/exceptions/{exception_id}/override`, `/approve`, `/reject`) using `Exception.override_*` (migration 0085). Roles: `security/authentication.py::require_role`, `security/access.py`, user roles in migration 0121.
- **Touch:** `audit/error_routes.py` (`ErrorDefinitionBody`, `error_definition_state`, `definition_text`, package preview/apply), `totals/models.py::SalesAuditErrorDefinition`, `cases/evidence.py`, `ErrorDefinitionPackageBody`, `frontend/components/settings/ErrorDefinitionPackages.tsx`, `frontend/components/AuditErrorDetail.tsx`.
- **New:** override-requirement setting (none/reason/reason and approver) and store/HQ/both on the definition row; enforcement in both override paths. Neither field exists. No store-versus-HQ concept exists in role checks.
- **Tests:** `audit/tests/test_routes.py`, `test_error_overview.py`, `test_error_at_scale.py`; `controls/rules/tests/test_packages.py`; `frontend/app/audit-errors/page.test.tsx`; the evidence-override tests are inside `agents/tests/test_evidence.py` (check before assuming).

### 12. Optional evidence list and exposure on error definitions
- **Exists:** `SalesAuditErrorDefinition` has code, texts, form, tab, `recommended_solution`, `active`, `export_impact_configured`, `export_block_destinations` (migrations 0072, 0083). Fallback in `reconciliation.error_definition` hardcodes `exposure: "zero"`. It also uses `DEFAULT_EVIDENCE`. `run_investigation` and `provide_case_evidence` use `ontology.get(...)` only. They ignore the table for evidence, so a rule-raised code always falls to `DEFAULT_EVIDENCE`. `EVIDENCE_SOURCES` maps requirement names to sources. Package import/export: `error_routes.py` `/definitions/package*`.
- **Touch:** `totals/models.py`, new migration, `audit/error_routes.py` (body, state, package diff), `reconciliation.error_definition`, `investigation_service.run_investigation`, `nimbus.investigate`, `cases/evidence.py`, `frontend/components/settings/ErrorDefinitionPackages.tsx`, `AuditErrorDetail.tsx`.
- **New:** the two columns and the "evidence not defined" flag in the error-code admin.
- **Tests:** same as 11, plus `ingest/tests/test_audit_errors.py`, `test_line_audit_rules.py`.

### 13. Evidence panel in the case screen
- **Exists:** `frontend/app/cases/[id]/page.tsx` (1820 lines, one file). Tabs `overview | records | activity`. Evidence table at ~line 1424 ("Records & evidence" tab). Request/reject/override/provide forms at ~376-471 and ~1576-1761. Awaiting-evidence banner ~723. Evidence % at ~582. Types (`EvidenceItem`) and client in `frontend/lib/api.ts`. Backend data: `GET /api/cases/{id}/evidence`, `/collaboration` (`case_collaboration`), `/detail`, `/history`. Prototype referenced by the spec is at `.scratch/evidence-panel-and-quality/prototype/panel.html`. Roles in the UI come from `components/MeProvider.tsx`.
- **Touch:** `app/cases/[id]/page.tsx`, `lib/api.ts`, `components/Badge.tsx`, `components/ui/`, backend `cases/evidence.py`, `cases/schemas.py`, `cases/routes_lifecycle.py::get_case_detail`; `DESIGN_SYSTEM.md` and `app/styles` for navy tokens.
- **New:** the panel (header, state banner, per-item finding/stale/conflict badges, role-gated actions), a derived state (Collecting/Ready/Conflict/Stale/Needs review/Override pending/Decided), a resolve-conflict action, and a mark-reviewed action.
- **Tests:** none for the case page (`frontend/app/cases/` has no test file). Closest patterns: `app/store-days/[storeId]/[date]/page.test.tsx`, `app/audit-errors/page.test.tsx`, `components/CommandCenter.test.tsx`; vitest config `frontend/vitest.config.mts`, helpers in `frontend/test/`.

### 14. Missing quality signals and the four measures
- **Exists:** `AGENT_RUN` events (trace: model, tool_calls, input/output tokens; `cost_usd`, `turns` on the SDK path only). There is no duration. The Messages API path sets no cost. The store-day agent writes no run record (it returns counts). `STORE_DAY_REOPENED` events (reason in `after_state`, no coded reason). `DUPLICATE_SALE_EXCLUDED` events. `EvidenceRequest` rejection/override fields. `HumanDecision` (decision, override_reason). `Case.sla_due_at`, `snoozed_until`. Related reads: `diagnostics/activity.py` (event list includes `STORE_DAY_REOPENED`), `diagnostics/health.py`, `command_center/routes.py`, `agents/routes.py` (`/shadow` compares agent vs human; check before reuse).
- **Touch:** `investigator.py` (timing, cost on every path), `investigation_service.py`, `store_days/automation.py`, `cases/models.py`, `cases/records.py`, `store_days/closures.py`, `diagnostics/`, `agents/routes.py`, new migration for a run table and indexes.
- **New:** run table, case-closed time, SLA breach record, priority history, escalation time/target, recommendation origin, finding-held outcome, coded reopen reason and reopen-to-action link, the four rate queries, thresholds and auto level drop, notifications (`frontend/components/Notifications.tsx` exists), and any metrics view.
- **Tests:** `diagnostics/tests/test_diagnostics.py`, `agents/tests/test_investigator.py`.

## Cross-cutting
- **Migrations:** every model change above needs a numbered file after 0133. The most recent migrations tend to be small single-purpose files, e.g. `0024_evidence_override.py` (adds nullable columns to `evidence_requests`).
- **Frontend settings** pages for agents, close and controls: `frontend/app/settings/{agents,close,controls}` and `components/AgentsConfig.tsx`, `components/settings/CloseControls.tsx`.
- **Docs to keep aligned:** `documents/AUDIT_EVIDENCE_DECISION_LOG.md` (referenced by both decision documents, not read here).
